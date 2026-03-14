# SWL Demod Tool - User Guide

Terminal UI demodulator for the Elad FDM-DUO software-defined radio.

A native C/GTK4 GUI port is available separately as [HFDemodGTK](https://github.com/dielectric-coder/HFDemodGTK).

## Prerequisites

- A supported SDR (currently Elad FDM-DUO; more backends planned)
- For Elad FDM-DUO: IQ and CAT TCP servers running (e.g., via [EladSpectrum](https://github.com/mikewam/EladSpectrum))
- A working audio output device
- Python 3.10+
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

### Optional Accelerators

For faster DSP processing, install the optional `accel` extras:

```bash
pip install -e ".[accel]"
```

This adds **pyfftw** (FFTW3-backed FFT, typically 2-3× faster) and **numba** (JIT compilation for per-sample loops like the PLL, noise blanker, and CW envelope tracker). Both are optional — the tool works fine without them using NumPy/SciPy.

### DRM Support

DRM decoding requires the Dream 2.2 binary. Build it from source or install from your package manager.

The app looks for Dream at `../DRM/dream-2.2/dream` (relative to the project) or in `PATH`. You can also set the path in the config file.

## Running

```bash
swl-demod
```

The app auto-connects on startup using the selected SDR backend (default: Elad FDM-DUO at `localhost:4533`/`4532`).

### Command-Line Options

| Option           | Description                        | Default        |
|------------------|------------------------------------|----------------|
| `--sdr`          | SDR backend to use                 | `elad-fdmduo`  |
| `--host`         | Server hostname or IP (Elad)       | from config    |
| `--iq-port`      | IQ stream TCP port (Elad)          | 4533           |
| `--cat-port`     | CAT control TCP port (Elad)        | 4532           |
| `--audio-device` | Audio output device name           | default        |
| `--version`      | Show version and exit              |                |
| `--debug`        | Enable debug logging to `swl-demod.log` |           |

### SDR Backends

| Backend          | Description                              |
|------------------|------------------------------------------|
| `elad-fdmduo`    | Elad FDM-DUO via TCP IQ + CAT server (default) |

Select a backend with `--sdr`:
```bash
swl-demod --sdr elad-fdmduo --host 192.168.1.10
```

## Keyboard Controls

All keybindings are configurable via the `[keys]` section in `config.conf`.

| Key             | Action                              |
|-----------------|-------------------------------------|
| `q`             | Quit                                |
| `Escape`        | Unfocus text input / close popup    |
| `?`             | Show keyboard shortcuts help        |
| `c`             | Connect to IQ and CAT servers       |
| `x`             | Disconnect                          |
| `r`             | Reconnect                           |
| `0`             | Toggle mute                         |
| `a`             | Toggle AGC                          |
| `+` / `-`       | AF gain up / down                   |
| `m`             | Select demod mode (popup)           |
| `b`             | Select bandwidth (popup)            |
| `]` / `[`       | Bandwidth up / down                 |
| `v`             | Select VFO (popup)                  |
| `→` / `←`       | Tune up / down (by tune step)       |
| `s`             | Select tune step (popup)            |
| `/`             | Direct frequency entry (kHz, tunes active VFO) |
| `↑` / `↓`       | RIT offset up / down (SSB/CW only) |
| `f`             | Cycle RIT step (1 / 10 / 100 Hz)    |
| `Shift+→`       | Zoom into spectrum                  |
| `Shift+←`       | Zoom out of spectrum                |
| `d`             | Toggle spectrum display             |
| `n`             | Cycle Noise Blanker (Off / Low / Med / High) |
| `N` (Shift+N)   | Cycle DNR level (Off / 1 / 2 / 3)  |
| `Alt+n`         | Toggle auto notch filter (DNF)      |
| `p`             | Toggle CW Audio Peak Filter (APF)  |
| `l`             | Create SWL log entry                |
| `t`             | Clear decoded text (CW/RTTY/PSK31/MFSK16) |

## Display Layout

```
  SWL Demod Tool v0.5.4     12:34:56 UTC
  ╭─░▒▓  Freq ► ╰─⏺ [kHz]
    IQ ● Elad FDM-DUO  localhost:4533  192000 Hz 32-bit IQ
   CAT ● Elad FDM-DUO  localhost:4532
 Audio ● 48000 Hz
  VFO: A    Frequency: 7.100000 MHz    Mode: CW+    BW: 500 Hz
  ▁▁▂▂▃▅▇█▇▅▃▂▂▁▁▁   (9-row spectrum graph)
  AF Gain: [████████░░░░░]  -4.4 dB          AGC: [██████████░░░] +40 dB        NB: ON (Med)
  AF Peak: [████░░░░░░░░░]  -42 dB           BUF: [████████░░░░░] 80% U:0       DNR: 2
  RF Peak: [██████░░░░░░░]  -85.3 dBFS         S: [████████░░░░░] S7            DNF: OFF
                                                                                 APF: OFF
   Tune: [░░░░░░░░░░█░░░░░░░░░░░] +  3.1 Hz    SNR: 18 dB    22 WPM    RIT: +30 Hz  Step: 10Hz
```

### Mode Info Panel

A dedicated panel below the audio info displays mode-specific indicators:

**CW modes (CW+/CW-):** Tuning indicator (center-zero bar, ±150 Hz range), tone SNR, estimated speed in WPM, RIT offset, and live decoded Morse text. Press `t` to clear the text buffer. When no tone is detected, readings hold for ~1 second before blanking.

**DRM mode:** Sync status with labeled fields, SNR, robustness mode, SDC/MSC constellation (QAM), station label, bitrate, audio codec, country, language, and text messages. The sync line shows six individually labeled fields:
- `io:` `time:` `frame:` `fac:` `sdc:` `msc:` — each with a colored status indicator:
  - Green `O` = OK
  - Yellow `*` = data error
  - Dim `-` = not present
- Coding info shows the SDC and MSC QAM constellation (e.g., `SDC 16-QAM, MSC 64-QAM`) when available from the Dream decoder

**RTTY mode:** Baud rate, shift, mark/space tone level bars, active tone indicator, SNR, and live decoded Baudot text.

**PSK31 mode:** Baud rate, SNR, and live decoded Varicode text.

**MFSK16 mode:** Baud rate, tone count, bandwidth, detected tone index with confidence, SNR, and live decoded MFSK Varicode text.

**SAM modes:** PLL tracking offset in Hz.

**SSB modes (USB/LSB):** RIT offset.

### Panels

- **Title bar** - App name and UTC clock
- **Connection status** - IQ stream, CAT control (with backend name and host:port), and audio output status with sample rate info
- **Radio info** - Active VFO, tuned frequency, operating mode, bandwidth
- **Spectrum** - Multi-row bar graph of the received spectrum with center marker (▲), station name (from SWLScheduleTool), and zoom span
- **Audio info** - AF Gain (dB), AF Peak (audio level), RF Peak (IQ spectrum peak dBFS), S-meter, AGC gain bar, buffer fill, noise reduction status (NB, DNR, DNF, APF)
- **Mode info** - Mode-specific indicators (CW tuning/SNR/WPM, DRM status, SAM offset, SNR, RIT)

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
| RTTY+ | FSK (mark/space, normal) | Radio Teletype, 45.45 Bd, 170 Hz shift, Baudot |
| RTTY- | FSK (mark/space, reverse) | Radio Teletype, reversed polarity |
| PSK31 | BPSK differential       | Phase Shift Keying, 31.25 Bd, Varicode |
| MFSK16| 16-tone FSK + Viterbi FEC | Multi-FSK, 15.625 Bd, MFSK Varicode |
| DRM   | Dream decoder | Digital Radio Mondiale, requires Dream binary |

### SAM and ECSS

The SAM modes use a phase-locked loop (PLL) to track the AM carrier and perform coherent detection, which is more resistant to selective fading than envelope detection.

SAM-U and SAM-L are the automatic equivalent of **ECSS** (Exalted Carrier Selectable Sideband) — a classic SWL technique where you zero-beat an AM carrier in SSB mode to select one sideband and reject interference on the other. Traditional ECSS requires manual tuning and constant re-adjustment for drift; the PLL in SAM-U/SAM-L does this automatically.

When a SAM mode is active, the mode info panel displays the PLL tracking offset in Hz (e.g., `PLL Offset: +  12.3 Hz`), showing how far the detected carrier is from the tuned center frequency.

### CW Mode

CW+ and CW- demodulate Morse code (CW) signals using a product detector with a 700 Hz BFO (beat frequency oscillator) tone offset. CW+ uses the upper sideband (+700 Hz), CW- uses the lower sideband (-700 Hz). The default bandwidth is 500 Hz; use `]`/`[` to adjust from 100 Hz (very narrow, contest use) to 1000 Hz (wide, signal finding) in 50 Hz steps.

The mode info panel shows a tuning indicator, tone SNR, estimated keying speed (WPM), and live decoded Morse text. The decoder needs ~4 elements to establish timing, then produces characters as they are keyed. Unknown sequences display as `␣`. Press `t` to clear the decoded text. Use `PgUp`/`PgDn` for 10 Hz RIT tuning to zero-beat the signal precisely.

### RTTY Mode

RTTY (Radio Teletype) demodulates FSK signals using the amateur standard: 2125 Hz mark tone, 2295 Hz space tone (170 Hz shift), 45.45 baud. The demodulator uses dual bandpass filters to isolate mark and space tones, compares their envelopes to make bit decisions, and recovers the bit clock from the start bit. Characters are decoded using the ITA2/Baudot 5-bit code with LTRS/FIGS shift support. Default bandwidth is 2400 Hz (adjustable 1200-3200 Hz). Decoded text appears in the mode info panel; press `t` to clear.

RTTY+ uses normal polarity (mark low, space high) — the standard amateur convention used by stations like W1AW. RTTY- reverses mark/space interpretation for commercial or maritime stations that use inverted polarity.

The mode info panel shows a mark/space tuning indicator with bar graphs for each tone's level and an active tone label (`MARK`/`SPC`). When properly tuned, you should see both bars active with clear alternation as the signal is received.

### PSK31 Mode

PSK31 (BPSK31) demodulates Phase Shift Keying signals at 31.25 baud. The audio signal is downconverted to baseband at the carrier frequency (~1000 Hz), lowpass filtered, and accumulated over each symbol period. Differential phase detection compares consecutive symbols: same phase = 1, phase reversal = 0. Characters are decoded using Varicode (variable-length codes where common characters like 'e' and space have the shortest codes, separated by two consecutive zeros). Default bandwidth is 500 Hz (adjustable 200-1000 Hz). Decoded text appears in the mode info panel; press `t` to clear.

### MFSK16 Mode

MFSK16 (Multi-Frequency Shift Keying, 16 tones) demodulates signals at 15.625 baud using 16 orthogonal tones spaced 15.625 Hz apart (250 Hz total bandwidth). Each symbol encodes 4 bits via Gray-coded tone selection. The signal is protected by a K=7 R=1/2 convolutional code (Viterbi soft-decision decoding) with convolutional interleaving, providing robust performance on HF fading channels. Characters are decoded using the IZ8BLY MFSK Varicode (variable-length codes with `001` delimiter). Default bandwidth is 500 Hz (adjustable 200-1000 Hz). Decoded text appears in the mode info panel; press `t` to clear.

The mode info panel shows the detected tone index, tone confidence percentage, SNR, and live decoded text. The Viterbi decoder needs approximately 30 symbols (~2 seconds) of warmup before producing output.

MFSK16 is commonly used on HF for keyboard-to-keyboard QSOs and is popular for its strong error correction and resistance to multipath fading. It is supported by fldigi and other amateur radio software.

### RIT (Receiver Incremental Tuning)

In SSB and CW modes, `↑`/`↓` tune the receiver by the current RIT step. Press `f` to cycle the step size: 1 → 10 → 100 Hz. The cumulative RIT offset and current step are shown in the mode info panel. RIT resets to zero when using coarse tuning, direct frequency entry, or changing mode.

### Noise Reduction

Two noise reduction features operate at different stages of the DSP pipeline:

**Noise Blanker (NB)** — Press `n` to toggle. Operates on the raw IQ signal before any filtering. Detects and zeros out short impulse noise (power line interference, ignition noise, switching power supplies). Use `Shift+N` to cycle threshold sensitivity:

| Threshold | Factor | Best for |
|-----------|--------|----------|
| Low       | 10×    | Strong impulses only |
| Med       | 20×    | General purpose (default) |
| High      | 40×    | Weak/frequent impulses |

**Dynamic Noise Reduction (DNR)** — Press `f` to cycle through levels. Operates on the detected audio using a spectral gate with percentile-based noise floor estimation. Reduces broadband noise (hiss) while preserving signal content. The noise floor takes 1-2 seconds to settle after tuning.

| Level | Noise reduction | Character |
|-------|----------------|-----------|
| Off   | —              | —         |
| 1     | -2 dB          | Gentle, minimal artifacts |
| 2     | -3 dB          | Moderate |
| 3     | -4 dB          | Aggressive, deepest suppression |

DNR works best on stationary broadband noise (atmospheric hiss, receiver thermal noise) with a signal present. It has no effect in DRM mode (Dream handles its own decoding).

### SNR Indicator

In AM, SAM, USB, and LSB modes, the mode info panel shows an estimated in-band signal-to-noise ratio. The measurement compares total passband power to the noise floor (estimated from the median of spectral bins). It updates continuously and takes a few seconds to stabilize after tuning.

## SWL Logging

Press `l` to open a log entry form. The form is pre-filled with the current frequency, mode, bandwidth, and station name. Enter a SINPO rating (e.g. `45444`) and optional remarks, then press Enter to save. Press Esc to cancel.

Log entries are appended as CSV rows to `~/Documents/swl-log.csv` (configurable). The CSV columns are:

```
date,time_utc,listener,station,frequency_khz,mode,bandwidth,sinpo,remarks
```

The listener name is remembered across sessions — it is saved to the config file each time you submit a log entry.

Mode and bandwidth are independent of the radio's settings — they are controlled locally in the app. VFO and frequency are polled from the radio so changes made on the radio or other apps are reflected.

## VFO Control

Press `v` to toggle between VFO-A and VFO-B. The frequency display updates to show the selected VFO's frequency. Tuning controls (`→`/`←`, `Alt+→`/`Alt+←`, `/`) tune the active VFO.

## Spectrum Display

The spectrum shows a multi-row Unicode bar chart with a center marker (▲) indicating the tuned frequency. The info line below shows:
- **Station name** (bold gold, left side) — auto-resolved from SWLScheduleTool schedule or received via FIFO
- **Span** (right side) — the visible frequency span

### Zoom

Use `Shift+Right` to zoom in and `Shift+Left` to zoom out. Zoom levels halve/double the visible span:

192 kHz -> 96 -> 48 -> 24 -> 12 -> 6 -> 3 kHz

### Station Name Integration

Station names are resolved automatically from the [SWLScheduleTool](https://github.com/dielectric-coder/SWLScheduleTool) schedule database. When the tuned frequency changes, the app looks up `sked-current.csv` and displays the name of any station currently on-air at that frequency. The lookup checks the sibling `SWLScheduleTool` directory or `~/.local/share/eibi-swl/`.

Additionally, when SWLScheduleTool tunes the radio directly, it sends the station name via `$XDG_RUNTIME_DIR/swldemod-station.fifo`, which takes priority over the automatic lookup.

## S-Meter

The S-meter reads signal strength directly from the radio via CAT command (`SM0;`). Values range from S0 to S9+60 as reported by the FDM-DUO hardware.

## Configuration

Configuration is stored at `$XDG_CONFIG_HOME/swl-demod-tool/config.conf` (typically `~/.config/swl-demod-tool/config.conf`).

```ini
[sdr]
backend = elad-fdmduo

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

The `[sdr]` section selects the SDR backend. The `[server]` section provides connection details for the Elad backend. The `[drm]` section is optional — if `dream_path` is empty or omitted, the app searches for Dream automatically.

The `[noise_reduction]` section stores noise reduction defaults:

```ini
[noise_reduction]
nb_enabled = false
nb_threshold = Med
dnr_level = 0
```

The `[logging]` section configures SWL log entry defaults:

```ini
[logging]
listener = Your Name
log_file = ~/Documents/swl-log.csv
```

Command-line options override config file values.
