# SWL Demod Tool - User Guide

TUI demodulator for the Elad FDM-DUO software-defined radio.

## Prerequisites

- An Elad FDM-DUO with IQ and CAT TCP servers running (e.g., via [EladSpectrum](https://github.com/mikewam/EladSpectrum))
- Python 3.10+
- A working audio output device
- For DRM mode: the [Dream](http://drm.sourceforge.net) DRM decoder binary

## Installation

```bash
pip install swl-demod-tool
```

Or from source:

```bash
git clone https://github.com/dielectric-coder/SWLDemodTool.git
cd SWLDemodTool
pip install -e .
```

### DRM Support

DRM decoding requires the Dream binary. Build it from source or install from your package manager. The app looks for it at `../DRM/dream-2.1.1-svn808/dream/dream` (relative to the project) or in your `PATH`. You can also set the path in the config file.

## Running

```bash
swl-demod
```

The app auto-connects on startup to `localhost:4533` (IQ) and `localhost:4532` (CAT).

### Command-Line Options

| Option           | Description                        | Default        |
|------------------|------------------------------------|----------------|
| `--host`         | Server hostname or IP              | from config    |
| `--iq-port`      | IQ stream TCP port                 | 4533           |
| `--cat-port`     | CAT control TCP port               | 4532           |
| `--audio-device` | Audio output device name           | default        |
| `--version`      | Show version and exit              |                |

## Keyboard Controls

| Key             | Action                              |
|-----------------|-------------------------------------|
| `c`             | Connect to IQ and CAT servers       |
| `d`             | Disconnect                          |
| `r`             | Reconnect                           |
| `m`             | Toggle mute                         |
| `a`             | Toggle AGC                          |
| `x`             | Cycle mode (AM → USB → LSB → DRM)  |
| `v`             | Toggle VFO (A ↔ B)                  |
| `+` / `-`       | Volume up / down                    |
| `]` / `[`       | Increase / decrease demod bandwidth |
| `→` / `←`       | Tune up / down (1 kHz steps)        |
| `Alt+→` / `Alt+←` | Fine tune (100 Hz steps)         |
| `/`             | Direct frequency entry (kHz)        |
| `Shift+→`       | Zoom into spectrum                  |
| `Shift+←`       | Zoom out of spectrum                |
| `q`             | Quit                                |
| `Escape`        | Unfocus text input                  |

## Display Layout

```
  SWL Demod Tool v0.3.0     12:34:56 UTC
    IQ ● localhost:4533  192000 Hz 32-bit IQ
   CAT ● localhost:4532
 Audio ● 48000 Hz
  VFO: A    Frequency: 7.100000 MHz    Mode: USB    BW: 3000 Hz
  ▁▁▂▂▃▅▇█▇▅▃▂▂▁▁▁   (9-row spectrum graph)
    Vol: [████████████░░░░░░░░]  60%        AGC:  ON  (+40 dB)
  Audio: [████░░░░░░░░░░░░░░░░]  -42 dB    Buf: [██████████░░░░░░░░░░] 50%
   Peak: [██████░░░░░░░░░░░░░░]  -85.3 dBFS  S: [████████░░░░░░░░░░░░] S7
```

### DRM Mode Display

When DRM mode is active, the radio info line shows DRM decoder status:

```
  VFO: A    Frequency: 5.980000 MHz    Mode: DRM    DRM  Sync: OOO-OO  SNR: 18.3 dB    Mode: B    [BBC WS]    23.1 kbps    Audio: 10/10
```

The sync indicators show six status fields (IO, Time, Frame, FAC, SDC, MSC):
- Green `O` = OK
- Red `X` = CRC error
- Yellow `*` = data error
- Dim `-` = not present

While acquiring a signal, the display shows "Acquiring..." until Dream locks onto a valid DRM signal.

### Panels

- **Title bar** - App name and UTC clock
- **Connection status** - IQ stream, CAT control, and audio output status with sample rate info
- **Radio info** - Active VFO, tuned frequency, operating mode, bandwidth (or DRM status)
- **Spectrum** - Multi-row bar graph of the received spectrum with center frequency marker and zoom span
- **Audio info** - Volume, audio level, peak signal, S-meter, AGC status, buffer fill

## Demodulation Modes

| Mode | Detection | Notes |
|------|-----------|-------|
| AM   | Envelope (magnitude) | Default, 5 kHz bandwidth |
| USB  | Product (I channel) | Upper sideband, 3 kHz bandwidth |
| LSB  | Product (I channel) | Lower sideband, 3 kHz bandwidth |
| DRM  | Dream decoder | Digital Radio Mondiale, requires Dream binary |

Mode and bandwidth are independent of the radio's settings — they are controlled locally in the app. VFO and frequency are polled from the radio so changes made on the radio or other apps are reflected.

## VFO Control

Press `v` to toggle between VFO-A and VFO-B. The frequency display updates to show the selected VFO's frequency. Tuning controls (`→`/`←`, `Alt+→`/`Alt+←`, `/`) tune the active VFO.

## Spectrum Zoom

Use `Shift+Right` to zoom in and `Shift+Left` to zoom out. Zoom levels halve/double the visible span:

192 kHz -> 96 -> 48 -> 24 -> 12 -> 6 -> 3 kHz

The current span is shown at the bottom-right of the spectrum display.

## S-Meter

The S-meter reads signal strength directly from the radio via CAT command (`SM0;`). Values range from S0 to S9+60 as reported by the FDM-DUO hardware.

## Configuration

Configuration is stored at `$XDG_CONFIG_HOME/swl-demod-tool/config.conf` (typically `~/.config/swl-demod-tool/config.conf`).

```ini
[server]
host = localhost
iq_port = 4533
cat_port = 4532

[audio]
device = default
sample_rate = 48000
buffer_size = 1024

[drm]
dream_path = /path/to/dream
```

The `[drm]` section is optional. If `dream_path` is empty or omitted, the app searches for Dream automatically.

Command-line options override config file values.
