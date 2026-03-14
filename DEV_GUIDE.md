# SWL Demod Tool - Developer Guide

## Project Structure

```
src/swl_demod_tool/
    __init__.py       # Version string
    app.py            # Textual TUI application (entry point: main())
    sdr/              # Pluggable SDR backend package
        __init__.py   # Package exports (SDRSource, SDRInfo, create_sdr_source)
        base.py       # Abstract SDRSource base class and SDRInfo dataclass
        elad_fdmduo.py# Elad FDM-DUO backend (wraps IQClient + CATClient)
        registry.py   # Backend registry and factory function
    iq_client.py      # TCP client for Elad IQ sample stream (used by elad_fdmduo backend)
    cat_client.py     # TCP client for Elad CAT radio control (used by elad_fdmduo backend)
    dsp.py            # DSP: FFT spectrum, sparkline rendering, AM/SSB/CW/RTTY/PSK31/MFSK16 demodulator, noise reduction
    drm.py            # DRM decoder integration (Dream subprocess)
    audio.py          # Audio output via sounddevice with ring buffer
    wefax.py          # WEFAX decoder (APT line detection, image assembly)
    config.py         # INI config file handling
```

## Architecture

Real-time data pipeline: **IQ network stream -> DSP -> audio output**, with a Textual TUI for display and control.

### Threading Model

Multiple threads cooperate:

1. **Main thread** - Textual event loop, UI rendering, timer callbacks
2. **IQ receive thread** - Daemon thread in SDR backend (e.g. `IQClient`), reads IQ stream, calls `_on_iq_data()` callback
3. **Audio callback thread** - Managed by sounddevice, pulls from ring buffer
4. **Station FIFO reader thread** - Daemon thread, blocks on `$XDG_RUNTIME_DIR/swldemod-station.fifo` waiting for station names from external tools (e.g. SWLScheduleTool). Updates `station_name` reactive via `call_from_thread()`.
5. **DRM audio reader thread** (DRM mode only) - Reads decoded int16 audio from Dream's stdout
6. **DRM status socket thread** (DRM mode only) - Reads JSON status from Dream's Unix domain socket
7. **DRM stderr drain thread** (DRM mode only) - Drains stderr to prevent pipe blocking

Data flow (AM/SSB mode):
```
SDR backend -> SDRSource.start_streaming(callback) -> DemodApp._on_iq_data()
    -> compute_spectrum_db() -> spectrum buffer (for display)
    -> Demodulator.process() -> AudioOutput.write() -> ring buffer -> speakers
```

Data flow (DRM mode):
```
SDR backend -> SDRSource.start_streaming(callback) -> DemodApp._on_iq_data()
    -> compute_spectrum_db() -> spectrum buffer (for display)
    -> DRMDecoder.write_iq() -> Dream stdin (int16 stereo IQ)
                                Dream stdout -> _read_audio() -> AudioOutput.write() -> ring buffer -> speakers
                                Dream status socket -> _read_status_socket() -> status dict -> display
```

UI updates from background threads are marshalled via `call_from_thread()`.

### Thread Safety

- **Audio ring buffer** is lock-free (single-writer from IQ/DRM thread, single-reader from sounddevice callback). One slot is reserved to distinguish full from empty.
- **`Demodulator._lock`** protects state shared between UI and IQ threads: `agc_enabled`, `volume`, `muted`, CW text/timing/WPM. Access these via the thread-safe properties and `get_*`/`clear_*` methods.
- **`DRMDecoder._lock`** protects `self._process` (preventing race between `write_iq` and `stop`) and `self.status` dict.
- **`_cat_polling` flag** in `DemodApp` prevents concurrent `_poll_cat` worker threads from accumulating when the CAT server is slow.

### SDR Backend Architecture

The `sdr/` package provides a pluggable backend system:

