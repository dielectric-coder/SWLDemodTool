# Real-Time Graphs in Python TUI Applications

## Overview

Real-time terminal plotting is feasible in Python, with quality ranging from character-based rendering to full pixel-resolution graphics depending on the terminal emulator in use.

---

## Rendering Approaches

### 1. Character-Based (Universal)

**Textual + textual-plotext** is the recommended stack for character-based TUI dashboards:

- Uses Plotext under the hood for terminal-native rendering
- Textual's `set_interval()` drives real-time refresh cleanly
- Works in any terminal emulator, over SSH, on any platform

```python
from textual.app import App
from textual_plotext import PlotextPlot

class MyApp(App):
    def on_mount(self):
        self.set_interval(0.1, self.refresh_plot)

    def refresh_plot(self):
        plot = self.query_one(PlotextPlot)
        plot.plt.clear_data()
        plot.plt.plot(your_data)
        plot.refresh()
```

**Rich** alone is workable but causes flicker — it redraws the full screen on each update. Acceptable at ~1 Hz, not suitable for fast streaming.

**Practical limits:**
- Refresh rates above 10–20 Hz produce flicker depending on terminal
- Resolution is coarse (characters, not pixels)

---

### 2. Pixel-Resolution Graphics (Terminal Graphics Protocols)

Modern terminal emulators support sending raw image data via escape sequences, enabling full-resolution rendering inline in the terminal.

#### Kitty Graphics Protocol
- Developed by Kovid Goyal (Kitty terminal author)
- Sends PNG/RGBA image data via escape sequences
- Matplotlib backend available: [`matplotlib-backend-kitty`](https://github.com/jktr/matplotlib-backend-kitty)
- Full matplotlib quality rendered in-terminal

```bash
pip install matplotlib-backend-kitty
MPLBACKEND=module://matplotlib-backend-kitty python your_script.py
```

Or in code:
```python
import matplotlib
matplotlib.use('module://matplotlib-backend-kitty')
import matplotlib.pyplot as plt
```

**Real-time loop pattern:**
```python
import matplotlib
import matplotlib.pyplot as plt
import time

matplotlib.use('module://matplotlib-backend-kitty')

while True:
    plt.clf()
    plt.plot(get_latest_data())
    plt.show()
    time.sleep(0.1)
```

#### iTerm2 Inline Images Protocol
- Similar concept, different escape sequence format
- Also supported by: WezTerm, Konsole (partial)
- `term-image` library supports both Kitty and iTerm2 protocols

#### Sixel
- Older protocol, wider compatibility (xterm, mlterm)
- Lower color depth
- Supported via `matplotlib-sixel`

---

## Porting Existing Matplotlib Apps to Kitty Backend

If your app already uses matplotlib, porting is mostly a one-line backend change.

**What works transparently:**
- All standard `plt.*` calls, axes, subplots
- `plt.show()` renders inline
- `plt.pause()` for animation loops

**What breaks or needs adjustment:**
- Interactive GUI features (`plt.ginput()`, zoom/pan) — not available
- `plt.ion()` / interactive mode behaves differently
- Apps relying on a GUI window event loop need restructuring to a `while True` + `plt.clf()` + `plt.pause()` loop

> If your app is non-interactive (plot + display only), porting is trivial. GUI-dependent apps require minor restructuring.

---

## Terminal Detection

An app can detect the terminal at runtime and automatically select the best rendering mode.

### Environment Variable (Simple)

```python
import os

TERM = os.environ.get("TERM", "")
TERM_PROGRAM = os.environ.get("TERM_PROGRAM", "")

is_kitty = TERM == "xterm-kitty" or TERM_PROGRAM == "kitty"
```

| Terminal | `$TERM` | `$TERM_PROGRAM` |
|---|---|---|
| Kitty | `xterm-kitty` | `kitty` |
| WezTerm | `xterm-256color` | `WezTerm` |
| Standard SSH | `xterm-256color` | *(unset)* |

### Library-Based (More Robust)

`term-image` auto-detects supported protocols:

```python
from term_image.utils import query_terminal_support
# Automatically queries what protocols are available
```

### Recommended Pattern

```python
import os

def get_renderer():
    if os.environ.get("TERM") == "xterm-kitty":
        import matplotlib
        matplotlib.use("module://matplotlib-backend-kitty")
        return "kitty"
    elif os.environ.get("TERM_PROGRAM") == "WezTerm":
        import matplotlib
        matplotlib.use("module://matplotlib-backend-kitty")  # same protocol
        return "wezterm"
    else:
        return "textual"  # character-based TUI fallback

mode = get_renderer()
```

---

## Platform & Environment Support

| Environment | Kitty Backend | Notes |
|---|---|---|
| Kitty terminal (local) | ✅ | Works natively |
| WezTerm (local) | ✅ | Same protocol, cross-platform |
| iTerm2 (macOS) | ⚠️ | Use iTerm2 protocol variant |
| Windows Terminal | ❌ | No Kitty protocol support |
| Windows (native) | ❌ | No Kitty port available |
| Windows + WSL | ❌ | Terminal is Windows-side; use WezTerm instead |
| Yocto embedded target | ❌ | Kitty is a desktop GPU-accelerated app |
| SSH (plain) | ⚠️ | See SSH section below |

---

## SSH Caveats

SSH sessions do not automatically carry over the local terminal environment.

### The Problem

When SSHing into a remote machine, `$TERM` is typically reset to `xterm-256color` regardless of your local terminal. The remote script will not detect Kitty and will fall back to the TUI mode.

Additionally, even if detection works, pixel image data must travel back through the SSH pipe — this requires specific support from the terminal.

### Solution: `kitty +kitten ssh`

Kitty ships with a helper that forwards terminal capabilities over SSH:

```bash
kitty +kitten ssh user@remotehost
```

This:
- Propagates the correct `$TERM` and capabilities to the remote session
- Handles escape sequence forwarding so pixel graphics render correctly on your local Kitty terminal

### SSH Compatibility Matrix

| Scenario | Detection | Graphics |
|---|---|---|
| Script runs locally in Kitty | ✅ | ✅ |
| SSH via `kitty +kitten ssh` | ✅ | ✅ |
| SSH via plain `ssh` | ❌ | ❌ |
| SSH from WezTerm | ⚠️ | Depends on config |

### Recommendation

For tools deployed over SSH (e.g., connecting to a DAQ host):

- **Default to the Textual TUI fallback** — works everywhere without configuration
- **Make Kitty mode opt-in** via a `--kitty` flag or environment variable, with documentation pointing to `kitty +kitten ssh`

---

## Summary: Choosing Your Stack

| Use Case | Recommended Stack |
|---|---|
| Universal compatibility (SSH, embedded, Windows) | Textual + textual-plotext |
| High-quality local display, Kitty terminal | matplotlib-backend-kitty |
| High-quality local display, Windows | WezTerm + matplotlib-backend-kitty |
| Combined TUI dashboard + pixel plots | WezTerm/Kitty multiplexer panes |
