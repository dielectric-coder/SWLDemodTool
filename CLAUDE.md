# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SWL Demod Tool — TUI demodulator for the Elad FDM-DUO software-defined radio. Connects to an IQ sample server and CAT control server over TCP, demodulates AM/SSB/DRM audio, and displays a live spectrum in the terminal using Textual.

## Commands

```bash
# Install in dev mode
pip install -e .

# Run the app
swl-demod
swl-demod --host 192.168.1.10 --iq-port 4533 --cat-port 4532 --audio-device default

# No tests exist yet. No linter is configured.
```

## Architecture

Real-time data pipeline: **IQ network stream -> DSP -> audio output**, with a Textual TUI for display and control.

### Module Responsibilities

- **`app.py`** — Textual `App` subclass (`DemodApp`). TUI layout, keybindings, periodic UI refresh (1s tick + 100ms display update), coordinates all components. Entry point is `main()`.
- **`iq_client.py`** — TCP client for the Elad Spectrum IQ server. Reads a 16-byte `ELAD` magic header (sample rate, bit depth), then streams 12288-byte chunks of 32-bit signed int IQ pairs, converting to normalized `complex64`. Daemon thread with callback delivery.
- **`cat_client.py`** — TCP client for CAT control. Kenwood-style commands (`;`-terminated). Polls VFO (`FR;`), frequency (`FA;`/`FB;`), and S-meter (`SM0;`). Supports VFO-A/B switching and tuning.
- **`dsp.py`** — FFT spectrum (Blackman window, 4096-point), multi-row Unicode bar chart with peak-hold downsampling, and `Demodulator` class (FIR lowpass -> decimate -> AM/SSB detection -> DC removal -> AGC).
- **`drm.py`** — DRM decoder integration. Spawns the Dream DRM decoder as a subprocess using stdin/stdout pipes (`-I -` / `-O -`). Feeds raw int16 stereo IQ to Dream's stdin, reads decoded audio from stdout, parses status from stderr.
- **`audio.py`** — `sounddevice` OutputStream with manual ring buffer. Handles underruns with silence.
- **`config.py`** — INI config via `configparser` at `$XDG_CONFIG_HOME/swl-demod-tool/config.conf`.

### Threading Model

Multiple threads cooperate: main Textual event loop, IQ receive daemon thread (`IQClient`), sounddevice audio callback thread, and in DRM mode two additional threads (Dream audio reader, Dream stderr parser). IQ data flows from network thread into `_on_iq_data()` which does DSP and pushes audio to the ring buffer (or pipes IQ to Dream in DRM mode). UI updates marshalled via `call_from_thread()`.

### Key Constants

- IQ chunk: 12288 bytes = 1536 IQ pairs (32-bit I + 32-bit Q)
- FFT size: 4096, spectrum averaging: 3 frames, display height: 9 rows
- Demod: 127-tap FIR, decimation factor 4, AGC target 0.3 RMS
- Spectrum zoom: 1x to 1/64x via Shift+arrow keys
- DRM: Dream subprocess with `-c 6` (IQ positive zero-IF), status updates every 1s

### DRM Integration

The DRM mode uses the [Dream](http://drm.sourceforge.net) open-source DRM decoder. Dream is spawned as a subprocess following the same approach as [openwebrx](https://github.com/jketterl/openwebrx):
- IQ data is piped to Dream's stdin as raw int16 interleaved stereo
- Decoded audio is read from Dream's stdout as raw int16 stereo
- Status (sync, SNR, service label, bitrate, mode) is parsed from stderr
- Dream binary is auto-detected from `../DRM/` or `PATH`, or configured via `config.conf`

## Related Documentation

- `USER_GUIDE.md` — Installation, usage, keybindings, configuration
- `DEV_GUIDE.md` — Architecture details, protocols, DSP pipeline, constants
- `CHANGELOG.md` — Version history
