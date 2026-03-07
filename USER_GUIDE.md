# Elad Demod - User Guide

TUI demodulator for the Elad FDM-DUO software-defined radio.

## Prerequisites

- An Elad FDM-DUO with IQ and CAT TCP servers running (e.g., via [EladSpectrum](https://github.com/mikewam/EladSpectrum))
- Python 3.10+
- A working audio output device

## Installation

```bash
pip install elad-demod
```

Or from source:

```bash
git clone https://github.com/mikewam/elad-demod.git
cd elad-demod
pip install -e .
```

## Running

```bash
elad-demod
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
| `+` / `-`       | Volume up / down                    |
| `]` / `[`       | Increase / decrease demod bandwidth |
| `Shift+Right`   | Zoom into spectrum                  |
| `Shift+Left`    | Zoom out of spectrum                |
| `q` / `Escape`  | Quit                                |

## Display Layout

```
  Elad Demod v0.1.0     12:34:56 UTC
    IQ ● localhost:4533  192000 Hz 32-bit IQ
   CAT ● localhost:4532
 Audio ● 48000 Hz
  Frequency: 7.100000 MHz    Mode: USB    BW: 3000 Hz
  ▁▁▁▂▂▃▅▇█▇▅▃▂▂▁▁▁   (9-row spectrum graph)
                  ↑
               7.100          Span: 192 kHz
    Vol: [████████████░░░░░░░░]  60%        AGC:  ON  (+40 dB)
  Audio: [████░░░░░░░░░░░░░░░░]  -42 dB    Buf: [██████████░░░░░░░░░░] 50%
   Peak: [██████░░░░░░░░░░░░░░]  -85.3 dBFS  S: [████████░░░░░░░░░░░░] S7
```

### Panels

- **Title bar** - App name and UTC clock
- **Connection status** - IQ stream, CAT control, and audio output status with sample rate info
- **Radio info** - Tuned frequency, operating mode, demodulation bandwidth
- **Spectrum** - Multi-row bar graph of the received spectrum with center frequency marker and zoom span
- **Audio info** - Volume, audio level, peak signal, S-meter, AGC status, buffer fill

## Spectrum Zoom

Use `Shift+Right` to zoom in and `Shift+Left` to zoom out. Zoom levels halve/double the visible span:

192 kHz -> 96 -> 48 -> 24 -> 12 -> 6 -> 3 kHz

The current span is shown at the bottom-right of the spectrum display.

## S-Meter

The S-meter reads signal strength directly from the radio via CAT command (`SM0;`). Values range from S0 to S9+60 as reported by the FDM-DUO hardware.

## Configuration

Configuration is stored at `$XDG_CONFIG_HOME/elad-demod/config.conf` (typically `~/.config/elad-demod/config.conf`).

```ini
[server]
host = localhost
iq_port = 4533
cat_port = 4532

[audio]
device = default
sample_rate = 48000
buffer_size = 1024
```

Command-line options override config file values.
