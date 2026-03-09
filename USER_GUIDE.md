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

DRM decoding requires the Dream 2.2 binary. Build it from source or install from your package manager. The app looks for it at `../DRM/dream-2.2/dream` (relative to the project) or in your `PATH`. You can also set the path in the config file.

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
| `--debug`        | Enable debug logging to `swl-demod.log` |           |

## Keyboard Controls

| Key             | Action                              |
|-----------------|-------------------------------------|
| `c`             | Connect to IQ and CAT servers       |
| `d`             | Disconnect                          |
| `r`             | Reconnect                           |
| `m`             | Toggle mute                         |
| `a`             | Toggle AGC                          |
| `x`             | Cycle mode (AM → SAM → SAM-U → SAM-L → USB → LSB → CW+ → CW- → DRM) |
| `v`             | Toggle VFO (A ↔ B)                  |
| `+` / `-`       | Volume up / down                    |
| `]` / `[`       | Increase / decrease demod bandwidth |
| `→` / `←`       | Tune up / down (1 kHz steps)        |
| `Alt+→` / `Alt+←` | Fine tune (100 Hz steps)         |
| `PgUp` / `PgDn` | RIT tune up / down (10 Hz steps, SSB/CW only) |
| `/`             | Direct frequency entry (kHz, tunes active VFO) |
| `Shift+→`       | Zoom into spectrum                  |
| `Shift+←`       | Zoom out of spectrum                |
| `q`             | Quit                                |
| `Escape`        | Unfocus text input                  |

## Display Layout

```
  SWL Demod Tool v0.4.0     12:34:56 UTC
  ╭─░▒▓  Freq ► ╰─⏺ [kHz]
    IQ ● localhost:4533  192000 Hz 32-bit IQ
   CAT ● localhost:4532
 Audio ● 48000 Hz
  VFO: A    Frequency: 7.100000 MHz    Mode: CW+    BW: 500 Hz
  ▁▁▂▂▃▅▇█▇▅▃▂▂▁▁▁   (9-row spectrum graph)
    Vol: [████████████░░░░░░░░]  60%        AGC:  ON  (+40 dB)
  Audio: [████░░░░░░░░░░░░░░░░]  -42 dB    Buf: [██████████░░░░░░░░░░] 50%
   Peak: [██████░░░░░░░░░░░░░░]  -85.3 dBFS  S: [████████░░░░░░░░░░░░] S7
   Tune: [░░░░░░░░░░█░░░░░░░░░░] +  3.1 Hz    SNR: 18 dB    22 WPM    RIT:  +30 Hz
```

### Mode Info Panel

A dedicated panel below the audio info displays mode-specific indicators:

**CW modes (CW+/CW-):** Tuning indicator (center-zero bar, ±150 Hz range), tone SNR, estimated speed in WPM, and RIT offset. When no tone is detected, readings hold for ~1 second before blanking.

**DRM mode:** Sync status, SNR, robustness mode, station label, bitrate, and text messages. The sync indicators show six status fields (IO, Time, Frame, FAC, SDC, MSC):
- Green `O` = OK
- Red `X` = CRC error
- Yellow `*` = data error
- Dim `-` = not present

**SAM modes:** PLL tracking offset in Hz.

**SSB modes (USB/LSB):** RIT offset.

### Panels

- **Title bar** - App name and UTC clock
- **Connection status** - IQ stream, CAT control, and audio output status with sample rate info
- **Radio info** - Active VFO, tuned frequency, operating mode, bandwidth
- **Spectrum** - Multi-row bar graph of the received spectrum with center frequency marker and zoom span
- **Audio info** - Volume, audio level, peak signal, S-meter, AGC status, buffer fill
- **Mode info** - Mode-specific indicators (CW tuning/SNR/WPM, DRM status, SAM offset, RIT)

## Demodulation Modes

| Mode  | Detection | Notes |
|-------|-----------|-------|
| AM    | Envelope (magnitude) | Default, 5 kHz bandwidth |
| SAM   | PLL synchronous (both sidebands) | Fading-resistant, 5 kHz bandwidth |
| SAM-U | PLL synchronous (upper sideband) | ECSS upper — rejects lower sideband interference |
| SAM-L | PLL synchronous (lower sideband) | ECSS lower — rejects upper sideband interference |
| USB   | Product (I channel) | Upper sideband, 2.4 kHz bandwidth |
| LSB   | Product (I channel) | Lower sideband, 2.4 kHz bandwidth |
| CW+   | Product + BFO (+700 Hz) | Morse code, upper sideband, 500 Hz bandwidth |
| CW-   | Product + BFO (-700 Hz) | Morse code, lower sideband, 500 Hz bandwidth |
| DRM   | Dream decoder | Digital Radio Mondiale, requires Dream binary |

### SAM and ECSS

The SAM modes use a phase-locked loop (PLL) to track the AM carrier and perform coherent detection, which is more resistant to selective fading than envelope detection.

SAM-U and SAM-L are the automatic equivalent of **ECSS** (Exalted Carrier Selectable Sideband) — a classic SWL technique where you zero-beat an AM carrier in SSB mode to select one sideband and reject interference on the other. Traditional ECSS requires manual tuning and constant re-adjustment for drift; the PLL in SAM-U/SAM-L does this automatically.

When a SAM mode is active, the mode info panel displays the PLL tracking offset in Hz (e.g., `PLL Offset: +  12.3 Hz`), showing how far the detected carrier is from the tuned center frequency.

### CW Mode

CW+ and CW- demodulate Morse code (CW) signals using a product detector with a 700 Hz BFO (beat frequency oscillator) tone offset. CW+ uses the upper sideband (+700 Hz), CW- uses the lower sideband (-700 Hz). The default bandwidth is 500 Hz; use `]`/`[` to adjust from 100 Hz (very narrow, contest use) to 1000 Hz (wide, signal finding) in 50 Hz steps.

The mode info panel shows a tuning indicator, tone SNR, and estimated keying speed (WPM). Use `PgUp`/`PgDn` for 10 Hz RIT tuning to zero-beat the signal precisely.

### RIT (Receiver Incremental Tuning)

In SSB and CW modes, `PgUp`/`PgDn` tune the receiver in 10 Hz steps. The cumulative RIT offset is shown in the mode info panel. RIT resets to zero when using coarse/fine tuning, direct frequency entry, or changing mode.

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
