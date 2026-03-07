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


def spectrum_to_sparkline(db_values, width=60, min_db=-120.0, max_db=-20.0):
    """Convert dB spectrum to a Unicode sparkline string."""
    blocks = " ▁▂▃▄▅▆▇█"
    n_blocks = len(blocks) - 1

    indices = np.linspace(0, len(db_values) - 1, width).astype(int)
    resampled = db_values[indices]
    normalized = np.clip((resampled - min_db) / (max_db - min_db), 0.0, 1.0)

    return "".join(blocks[int(v * n_blocks)] for v in normalized)


# ---------------------------------------------------------------------------
# Demodulation pipeline
# ---------------------------------------------------------------------------

class Demodulator:
    """AM demodulator with decimation, DC removal, and AGC.

    Pipeline: IQ (192 kHz) → lowpass → decimate (÷4) → envelope → DC remove → AGC → audio (48 kHz)
    """

    def __init__(self, iq_sample_rate=192000, audio_rate=48000, bandwidth=5000):
        self.iq_sample_rate = iq_sample_rate
        self.audio_rate = audio_rate
        self.decimation = iq_sample_rate // audio_rate  # 4
        self.bandwidth = bandwidth

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
        """Demodulate AM from complex IQ samples.

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

        # AM envelope detection: magnitude
        envelope = np.sqrt(i_dec ** 2 + q_dec ** 2)

        # DC removal (block-based: subtract smoothed mean)
        block_mean = np.mean(envelope)
        self._dc_avg = self._dc_alpha * self._dc_avg + (1 - self._dc_alpha) * block_mean
        audio = envelope - self._dc_avg

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

    def reset(self):
        """Reset all filter and AGC state."""
        self._lp_zi_i = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
        self._lp_zi_q = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
        self._dc_avg = 0.0
        self._agc_gain = 100.0