- **`SDRSource`** (ABC) — Defines the interface: `connect()`, `disconnect()`, `start_streaming(callback)`, plus optional radio control methods (`get_frequency()`, `set_frequency()`, `get_active_vfo()`, `set_active_vfo()`, `get_s_meter()`, `get_mode()`). IQ-only backends need only implement the streaming methods; control methods default to no-ops.
- **`SDRInfo`** — Dataclass returned by `SDRSource.info` after connection: `sample_rate`, `sample_bits`, `label`.
- **`create_sdr_source(name, config, args)`** — Factory function with lazy imports. Backend-specific dependencies are only loaded when selected.
- **`EladFDMDuoSource`** — Wraps `IQClient` + `CATClient` as a single `SDRSource`. The existing TCP clients are unchanged.

To add a new backend:
1. Create `sdr/<name>.py` implementing `SDRSource`
2. Register it in `sdr/registry.py`
3. Add backend-specific construction logic in `create_sdr_source()`

### Elad IQ Protocol

The Elad Spectrum IQ server sends:
1. 16-byte header: `ELAD` magic (4B) + sample rate (4B, uint32) + format bits (4B, uint32) + reserved (4B)
2. Continuous stream of 12288-byte chunks = 1536 IQ pairs (32-bit signed int I + 32-bit signed int Q)

Samples are normalized to float32 [-1, 1] range by dividing by 2^31.

### Elad CAT Protocol

Kenwood TS-480 compatible, semicolon-terminated ASCII commands over TCP.

| Command  | Response        | Description                              |
|----------|-----------------|------------------------------------------|
| `IF;`    | `IF...;`        | Frequency (chars 2-13), mode (char 29)   |
| `FA;`    | `FA...;`        | VFO-A frequency (11-digit Hz)            |
| `FB;`    | `FB...;`        | VFO-B frequency (11-digit Hz)            |
| `FR;`    | `FR0;`/`FR1;`   | Active VFO (0=A, 1=B)                    |
| `FR0;`   | `FR0;`          | Set active VFO to A                      |
| `FR1;`   | `FR1;`          | Set active VFO to B                      |
| `FA...;` | `FA...;`        | Set VFO-A frequency                      |
| `FB...;` | `FB...;`        | Set VFO-B frequency                      |
| `SM0;`   | `SM0PPPP;`      | S-meter (4-digit value, see table below) |

The CAT poller queries VFO and frequency each cycle so external frequency changes are tracked. Mode and bandwidth are local to the app and not polled from the radio.

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
- Center marker (▲) on the info line
- Station name from FIFO (bold gold, left side) when available
- Span indicator (right side)

**AM/SAM/SSB/CW/RTTY/PSK31/MFSK16 demodulation:**
```
IQ (192 kHz) -> [Noise Blanker] (impulse detection + zeroing)
             -> FIR lowpass (127-tap, scipy firwin)
             -> decimate (divide by 4)
             -> SNR measurement (percentile-based, passband bins)
             -> detection:
                  AM    = envelope (magnitude)
                  SAM   = PLL coherent (dot)
                  SAM-U = PLL coherent (dot + cross)
                  SAM-L = PLL coherent (dot - cross)
                  USB/LSB = product (I channel)
                  CW+/CW- = audio-rate lowpass (255-tap) -> BFO mix (±700 Hz)
                  RTTY+/RTTY- = dual bandpass (mark/space) -> envelope compare -> Baudot decode
                  PSK31 = NCO downconvert -> lowpass I/Q -> differential phase -> Varicode decode
                  MFSK16 = FFT tone detect -> soft decode -> convolutional interleaver -> Viterbi -> MFSK Varicode
             -> [DNR] (spectral gate, STFT overlap-add)
             -> DC removal (smoothed mean subtraction)
             -> AGC (block-based, fast attack / slow decay)
             -> volume / mute
             -> hard clip [-1, 1]
             -> audio output (48 kHz)
```

**Noise Blanker:** Operates on raw IQ at full sample rate (192 kHz). Computes instantaneous magnitude and compares against a slow EMA average. Samples exceeding `threshold × average` are zeroed, with an 8-sample lookahead delay buffer and 4-sample holdoff to catch the rising edge of impulses. Three threshold presets (Low 10×, Med 20×, High 40×).

