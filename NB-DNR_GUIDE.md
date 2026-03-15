# Noise Blanker & Dynamic Noise Reduction — Code Guide

This document explains the NB and DNR implementations in `src/swl_demod_tool/dsp.py`.

## Pipeline Position

```
IQ (192 kHz) -> [NB] -> FIR lowpass -> decimate (÷4) -> SNR -> detect -> [DNR] -> DC remove -> AGC -> audio (48 kHz)
```

NB operates on raw IQ at full sample rate (before any filtering). DNR operates on detected audio (after AM/SSB/CW/RTTY/PSK31 demodulation, before DC removal and AGC). This ordering is deliberate: NB catches impulses before they spread through the lowpass filter, while DNR works on the baseband audio where broadband noise is most apparent.

---

## Noise Blanker (NB)

**Purpose:** Remove short impulse noise — power line interference, ignition noise, switching power supplies — by detecting and zeroing abnormally large samples.

### Algorithm (`_noise_blank()`)

The NB uses sample-by-sample processing on complex IQ data:

1. **Running average magnitude** — An exponential moving average (EMA) of sample magnitudes tracks the "normal" signal level. Only non-impulse samples update the average (samples exceeding `threshold × avg` are excluded from the EMA update). This prevents impulses from inflating the baseline.

   ```python
   if mag[i] < threshold * avg or avg < 1e-15:
       avg += _NB_EMA_ALPHA * (mag[i] - avg)
   ```

   `_NB_EMA_ALPHA = 0.001` gives a very slow time constant (~1000 samples), so the average tracks the signal envelope without reacting to impulses.

2. **Impulse detection** — If `mag[i] > threshold × avg`, the sample is flagged as an impulse and a holdoff counter is set.

3. **Lookahead delay buffer** — The output is delayed by `_NB_LOOKAHEAD = 8` samples using a FIFO buffer. This is critical: impulses have a rising edge, and by the time the impulse is detected, several preceding samples are already contaminated. The delay buffer lets us blank those leading-edge samples retroactively.

   ```
   Input stream:  ...normal...normal...RISING..IMPULSE..IMPULSE..FALLING..normal...
   Detection:                                    ^--- detected here
   Holdoff:        <--------- blanking window extends back via delay -------->
   ```

4. **Holdoff** — After an impulse is detected, blanking continues for `_NB_HOLDOFF = 4` additional samples past the delay window. The total blanking window is `lookahead + holdoff = 12` samples centered around the impulse.

5. **Blanking** — During the blanking window, output samples are replaced with zero (complete removal, not interpolation). Zero-filling is acceptable because the subsequent FIR lowpass filter smooths the gap.

6. **Max-blank safety** (`_NB_MAX_BLANK = 64`) — If blanking persists for 64 consecutive samples without interruption, the blanker assumes the trigger is not impulse noise (e.g., a strong continuous signal exceeding the threshold). It force-resets the EMA average to the current sample magnitude, clears the holdoff, and resumes normal output. This prevents the NB from muting the audio when a strong station is received with a low threshold setting.

### Threshold Presets

| Preset | Factor | Behavior |
|--------|--------|----------|
| Low    | 10×    | Only blanks very strong impulses; minimal false triggering on signal peaks |
| Med    | 20×    | General purpose (default) |
| High   | 40×    | Catches weaker/more frequent impulses; may blank strong signal transients |

Higher factor = more sensitive (catches weaker impulses), despite the counterintuitive naming. The factor represents how many times above the average a sample must be to trigger blanking.

### State Variables

| Variable | Type | Purpose |
|----------|------|---------|
| `_nb_avg_mag` | float | Running EMA of magnitude (persists across chunks) |
| `_nb_delay_buf` | complex64[8] | Lookahead FIFO buffer |
| `_nb_holdoff_count` | int | Remaining blanking samples after impulse |
| `_nb_threshold` | float | Current threshold factor (from preset) |
| `_nb_threshold_name` | str | Current preset name ("Low"/"Med"/"High") |
| `_NB_MAX_BLANK` | int (const) | Max consecutive blanked samples before forced reset (64) |

