# ScreenChalk

**ScreenChalk** is a lightweight screen annotation tool for macOS — chalk for
your screen. Draw directly over anything during presentations, screen shares,
tutorials, and chart analysis. Inspired by the classic Windows freeware
[Pointofix](https://www.pointofix.de/).

Single-file Python app built on PyQt6. No Electron, no background services.

> **Note:** ScreenChalk is an independent project and is not affiliated with
> or endorsed by the authors of the original Pointofix.

## Features

- **Two modes** — *Frozen* (capture the screen as a still canvas, like the
  original Pointofix) and *Live* (transparent overlay; video and scrolling
  content keep moving underneath)
- **Drawing tools** — freehand pen, line, polyline (TradingView-style path),
  full-width horizontal/vertical line, arrow, double arrow, rectangle,
  ellipse (outline/filled), text, check / cross stamps, step-number stamps
- **Colors** — six transparent highlighter colors + six opaque colors,
  five pen sizes (also scales text, stamps, and numbers)
- **Editing** — eraser, clear all, undo/redo (40 steps) with
  Cmd+Z / Cmd+Shift+Z
- **Presentation** — spotlight (dim everything except the cursor area) and a
  fading laser pointer
- **Zoom / color picker** — wheel-zoom up to 8x, drag to pan, live coordinates
  and hex color readout, Shift+click copies the hex value
- **Export** — copy to clipboard, save as PNG/JPEG, one-click quick-save to
  Desktop with a timestamped filename; all exports respect the area selection
- **Pause & resume** — hide the canvas to use other apps, come back with all
  annotations intact
- **Global hotkey** — **F6** toggles: idle -> freeze & start, drawing -> pause,
  paused -> resume (requires `pynput`)
- **Multi-monitor** — the canvas opens on the screen under your cursor

## Install

```bash
pip3 install -r requirements.txt
python3 screenchalk.py
```

Requires Python 3.10+ and macOS. `pynput` is optional — without it everything
works except the F6 global hotkey.

## Permissions (first run)

macOS will require you to grant permissions to your terminal (or Python):

| Permission | Needed for |
|---|---|
| **Screen Recording** | Frozen mode, and exporting in live mode |
| **Accessibility** | F6 global hotkey (pynput) |

Both are under *System Settings -> Privacy & Security*. Restart the app after
granting.

## Usage

A small floating strip appears at the top-right of your screen (drag to move,
right-click to quit):

- **❄** start in frozen mode &nbsp; **▶** start in live mode &nbsp; **↩**
  resume (shown while paused)

While drawing, a compact vertical toolbar sits on the left (drag by the ⠿
handle). Top to bottom: mode buttons (click the highlighted one to stop, the
other to switch modes), Done, pen sizes, color palette, drawing tools, editing
tools, presentation tools, and export actions.

### Tool notes

- **Polyline 〽** — click to add points, double-click or right-click to
  finish, ESC to cancel, Shift snaps each segment to 45°
- **H/V line ━** — one click drops a horizontal line across the whole screen
  (great for support/resistance levels); Shift+click drops a vertical line
- **Text T** — type, click anywhere to move the box, press Enter to place,
  ESC to cancel
- **Step numbers ①** — each click stamps the next number; right-click resets
  the counter
- **Shift** constrains lines/arrows to 45° steps and rectangles/ellipses to
  squares/circles
- **Area ⬚** — drag to select a region; copy/save then export only that
  region; single click clears the selection

## License

MIT — see [LICENSE](LICENSE).