**DNR (Dynamic Noise Reduction):** Spectral gate using 512-point STFT with 50% overlap (Hann window, proper synthesis window for OLA reconstruction). Noise floor estimated per-frame as the 30th percentile of passband bin powers (bins within the demodulation bandwidth, excluding DC). Bins above `threshold × noise_floor` pass through at gain 1.0; bins at or below get attenuated to `gain_floor`; smooth linear interpolation between. DC bin always passes (preserves AM carrier). Temporal gain smoothing (0.5 factor) prevents per-frame flutter. Three levels control the gate threshold and floor depth.

**SNR Estimator:** Measures in-band SNR from decimated IQ using a 1024-point FFT. Compares total passband power (mean of passband bins) to the noise floor (median of passband bins — robust to narrowband signals). Asymmetric smoothing: noise floor rises slowly (0.005) and drops fast (0.1). Result clamped to 0-60 dB.

**SAM PLL:** PI loop filter (~30 Hz bandwidth at 48 kHz) with atan2-normalized phase error. Tracks carrier drift without following audio modulation. The PLL coasts through near-zero input samples (e.g. blanked by the noise blanker) — holding frequency and phase while outputting silence — to prevent loss of lock.

**Optional accelerators:** FFT calls use `pyfftw` when installed (FFTW3 with wisdom caching, typically 2-3× faster). Three per-sample inner loops (noise blanker, PLL, CW envelope tracker) use `numba` JIT when installed. Both fall back to NumPy/SciPy transparently. Install via `pip install -e ".[accel]"`.

**CW BFO:** 700 Hz tone offset mixed with the decimated complex signal. CW+ shifts up (+700 Hz, upper sideband), CW- shifts down (-700 Hz, lower sideband). Phase accumulator persists across chunks for glitch-free output. Default bandwidth 500 Hz (adjustable 100-1000 Hz). CW uses a two-stage filter: wide 2400 Hz pre-decimation anti-alias (127-tap at 192 kHz), then narrow post-decimation audio-rate lowpass (255-tap at 48 kHz) applied to I/Q before BFO mixing.

**CW tone analysis:** An 8192-sample rolling buffer feeds an FFT for tone detection (~5.9 Hz/bin). Tone presence is determined by spectral concentration within the BFO ± bandwidth passband (threshold 0.25). Peak frequency uses parabolic interpolation for sub-bin accuracy, with exponential smoothing. SNR is measured as peak-to-noise ratio within the passband.

**CW speed measurement:** Sample-level envelope detection (attack ~0.5 ms, decay ~10 ms) with adaptive threshold at 40% of peak. Key-down durations are collected and clustered using iterative dit/dah separation. WPM is estimated from median dit duration (standard: 1200 / dit_ms). Exponential smoothing on the WPM output for stability.

**CW Morse decoder:** Element classification uses the smoothed dit duration: marks < 2× dit = dit, ≥ 2× dit = dah. Inter-character space detected at > 2.5× dit, inter-word space at > 5× dit. Elements are accumulated per character and decoded via ITU Morse lookup table. Pending characters are flushed after prolonged silence. The decoded text buffer holds the last 120 characters (scrolling).

**RTTY FSK demodulator:** Two 255-tap bandpass FIR filters (80 Hz bandwidth each) isolate the mark (2125 Hz) and space (2295 Hz) tones from the decimated I channel audio. Envelope detection computes the magnitude of each filtered signal. An EMA-smoothed discriminator (mark minus space) makes the bit decision. RTTY- reverses the discriminator polarity for stations using inverted mark/space. Clock recovery synchronizes on the start bit (space-to-mark transition at mid-bit), then samples 5 data bits (LSB first) and a stop bit. The 5-bit code is decoded via ITA2/Baudot lookup tables with LTRS/FIGS shift state tracking. Smoothed mark/space envelope levels (EMA α=0.15) are exposed for the UI tuning indicator.

**BPSK31 demodulator:** A numerically-controlled oscillator (NCO) at 1000 Hz downconverts the decimated I channel audio to baseband I/Q. Both channels are lowpass filtered (127-tap, 100 Hz cutoff). Samples are accumulated over one symbol period (48000/31.25 = 1536 samples). At each symbol boundary, the accumulated I/Q value is compared to the previous symbol using a normalized dot product: positive = same phase (bit 1), negative = phase reversal (bit 0). Bits are accumulated into a Varicode buffer; a `00` sequence signals character completion, and the preceding bits are looked up in a 128-entry decode table (full ASCII). Codes longer than 20 bits without a separator are discarded as noise.

