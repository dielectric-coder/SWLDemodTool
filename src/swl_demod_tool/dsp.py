"""DSP functions for spectrum computation and demodulation."""

import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi


def compute_spectrum_db(iq_samples, fft_size=4096):
    """Compute power spectrum in dB from IQ samples.

    Returns array of fft_size dB values, DC-centered.
    """
    if len(iq_samples) < fft_size:
        padded = np.zeros(fft_size, dtype=np.complex64)
        padded[:len(iq_samples)] = iq_samples
        iq_samples = padded

    window = np.blackman(fft_size).astype(np.float32)
    windowed = iq_samples[:fft_size] * window
    spectrum = np.fft.fftshift(np.fft.fft(windowed))
    power = np.maximum(np.abs(spectrum) ** 2, 1e-20)
    db = 10.0 * np.log10(power) - 10.0 * np.log10(fft_size)
    return db.astype(np.float32)


def spectrum_to_sparkline(db_values, width=60, height=5, min_db=-120.0, max_db=-20.0):
    """Convert dB spectrum to a multi-row Unicode bar chart.

    Each column is drawn bottom-up using block characters across *height* rows.
    """
    blocks = " ▁▂▃▄▅▆▇█"
    n_blocks = len(blocks) - 1

    # Peak-hold downsampling: take the max in each bin range so narrow
    # signals (carriers, spurs) are not lost between sample points.
    n = len(db_values)
    if width > n:
        # Fewer data points than columns: interpolate up
        indices = np.linspace(0, n - 1, width).astype(int)
        resampled = db_values[indices]
    else:
        edges = np.linspace(0, n, width + 1).astype(int)
        resampled = np.array([np.max(db_values[edges[i]:edges[i+1]])
                              for i in range(width)], dtype=np.float32)
    normalized = np.clip((resampled - min_db) / (max_db - min_db), 0.0, 1.0)

    # Total sub-steps across all rows (each row has n_blocks sub-steps)
    total_steps = height * n_blocks
    fills = (normalized * total_steps).astype(int)

    rows = []
    for row in range(height - 1, -1, -1):  # top row first
        cell_fills = np.clip(fills - row * n_blocks, 0, n_blocks)
        rows.append("".join(blocks[c] for c in cell_fills))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Demodulation pipeline
# ---------------------------------------------------------------------------

