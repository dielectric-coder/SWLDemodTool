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
| `x`             | Cycle mode (AM → SAM → ... → CW± → RTTY → PSK31 → DRM) |
| `v`             | Toggle VFO (A ↔ B)                  |
| `+` / `-`       | Volume up / down                    |
| `]` / `[`       | Increase / decrease demod bandwidth |
| `→` / `←`       | Tune up / down (1 kHz steps)        |
| `Alt+→` / `Alt+←` | Fine tune (100 Hz steps)         |
| `PgUp` / `PgDn` | RIT tune up / down (10 Hz steps, SSB/CW only) |
| `/`             | Direct frequency entry (kHz, tunes active VFO) |
| `Shift+→`       | Zoom into spectrum                  |
| `Shift+←`       | Zoom out of spectrum                |
| `n`             | Toggle Noise Blanker on/off         |
| `N` (Shift+N)   | Cycle NB threshold (Low / Med / High) |
| `f`             | Cycle DNR level (Off / 1 / 2 / 3)  |
| `p`             | Toggle CW Audio Peak Filter (APF)  |
| `t`             | Clear decoded text (CW/RTTY/PSK31)  |
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
     NB: ON (Med)    DNR: 2
   Tune: [░░░░░░░░░░█░░░░░░░░░░] +  3.1 Hz    SNR: 18 dB    22 WPM    RIT:  +30 Hz
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

**RTTY mode:** Baud rate, shift, SNR, and live decoded Baudot text.

**PSK31 mode:** Baud rate, SNR, and live decoded Varicode text.

**SAM modes:** PLL tracking offset in Hz.

**SSB modes (USB/LSB):** RIT offset.

### Panels

- **Title bar** - App name and UTC clock
- **Connection status** - IQ stream, CAT control, and audio output status with sample rate info
- **Radio info** - Active VFO, tuned frequency, operating mode, bandwidth
- **Spectrum** - Multi-row bar graph of the received spectrum with center frequency marker and zoom span
- **Audio info** - Volume, audio level, peak signal, S-meter, AGC status, buffer fill, noise reduction status
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
| RTTY  | FSK (mark/space)        | Radio Teletype, 45.45 Bd, 170 Hz shift, Baudot |
| PSK31 | BPSK differential       | Phase Shift Keying, 31.25 Bd, Varicode |
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

### PSK31 Mode

PSK31 (BPSK31) demodulates Phase Shift Keying signals at 31.25 baud. The audio signal is downconverted to baseband at the carrier frequency (~1000 Hz), lowpass filtered, and accumulated over each symbol period. Differential phase detection compares consecutive symbols: same phase = 1, phase reversal = 0. Characters are decoded using Varicode (variable-length codes where common characters like 'e' and space have the shortest codes, separated by two consecutive zeros). Default bandwidth is 500 Hz (adjustable 200-1000 Hz). Decoded text appears in the mode info panel; press `t` to clear.

### RIT (Receiver Incremental Tuning)

In SSB and CW modes, `PgUp`/`PgDn` tune the receiver in 10 Hz steps. The cumulative RIT offset is shown in the mode info panel. RIT resets to zero when using coarse/fine tuning, direct frequency entry, or changing mode.

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

The `[noise_reduction]` section stores noise reduction defaults:

```ini
[noise_reduction]
nb_enabled = false
nb_threshold = Med
dnr_level = 0
```

Command-line options override config file values.