### Thread Safety

`_nb_enabled` and `_nb_threshold_name`/`_nb_threshold` are protected by `Demodulator._lock`. The NB state variables (`_nb_avg_mag`, `_nb_delay_buf`, `_nb_holdoff_count`) are only accessed from the IQ processing thread, so they don't need locking.

---

## Dynamic Noise Reduction (DNR)

**Purpose:** Reduce broadband noise (atmospheric hiss, receiver thermal noise) from detected audio using a spectral gate. Unlike the NB which targets short impulses, DNR targets continuous background noise.

### Algorithm (`_apply_dnr()`)

DNR uses Short-Time Fourier Transform (STFT) overlap-add processing:

#### 1. STFT Analysis

Audio is accumulated in `_dnr_in_buf` and processed in overlapping frames:

- **FFT size:** 512 samples (~10.7 ms at 48 kHz)
- **Hop size:** 256 samples (50% overlap)
- **Analysis window:** Hann (raised cosine)

```python
spectrum = np.fft.rfft(frame * self._dnr_window)
power = np.abs(spectrum) ** 2
```

The `rfft` returns 257 bins (DC through Nyquist) for real input.

#### 2. Noise Floor Estimation

The noise floor is estimated as the **30th percentile** of power in the passband bins:

```python
bw_bins = max(4, int(self.bandwidth / (self.audio_rate / fft_size)))
passband_power = power[1:bw_bins + 1]  # skip DC
frame_noise = np.percentile(passband_power, _DNR_NOISE_PERCENTILE)
```

**Why percentile?** In a typical AM/SSB signal, most frequency bins contain noise. Only a few bins carry the signal (carrier, sidebands, speech formants). The 30th percentile naturally excludes signal-bearing bins and gives a robust noise-only estimate. This is more reliable than minimum-statistics or calibration-based approaches because:

- It works immediately (no calibration period needed)
- It handles continuous signals like AM carriers (which would fool min-stats trackers)
- It adapts as the noise floor changes (tuning to different bands)

**Why passband only?** After the pre-decimation lowpass filter, bins outside the demodulation bandwidth are near-zero. Including them would make the percentile meaninglessly small, preventing any noise reduction.

**Temporal smoothing:** The noise floor estimate is smoothed across frames to prevent rapid fluctuations:

```python
self._dnr_noise_floor = 0.90 * self._dnr_noise_floor + 0.10 * frame_noise
```

#### 3. Spectral Gate

Each frequency bin gets a gain between `gain_floor` and `1.0` based on its SNR relative to the noise floor:

```python
snr_bin = power / noise_floor
gain = where(snr_bin >= gate_thresh, 1.0,
       where(snr_bin <= 1.0, gain_floor,
             linear_interpolation))
```

The gate has three regions:
- **Above threshold** (`snr_bin >= gate_thresh`): gain = 1.0 (signal passes through)
- **Below noise** (`snr_bin <= 1.0`): gain = `gain_floor` (maximum attenuation)
- **Transition zone** (1.0 to `gate_thresh`): linear interpolation between `gain_floor` and 1.0

This smooth transition avoids the "musical noise" artifacts that hard gating produces.

**DC bin always passes** (`gain[0] = 1.0`) to preserve the AM carrier.

#### 4. Temporal Gain Smoothing

Per-bin gain is smoothed between consecutive frames to prevent flutter:

```python
gain = 0.5 * prev_gain + 0.5 * gain
```

Without this, bins near the threshold would rapidly switch between attenuated and full gain, creating audible artifacts.

#### 5. Ramp-In

During the first 5 frames after DNR is enabled (or after reset), gains ramp from 1.0 to their computed values. This prevents an audible click when DNR engages.

#### 6. Overlap-Add Synthesis

The modified spectrum is converted back to time domain and combined using proper overlap-add:

```python
out_frame = np.fft.irfft(filtered, n=fft_size) * self._dnr_synth_window
out_frame[:hop] += self._dnr_prev_frame  # add previous frame's tail
```

