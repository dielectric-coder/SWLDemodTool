# Elad Demod - Developer Guide

## Project Structure

```
src/elad_demod/
    __init__.py       # Version string
    app.py            # Textual TUI application (entry point: main())
    iq_client.py      # TCP client for IQ sample stream
    cat_client.py     # TCP client for CAT radio control
    dsp.py            # DSP: FFT spectrum, sparkline rendering, AM demodulator
    audio.py          # Audio output via sounddevice with ring buffer
    config.py         # INI config file handling
```

## Architecture

Real-time data pipeline: **IQ network stream -> DSP -> audio output**, with a Textual TUI for display and control.

### Threading Model

Three threads cooperate:

1. **Main thread** - Textual event loop, UI rendering, timer callbacks
2. **IQ receive thread** - Daemon thread in `IQClient`, reads TCP stream, calls `_on_iq_data()` callback
3. **Audio callback thread** - Managed by sounddevice, pulls from ring buffer

Data flow:
```
IQ TCP stream -> IQClient._receive_loop() -> DemodApp._on_iq_data()
    -> compute_spectrum_db() -> spectrum buffer (for display)
    -> Demodulator.process() -> AudioOutput.write() -> ring buffer -> speakers
```

UI updates from the IQ thread are marshalled via `call_from_thread()`.

### IQ Protocol

The Elad Spectrum IQ server sends:
1. 16-byte header: `ELAD` magic (4B) + sample rate (4B, uint32) + format bits (4B, uint32) + reserved (4B)
2. Continuous stream of 12288-byte chunks = 1536 IQ pairs (32-bit signed int I + 32-bit signed int Q)

Samples are normalized to float32 [-1, 1] range by dividing by 2^31.

### CAT Protocol

Kenwood TS-480 compatible, semicolon-terminated ASCII commands over TCP.

| Command | Response        | Description                              |
|---------|-----------------|------------------------------------------|
| `IF;`   | `IF...;`        | Frequency (chars 2-13), mode (char 29)   |
| `SM0;`  | `SM0PPPP;`      | S-meter (4-digit value, see table below) |

**S-meter mapping (SM command P2 values):**

| Value | S-Unit | Value | S-Unit |
|-------|--------|-------|--------|
| 0000  | S0     | 0011  | S9     |
| 0002  | S1     | 0012  | S9+10  |
| 0003  | S2     | 0014  | S9+20  |
| 0004  | S3     | 0016  | S9+30  |
| 0005  | S4     | 0018  | S9+40  |
| 0006  | S5     | 0020  | S9+50  |
| 0008  | S6     | 0022  | S9+60  |
| 0009  | S7     |       |        |
| 0010  | S8     |       |        |

Mode codes in IF response (char 29): 1=LSB, 2=USB, 3=CW, 4=FM, 5=AM, 7=CW-R

### DSP Pipeline

**Spectrum display:**
- 4096-point FFT with Blackman window
- 3-frame averaging
- Peak-hold downsampling (max per display bin) to preserve narrow signals
- Multi-row Unicode block character rendering

**AM demodulation:**
```
IQ (192 kHz) -> FIR lowpass (127-tap, scipy firwin)
             -> decimate (divide by 4)
             -> envelope detection (magnitude)
             -> DC removal (smoothed mean subtraction)
             -> AGC (block-based, fast attack / slow decay)
             -> volume / mute
             -> hard clip [-1, 1]
             -> audio output (48 kHz)
```

### Key Constants

| Constant          | Value   | Location      |
|-------------------|---------|---------------|
| FFT size          | 4096    | `app.py`      |
| Spectrum averaging| 3 frames| `app.py`      |
| FIR taps          | 127     | `dsp.py`      |
| Decimation factor | 4       | `dsp.py`      |
| AGC target RMS    | 0.3     | `dsp.py`      |
| IQ chunk size     | 12288 B | `iq_client.py`|
| Audio buffer      | 1 sec   | `audio.py`    |

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Running

```bash
elad-demod --host <server-ip>
```

Requires an Elad Spectrum IQ+CAT server (e.g., from [EladSpectrum](https://github.com/mikewam/EladSpectrum)).