**MFSK16 demodulator:** 16-tone FSK at 15.625 baud (15.625 Hz tone spacing, orthogonal signaling). Each symbol carries 4 bits (Gray-coded tone index). A 3072-sample symbol buffer (64 ms at 48 kHz) feeds an FFT for tone detection. Soft-decision bits are computed as weighted sums of all tone magnitudes (fldigi softdecode). A fldigi-compatible convolutional interleaver (3D table, size=4, depth=10, REV direction with main-diagonal extraction) de-interleaves the soft symbols. A K=7 R=1/2 soft-decision Viterbi decoder (polynomials 0x6d/0x4f, traceback depth 20) recovers data bits. Characters are decoded using the IZ8BLY MFSK Varicode table (variable-length codes with `001` delimiter, 256-entry table). Default bandwidth 500 Hz (adjustable 200-1000 Hz).

**Audio Peak Filter (APF):** A biquad IIR bandpass filter (Q=15, ~50 Hz bandwidth) centered on the CW BFO frequency (700 Hz). Applied post-detection in CW modes when enabled. Helps isolate a single CW signal in a crowded band by suppressing adjacent signals.

**RIT:** Configurable tuning steps (1/10/100 Hz, cycle with `f`) via Up/Down arrows in SSB/CW modes. Cumulative offset and current step size tracked and displayed; resets on coarse tuning, direct frequency entry, or mode change.

### DRM Integration

DRM decoding uses the Dream 2.2 open-source decoder as a subprocess, following the [openwebrx](https://github.com/jketterl/openwebrx) approach:

```
Dream command: dream -c 6 --sigsrate {iq_rate} --audsrate 48000 -I - -O - --status-socket /tmp/swl_drm_XXXXX/status.sock
```

- `-I -` reads raw int16 stereo IQ from stdin
- `-O -` writes decoded int16 stereo audio to stdout
- `-c 6` selects IQ positive, zero-IF input mode
- `--status-socket` broadcasts JSON status via a Unix domain socket (sync, SNR, mode, SDC/MSC QAM constellation, service label, text, bitrate, audio codec)

The `DRMDecoder` class manages the subprocess lifecycle:
- `start(audio_callback)` — spawns Dream, starts audio reader / status socket / stderr drain threads
- `write_iq(complex64)` — decimates to 48 kHz, converts to int16 stereo, writes to stdin
- `_read_audio()` — reads decoded int16 stereo from stdout with frame-aligned buffering (4-byte stereo frames; remainder bytes carried across reads to prevent misalignment)
- `get_status()` — returns latest parsed status dict
- `stop()` — terminates Dream, cleans up socket file and threads

Dream binary auto-detection order: configured path, `../DRM/dream-2.2/dream`, `PATH`.

### Key Constants

| Constant          | Value   | Location      |
|-------------------|---------|---------------|
| FFT size          | 4096    | `app.py`      |
| Spectrum averaging| 3 frames| `app.py`      |
| FIR taps          | 127     | `dsp.py`      |
| Decimation factor | 4       | `dsp.py`      |
| AGC target RMS    | 0.3     | `dsp.py`      |
| IQ chunk size     | 12288 B | `iq_client.py`|
| MFSK16 tones      | 16      | `dsp.py`      |
| MFSK16 baud       | 15.625  | `dsp.py`      |
| MFSK16 Viterbi    | K=7 R=1/2| `dsp.py`     |
| Audio buffer      | 1 sec   | `audio.py`    |
| DRM status socket   | Unix  | `drm.py`      |

## Development Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Running

```bash
swl-demod --host <server-ip>
```

Default backend requires an Elad Spectrum IQ+CAT server (e.g., from [EladSpectrum](https://github.com/mikewam/EladSpectrum)). Select backend with `--sdr <name>`.
