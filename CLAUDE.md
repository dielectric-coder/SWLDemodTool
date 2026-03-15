# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SWL Demod Tool — Terminal UI SDR demodulator with pluggable backends, built with Python and Textual. Supports multiple SDR hardware via a backend abstraction (`sdr/` package). Demodulates AM/SSB/CW/RTTY/PSK31/MFSK16/WEFAX/DRM audio and displays a live spectrum. Default backend: Elad FDM-DUO via TCP IQ + CAT server.

A native C/GTK4 GUI port is available separately as [HFDemodGTK](https://github.com/dielectric-coder/HFDemodGTK).

## Commands

```bash
# Install in dev mode
pip install -e .

# Install with optional DSP accelerators (pyfftw + numba)
pip install -e ".[accel]"

# Run (default Elad backend)
swl-demod
swl-demod --sdr elad-fdmduo --host 192.168.1.10 --iq-port 4533 --cat-port 4532 --audio-device default

# No tests exist yet. No linter is configured.
```

## Architecture

Real-time data pipeline: **IQ network stream -> DSP -> audio output**, with a Textual TUI for display and control.

### Module Responsibilities

- **`app.py`** — Textual `App` subclass (`DemodApp`). TUI layout, keybindings, periodic UI refresh (1s tick + 100ms display update), coordinates all components. Entry point is `main()`. Uses `SDRSource` abstraction for all IQ/control operations. Listens on `$XDG_RUNTIME_DIR/swldemod-station.fifo` for station names from external tools (e.g. SWLScheduleTool). Also auto-resolves station names from the SWLScheduleTool schedule CSV (`sked-current.csv`) when the tuned frequency changes. Includes `LogEntryScreen` modal for SWL logging to CSV.
- **`sdr/`** — Pluggable SDR backend package:
  - **`base.py`** — `SDRSource` ABC (connect/disconnect/stream + optional radio control) and `SDRInfo` dataclass.
  - **`elad_fdmduo.py`** — Elad FDM-DUO backend wrapping `IQClient` + `CATClient`.
  - **`registry.py`** — Backend map and `create_sdr_source()` factory with lazy imports.
- **`iq_client.py`** — TCP client for the Elad Spectrum IQ server (used internally by the Elad backend). Reads a 16-byte `ELAD` magic header (sample rate, bit depth), then streams 12288-byte chunks of 32-bit signed int IQ pairs, converting to normalized `complex64`. Daemon thread with callback delivery.
- **`cat_client.py`** — TCP client for CAT control (used internally by the Elad backend). Kenwood-style commands (`;`-terminated). Polls VFO (`FR;`), frequency (`FA;`/`FB;`), and S-meter (`SM0;`). Supports VFO-A/B switching and tuning.
- **`dsp.py`** — FFT spectrum (Blackman window, 4096-point), multi-row Unicode bar chart with peak-hold downsampling, and `Demodulator` class (FIR lowpass -> decimate -> AM/SSB/CW/RTTY/PSK31/MFSK16/WEFAX detection -> DC removal -> AGC). CW modes include two-stage filtering, BFO tone mixing, tone detection with SNR measurement, and keying speed estimation. RTTY uses dual bandpass mark/space filters with Baudot decoder. PSK31 uses NCO downconversion with differential phase detection and Varicode decoder. MFSK16 uses FFT tone detection with soft-decision Viterbi FEC and IZ8BLY MFSK Varicode.
- **`drm.py`** — DRM decoder integration. Spawns the Dream 2.2 decoder as a subprocess using stdin/stdout pipes (`-I -` / `-O -`). Feeds raw int16 stereo IQ to Dream's stdin, reads decoded audio from stdout, reads JSON status from a Unix domain socket (`--status-socket`).
- **`audio.py`** — `sounddevice` OutputStream with manual ring buffer. Handles underruns with silence.
- **`wefax.py`** — WEFAX decoder: FM subcarrier demodulation (1900 Hz center, 1500-2300 Hz deviation), Goertzel-based start/stop tone detection, phasing sync, line-by-line image assembly, PNG auto-save to `~/Pictures/fax/`. IOC 576, 120 RPM.
- **`decode_viewer.py`** — GTK4 decode viewer for real-time WEFAX image display. Polls temp directory for progressive rendering. Generic viewer framework extensible for future modes.
- **`config.py`** — INI config via `configparser` at `$XDG_CONFIG_HOME/swl-demod-tool/config.conf`. Sections: `[sdr]`, `[server]`, `[audio]`, `[drm]`, `[noise_reduction]`, `[wefax]`, `[logging]`, `[state]`, `[keys]`.

### Threading Model

Multiple threads cooperate: main Textual event loop, IQ receive daemon thread (SDR backend, e.g. `IQClient`), sounddevice audio callback thread, station FIFO reader daemon thread, and in DRM mode three additional threads (Dream audio reader, status socket reader, stderr drain). IQ data flows from the SDR backend thread into `_on_iq_data()` which does DSP and pushes audio to the ring buffer (or pipes IQ to Dream in DRM mode). The station FIFO thread uses `O_NONBLOCK` + `select.select()` on `$XDG_RUNTIME_DIR/swldemod-station.fifo` for clean shutdown. UI updates marshalled via `call_from_thread()`. The audio ring buffer is lock-free (single-writer/single-reader); overflow drops oldest input samples without touching the reader position. `Demodulator._lock` protects all shared UI/IQ thread state including filter state, getters, bandwidth, and mode transitions. `DRMDecoder._lock` protects process handle and status dict; `stop()` joins all threads. `_cat_polling_lock` prevents concurrent CAT poll workers.

### Key Constants

- IQ chunk: 12288 bytes = 1536 IQ pairs (32-bit I + 32-bit Q)
- FFT size: 4096, spectrum averaging: 3 frames, display height: 9 rows
- Demod: 127-tap FIR (pre-decimation), decimation factor 4, AGC target 0.3 RMS
- CW: 255-tap post-decimation audio-rate filter, 700 Hz BFO, 8192-sample FFT for tone analysis, APF biquad bandpass (Q=15)
- RTTY: 255-tap dual bandpass filters, 2125/2295 Hz mark/space, 170 Hz shift, 45.45 baud, ITA2/Baudot 5-bit
- PSK31: NCO at 1000 Hz, 127-tap lowpass I/Q, 31.25 baud differential BPSK, Varicode (128-entry ASCII)
- MFSK16: 16 tones, 15.625 Hz spacing, 15.625 baud, K=7 R=1/2 soft Viterbi, convolutional interleaver (size=4, depth=10), IZ8BLY Varicode
- WEFAX: FM subcarrier 1900 Hz center, 1500-2300 Hz deviation, IOC 576, 120 RPM, Goertzel tone detection
- NB max-blank: 64 consecutive samples before forced reset
- Spectrum zoom: 1x to 1/64x via Shift+arrow keys
- DRM: Dream 2.2 subprocess with `-c 6` (IQ positive zero-IF), IQ decimated to 48 kHz, JSON status via Unix socket

### DRM Integration

The DRM mode uses the [Dream](http://drm.sourceforge.net) 2.2 open-source DRM decoder. Dream is spawned as a subprocess following the same approach as [openwebrx](https://github.com/jketterl/openwebrx):
- IQ data is piped to Dream's stdin as raw int16 interleaved stereo
- Decoded audio is read from Dream's stdout as raw int16 stereo
- Status (sync detail per field, SNR, SDC/MSC QAM constellation, service label, text, bitrate, audio codec, mode) is read from a Unix domain socket (`--status-socket`) as JSON
- Dream binary is auto-detected from `../DRM/` or `PATH`, or configured via `config.conf`

## Related Documentation

- `USER_GUIDE.md` — Installation, usage, keybindings, configuration
- `DEV_GUIDE.md` — Architecture details, protocols, DSP pipeline, constants
- `CHANGELOG.md` — Version history
- `TECHNOTES.md` — Technical notes (buffer underruns, etc.)