class Demodulator:
    """AM/SSB/SAM demodulator with decimation, DC removal, and AGC.

    Pipeline: IQ (192 kHz) → lowpass → decimate (÷4) → detect → DC remove → AGC → audio (48 kHz)
    Detection: AM = envelope (magnitude), USB/LSB = product detector (real part),
               SAM = PLL synchronous detection.
    """

    def __init__(self, iq_sample_rate=192000, audio_rate=48000, bandwidth=5000):
        self.iq_sample_rate = iq_sample_rate
        self.audio_rate = audio_rate
        self.decimation = iq_sample_rate // audio_rate  # 4
        self.bandwidth = bandwidth
        self.mode = "AM"  # "AM", "SAM", "SAM-U", "SAM-L", "USB", "LSB"

        # Design lowpass FIR filter for anti-alias before decimation
        # Cutoff at bandwidth relative to Nyquist (iq_sample_rate/2)
        num_taps = 127
        self._lp_taps = firwin(num_taps, bandwidth, fs=iq_sample_rate).astype(np.float32)
        # Filter state for continuity across chunks
        self._lp_zi_i = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
        self._lp_zi_q = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0

        # DC removal state (block-based: subtract running mean)
        self._dc_avg = 0.0
        self._dc_alpha = 0.99  # smoothing for DC estimate

        # AGC state (block-based for speed)
        self._agc_target = 0.3       # Target RMS output level
        self._agc_gain = 100.0       # Start with moderate gain for normalized input
        self._agc_attack = 0.1       # Per-block attack rate
        self._agc_decay = 0.005      # Per-block decay rate
        self._agc_max_gain = 100000.0
        self._agc_enabled = True

        # Volume (linear, 0..1)
        self.volume = 0.5
        self.muted = False

        # PLL state for synchronous AM
        self._pll_phase = 0.0       # NCO phase accumulator (radians)
        self._pll_freq = 0.0        # NCO frequency offset (radians/sample)
        # PI loop filter coefficients — ~30 Hz loop bandwidth at 48 kHz
        self._pll_alpha = 0.005     # Proportional gain
        self._pll_beta = 1.5e-5     # Integral gain

    def set_bandwidth(self, bandwidth):
        """Update the demodulation bandwidth (Hz)."""
        if bandwidth == self.bandwidth:
            return
        self.bandwidth = max(100, min(bandwidth, self.iq_sample_rate // 2 - 1))
        num_taps = 127
        self._lp_taps = firwin(num_taps, self.bandwidth, fs=self.iq_sample_rate).astype(np.float32)
        self._lp_zi_i = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
        self._lp_zi_q = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0

    def process(self, iq_samples):
        """Demodulate AM/SSB from complex IQ samples.

        Returns float32 audio array at audio_rate, or empty array if input too short.
        """
        if len(iq_samples) < self.decimation:
            return np.array([], dtype=np.float32)

        # Separate I and Q
        i_in = iq_samples.real.astype(np.float32)
        q_in = iq_samples.imag.astype(np.float32)

        # Lowpass filter I and Q independently (anti-alias)
        i_filt, self._lp_zi_i = lfilter(self._lp_taps, 1.0, i_in, zi=self._lp_zi_i)
        q_filt, self._lp_zi_q = lfilter(self._lp_taps, 1.0, q_in, zi=self._lp_zi_q)

        # Decimate
        i_dec = i_filt[::self.decimation]
        q_dec = q_filt[::self.decimation]

        # Detection
        if self.mode in ("USB", "LSB"):
            # SSB product detector: real part of the complex baseband signal
            detected = i_dec.astype(np.float32)
        elif self.mode in ("SAM", "SAM-U", "SAM-L"):
            # Synchronous AM: PLL locks onto carrier, coherent product detection
            # SAM = both sidebands, SAM-U = upper only, SAM-L = lower only
            detected = self._pll_detect(i_dec, q_dec, self.mode)
        else:
            # AM envelope detection: magnitude
            detected = np.sqrt(i_dec ** 2 + q_dec ** 2)

        # DC removal (block-based: subtract smoothed mean)
        block_mean = np.mean(detected)
        self._dc_avg = self._dc_alpha * self._dc_avg + (1 - self._dc_alpha) * block_mean
        audio = detected - self._dc_avg

        # AGC (block-based for speed)
        if self._agc_enabled:
            audio = self._apply_agc(audio)

        # Volume and mute
        if self.muted:
            return np.zeros(len(audio), dtype=np.float32)

        audio = audio * self.volume

        # Hard clip to prevent speaker damage
        np.clip(audio, -1.0, 1.0, out=audio)

        return audio.astype(np.float32)

    def _apply_agc(self, audio):
        """Apply block-based automatic gain control."""
        # Measure block RMS
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 1e-15:
            return audio * self._agc_gain

        # Desired gain to hit target
        desired_gain = self._agc_target / rms

        # Smooth gain: fast attack, slow decay
        if desired_gain < self._agc_gain:
            self._agc_gain += self._agc_attack * (desired_gain - self._agc_gain)
        else:
            self._agc_gain += self._agc_decay * (desired_gain - self._agc_gain)

        self._agc_gain = max(0.001, min(self._agc_gain, self._agc_max_gain))

        return audio * self._agc_gain

    def get_agc_gain_db(self):
        """Return current AGC gain in dB."""
        if self._agc_gain > 0:
            return 20.0 * np.log10(self._agc_gain)
        return -120.0

    def _pll_detect(self, i_samples, q_samples, mode="SAM"):
        """PLL-based synchronous AM detection.

        Tracks the carrier with a phase-locked loop and performs coherent
        product detection.  After derotation the in-phase (dot) component
        carries the DSB audio.  The quadrature (cross) component is the
        Hilbert transform, so:
          SAM   = dot           (both sidebands)
          SAM-U = dot + cross   (upper sideband)
          SAM-L = dot - cross   (lower sideband)
        """
        n = len(i_samples)
        out = np.empty(n, dtype=np.float32)
        phase = self._pll_phase
        freq = self._pll_freq
        alpha = self._pll_alpha
        beta = self._pll_beta

        for k in range(n):
            # NCO output (local oscillator)
            cos_p = np.cos(phase)
            sin_p = np.sin(phase)

            # Derotate: project signal onto NCO axes
            dot = i_samples[k] * cos_p + q_samples[k] * sin_p
            cross = -i_samples[k] * sin_p + q_samples[k] * cos_p

            # Sideband selection
            if mode == "SAM-U":
                out[k] = dot + cross
            elif mode == "SAM-L":
                out[k] = dot - cross
            else:
                out[k] = dot

            # Phase error via atan2 — normalized, independent of amplitude
            error = np.arctan2(cross, dot)

            # PI loop filter
            freq += beta * error
            phase += freq + alpha * error

        # Wrap phase to [-π, π]
        self._pll_phase = (phase + np.pi) % (2 * np.pi) - np.pi
        self._pll_freq = np.clip(freq, -0.5, 0.5)
        return out

    def reset(self):
        """Reset all filter, AGC, and PLL state."""
        self._lp_zi_i = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
        self._lp_zi_q = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
        self._dc_avg = 0.0
        self._agc_gain = 100.0
        self._pll_phase = 0.0
        self._pll_freq = 0.0
