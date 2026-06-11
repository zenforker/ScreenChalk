#!/usr/bin/env python3
"""
ScreenChalk — screen annotation tool for macOS (PyQt6)
v2 features: spotlight, laser pointer, step numbers, multi-monitor
(canvas opens on the screen under the cursor), global hotkey F6
(idle -> freeze & start / drawing -> pause / paused -> resume)

Run:  python3 screenchalk.py
Deps: pip install PyQt6 pynput
Permissions: System Settings -> Privacy & Security -> Screen Recording & Accessibility
"""

import math
import sys
from enum import Enum, auto

from PyQt6.QtCore import (QObject, QPoint, QPointF, QRect, QRectF,
                          QSize, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import (QAction, QBrush, QColor, QCursor, QFont, QFontMetrics,
                         QGuiApplication,
                         QImage, QKeySequence, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF, QShortcut, QRadialGradient)
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFileDialog, QGridLayout,
                             QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget)

# Try pynput for the global hotkey; degrade gracefully if missing
try:
    from pynput import keyboard
    from pynput.keyboard import Key
    HAS_PYNPUT = True
except ImportError:
    HAS_PYNPUT = False


# ---------------------------------------------------------------- constants

class Tool(Enum):
    PEN = auto()
    ERASER = auto()
    LINE = auto()
    ARROW = auto()
    DARROW = auto()
    RECT = auto()
    RECT_F = auto()
    ELLIPSE = auto()
    ELLIPSE_F = auto()
    TEXT = auto()
    CHECK = auto()
    CROSS = auto()
    AREA = auto()
    ZOOM = auto()
    # --- extended tools ---
    PATH = auto()       # polyline (TradingView-style path)
    HLINE = auto()      # full-width horizontal / vertical line
    SPOTLIGHT = auto()  # spotlight
    LASER = auto()      # laser pointer
    NUMBER = auto()     # step number stamp


PEN_SIZES = [2, 4, 8, 14, 22]

TRANSPARENT_COLORS = [
    QColor(255, 235, 0, 110), QColor(0, 200, 80, 110), QColor(255, 80, 180, 110),
    QColor(0, 160, 255, 110), QColor(255, 140, 0, 110), QColor(186, 0, 255, 180),
]
OPAQUE_COLORS = [
    QColor(220, 0, 0), QColor(0, 90, 220), QColor(0, 150, 0),
    QColor(255, 200, 0), QColor(0, 0, 0), QColor(255, 255, 255),
]

UNDO_LIMIT = 40


def shift_down() -> bool:
    return bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)


def constrain_45(p0: QPointF, p1: QPointF) -> QPointF:
    d = p1 - p0
    length = math.hypot(d.x(), d.y())
    if length < 1e-6:
        return p1
    ang = round(math.atan2(d.y(), d.x()) / (math.pi / 4)) * (math.pi / 4)
    return QPointF(p0.x() + length * math.cos(ang), p0.y() + length * math.sin(ang))


def constrain_square(p0: QPointF, p1: QPointF) -> QPointF:
    dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
    side = max(abs(dx), abs(dy))
    return QPointF(p0.x() + math.copysign(side, dx or 1),
                   p0.y() + math.copysign(side, dy or 1))


# ---------------------------------------------------------------- text input

class InlineText(QLineEdit):
    def __init__(self, canvas, pos: QPointF, color: QColor, font: QFont):
        super().__init__(canvas)
        self.canvas = canvas
        self.anchor = pos
        c = QColor(color)
        c.setAlpha(255)
        self.setFont(font)
        self.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,200); color: {c.name()};"
            f" border: 1px dashed {c.name()}; padding: 2px; }}")
        fm_h = self.fontMetrics().height()
        self.move(int(pos.x()), int(pos.y() - fm_h / 2))
        self.setMinimumWidth(220)
        self.returnPressed.connect(self.commit)
        self.show()
        self.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.canvas.text_edit = None
            self.deleteLater()
            return
        super().keyPressEvent(e)

    def commit(self):
        text = self.text()
        if text:
            self.canvas.commit_text(self.anchor, text, self.font())
        self.canvas.text_edit = None
        self.deleteLater()


# ---------------------------------------------------------------- toolbar

class FlatBtn(QToolButton):
    def __init__(self, glyph, tip, checkable=False, fixed=25):
        super().__init__()
        self.setText(glyph)
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setFixedSize(fixed, fixed)
        self.setStyleSheet("""
            QToolButton { border: 1px solid transparent; border-radius: 4px;
                          background: transparent; font-size: 16px; }
            QToolButton:hover { background: #e4e4e4; border-color: #c8c8c8; }
            QToolButton:checked { background: #cfe3ff; border-color: #4a90e2; }
            QToolButton:disabled { color: #bbb; }
        """)


class ColorBtn(QToolButton):
    def __init__(self, color: QColor, tip):
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(15, 15)
        self.setToolTip(tip)
        a = color.alpha()
        rgba = f"rgba({color.red()},{color.green()},{color.blue()},{a / 255:.2f})"
        near_white = min(color.red(), color.green(), color.blue()) > 230
        edge = "1px solid #ccc" if near_white else "none"
        self.setStyleSheet(f"""
            QToolButton {{ border: {edge}; border-radius: 0px; background: {rgba}; }}
            QToolButton:checked {{ border: 2px solid #222; }}
        """)


