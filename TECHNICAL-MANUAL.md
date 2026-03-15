# SWL Demod Tool — Technical Manual

A deep-dive into the software architecture, DSP pipelines, and radio theory behind this terminal-based SDR demodulator.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [IQ Sampling and Complex Signals](#2-iq-sampling-and-complex-signals)
3. [The IQ Network Client](#3-the-iq-network-client)
4. [Spectrum Analysis](#4-spectrum-analysis)
5. [The Demodulation Pipeline](#5-the-demodulation-pipeline)
6. [Noise Reduction](#6-noise-reduction)
7. [Automatic Gain Control](#7-automatic-gain-control)
8. [AM Demodulation](#8-am-demodulation)
9. [Synchronous AM (SAM) and the PLL](#9-synchronous-am-sam-and-the-pll)
10. [SSB Demodulation](#10-ssb-demodulation)
11. [CW (Morse) Demodulation and Decoding](#11-cw-morse-demodulation-and-decoding)
12. [RTTY Demodulation and Baudot Decoding](#12-rtty-demodulation-and-baudot-decoding)
13. [PSK31 Demodulation and Varicode](#13-psk31-demodulation-and-varicode)
14. [MFSK16 with Viterbi FEC](#14-mfsk16-with-viterbi-fec)
15. [WEFAX (Weather Fax) Decoding](#15-wefax-weather-fax-decoding)
16. [DRM Decoding via Dream](#16-drm-decoding-via-dream)
17. [Audio Output and the Lock-Free Ring Buffer](#17-audio-output-and-the-lock-free-ring-buffer)
18. [CAT Control Protocol](#18-cat-control-protocol)
19. [Threading Model and Data Flow](#19-threading-model-and-data-flow)
20. [SNR Estimation](#20-snr-estimation)
21. [Glossary](#21-glossary)

---

## 1. System Overview

SWL Demod Tool is a real-time software-defined radio (SDR) receiver that runs entirely in a terminal. The radio hardware is an Elad FDM-DUO connected to a Raspberry Pi CM5 computer, which serves IQ samples and CAT control over TCP. This tool connects to those TCP services, receives raw IQ samples, demodulates them into audio, and plays the result through your sound card — all while displaying a live spectrum.

```
┌──────────────┐   TCP/IQ    ┌──────────────┐          ┌───────────────┐
│  Elad        │────────────▶│  IQ Client   │─────────▶│  Demodulator  │
│  FDM-DUO     │             │  (iq_client)  │          │  (dsp.py)     │
│  IQ Server   │             └──────────────┘          └───────┬───────┘
└──────────────┘                                               │ float32 audio
                                                               ▼
┌──────────────┐   TCP/CAT   ┌──────────────┐          ┌───────────────┐
│  Elad        │◀───────────▶│  CAT Client  │          │  Audio Output │
│  CAT Server  │             │  (cat_client) │          │  (audio.py)   │
└──────────────┘             └──────────────┘          └───────┬───────┘
                                                               │
                                                               ▼
                                                         Sound Card
```

The data pipeline is: **IQ samples → DSP → audio**. A separate CAT control connection handles frequency tuning and mode information. The Textual TUI provides the user interface.

---

## 2. IQ Sampling and Complex Signals

### What Are IQ Samples?

In an SDR, the radio hardware does not output audio directly. Instead, it digitizes the raw radio signal as a pair of channels: **In-phase (I)** and **Quadrature (Q)**. Together they form a *complex signal*:

```
s(t) = I(t) + j·Q(t)
```

This is equivalent to a signal that has both *amplitude* and *phase* information at every sample. A real-valued signal (ordinary audio) can only represent positive and negative frequencies symmetrically, but a complex signal distinguishes positive from negative frequencies — essential for SDR because it lets you see both sidebands of a carrier independently.

### Why Complex?

Consider tuning to 7.100 MHz. The radio's local oscillator is set to 7.100 MHz. After mixing, a station at 7.102 MHz appears at +2 kHz in the complex baseband, and a station at 7.098 MHz appears at −2 kHz. With real-only sampling, both would appear at 2 kHz and you could not separate them — this is called *image aliasing*. IQ sampling eliminates this problem.

### Normalization

The Elad FDM-DUO sends 32-bit signed integer IQ pairs. These are normalized to the range `[-1.0, +1.0]` by dividing by `2^31`:

```python
scale = 1.0 / 2147483648.0   # 1 / 2^31
i_samples = raw[0::2].astype(float32) * scale
q_samples = raw[1::2].astype(float32) * scale
iq = i_samples + 1j * q_samples
```

The result is a NumPy `complex64` array — each element is 8 bytes (4 bytes I + 4 bytes Q, float32 precision).

---

## 3. The IQ Network Client

**Module:** `iq_client.py`

The Elad FDM-DUO's Spectrum IQ server uses a simple TCP protocol:

### Connection Handshake

1. Connect via TCP to `host:port` (default 4533)
2. Read a 16-byte header:

```
Bytes 0-3:   "ELAD" (magic identifier)
Bytes 4-7:   Sample rate in Hz (uint32, little-endian) — typically 192000
Bytes 8-11:  Format bits (uint32) — typically 32
Bytes 12-15: Reserved
```

### Streaming

After the header, IQ data flows continuously in fixed-size chunks:

- **Chunk size:** 12,288 bytes
- **Format:** Interleaved 32-bit signed integers: I₀, Q₀, I₁, Q₁, ...
- **Samples per chunk:** 12288 / 8 = 1536 IQ pairs

Each chunk represents `1536 / 192000 = 8 ms` of signal at the standard 192 kHz sample rate.

The client runs in a **daemon thread** that loops over `recv()` calls, converting each chunk to `complex64` and delivering it via a callback to the main application.

### Reliable Socket Reads

TCP is a stream protocol — a single `recv()` may return fewer bytes than requested. The `_recv_exact(n)` method loops until exactly `n` bytes have been received:

```python
def _recv_exact(self, n):
    data = bytearray()
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:       # Connection closed
            return None
        data.extend(chunk)
    return bytes(data)
```

This guarantees that each IQ chunk is a complete, aligned block.

---

## 4. Spectrum Analysis

**Functions:** `compute_spectrum_db()`, `spectrum_to_sparkline()`

### Computing the Power Spectrum

The spectrum display shows signal power vs. frequency. Computing it requires the **Discrete Fourier Transform (DFT)**, implemented efficiently via the **Fast Fourier Transform (FFT)**.

**Steps:**

1. **Windowing**: Multiply the IQ samples by a Blackman window function. Raw FFT of a finite-length signal introduces *spectral leakage* — energy from a strong signal smears into adjacent frequency bins. The Blackman window reduces this leakage by tapering the signal to zero at the edges, at the cost of slightly wider peaks.

2. **FFT**: A 4096-point complex FFT maps 4096 time-domain samples into 4096 frequency-domain bins. With a 192 kHz sample rate, each bin spans `192000 / 4096 ≈ 46.9 Hz`.

3. **fftshift**: The raw FFT output places DC (0 Hz) at bin 0, positive frequencies in bins 1–2047, and negative frequencies in bins 2048–4095. `fftshift` reorders this so DC is in the center — matching the conventional spectrum display.

4. **Power in dB**: `P_dB = 10·log10(|X[k]|²) - 10·log10(N)`, where the subtraction normalizes for FFT length.

```python
windowed = iq_samples[:4096] * blackman_window
spectrum = fftshift(fft(windowed))
power = max(|spectrum|², 1e-20)        # floor to avoid log(0)
db = 10 * log10(power) - 10 * log10(4096)
```

### Multi-Row Unicode Bar Chart

The spectrum is rendered as a 9-row text display using Unicode block characters (`▁▂▃▄▅▆▇█`). Each column represents one terminal character width. When the display is narrower than the FFT size, bins are downsampled using **peak-hold** — each column shows the *maximum* power in its bin range, ensuring that narrow signals like carriers remain visible.

Below the chart, a center marker (▲) indicates the tuned frequency. The info line shows the station name (bold gold, left side, when received from SWLScheduleTool via FIFO) and the visible span (right side).

### Station Name FIFO

SWL Demod Tool listens on `$XDG_RUNTIME_DIR/swldemod-station.fifo` for station names from external tools. When SWLScheduleTool tunes the radio, it writes the station name to this FIFO. The name is displayed on the spectrum info line and clears automatically on manual tune. The FIFO is created on startup if it doesn't exist; a daemon thread blocks on `open()` waiting for writers.

### Spectrum Zoom

Zoom levels from 1x to 1/64x are achieved by selecting a subset of FFT bins around the center frequency. At 1/64x zoom with 192 kHz sample rate, the visible span is approximately 3 kHz.

### Optional Acceleration: pyfftw

When the `pyfftw` package is installed, all FFT operations use FFTW3 (the Fastest Fourier Transform in the West) instead of NumPy's default. FFTW precomputes optimized "plans" for each FFT size and caches them for reuse, offering 2-5x speedup on repeated transforms of the same size.

---

## 5. The Demodulation Pipeline

**Class:** `Demodulator` in `dsp.py`

All modes follow a common pipeline:

```
IQ (192 kHz)
    │
    ├──▶ [Noise Blanker]     (optional, operates on raw IQ)
    │
    ├──▶ FIR Lowpass Filter   (127-tap, sets receive bandwidth)
    │
    ├──▶ Decimation ÷4        (192 kHz → 48 kHz)
    │
    ├──▶ Detection             (mode-specific: AM, SAM, SSB, CW, RTTY, PSK31, MFSK16)
    │
    ├──▶ [Spectral DNR]       (optional, post-detection noise reduction)
    │
    ├──▶ [Auto Notch]         (optional, removes persistent tones)
    │
    ├──▶ DC Removal            (high-pass, alpha=0.99)
    │
    ├──▶ AGC                   (automatic gain control)
    │
    └──▶ Volume / Mute → Audio Output (48 kHz, float32)
```

### Anti-Alias Filtering and Decimation

**Theory:** To reduce the sample rate by a factor of N (here, 4), you must first remove all frequency content above the new Nyquist frequency (`48000/2 = 24000 Hz`) to prevent aliasing. This is done with a **Finite Impulse Response (FIR) lowpass filter**.

**Implementation:** A 127-tap FIR filter (designed by `scipy.signal.firwin`) is applied separately to the I and Q channels. The cutoff frequency equals the user-selected bandwidth. After filtering, every 4th sample is retained (decimation by 4).

The FIR filter uses `scipy.signal.lfilter` with persistent initial conditions (`zi`), ensuring continuity across successive chunks — without this, each chunk boundary would introduce a transient click.

**Why separate I/Q filtering?** The I and Q channels must be filtered identically to preserve the complex signal's phase relationship. Filtering the complex signal as a whole would require a complex-valued filter; filtering I and Q separately with the same real filter is equivalent and simpler.

### DC Removal

After detection, a slow-tracking high-pass filter removes any DC offset from the audio:

```python
dc_avg = alpha * dc_avg + (1 - alpha) * block_mean
audio = detected - dc_avg
```

With `alpha = 0.99`, the time constant is approximately `1 / (1 - 0.99) = 100` blocks, slow enough to track drift without distorting bass frequencies.

---

## 6. Noise Reduction

### Noise Blanker (NB)

**Purpose:** Remove impulsive interference (power line noise, ignition, lightning) from the raw IQ signal *before* filtering and demodulation.

**Theory:** Impulse noise manifests as brief, extremely high-amplitude spikes. A noise blanker detects these spikes by comparing each sample's magnitude against a running average. When a sample exceeds `threshold * average`, it is replaced with silence (zero). A **lookahead delay buffer** (8 samples) lets the blanker begin zeroing *before* the impulse arrives in the output, and a **holdoff** period extends blanking after the detected peak.

**Algorithm:**

```
For each sample:
    if magnitude < threshold * running_avg:
        update running_avg with EMA (alpha=0.001)
    if magnitude > threshold * running_avg:
        holdoff = holdoff_extension + lookahead
    else if holdoff > 0:
        holdoff -= 1
    output = delay_buffer[oldest] if holdoff == 0 else 0
    push current sample into delay buffer
```

Three threshold presets are available: Low (10x), Med (20x), High (40x).

A safety limit (`_NB_MAX_BLANK = 64`) prevents runaway blanking: if 64 consecutive samples are blanked without interruption, the blanker assumes the signal is not impulse noise, force-resets the EMA average to the current magnitude, and resumes normal output.

### Dynamic Noise Reduction (DNR)

**Purpose:** Reduce broadband noise from the *detected audio* using a spectral gate.

**Theory:** A spectral gate works in the frequency domain. For each short segment of audio:

1. **STFT** (Short-Time Fourier Transform): Window the signal with a Hann window (512 samples, 50% overlap), then FFT to get the spectrum.
2. **Noise floor estimation:** The 30th percentile of passband bin powers is used as the noise floor — a robust estimator that ignores both signal peaks and extreme outliers.
3. **Gain computation:** Bins whose power exceeds `threshold * noise_floor` pass through at unity gain. Bins at or below the noise floor are attenuated to `gain_floor`. A smooth interpolation region in between prevents harsh transitions.
4. **Temporal smoothing:** Gain values are smoothed across frames (`alpha = 0.5`) to prevent audible "flutter" from frame-to-frame gain changes.
5. **Overlap-add synthesis:** The modified spectrum is inverse-FFTed, windowed again (synthesis window), and overlap-added to produce the output.

The **synthesis window** is pre-computed to compensate for the overlap-add energy accumulation, ensuring that the output amplitude is correct when no gating is applied.

Three levels control aggressiveness:
- Level 1 (Gentle): threshold=2x, floor=0.15
- Level 2 (Moderate): threshold=3x, floor=0.08
- Level 3 (Aggressive): threshold=5x, floor=0.03

### Auto Notch

**Purpose:** Automatically detect and null persistent narrow-band tonal interference (heterodynes, carriers from adjacent stations).

**Algorithm:**
1. **STFT analysis** (1024-point, 50% overlap)
2. For each frequency bin, compare its power to the **local median** of neighboring bins (8 bins on each side, excluding the notch region itself). If the bin exceeds the median by 10x, it is flagged as a tonal peak.
3. **Persistence tracking:** A smoothed persistence value (`alpha = 0.85`) accumulates across frames. Only tones that persist for several frames are notched — transient signals pass through.
4. Where persistence exceeds 0.3, the gain is set to 0.01 (-40 dB). Temporal smoothing (`alpha = 0.6`) prevents abrupt notch transitions.
5. Overlap-add synthesis reconstructs the audio.

---

## 7. Automatic Gain Control

**Purpose:** Maintain a consistent output level regardless of signal strength.

**Theory:** Radio signals can vary by 80+ dB. Without AGC, tuning from a weak to a strong station would produce painfully loud audio, or vice versa. AGC measures the signal level and adjusts a gain factor to keep the output at a target level.

**Implementation:** Block-based AGC measures the RMS (Root Mean Square) power of each audio chunk:

```python
rms = sqrt(mean(audio ** 2))
desired_gain = target / rms        # target = 0.3
```

The actual gain tracks the desired gain with asymmetric time constants:
- **Attack** (gain decreasing, signal getting louder): fast (`alpha = 0.1`)
- **Decay** (gain increasing, signal getting quieter): slow (`alpha = 0.005`)

This asymmetry is deliberate: when a strong signal suddenly appears, gain should drop quickly to prevent clipping. When a signal fades, gain should rise slowly to avoid pumping up background noise.

The gain is clamped to `[0.001, 100000]` to prevent runaway amplification of pure silence.

---

## 8. AM Demodulation

**Theory:** Amplitude Modulation encodes audio by varying the *amplitude* of a carrier wave:

```
s(t) = [1 + m * a(t)] * cos(2*pi*f_c*t)
```

where `a(t)` is the audio signal, `m` is the modulation depth, and `f_c` is the carrier frequency. After mixing to baseband (which the SDR hardware does), the carrier is at DC and the signal is:

```
I(t) + j*Q(t)  where  envelope = sqrt(I^2 + Q^2)
```

**Envelope detection** simply computes the magnitude of the complex signal:

```python
detected = sqrt(i_dec ** 2 + q_dec ** 2)
```

This is the simplest and most robust AM demodulation method. It works regardless of whether the receiver is exactly tuned to the carrier — a small tuning error just adds a slight flutter that the ear barely notices.

**Limitation:** Envelope detection responds to the *total* power in the passband, including noise. It cannot reject interference from the unwanted sideband. For these situations, synchronous AM is superior.

---

## 9. Synchronous AM (SAM) and the PLL

**Theory:** Synchronous AM demodulation regenerates the carrier locally and uses it to coherently detect the signal, like a "lock-in amplifier." This eliminates the selective fading distortion that affects envelope detection, and enables independent sideband selection (SAM-U, SAM-L).

### The Phase-Locked Loop (PLL)

The core of SAM is a **Phase-Locked Loop** — a feedback system that locks a local oscillator to the carrier:

```
        ┌──────────────────────┐
        │  IQ Signal In        │
        │  s(k) = I(k) + jQ(k)│
        └───────────┬──────────┘
                    │
                    ▼
           ┌────────────────┐
           │ Phase Detector │──── error = atan2(cross, dot)
           └────────┬───────┘
                    │
                    ▼
           ┌────────────────┐
           │ Loop Filter    │──── freq += beta * error
           │ (PI controller)│     phase += freq + alpha * error
           └────────┬───────┘
                    │
                    ▼
           ┌────────────────┐
           │ NCO            │──── cos(phase), sin(phase)
           └────────────────┘
```

Each sample is processed as follows:

```python
cos_p = cos(phase)
sin_p = sin(phase)

# Demodulate: project signal onto local carrier
dot   =  I * cos_p + Q * sin_p     # In-phase component (audio)
cross = -I * sin_p + Q * cos_p     # Quadrature component (error signal)

# Phase error: angle between signal and local carrier
error = atan2(cross, dot)

# Update loop filter (PI controller)
freq  += beta * error        # Integral: tracks carrier frequency offset
phase += freq + alpha * error  # Proportional + Integral
```

- `alpha = 0.005` (proportional gain) sets the loop bandwidth (~30 Hz at 48 kHz)
- `beta = 1.5e-5` (integral gain) allows the PLL to track slow carrier drift

### Sideband Selection

- **SAM:** Output is `dot` (both sidebands)
- **SAM-U:** Output is `dot + cross` (upper sideband only)
- **SAM-L:** Output is `dot - cross` (lower sideband only)

The math behind this: `dot + cross` is equivalent to the Hilbert transform of the upper sideband, while `dot - cross` gives the lower sideband. This allows rejecting interference on one sideband while preserving the other.

### PLL Coasting

When the Noise Blanker zeros out samples, the PLL receives near-zero input. Taking `atan2(0, 0)` produces undefined phase, which could destabilize the loop. The implementation detects near-zero input (`I^2 + Q^2 < 1e-20`) and **coasts** — holding the current frequency and phase estimate, outputting silence — until valid samples return.

---

## 10. SSB Demodulation

**Theory:** Single-Sideband (SSB) transmits only one sideband of an AM signal, suppressing both the carrier and the other sideband. This doubles spectrum efficiency and concentrates all power in the useful signal.

- **USB (Upper Sideband):** Audio frequencies appear as positive offsets from the carrier. Conventional above 10 MHz.
- **LSB (Lower Sideband):** Audio frequencies appear as negative offsets. Conventional below 10 MHz.

**Demodulation:** After mixing to baseband and filtering to the desired bandwidth, SSB demodulation is trivially the I channel:

```python
detected = i_dec   # The real part of the complex baseband signal
```

The FIR lowpass filter, combined with the complex-to-real conversion, acts as a sideband selector: positive frequencies (USB) appear in the I channel directly, while negative frequencies (LSB) also appear in I but with a frequency inversion that the ear perceives as correct for LSB reception.

This works because the SDR already performs complex downconversion — the frequency selectivity happens in the FIR filter, and the I channel output is already the demodulated audio.

---

## 11. CW (Morse) Demodulation and Decoding

CW (Continuous Wave) is the simplest digital mode: an unmodulated carrier is keyed on and off to spell out Morse code.

### Two-Stage Filtering

CW signals are very narrow (typically 50-200 Hz bandwidth), but the initial anti-alias filter must pass a wider band to avoid aliasing at the decimation step. The solution is two-stage filtering:

1. **Pre-decimation:** A wide FIR (127 taps, 2400 Hz cutoff at 192 kHz) prevents aliasing
2. **Post-decimation:** A narrow FIR (255 taps, user-set bandwidth at 48 kHz) isolates the CW signal

### BFO (Beat Frequency Oscillator)

A bare carrier at baseband (0 Hz) is inaudible. The **Beat Frequency Oscillator** mixes the signal with a 700 Hz tone to produce an audible sidetone:

```python
phases = bfo_phase + (2 * pi * 700 / 48000) * arange(n)
detected = Re(complex_baseband * exp(j * phases))
```

CW+ and CW- use opposite BFO signs to receive on the upper or lower side of the carrier respectively.

### Audio Peak Filter (APF)

An optional narrow IIR bandpass filter (biquad, Q=15, approximately 50 Hz bandwidth) centered on 700 Hz. This further isolates a single CW signal when multiple stations are present:

```python
# Biquad bandpass coefficients
w0 = 2 * pi * f_center / fs
alpha = sin(w0) / (2 * Q)
b = [alpha, 0, -alpha] / (1 + alpha)
a = [1, -2*cos(w0)/(1+alpha), (1-alpha)/(1+alpha)]
```

### Tone Analysis

An 8192-point FFT on the detected audio measures:
- **Peak frequency** (with sub-bin accuracy via parabolic interpolation)
- **Tone SNR** (peak bin power vs. mean of surrounding bins)
- **Tone presence** (whether energy is concentrated in a single bin)

Parabolic interpolation refines the peak frequency beyond the FFT bin resolution:

```python
# Given bins a (left), b (peak), c (right):
delta = 0.5 * (a - c) / (a - 2*b + c)
peak_hz = (peak_bin + delta) * bin_spacing
```

### Envelope Tracking and Morse Decoding

The Morse decoder extracts the keying envelope from the audio:

1. **Envelope follower:** An asymmetric EMA with fast attack (`alpha = 0.06`, ~0.35 ms) and slow decay (`alpha = 0.003`, ~7 ms). This smoothly follows the keying while rejecting noise spikes.

2. **Hysteresis thresholds:** Key-down is detected when the envelope rises above 40% of the peak. Key-up is detected when it falls below 20% of the peak. The gap between these thresholds prevents chatter on noisy signals.

3. **Debouncing:** Transitions shorter than 8 ms are ignored, rejecting noise glitches.

4. **Speed estimation:** Element durations (dits and dahs) are collected and classified using iterative k-means clustering:
   - Start with the median duration as the boundary
   - Iterate: compute median of dits (short) and dahs (long), update boundary
   - Dit duration gives WPM: `WPM = 1200 / dit_ms` (by international convention, "PARIS" = 50 units, 1 dit = 1 unit)

5. **Character decoding:** Dit/dah sequences are looked up in the International Morse Code table. Word gaps (>4 dit durations of silence) insert spaces.

---

## 12. RTTY Demodulation and Baudot Decoding

**Theory:** Radio Teletype (RTTY) uses **Frequency Shift Keying (FSK)** — two audio tones represent mark (1) and space (0):

- Mark: 2125 Hz
- Space: 2295 Hz
- Shift: 170 Hz (space - mark)
- Baud rate: 45.45 (standard amateur RTTY)

### FSK Detection

```
Audio ──┬──▶ Mark Bandpass (2125 Hz, 80 Hz BW) ──▶ Envelope ──┐
        │                                                       ├──▶ Discriminator
        └──▶ Space Bandpass (2295 Hz, 80 Hz BW) ──▶ Envelope ──┘
                                                     (mark - space)
```

Two 255-tap FIR bandpass filters separate the mark and space tones. The envelope of each filtered signal is computed as `|filtered|`. The discriminator is the difference: `mark_envelope - space_envelope`. Positive means mark; negative means space. An EMA smoother (`alpha = 0.3`) stabilizes the decision.

### RTTY+/- Polarity

Standard amateur RTTY uses "mark low, space high" (normal polarity). Some commercial and maritime stations use reversed polarity. RTTY- mode simply negates the discriminator.

### Asynchronous Bit Clock Recovery

RTTY is an *asynchronous* protocol — there is no separate clock signal. The receiver must synchronize to the data stream using the **start bit**:

1. **IDLE:** Wait for a space (start bit). On detection, begin timing.
2. **START:** At mid-bit (0.5 bit periods later), verify the start bit is still space. If mark, it was a false start — return to IDLE.
3. **DATA:** Sample 5 data bits at the center of each bit period (1 bit period apart). Bits are LSB-first.
4. **STOP:** Wait one bit period for the stop bit(s), then decode the character.

### ITA2 (Baudot) Code

Baudot is a 5-bit code with two character sets selected by shift characters:
- **LTRS** (letters): A-Z
- **FIGS** (figures): 0-9, punctuation

The 5-bit code `11111` switches to LTRS mode; `11011` switches to FIGS mode. This means if a shift character is lost due to noise, all subsequent characters decode incorrectly until the next shift — a fundamental weakness of Baudot.

---

## 13. PSK31 Demodulation and Varicode

**Theory:** PSK31 (Phase Shift Keying, 31.25 baud) is a narrow-band digital mode that encodes data as phase changes of an audio carrier:

- **BPSK (Binary PSK):** Two phases — same phase = "1", inverted phase = "0"
- **Baud rate:** 31.25 symbols/second
- **Bandwidth:** ~62.5 Hz (extremely narrow)

### NCO Downconversion

The PSK31 signal is centered at 1500 Hz in the audio passband (the standard audio offset convention used by fldigi and other PSK software). A **Numerically Controlled Oscillator (NCO)** mixes it to baseband:

```python
lo_freq = 2 * pi * 1500 / 48000   # radians per sample
lo_phases = accumulated_phase + lo_freq * arange(n)

bb_i = audio * cos(lo_phases)     # In-phase baseband
bb_q = audio * (-sin(lo_phases))  # Quadrature baseband
```

### Lowpass Filtering

A 127-tap FIR lowpass (100 Hz cutoff) on both I and Q channels removes out-of-band noise and adjacent signals. This is the primary selectivity for PSK31.

### Symbol Clock Recovery

Samples are accumulated over one symbol period (`48000 / 31.25 = 1536` samples). When the accumulator reaches one symbol period, the average I and Q values form the *received symbol* as a complex number.

### Differential Phase Detection

PSK31 uses **differential encoding** — information is in the *change* between consecutive symbols, not their absolute phase. This eliminates the need for absolute carrier phase recovery.

```python
# Normalized dot product of consecutive symbols:
dot = (current.real * prev.real + current.imag * prev.imag)
dot /= abs(current) * abs(prev)

bit = 1 if dot > 0 else 0
# dot > 0 means same phase means "1" (idle)
# dot < 0 means phase reversal means "0" (data)
```

### Varicode

PSK31 uses **Varicode** — a variable-length code where common characters have short codes (like Huffman coding):

| Character | Code | Length |
|-----------|------|--------|
| `e` | `11` | 2 bits |
| `t` | `101` | 3 bits |
| `space` | `1` | 1 bit |
| `A` | `1111101` | 7 bits |
| `0` (zero) | `10110111` | 8 bits |

Characters are separated by two or more consecutive `0` bits. The decoder accumulates bits until it sees `00`, then looks up the accumulated pattern in the Varicode table.

If 20+ bits accumulate without a `00` separator, the buffer is flushed — this is a noise recovery mechanism.

---

## 14. MFSK16 with Viterbi FEC

**Theory:** MFSK16 (Multiple Frequency Shift Keying, 16 tones) is a robust digital mode designed for HF conditions. It combines multi-tone FSK with powerful forward error correction (FEC):

- **16 tones** spaced 15.625 Hz apart
- **Baud rate:** 15.625 symbols/second (64 ms per symbol)
- **FEC:** K=7, R=1/2 convolutional code with soft-decision Viterbi decoding
- **Interleaving:** Convolutional interleaver (size=4, depth=10) spreads burst errors across time

### Tone Detection

Each symbol spans 3072 samples at 48 kHz (`48000 / 15.625 = 3072`). A symbol-length FFT has bin spacing of exactly 15.625 Hz, matching the tone spacing — each tone falls precisely in one FFT bin:

```python
spec = abs(FFT(symbol_buffer, n=3072))
base_bin = round(base_frequency / 15.625)
tone_mags = [spec[base_bin + i] for i in range(16)]
tone_index = argmax(tone_mags)
```

### Gray Coding

The 4-bit tone index is Gray-decoded to obtain the transmitted bit pattern. In Gray code, adjacent tones differ by only 1 bit. This means if noise shifts the detected tone by one position, only 1 bit is in error instead of potentially all 4 — a crucial property for soft-decision decoding.

The distinction between Gray *encoding* (binary to Gray: `i ^ (i >> 1)`) and *decoding* (Gray to binary) matters: the receiver must undo the transmitter's Gray encoding to recover the original bit pattern.

### Soft-Decision Decoding

Rather than making a hard "tone 7" or "tone 8" decision, the decoder produces **soft symbols** (0-255) indicating confidence:

```python
# For each of the 4 bits, weight by all 16 tones:
for each tone i:
    gray_decoded = gray_to_binary(i)
    for each bit k:
        if gray_decoded[k] == 1: b[k] += tone_mag[i]
        else:                    b[k] -= tone_mag[i]

# Scale to 0-255 (128 = erasure/unknown):
soft = 128 + (b / total_mag) * 256
```

### Convolutional Interleaver

Burst errors (e.g., from fading) corrupt consecutive symbols. The interleaver spreads symbols across time so that a burst error becomes scattered single-bit errors that the Viterbi decoder can correct.

The fldigi-compatible convolutional interleaver uses FIFO delay lines. Each of the 4 bit positions (size=4) passes through a delay proportional to `(SIZE - 1 - i) * DEPTH`, where DEPTH=10. This creates staggered delays:

- Bit 0: delay of 30 symbols
- Bit 1: delay of 20 symbols
- Bit 2: delay of 10 symbols
- Bit 3: delay of 0 symbols (passes through immediately)

The transmitter applies the complementary delays, so the total delay for each bit position is the same, but symbols that were adjacent at the transmitter arrive separated in time at the receiver.

### Viterbi Decoder

The K=7, R=1/2 convolutional code encodes 1 input bit into 2 output bits using two generator polynomials (`0x6d` and `0x4f`). The Viterbi algorithm finds the most likely bit sequence by maintaining 64 states (`2^(K-1)`) and tracking the accumulated soft-decision metric for each path.

**State machine:**
- 64 states, each representing the last 6 input bits
- For each pair of soft symbols received, update all state metrics:

```python
for each state:
    for input_bit in (0, 1):
        next_state = (state << 1 | input_bit) & 63
        expected_output = convolutional_encode(state, input_bit)
        branch_metric = correlation(received_soft, expected_output)
        total_metric = old_metric[state] + branch_metric
        if total_metric > best_metric[next_state]:
            best_metric[next_state] = total_metric
            survivor[next_state] = state
```

**Traceback:** After 20 steps (traceback depth), follow the survivor path backwards from the best state to decode one bit.

### MFSK Varicode (IZ8BLY)

Unlike PSK31 Varicode, MFSK uses a different variable-length code designed by IZ8BLY. Characters are encoded as variable-length bit patterns with `001` as the delimiter. A shift register accumulates bits; when the last 3 bits are `001`, the accumulated value (right-shifted by 1) is looked up in a 256-entry table.

---

## 15. WEFAX (Weather Fax) Decoding

**Module:** `wefax.py`

HF Weather Fax (WEFAX) transmits grayscale weather charts as analog images using FM subcarrier modulation. It is widely used by meteorological services worldwide.

### FM Subcarrier Demodulation

The audio signal carries an FM subcarrier centered at 1900 Hz:
- **Black:** 1500 Hz
- **White:** 2300 Hz
- **Deviation:** 800 Hz

The demodulator extracts the instantaneous frequency of the subcarrier and maps it linearly to a grayscale pixel value (0-255).

### Image Parameters (IOC 576)

- **IOC (Index of Cooperation):** 576 — defines the line width as `576 × π ≈ 1810` pixels
- **RPM:** 120 — gives 2 scan lines per second
- **Line duration:** 500 ms at 48 kHz audio rate

### State Machine

```
IDLE → START_TONE (300 Hz detected for ~3s)
     → PHASING (alternating B/W sync pulses for line alignment)
     → RECEIVING (pixel assembly, line by line)
     → STOP_TONE (450 Hz detected) → save PNG → IDLE
```

**Tone detection** uses Goertzel filters — efficient single-frequency DFT bins — tuned to the start (300 Hz) and stop (450 Hz) tones. The Goertzel algorithm is preferred over a full FFT because only two specific frequencies need monitoring.

**Phasing** synchronization detects the alternating black/white sync pulses at the start of each line, establishing the correct line-start position.

### Output

Completed faxes are saved as grayscale PNG files to `~/Pictures/fax/` (configurable via `[wefax]` config section). Intermediate data is written to a temp directory (`image.raw` + `meta.json`) for the GTK4 decode viewer to poll.

### GTK4 Decode Viewer

**Module:** `decode_viewer.py`

A separate GTK4 window polls the WEFAX temp directory and displays the image in real-time as scan lines arrive. The viewer is launched as a subprocess when WEFAX mode is selected and requires GTK4/PyGObject. It is designed as a generic decode viewer extensible for future modes.

---

## 16. DRM Decoding via Dream

**Module:** `drm.py`

DRM (Digital Radio Mondiale) is a digital broadcast standard for shortwave radio. Its modulation (COFDM — Coded Orthogonal Frequency Division Multiplexing) is far too complex to implement inline, so this tool wraps the **Dream 2.2** open-source decoder as a subprocess.

### Architecture

```
IQ Samples ──▶ [Decimate to 48 kHz] ──▶ Dream stdin (raw int16 stereo)
                                              │
                                              ▼
                                        Dream 2.2 Process
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                    stdout (decoded     Unix socket           stderr
                     raw int16 audio)   (JSON status)         (diagnostics)
                          │                   │
                          ▼                   ▼
                    Audio Ring Buffer    Status Display
```

### IQ Decimation

The Elad provides IQ at 192 kHz; Dream expects 48 kHz. A 127-tap FIR anti-alias filter followed by x4 decimation reduces the rate. The output is scaled to 16-bit signed integers and written as interleaved stereo (I=left, Q=right) to Dream's stdin pipe.

### Dream Subprocess

Dream is invoked with:
```
dream -c 6 --sigsrate 48000 --audsrate 48000 -I - -O - --status-socket /path
```

- `-c 6`: IQ input mode (positive zero-IF)
- `-I -` / `-O -`: stdin/stdout pipe mode
- `--status-socket`: Unix domain socket for JSON status updates

### Three Reader Threads

- **Audio reader:** Reads int16 stereo from stdout, mixes to mono float32, delivers via callback
- **Status reader:** Connects to the Unix domain socket, reads newline-delimited JSON with sync state, SNR, service info, QAM constellation types
- **Stderr drain:** Reads stderr to prevent pipe blocking (discards output)

### Status Information

The JSON status provides:
- **Sync indicators:** 6 fields (io, time, frame, fac, sdc, msc) — each is "O" (locked), "*" (acquiring), or "-" (not synced)
- **SNR** in dB
- **Robustness mode:** A (most robust), B, C, D (highest data rate)
- **QAM constellation:** 4-QAM, 16-QAM, or 64-QAM for SDC and MSC
- **Service info:** Label, text message, bitrate, audio mode, language, country

---

## 17. Audio Output and the Lock-Free Ring Buffer

**Module:** `audio.py`

### The Problem

The DSP thread produces audio in variable-sized chunks at irregular intervals (driven by network arrival). The sound card consumes audio at a fixed rate (48 kHz) in fixed-size blocks (1024 samples). A buffer is needed to decouple these two timelines.

### Lock-Free Ring Buffer

A circular buffer with separate read and write positions, designed for exactly one writer and one reader — no locks required:

```
                    write_pos
                       |
                       v
+---+---+---+---+---+---+---+---+---+---+
| * | * | * | * | * |   |   |   |   |   |
+---+---+---+---+---+---+---+---+---+---+
  ^
  |
read_pos
```

**Key invariant:** `write_pos == read_pos` means *empty*. To distinguish full from empty, one slot is always reserved — the buffer can hold `capacity - 1` samples.

**Why lock-free?** The `sounddevice` audio callback runs in a real-time thread. Taking a lock in this thread risks **priority inversion** — if the DSP thread holds the lock and gets preempted, the audio thread blocks waiting for it, causing an audible glitch. The lock-free design guarantees that the audio callback always completes in bounded time.

### Underrun Handling

When the audio callback finds the buffer empty, it fills the output with silence:

```python
if available >= frames:
    # Copy from ring buffer
else:
    outdata[:, 0] = 0.0       # Silence
    self._underrun_count += 1
```

This produces a brief dropout (click) but avoids crashing or playing stale data.

### Overflow Handling

When the DSP thread writes faster than the sound card reads (e.g., during startup), the oldest *input* samples are dropped and only the most recent samples that fit are written:

```python
if n > available:
    skip = n - available
    samples = samples[skip:]
    n = available
```

This preserves the lock-free invariant — `_read_pos` is never modified by the writer. The alternative (advancing `_read_pos` from the writer) would create a data race with the audio callback thread.

---

## 18. CAT Control Protocol

**Module:** `cat_client.py`

The Elad FDM-DUO uses the **Kenwood TS-480** CAT protocol — ASCII commands terminated by semicolons:

### Commands

| Command | Description | Example Response |
|---------|-------------|------------------|
| `FA;` | Query VFO-A frequency | `FA00007100000;` (7.100 MHz) |
| `FB;` | Query VFO-B frequency | `FB00014200000;` (14.200 MHz) |
| `FA00007100000;` | Set VFO-A to 7.100 MHz | Echo or ACK |
| `IF;` | Query radio information | 38-char string (freq, mode, etc.) |
| `SM0;` | Query S-meter | `SM00005;` (S-meter raw value) |
| `FR0;` / `FR1;` | Select VFO-A / VFO-B | Echo |

### Mode Extraction

The `IF;` response is a fixed-format string. Character at position 29 encodes the operating mode:

| Code | Mode |
|------|------|
| 1 | LSB |
| 2 | USB |
| 3 | CW |
| 4 | FM |
| 5 | AM |
| 7 | CW-R |

### S-Meter Conversion

The raw SM value (0-22) is mapped to S-units using a lookup table derived from the FDM-DUO manual:

```
Raw: 0  2  3  4  5  6  8  9  10 11 12 14 16 18 20 22
S:   S0 S1 S2 S3 S4 S5 S6 S7 S8 S9 +10 +20 +30 +40 +50 +60
```

Binary search (`bisect`) gives O(log n) lookup.

---

## 19. Threading Model and Data Flow

```
+------------------------------------------------------------------+
|                         Main Thread                              |
|  Textual Event Loop: UI rendering, keybindings, periodic         |
|  updates (1s status poll, 100ms display refresh)                 |
|                                                                  |
|  call_from_thread() marshals updates from other threads          |
+-----------------------------------+------------------------------+
                                    |
        +------------------+------------------+------------------+
        |                  |                  |                  |
        v                  v                  v                  v
+---------------+  +---------------+  +--------------+  +----------------+
| IQ Receive    |  | sounddevice   |  | Station FIFO |  | DRM Threads    |
| Thread        |  | Audio Callback|  | Reader       |  | (x3, DRM only) |
| (daemon)      |  | (real-time)   |  | (daemon)     |  | audio reader,  |
|               |  |               |  |              |  | status socket, |
| recv() loop   |  | Pulls from    |  | Blocks on    |  | stderr drain   |
| -> convert    |  | ring buffer   |  | FIFO open()  |  |                |
| -> callback   |  | every 1024    |  | -> station   |  | Dream subprocess|
+-------+-------+  +---------------+  | name update  |  | stdin/stdout/  |
                                       +--------------+  | socket         |
                                                         +----------------+
        |
        v
  _on_iq_data()
        |
        +----> compute_spectrum_db()     -> spectrum buffer
        |
        +----> [DRM mode] drm.write_iq() -> Dream stdin
        |
        +----> [Other modes] demod.process()
                    |
                    +----> audio.write() -> ring buffer -> audio callback
```

### Synchronization Points

- **`Demodulator._lock`:** Protects mutable state shared between IQ thread (calls `process()`) and UI thread (reads settings, decoded text). Held briefly during property reads/writes.
- **`DRMDecoder._lock`:** Protects process handle and status dict.
- **Ring buffer:** Lock-free by design (single writer, single reader).
- **`call_from_thread()`:** Textual's mechanism for safely updating reactive properties from non-main threads.

---

## 20. SNR Estimation

**Theory:** Signal-to-Noise Ratio measures signal quality. Higher SNR means clearer audio.

**Method:** Spectral analysis of the decimated IQ signal:

1. Compute a 1024-point FFT of the passband
2. **Noise floor:** Median of passband bin powers (robust to carriers and tonal components)
3. **Signal power:** Mean of all passband bin powers (signal + noise)
4. **SNR = (S+N)/N - 1**, converted to dB

The median is a robust estimator because a carrier or strong signal only affects a few bins, while noise affects all of them. The median is unaffected by outlier bins.

**Smoothing:** Asymmetric EMA tracking:
- Signal power: symmetric smoothing (`alpha = 0.85`)
- Noise floor: rises slowly (`alpha = 0.005`), falls moderately fast (`alpha = 0.1`)

The asymmetry prevents brief signal peaks from inflating the noise floor estimate.

---

## 21. Glossary

| Term | Definition |
|------|-----------|
| **AGC** | Automatic Gain Control — adjusts amplification to maintain constant output level |
| **APF** | Audio Peak Filter — narrow bandpass for CW signal isolation |
| **Baudot** | 5-bit character code used by RTTY (ITA2 standard) |
| **BFO** | Beat Frequency Oscillator — generates an audible tone from a CW carrier |
| **BPSK** | Binary Phase Shift Keying — two-phase digital modulation |
| **CAT** | Computer Aided Transceiver — serial control protocol for radios |
| **COFDM** | Coded Orthogonal Frequency Division Multiplexing — modulation used by DRM |
| **Complex Baseband** | Signal representation where the carrier is at 0 Hz, with I and Q channels |
| **Decimation** | Reducing sample rate by discarding samples (after anti-alias filtering) |
| **DNR** | Dynamic Noise Reduction — spectral gate that suppresses noise between signals |
| **DRM** | Digital Radio Mondiale — digital broadcasting standard for shortwave |
| **EMA** | Exponential Moving Average — `y[n] = alpha * x[n] + (1 - alpha) * y[n-1]` |
| **FEC** | Forward Error Correction — redundancy that allows the receiver to fix errors |
| **FFT** | Fast Fourier Transform — efficient algorithm for computing the DFT |
| **FIR** | Finite Impulse Response — filter type with fixed-length tap coefficients |
| **FSK** | Frequency Shift Keying — digital modulation using frequency changes |
| **Gray Code** | Binary encoding where adjacent values differ by only one bit |
| **IIR** | Infinite Impulse Response — recursive filter type (e.g., biquad) |
| **IQ** | In-phase and Quadrature — the two components of a complex radio signal |
| **LSB** | Lower Sideband — SSB mode using frequencies below the carrier |
| **MFSK** | Multiple Frequency Shift Keying — FSK with more than two tones |
| **NCO** | Numerically Controlled Oscillator — software sine wave generator |
| **Nyquist** | Half the sample rate; the maximum frequency representable without aliasing |
| **PLL** | Phase-Locked Loop — feedback system that tracks a carrier's phase and frequency |
| **PSK31** | Phase Shift Keying at 31.25 baud — narrow-band digital mode |
| **RMS** | Root Mean Square — measure of signal amplitude: `sqrt(mean(x^2))` |
| **RTTY** | Radio Teletype — FSK-based text communication mode |
| **SAM** | Synchronous AM — AM demodulation using carrier regeneration via PLL |
| **SDR** | Software Defined Radio — radio where signal processing is done in software |
| **SNR** | Signal-to-Noise Ratio — quality measure in decibels |
| **SSB** | Single Sideband — AM variant transmitting only one sideband |
| **STFT** | Short-Time Fourier Transform — windowed FFT for spectral analysis of non-stationary signals |
| **USB** | Upper Sideband — SSB mode using frequencies above the carrier |
| **Varicode** | Variable-length character encoding (used by PSK31 and MFSK16) |
| **VFO** | Variable Frequency Oscillator — the tuning element of a radio |
| **Viterbi** | Maximum-likelihood sequence decoder for convolutional codes |
| **WEFAX** | Weather Fax — analog image transmission via FM subcarrier on HF |
| **Windowing** | Multiplying signal by a taper function to reduce spectral leakage in FFT |
