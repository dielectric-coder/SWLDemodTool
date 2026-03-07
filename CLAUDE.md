# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SWL Demod Tool — TUI demodulator for the Elad FDM-DUO software-defined radio. Connects to an IQ sample server and CAT control server over TCP, demodulates AM audio, and displays a live spectrum in the terminal using Textual.

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
- **`cat_client.py`** — TCP client for CAT control. Kenwood-style commands (`;`-terminated). Parses `IF;` for frequency/mode, `SM0;` for S-meter (hardware S-unit values S0-S9+60).
- **`dsp.py`** — FFT spectrum (Blackman window, 4096-point), multi-row Unicode bar chart with peak-hold downsampling, and `Demodulator` class (FIR lowpass -> decimate -> AM envelope -> DC removal -> AGC).
- **`audio.py`** — `sounddevice` OutputStream with manual ring buffer. Handles underruns with silence.
- **`config.py`** — INI config via `configparser` at `$XDG_CONFIG_HOME/swl-demod-tool/config.conf`.

### Threading Model

Three threads: main Textual event loop, IQ receive daemon thread (`IQClient`), sounddevice audio callback thread. IQ data flows from network thread into `_on_iq_data()` which does DSP and pushes audio to the ring buffer. UI updates marshalled via `call_from_thread()`.

### Key Constants

- IQ chunk: 12288 bytes = 1536 IQ pairs (32-bit I + 32-bit Q)
- FFT size: 4096, spectrum averaging: 3 frames, display height: 9 rows
- Demod: 127-tap FIR, decimation factor 4, AGC target 0.3 RMS
- Spectrum zoom: 1x to 1/64x via Shift+arrow keys

## Related Documentation

- `USER_GUIDE.md` — Installation, usage, keybindings, configuration
- `DEV_GUIDE.md` — Architecture details, protocols, DSP pipeline, constants
- `CHANGELOG.md` — Version history