class SizeBtn(QToolButton):
    def __init__(self, size_px: int):
        super().__init__()
        self.size_px = size_px
        self.setCheckable(True)
        self.setFixedSize(23, 23)
        self.setToolTip(f"Pen size {size_px}px")
        self.setStyleSheet("""
            QToolButton { border: 1px solid transparent; border-radius: 4px;
                          background: transparent; }
            QToolButton:hover { background: #e4e4e4; border-color: #c8c8c8; }
            QToolButton:checked { background: #cfe3ff; border: 1px solid #4a90e2; }
        """)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = min(3 + self.size_px * 0.7, 17)
        p.setBrush(QBrush(QColor("#333")))
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF((self.width() - d) / 2, (self.height() - d) / 2, d, d)
        p.drawEllipse(r)


class Toolbar(QWidget):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._drag = None
        self.setStyleSheet(
            "Toolbar { background: rgba(250,250,250,238); border: 1px solid #aaa;"
            " border-radius: 10px; }")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 1, 2, 2)
        root.setSpacing(1)

        def grid_of(widgets, cols=2, spacing=0):
            g = QGridLayout()
            g.setSpacing(spacing)
            for i, w in enumerate(widgets):
                g.addWidget(w, i // cols, i % cols,
                            alignment=Qt.AlignmentFlag.AlignHCenter)
            root.addLayout(g)

        def separator(color="#ccc"):
            line = QLabel()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background:{color};")
            root.addWidget(line)

        grip = QLabel("⠿ ⠿ ⠿")
        grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grip.setStyleSheet("color:#999; font-size:9px;")
        grip.setFixedHeight(10)
        root.addWidget(grip)

        self.mode_frozen = FlatBtn("❄", "Frozen mode")
        self.mode_live = FlatBtn("▶", "Live mode")
        self.mode_frozen.clicked.connect(lambda: canvas.on_mode_btn(live=False))
        self.mode_live.clicked.connect(lambda: canvas.on_mode_btn(live=True))
        grid_of([self.mode_frozen, self.mode_live])

        done = FlatBtn("✔ Done", "Close canvas", fixed=24)
        done.setFixedSize(56, 24)
        done.setStyleSheet(done.styleSheet() +
                           "QToolButton{background:#d9f2d9;font-size:11px;border-color:#9c9;}")
        done.clicked.connect(canvas.finish)
        root.addWidget(done, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.size_group = QButtonGroup(self)
        size_widgets = []
        for s in PEN_SIZES:
            b = SizeBtn(s)
            b.clicked.connect(lambda _, v=s: canvas.set_pen_size(v))
            self.size_group.addButton(b)
            size_widgets.append(b)
            if s == canvas.pen_size:
                b.setChecked(True)
        grid_of(size_widgets)
        separator()

        self.color_group = QButtonGroup(self)
        color_widgets = []
        for ct, co in zip(TRANSPARENT_COLORS, OPAQUE_COLORS):
            bt = ColorBtn(ct, "Transparent (highlighter)")
            bt.clicked.connect(lambda _, col=ct: canvas.set_color(col))
            self.color_group.addButton(bt)
            color_widgets.append(bt)
            bo = ColorBtn(co, "Opaque")
            bo.clicked.connect(lambda _, col=co: canvas.set_color(col))
            self.color_group.addButton(bo)
            color_widgets.append(bo)
            if co is OPAQUE_COLORS[0]:
                bo.setChecked(True)
        grid_of(color_widgets, spacing=3)
        separator()

        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        specs = [
            (Tool.PEN, "∿", "Freehand pen"),
            (Tool.LINE, "╱", "Line (Shift: 45° steps)"),
            (Tool.PATH, "〽", "Polyline (click to add points, double/right-click to finish, ESC to cancel, Shift: 45°)"),
            (Tool.HLINE, "━", "Horizontal line across screen (Shift+click = vertical)"),
            (Tool.ARROW, "➝", "Arrow (Shift: 45° steps)"),
            (Tool.DARROW, "↔", "Double arrow (Shift: 45° steps)"),
            (Tool.RECT, "▭", "Rectangle (Shift: square)"),
            (Tool.RECT_F, "▰", "Filled rectangle (Shift: square)"),
            (Tool.ELLIPSE, "◯", "Ellipse (Shift: circle)"),
            (Tool.ELLIPSE_F, "⬤", "Filled ellipse (Shift: circle)"),
            (Tool.TEXT, "T", "Text (click to move, Enter to place, ESC to cancel)"),
            (Tool.CHECK, "✓", "Green check mark"),
            (Tool.CROSS, "✗", "Red cross mark"),
            (Tool.AREA, "⬚", "Select area for copy/save (click to reset)"),
            (Tool.ERASER, "🧽", "Eraser"),
            (Tool.ZOOM, "🔭", "Zoom / color picker (wheel to zoom, drag to pan, Shift+click copies hex)"),
            # --- extended tools ---
            (Tool.SPOTLIGHT, "🔦", "Spotlight (highlight cursor area)"),
            (Tool.LASER, "🔴", "Laser pointer (fades when idle)"),
            (Tool.NUMBER, "①", "Step number stamp (right-click resets counter)"),
        ]
        self.tool_btns = {}
        tool_widgets = []
        
        self.undo_btn = FlatBtn("↶", "Undo (Cmd+Z)")
        self.undo_btn.clicked.connect(canvas.undo)
        self.redo_btn = FlatBtn("↷", "Redo (Cmd+Shift+Z)")
        self.redo_btn.clicked.connect(canvas.redo)

        clear_btn = FlatBtn("🗑", "Clear all")
        clear_btn.clicked.connect(canvas.clear_all)

        for tool, glyph, tip in specs:
            b = FlatBtn(glyph, tip, checkable=True)
            b.clicked.connect(lambda _, t=tool: canvas.set_tool(t))
            self.tool_group.addButton(b)
            self.tool_btns[tool] = b

        # Drawing group
        # Row pairs: pen+hline / line+path / arrows / rects / ellipses /
        # text+number / check+cross
        draw_order = [Tool.PEN, Tool.HLINE, Tool.LINE, Tool.PATH,
                      Tool.ARROW, Tool.DARROW,
                      Tool.RECT, Tool.RECT_F, Tool.ELLIPSE, Tool.ELLIPSE_F,
                      Tool.TEXT, Tool.NUMBER, Tool.CHECK, Tool.CROSS]
        grid_of([self.tool_btns[t] for t in draw_order])
        separator("#000")
        # Editing group: eraser/clear, undo/redo
        grid_of([self.tool_btns[Tool.ERASER], clear_btn,
                 self.undo_btn, self.redo_btn])
        separator()
        # Presentation group: spotlight / laser
        grid_of([self.tool_btns[Tool.SPOTLIGHT], self.tool_btns[Tool.LASER]])
        
        # Arrow and filled-rect glyphs render small; bump their font size
        for t in (Tool.ARROW, Tool.RECT_F):
            self.tool_btns[t].setStyleSheet(
                self.tool_btns[t].styleSheet() + "QToolButton{font-size:20px;}")
        self.tool_btns[Tool.CHECK].setStyleSheet(
            self.tool_btns[Tool.CHECK].styleSheet() + "QToolButton{color:#00a000; font-weight:bold; font-size:13px;}")
        self.tool_btns[Tool.CROSS].setStyleSheet(
            self.tool_btns[Tool.CROSS].styleSheet() + "QToolButton{color:#d20000; font-weight:bold; font-size:13px;}")
        separator()

        self.pause_btn = FlatBtn("🗗", "Pause: hide canvas, keep annotations (resume via ↩ on the strip)")
        self.pause_btn.clicked.connect(canvas.pause)
        action_btns = [self.tool_btns[Tool.AREA]]
        action_btns.append(self.tool_btns[Tool.ZOOM])
        action_btns.append(self.pause_btn)
        for glyph, tip, fn in [
            ("📋", "Copy screen/area to clipboard", canvas.copy_clip),
            ("💾", "Save screen/area as image", canvas.save_file),
            ("⚡", "Quick-save to Desktop (timestamped, no dialog)", canvas.quick_save),
        ]:
            b = FlatBtn(glyph, tip)
            b.clicked.connect(fn)
            action_btns.append(b)
        grid_of(action_btns)

        self.tool_btns[Tool.PEN].setChecked(True)
        self.adjustSize()

    MODE_ACTIVE_STYLE = """
        QToolButton { border: 1px solid #e06050; border-radius: 4px; background: #ffd9d2; font-size: 16px; }
        QToolButton:hover { background: #ffc4ba; }
    """
    MODE_IDLE_STYLE = """
        QToolButton { border: 1px solid transparent; border-radius: 4px; background: transparent; font-size: 16px; }
        QToolButton:hover { background: #e4e4e4; border-color: #c8c8c8; }
    """

    def set_mode_indicator(self, live: bool):
        self.mode_frozen.setStyleSheet(self.MODE_IDLE_STYLE if live else self.MODE_ACTIVE_STYLE)
        self.mode_live.setStyleSheet(self.MODE_ACTIVE_STYLE if live else self.MODE_IDLE_STYLE)

    def set_live_mode(self, live: bool):
        for t in (Tool.ZOOM, Tool.SPOTLIGHT):
            self.tool_btns[t].setEnabled(not live)
            tip = self.tool_btns[t].toolTip()
            base = tip.split(" (")[0]
            self.tool_btns[t].setToolTip(base + (" (unavailable in live mode)" if live else ""))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            new = self.pos() + e.position().toPoint() - self._drag
            new.setX(max(0, min(new.x(), self.canvas.width() - self.width())))
            new.setY(max(0, min(new.y(), self.canvas.height() - self.height())))
            self.move(new)

    def mouseReleaseEvent(self, e):
        self._drag = None


# ---------------------------------------------------------------- canvas

class Canvas(QWidget):
    def __init__(self, launcher):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.launcher = launcher
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.live = False
        self._screen = None
        self.screenshot: QPixmap | None = None
        self.annot: QPixmap | None = None
        self.undo_stack: list[QPixmap] = []
        self.redo_stack: list[QPixmap] = []

        self.tool = Tool.PEN
        self.last_draw_tool = Tool.PEN
        self.color = OPAQUE_COLORS[0]
        self.pen_size = PEN_SIZES[1]

        self.drawing = False
        self.start_pt = QPointF()
        self.cur_pt = QPointF()
        self.path_pts: list[QPointF] = []

        self.area: QRect | None = None
        self._suppress_overlays = False
        self.text_edit: InlineText | None = None
        self.cursor_pos = QPointF(-100, -100)

        self.zoom = 1.0
        self.zoom_off = QPointF(0, 0)
        self.panning = False
        self.pan_anchor = QPointF()

        # --- extended tool state ---
        self.step_counter = 1
        self.path_vertices: list[QPointF] = []   # in-progress polyline vertices
        self.laser_pos: QPointF | None = None
        self.laser_timer = QTimer(self)
        self.laser_timer.setInterval(1500)  # fade after 1.5 s idle
        self.laser_timer.timeout.connect(self._clear_laser)
        self.flash_text: str | None = None
        self.flash_timer = QTimer(self)
        self.flash_timer.setSingleShot(True)
        self.flash_timer.timeout.connect(self._clear_flash)

        self.toolbar = Toolbar(self)
        QShortcut(QKeySequence.StandardKey.Undo, self, self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, self.redo)

    def begin(self, live: bool = False):
        self.live = live
        # Multi-monitor: the canvas opens on the screen under the cursor.
        # With macOS "Displays have separate Spaces" (default), one window
        # cannot span displays, so we target a single screen per session.
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        self._screen = screen
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()

        if live:
            self.screenshot = None
            self.annot = QPixmap(int(geo.width() * dpr), int(geo.height() * dpr))
            self.annot.setDevicePixelRatio(dpr)
        else:
            self.screenshot = screen.grabWindow(0)
            if self.screenshot.isNull() or self.screenshot.width() == 0:
                self.launcher.show_error("Screen capture failed. Grant permission in System Settings -> Privacy & Security -> Screen Recording, then restart.")
                return
            self.annot = QPixmap(self.screenshot.size())
            self.annot.setDevicePixelRatio(self.screenshot.devicePixelRatio())
        self.annot.fill(Qt.GlobalColor.transparent)
        
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.update_history_buttons()
        
        self.area = None
        self.zoom, self.zoom_off = 1.0, QPointF(0, 0)
        self.step_counter = 1  # reset step numbering
        self.path_vertices.clear()
        
        self.toolbar.set_live_mode(live)
        self.toolbar.set_mode_indicator(live)
        if live and self.tool in (Tool.ZOOM, Tool.SPOTLIGHT):
            self.set_tool(Tool.PEN)
            self.toolbar.tool_btns[Tool.PEN].setChecked(True)
            
        self.setGeometry(geo)
        self.toolbar.move(12, (geo.height() - self.toolbar.height()) // 2)
        self.show()
        self.raise_()
        self.activateWindow()

    def finish(self):
        if self.text_edit:
            self.text_edit.deleteLater()
            self.text_edit = None
        self.hide()
        self.launcher.show_idle()

    def pause(self):
        if self.text_edit:
            self.text_edit.commit()
        if self.path_vertices:
            self.finish_path()
        self.hide()
        self.launcher.show_paused()

    def resume(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def on_mode_btn(self, live: bool):
        if live == self.live:
            self.finish()
        else:
            self.finish()
            self.launcher.start_mode(live)

    def set_tool(self, tool: Tool):
        if self.text_edit:
            self.text_edit.commit()
        if self.tool == Tool.PATH and tool != Tool.PATH:
            self.finish_path()   # commit in-progress polyline when switching tools
        self.tool = tool
        if tool not in (Tool.ZOOM, Tool.AREA, Tool.SPOTLIGHT, Tool.LASER, Tool.NUMBER):
            self.last_draw_tool = tool
        if tool != Tool.ZOOM and self.zoom != 1.0:
            self.zoom, self.zoom_off = 1.0, QPointF(0, 0)
        self.update()

    def set_color(self, c: QColor):
        self.color = c

    def set_pen_size(self, s: int):
        self.pen_size = s
        self.update()

    def w2i(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self.zoom_off.x()) / self.zoom,
                       (p.y() - self.zoom_off.y()) / self.zoom)

    def update_history_buttons(self):
        if hasattr(self, 'toolbar'):
            self.toolbar.undo_btn.setEnabled(bool(self.undo_stack))
            self.toolbar.redo_btn.setEnabled(bool(self.redo_stack))

    def push_undo(self):
        self.undo_stack.append(self.annot.copy())
        if len(self.undo_stack) > UNDO_LIMIT:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.update_history_buttons()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.annot.copy())
            if len(self.redo_stack) > UNDO_LIMIT:
                self.redo_stack.pop(0)
            self.annot = self.undo_stack.pop()
            self.update()
            self.update_history_buttons()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.annot.copy())
            if len(self.undo_stack) > UNDO_LIMIT:
                self.undo_stack.pop(0)
            self.annot = self.redo_stack.pop()
            self.update()
            self.update_history_buttons()

    def clear_all(self):
        self.push_undo()
        self.annot.fill(Qt.GlobalColor.transparent)
        self.update()

    def composite(self) -> QPixmap:
        if self.live:
            self.toolbar.hide()
            self._suppress_overlays = True
            self.repaint()
            QApplication.processEvents()
            import time
            time.sleep(0.15)
            screen = self._screen or QGuiApplication.primaryScreen()
            out = screen.grabWindow(0)
            self._suppress_overlays = False
            self.toolbar.show()
            self.update()
            if out.isNull() or out.width() == 0:
                self.launcher.show_error("Exporting in live mode requires Screen Recording permission.")
                return QPixmap(1, 1)
        else:
            out = self.screenshot.copy()
            p = QPainter(out)
            p.drawPixmap(0, 0, self.annot)
            p.end()
        if self.area and self.area.width() > 4 and self.area.height() > 4:
            dpr = out.devicePixelRatio()
            dev = QRect(int(self.area.x() * dpr), int(self.area.y() * dpr),
                        int(self.area.width() * dpr), int(self.area.height() * dpr))
            out = out.copy(dev)
            out.setDevicePixelRatio(dpr)
        return out

    def copy_clip(self):
        QApplication.clipboard().setPixmap(self.composite())

    def quick_save(self):
        """Save straight to Desktop with a timestamped name, no dialog."""
        import datetime
        import os
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            desktop = os.path.expanduser("~")
        name = datetime.datetime.now().strftime("screenchalk_%Y%m%d_%H%M%S.png")
        path = os.path.join(desktop, name)
        ok = self.composite().save(path)
        self.flash(f"Saved: {name}" if ok else "Save failed")

    def flash(self, text: str, msec: int = 1800):
        """Show a transient message at the bottom of the canvas."""
        self.flash_text = text
        self.flash_timer.start(msec)
        self.update()

    def _clear_flash(self):
        self.flash_text = None
        self.update()

    def save_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save image", "screenchalk.png", "PNG (*.png);;JPEG (*.jpg)")
        if path:
            self.composite().save(path)

    def commit_text(self, pos: QPointF, text: str, font: QFont):
        self.push_undo()
        p = QPainter(self.annot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        p.setPen(QPen(c))
        p.setFont(font)
        fm_h = p.fontMetrics().ascent()
        p.drawText(QPointF(pos.x() + 3, pos.y() + fm_h / 2), text)
        p.end()
        self.update()

    def stamp(self, pos: QPointF, check: bool):
        self.push_undo()
        # Match the text tool's font height at the current pen size
        font = QFont()
        font.setPointSize(10 + self.pen_size * 2)
        font.setBold(True)
        s = QFontMetrics(font).height()
        p = QPainter(self.annot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(3.0, s * 0.16)
        if check:
            pen = QPen(QColor(0, 160, 0), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(QPointF(pos.x() - s * 0.45, pos.y() + s * 0.02))
            path.lineTo(QPointF(pos.x() - s * 0.12, pos.y() + s * 0.38))
            path.lineTo(QPointF(pos.x() + s * 0.5, pos.y() - s * 0.42))
            p.drawPath(path)
        else:
            pen = QPen(QColor(210, 0, 0), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            d = s * 0.42
            p.drawLine(QPointF(pos.x() - d, pos.y() - d), QPointF(pos.x() + d, pos.y() + d))
            p.drawLine(QPointF(pos.x() - d, pos.y() + d), QPointF(pos.x() + d, pos.y() - d))
        p.end()
        self.update()

    def zoom_at(self, widget_pt: QPointF, new_zoom: float):
        new_zoom = max(1.0, min(8.0, new_zoom))
        img_pt = self.w2i(widget_pt)
        self.zoom = new_zoom
        if new_zoom == 1.0:
            self.zoom_off = QPointF(0, 0)
        else:
            self.zoom_off = QPointF(widget_pt.x() - img_pt.x() * new_zoom, widget_pt.y() - img_pt.y() * new_zoom)
        self.update()

    def wheelEvent(self, e):
        if self.tool == Tool.ZOOM:
            factor = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
            self.zoom_at(e.position(), self.zoom * factor)
        else:
            super().wheelEvent(e)

    def pixel_hex(self, img_pt: QPointF) -> str:
        if not self.screenshot: return "#------"
        comp = self.screenshot.toImage()
        dpr = self.screenshot.devicePixelRatio()
        x = int(img_pt.x() * dpr)
        y = int(img_pt.y() * dpr)
        if 0 <= x < comp.width() and 0 <= y < comp.height():
            c = comp.pixelColor(x, y)
            a = self.annot.toImage().pixelColor(x, y)
            if a.alpha() > 0:
                af = a.alphaF()
                c = QColor(round(a.red() * af + c.red() * (1 - af)),
                           round(a.green() * af + c.green() * (1 - af)),
                           round(a.blue() * af + c.blue() * (1 - af)))
            return c.name().upper()
        return "#------"

    def mousePressEvent(self, e):
        pos = e.position()

        # Polyline: right-click finishes
        if e.button() == Qt.MouseButton.RightButton and self.tool == Tool.PATH:
            self.finish_path()
            return

        # Step number: right-click resets the counter
        if e.button() == Qt.MouseButton.RightButton and self.tool == Tool.NUMBER:
            self.step_counter = 1
            self.update()
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self.tool == Tool.ZOOM:
            if shift_down():
                QApplication.clipboard().setText(self.pixel_hex(self.w2i(pos)))
            else:
                self.panning = True
                self.pan_anchor = pos
            return

        ipos = self.w2i(pos)

        if self.tool == Tool.TEXT:
            if self.text_edit:
                # Before Enter: clicking elsewhere moves the text box
                self.text_edit.anchor = ipos
                fm_h = self.text_edit.fontMetrics().height()
                self.text_edit.move(int(ipos.x()), int(ipos.y() - fm_h / 2))
                self.text_edit.setFocus()
                return
            font = QFont()
            font.setPointSize(10 + self.pen_size * 2)
            font.setBold(True)
            self.text_edit = InlineText(self, ipos, self.color, font)
            return

        if self.tool == Tool.CHECK:
            self.stamp(ipos, check=True)
            return
        if self.tool == Tool.CROSS:
            self.stamp(ipos, check=False)
            return
            
        # Polyline: left click appends a vertex
        if self.tool == Tool.PATH:
            if self.path_vertices and shift_down():
                ipos = constrain_45(self.path_vertices[-1], ipos)
            self.path_vertices.append(ipos)
            self.update()
            return

        # H/V line: one click drops a full-span line (Shift = vertical)
        if self.tool == Tool.HLINE:
            self.push_undo()
            p = QPainter(self.annot)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(self.color), self.pen_size, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.FlatCap)
            p.setPen(pen)
            if shift_down():
                p.drawLine(QPointF(ipos.x(), 0), QPointF(ipos.x(), self.height()))
            else:
                p.drawLine(QPointF(0, ipos.y()), QPointF(self.width(), ipos.y()))
            p.end()
            self.update()
            return

        # Step number: left click stamps the next number
        if self.tool == Tool.NUMBER:
            self.push_undo()
            p = QPainter(self.annot)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            radius = 15 + self.pen_size * 2
            num_color = QColor(self.color)
            num_color.setAlpha(255)   # numbers always opaque; alpha digits look washed out
            # circle background
            p.setBrush(QBrush(QColor(255, 255, 255, 200)))
            p.setPen(QPen(num_color, 2))
            p.drawEllipse(QPointF(ipos.x(), ipos.y()), radius, radius)
            # digit
            p.setPen(QPen(num_color))
            font = QFont()
            font.setBold(True)
            font.setPointSize(12 + self.pen_size)
            p.setFont(font)
            fm = p.fontMetrics()
            text = str(self.step_counter)
            # center the digit
            p.drawText(QPointF(ipos.x() - fm.horizontalAdvance(text)/2, ipos.y() + fm.height()/3), text)
            p.end()
            self.step_counter += 1
            self.update()
            return

        self.drawing = True
        self.start_pt = ipos
        self.cur_pt = ipos
        self.path_pts = [ipos]
        if self.tool == Tool.ERASER:
            self.push_undo()
            self.erase_to(ipos, first=True)

    def mouseMoveEvent(self, e):
        pos = e.position()
        self.cursor_pos = pos

        # Laser: update position and restart the fade timer
        if self.tool == Tool.LASER:
            self.laser_pos = pos
            self.laser_timer.start()
            self.update()
            return

        if self.tool == Tool.ZOOM and self.panning:
            self.zoom_off += pos - self.pan_anchor
            self.pan_anchor = pos
            self.update()
            return

        if self.drawing:
            ipos = self.w2i(pos)
            self.cur_pt = ipos
            if self.tool == Tool.PEN:
                self.path_pts.append(ipos)
            elif self.tool == Tool.ERASER:
                self.erase_to(ipos)
                self.path_pts = [ipos]
        if self.drawing or self.tool in (Tool.ERASER, Tool.ZOOM, Tool.SPOTLIGHT, Tool.HLINE) \
                or (self.tool == Tool.PATH and self.path_vertices):
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.tool == Tool.ZOOM:
            self.panning = False
            return
        if not self.drawing:
            return
        self.drawing = False
        ipos = self.w2i(e.position())

        if self.tool == Tool.AREA:
            r = QRectF(self.start_pt, constrain_square(self.start_pt, ipos) if shift_down() else ipos).normalized().toRect()
            self.area = r if (r.width() > 4 and r.height() > 4) else None
            self.update()
            return
        if self.tool == Tool.ERASER:
            self.update()
            return

        self.push_undo()
        p = QPainter(self.annot)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.draw_shape(p, final=True)
        p.end()
        self.update()

    def mouseDoubleClickEvent(self, e):
        if self.tool == Tool.PATH and e.button() == Qt.MouseButton.LeftButton:
            if (len(self.path_vertices) >= 2 and
                    (self.path_vertices[-1] - self.path_vertices[-2]).manhattanLength() < 4):
                self.path_vertices.pop()
            self.finish_path()
        else:
            super().mouseDoubleClickEvent(e)

    def finish_path(self):
        """Commit the in-progress polyline (needs >= 2 points) as one stroke."""
        if len(self.path_vertices) >= 2:
            self.push_undo()
            p = QPainter(self.annot)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(self.color), self.pen_size, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            path = QPainterPath(self.path_vertices[0])
            for q in self.path_vertices[1:]:
                path.lineTo(q)
            p.drawPath(path)
            p.end()
        self.path_vertices.clear()
        self.update()

    def _clear_laser(self):
        self.laser_pos = None
        self.update()

    def erase_to(self, pt: QPointF, first=False):
        p = QPainter(self.annot)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        w = self.pen_size * 3
        pen = QPen(Qt.GlobalColor.black, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        prev = self.path_pts[-1] if (self.path_pts and not first) else pt
        p.drawLine(prev, pt)
        p.end()

    def current_end(self) -> QPointF:
        if self.tool in (Tool.LINE, Tool.ARROW, Tool.DARROW) and shift_down():
            return constrain_45(self.start_pt, self.cur_pt)
        if self.tool in (Tool.RECT, Tool.RECT_F, Tool.ELLIPSE, Tool.ELLIPSE_F) and shift_down():
            return constrain_square(self.start_pt, self.cur_pt)
        return self.cur_pt

    def draw_shape(self, p: QPainter, final: bool):
        c = QColor(self.color)
        pen = QPen(c, self.pen_size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        end = self.current_end()

        if self.tool == Tool.PEN:
            if len(self.path_pts) == 1:
                p.drawPoint(self.path_pts[0])
            else:
                path = QPainterPath(self.path_pts[0])
                for q in self.path_pts[1:]:
                    path.lineTo(q)
                p.drawPath(path)
        elif self.tool == Tool.LINE:
            p.drawLine(self.start_pt, end)
        elif self.tool in (Tool.ARROW, Tool.DARROW):
            self.draw_arrow(p, self.start_pt, end, c, both=(self.tool == Tool.DARROW))
        elif self.tool in (Tool.RECT, Tool.RECT_F):
            r = QRectF(self.start_pt, end).normalized()
            if self.tool == Tool.RECT_F:
                p.setBrush(QBrush(c))
                p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(r)
        elif self.tool in (Tool.ELLIPSE, Tool.ELLIPSE_F):
            r = QRectF(self.start_pt, end).normalized()
            if self.tool == Tool.ELLIPSE_F:
                p.setBrush(QBrush(c))
                p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(r)

    def draw_arrow(self, p: QPainter, a: QPointF, b: QPointF, c: QColor, both: bool):
        d = b - a
        if math.hypot(d.x(), d.y()) < 1e-6:
            return
        head = self.pen_size * 3 + 8

        def shaft_end(tip: QPointF, tail: QPointF) -> QPointF:
            ang = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
            return QPointF(tip.x() - head * 0.7 * math.cos(ang), tip.y() - head * 0.7 * math.sin(ang))

        a_shaft = shaft_end(a, b) if both else a
        b_shaft = shaft_end(b, a)
        p.drawLine(a_shaft, b_shaft)
        p.setBrush(QBrush(c))
        p.setPen(Qt.PenStyle.NoPen)
        for tip, tail, on in ((b, a, True), (a, b, both)):
            if not on:
                continue
            ang = math.atan2(tip.y() - tail.y(), tip.x() - tail.x())
            left = ang + math.radians(152)
            right = ang - math.radians(152)
            poly = QPolygonF([
                tip,
                QPointF(tip.x() + head * math.cos(left), tip.y() + head * math.sin(left)),
                QPointF(tip.x() + head * math.cos(right), tip.y() + head * math.sin(right)),
            ])
            p.drawPolygon(poly)
        pen = QPen(c, self.pen_size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def paintEvent(self, e):
        if not self.annot:
            return
        suppress = getattr(self, "_suppress_overlays", False)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        p.save()
        p.translate(self.zoom_off)
        p.scale(self.zoom, self.zoom)
        if self.screenshot:
            p.drawPixmap(0, 0, self.screenshot)
        p.drawPixmap(0, 0, self.annot)
        
        if self.drawing and self.tool not in (Tool.ERASER, Tool.AREA, Tool.SPOTLIGHT, Tool.LASER, Tool.NUMBER):
            self.draw_shape(p, final=False)

        # H/V line preview: translucent aiming line follows the cursor
        if self.tool == Tool.HLINE:
            gc = QColor(self.color)
            gc.setAlpha(min(gc.alpha(), 130))
            p.setPen(QPen(gc, self.pen_size, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.FlatCap))
            cur = self.w2i(self.cursor_pos)
            if shift_down():
                p.drawLine(QPointF(cur.x(), 0), QPointF(cur.x(), self.height()))
            else:
                p.drawLine(QPointF(0, cur.y()), QPointF(self.width(), cur.y()))

        # Polyline preview: fixed segments + rubber band + vertex dots
        if self.tool == Tool.PATH and self.path_vertices:
            c = QColor(self.color)
            pen = QPen(c, self.pen_size, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            prev = QPainterPath(self.path_vertices[0])
            for q in self.path_vertices[1:]:
                prev.lineTo(q)
            cur = self.w2i(self.cursor_pos)
            if shift_down():
                cur = constrain_45(self.path_vertices[-1], cur)
            prev.lineTo(cur)
            p.drawPath(prev)
            p.setPen(QPen(QColor(40, 40, 40), 1))
            p.setBrush(QBrush(QColor(255, 255, 255)))
            for q in self.path_vertices:
                p.drawEllipse(q, 2.5, 2.5)
            p.setBrush(Qt.BrushStyle.NoBrush)
            
        if not suppress:
            if self.drawing and self.tool == Tool.AREA:
                end = constrain_square(self.start_pt, self.cur_pt) if shift_down() else self.cur_pt
                r = QRectF(self.start_pt, end).normalized()
                self.draw_area_rect(p, r)
            elif self.area:
                self.draw_area_rect(p, QRectF(self.area))
                
        p.restore()

        if suppress:
            p.end()
            return

        # Spotlight: translucent mask with an odd-even-fill hole (widget
        # coords). Clear composition is avoided: on a translucent window it
        # would punch through to the real desktop, not the frozen image.
        if self.tool == Tool.SPOTLIGHT and not self.live:
            radius = 100 + self.pen_size * 5
            mask = QPainterPath()
            mask.setFillRule(Qt.FillRule.OddEvenFill)
            mask.addRect(QRectF(self.rect()))
            mask.addEllipse(self.cursor_pos, radius, radius)
            p.fillPath(mask, QColor(0, 0, 0, 180))

        # Laser: drawn on top, never written to annot (widget coords)
        if self.tool == Tool.LASER and self.laser_pos:
            radius = 10 + self.pen_size * 2
            gradient = QRadialGradient(self.laser_pos, radius)
            gradient.setColorAt(0, QColor(255, 50, 50, 255))
            gradient.setColorAt(0.4, QColor(255, 0, 0, 150))
            gradient.setColorAt(1, QColor(255, 0, 0, 0))
            p.setBrush(QBrush(gradient))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(self.laser_pos, radius, radius)

        if self.tool == Tool.ERASER and self.zoom == 1.0:
            w = self.pen_size * 3
            p.setPen(QPen(QColor(80, 80, 80), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(self.cursor_pos, w / 2, w / 2)

        if self.flash_text:
            self.draw_info(p, f"  {self.flash_text}  ")
        elif self.tool == Tool.ZOOM:
            ipt = self.w2i(self.cursor_pos)
            info = (f"  {self.zoom:.1f}×   X:{int(ipt.x())} Y:{int(ipt.y())}  "
                    f"{self.pixel_hex(ipt)}   (Shift+click copies hex)  ")
            self.draw_info(p, info)

        if self.drawing and self.tool == Tool.AREA:
            r = QRectF(self.start_pt, self.cur_pt).normalized()
            self.draw_info(p, f"  {int(r.width())} × {int(r.height())}  @ ({int(r.x())}, {int(r.y())})  ")
        p.end()

    def draw_area_rect(self, p: QPainter, r: QRectF):
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), 1.5 / self.zoom))
        p.drawRect(r)
        pen = QPen(QColor(20, 20, 20), 1.5 / self.zoom, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(r)

    def draw_info(self, p: QPainter, text: str):
        font = QFont()
        font.setPointSize(13)
        p.setFont(font)
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 8
        h = fm.height() + 8
        x = (self.width() - w) / 2
        y = self.height() - h - 14
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(30, 30, 30, 200))
        p.drawRoundedRect(QRectF(x, y, w, h), 6, 6)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, text)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            if self.tool == Tool.PATH and self.path_vertices:
                self.path_vertices.clear()   # abandon in-progress polyline
                self.update()
            elif self.zoom != 1.0:
                self.zoom_at(QPointF(self.width() / 2, self.height() / 2), 1.0)
            elif self.area:
                self.area = None
                self.update()
            else:
                self.finish()
        else:
            super().keyPressEvent(e)


# ---------------------------------------------------------------- launcher

class Launcher(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("ScreenChalk")
        self.setStyleSheet(
            "Launcher { background: rgba(250,250,250,238); border: 1px solid #aaa; border-radius: 8px; }")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.busy = False
        self.paused = False
        self._press_pos = None
        self.canvas = Canvas(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 2, 3, 3)
        lay.setSpacing(1)
        grip = QLabel("⠿")
        grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grip.setStyleSheet("color:#999; font-size:9px;")
        grip.setFixedHeight(10)
        lay.addWidget(grip)
        frozen = FlatBtn("❄", "Start frozen: capture the screen as a still canvas")
        frozen.clicked.connect(lambda: self.start_mode(live=False))
        live = FlatBtn("▶", "Start live: draw over the moving screen")
        live.clicked.connect(lambda: self.start_mode(live=True))
        resume = FlatBtn("↩", "Resume drawing (annotations kept)")
        resume.setStyleSheet(resume.styleSheet() + "QToolButton{background:#d9f2d9;border-color:#9c9;}")
        resume.clicked.connect(self.on_resume)
        resume.hide()
        self.btn_frozen, self.btn_live, self.btn_resume = frozen, live, resume
        lay.addWidget(frozen, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(live, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(resume, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.adjustSize()

        geo = QGuiApplication.primaryScreen().geometry()
        self.move(geo.right() - self.width() - 16, geo.top() + 60)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press_pos = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self._press_pos is not None:
            self.move(self.pos() + e.position().toPoint() - self._press_pos)

    def mouseReleaseEvent(self, e):
        self._press_pos = None

    def contextMenuEvent(self, e):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(QApplication.quit)
        menu.addAction(quit_act)
        menu.exec(e.globalPos())

    def show_idle(self):
        self.paused = False
        self.btn_resume.hide()
        self.btn_frozen.show()
        self.btn_live.show()
        self.adjustSize()
        self.show()
        self.raise_()

    def show_paused(self):
        self.paused = True
        self.btn_frozen.hide()
        self.btn_live.hide()
        self.btn_resume.show()
        self.adjustSize()
        self.show()
        self.raise_()

    def on_resume(self):
        self.paused = False
        self.hide()
        self.canvas.resume()

    def start_mode(self, live: bool):
        if self.busy or self.paused or self.canvas.isVisible():
            return
        self.busy = True
        self.hide()
        QTimer.singleShot(300, lambda: self._begin(live))

    def _begin(self, live: bool):
        self.canvas.begin(live=live)
        self.busy = False
        if not self.canvas.isVisible():
            self.show()

    def show_error(self, msg: str):
        from PyQt6.QtWidgets import QMessageBox
        self.show()
        QMessageBox.warning(self, "ScreenChalk", msg)


# ---------------------------------------------------------------- global hotkey

class HotkeyBridge(QObject):
    """Bridge from the pynput listener thread to the Qt main thread.
    pynput callbacks run outside Qt; touching QTimer/UI there is unsafe.
    Emitting a Qt signal across threads is safe (queued to the main loop)."""
    triggered = pyqtSignal()


def setup_global_hotkey(launcher):
    """Global hotkey F6 state machine: idle -> freeze & start;
    drawing -> pause; paused -> resume. One key toggles between
    drawing and using other apps."""
    if not HAS_PYNPUT:
        print("Note: pynput not installed; global hotkey (F6) disabled. Install: pip install pynput")
        return None

    bridge = HotkeyBridge()

    def on_f6():
        if launcher.canvas.isVisible():
            launcher.canvas.pause()
        elif launcher.paused:
            launcher.on_resume()
        else:
            launcher.start_mode(live=False)

    bridge.triggered.connect(on_f6)

    def on_press(key):
        try:
            if key == Key.f6:
                bridge.triggered.emit()
        except Exception:
            pass

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    print("Global hotkey enabled: F6 = freeze & start / pause / resume.")
    return bridge


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    launcher = Launcher()
    
    # set up the global hotkey
    hotkey_bridge = setup_global_hotkey(launcher)  # noqa: F841  keep ref alive
    
    launcher.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()