**Synthesis window:** A critical detail — the synthesis window is not just the Hann window. It's computed to ensure perfect reconstruction under OLA:

```python
# The sum of squared analysis windows at each position must equal 1
# synth_window = analysis_window / sum_of_squared_windows
```

With 50% Hann overlap, this gives `w(n) / (w(n)² + w(n + hop)²)` at each position. Without this correction, the output would have amplitude ripple at the hop rate.

### Level Presets

| Level | gate_threshold | gain_floor | Character |
|-------|---------------|------------|-----------|
| 1     | 2.0           | 0.15       | Gentle: bins must be 2× above noise to pass fully; noise bins attenuated to 15% (-16 dB) |
| 2     | 3.0           | 0.08       | Moderate: 3× threshold, -22 dB attenuation |
| 3     | 5.0           | 0.03       | Aggressive: 5× threshold, -30 dB attenuation |

Higher levels provide more noise reduction but increase the risk of signal distortion, especially on weak signals where the signal barely exceeds the noise floor.

### State Variables

| Variable | Type | Purpose |
|----------|------|---------|
| `_dnr_in_buf` | float32[] | Accumulator for incoming audio samples |
| `_dnr_prev_frame` | float32[256] | Previous frame's tail for overlap-add |
| `_dnr_noise_floor` | float | Smoothed noise floor estimate (scalar) |
| `_dnr_prev_gain` | float32[257] | Previous frame's per-bin gains (for temporal smoothing) |
| `_dnr_frame_count` | int | Frame counter (for ramp-in) |
| `_dnr_window` | float32[512] | Hann analysis window |
| `_dnr_synth_window` | float32[512] | OLA synthesis window |
| `_dnr_level` | int | Current level (0=off, 1-3) |

### Thread Safety

`_dnr_level` is protected by `Demodulator._lock` (read from UI thread, used in IQ thread). All other DNR state is only accessed from the IQ processing thread.

---

## Design Decisions and Trade-offs

### Why spectral gate instead of Wiener filter?

The Ephraim-Malah decision-directed Wiener filter was tried and rejected. Problems encountered:

1. **Minimum-statistics noise tracking** converges on continuous AM carriers — the carrier is always present, so the minimum tracker treats it as noise and suppresses the signal.
2. **Decision-directed SNR** with high alpha (0.98) makes all levels behave identically because the previous frame's estimate dominates.
3. **Per-bin Wiener gains** are sensitive to noise estimate accuracy. When the noise estimate is wrong (common with AM/SSB), the result is worse than no processing.

The spectral gate with percentile noise floor is simpler, more robust, and gives predictable results across signal types.

### Why percentile instead of minimum-statistics?

Minimum-statistics (finding the minimum power per bin over a sliding window) works well for speech-in-noise scenarios where the signal is intermittent. In SWL, signals are often continuous (AM carrier, CW tone, SSB with constant speech). The minimum tracker sees the signal as the "floor" and never finds the true noise. The 30th percentile across all passband bins in a single frame works because most bins contain noise at any given instant.

### Why not apply DNR before detection?

DNR operates on real-valued detected audio, not complex IQ. This is intentional:

1. After detection, the signal is baseband audio — noise characteristics are simpler.
2. The signal bandwidth is well-defined (set by the user's bandwidth control).
3. The demodulation process (AM envelope, SSB product) shapes the noise spectrum in ways that are easier to characterize post-detection.
4. Pre-detection noise reduction would need to operate on complex IQ with twice the bins and more complex signal/noise separation.

### DC removal interaction

The DC removal EMA (`_DC_ALPHA = 0.99`) has a long time constant (~100 blocks to converge). In the first seconds after tuning, residual DC can be much larger than the noise floor, masking the effect of DNR. This is expected — DNR's effect becomes apparent after the DC settles (~1-2 seconds).

---

## C/GTK4 Port

The C port ([HFDemodGTK](https://github.com/dielectric-coder/HFDemodGTK)) implements the same NB and DNR algorithms using FFTW3 instead of NumPy. See that repository for C-specific details.
