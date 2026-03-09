"""DSP functions for spectrum computation and demodulation."""

import math
import threading
import numpy as np
from scipy.signal import firwin, lfilter

# International Morse Code lookup: dit/dah sequence -> character
_MORSE_TABLE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z", ".----": "1", "..---": "2", "...--": "3",
    "....-": "4", ".....": "5", "-....": "6", "--...": "7",
    "---..": "8", "----.": "9", "-----": "0", ".-.-.-": ".",
    "--..--": ",", "..--..": "?", ".----.": "'", "-.-.--": "!",
    "-..-.": "/", "-.--.": "(", "-.--.-": ")", ".-...": "&",
    "---...": ":", "-.-.-.": ";", "-...-": "=", ".-.-.": "+",
    "-....-": "-", "..--.-": "_", ".-..-.": '"', ".--.-.": "@",
}

# Cached Blackman window for spectrum computation
_blackman_cache = {}


def compute_spectrum_db(iq_samples, fft_size=4096):
    """Compute power spectrum in dB from IQ samples.

    Returns array of fft_size dB values, DC-centered.
    """
    if len(iq_samples) < fft_size:
        padded = np.zeros(fft_size, dtype=np.complex64)
        padded[:len(iq_samples)] = iq_samples
        iq_samples = padded

    if fft_size not in _blackman_cache:
        _blackman_cache[fft_size] = np.blackman(fft_size).astype(np.float32)
    window = _blackman_cache[fft_size]

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

    n = len(db_values)
    if width > n:
        indices = np.linspace(0, n - 1, width).astype(int)
        resampled = db_values[indices]
    else:
        edges = np.linspace(0, n, width + 1).astype(int)
        # Ensure no empty bins from floating-point edge effects
        resampled = np.array([np.max(db_values[edges[i]:max(edges[i] + 1, edges[i + 1])])
                              for i in range(width)], dtype=np.float32)
    normalized = np.clip((resampled - min_db) / (max_db - min_db), 0.0, 1.0)

    total_steps = height * n_blocks
    fills = (normalized * total_steps).astype(int)

    rows = []
    for row in range(height - 1, -1, -1):
        cell_fills = np.clip(fills - row * n_blocks, 0, n_blocks)
        rows.append("".join(blocks[c] for c in cell_fills))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Demodulation pipeline
# ---------------------------------------------------------------------------

def _make_filter(num_taps, cutoff, fs):
    """Build an FIR lowpass filter and zero initial conditions."""
    taps = firwin(num_taps, cutoff, fs=fs).astype(np.float32)
    zi = np.zeros(len(taps) - 1, dtype=np.float32)
    return taps, zi


# Demodulator constants
_DC_ALPHA = 0.99
_AGC_TARGET = 0.3
_AGC_INITIAL_GAIN = 100.0
_AGC_ATTACK = 0.1
_AGC_DECAY = 0.005
_AGC_MAX_GAIN = 100000.0
_PLL_ALPHA = 0.005       # Proportional gain (~30 Hz loop BW at 48 kHz)
_PLL_BETA = 1.5e-5       # Integral gain
_CW_BFO_HZ = 700.0
_CW_FFT_SIZE = 8192
_CW_PREFILTER_HZ = 2400
_CW_ENV_ATTACK = 0.04    # ~0.5 ms at 48 kHz
_CW_ENV_DECAY = 0.002    # ~10 ms at 48 kHz
_CW_PEAK_DECAY = 0.99998 # ~1 s at 48 kHz
_CW_THRESHOLD_FRAC = 0.4
_CW_TONE_CONCENTRATION = 0.25
_CW_SNR_SMOOTH = 0.8
_CW_PEAK_HZ_SMOOTH = 0.85
_CW_WPM_SMOOTH = 0.8
_CW_DIT_SMOOTH = 0.8


class Demodulator:
    """AM/SSB/SAM/CW demodulator with decimation, DC removal, and AGC.

    Pipeline: IQ (192 kHz) -> lowpass -> decimate (÷4) -> detect -> DC remove -> AGC -> audio (48 kHz)
    """

    def __init__(self, iq_sample_rate=192000, audio_rate=48000, bandwidth=5000):
        self.iq_sample_rate = iq_sample_rate
        self.audio_rate = audio_rate
        assert iq_sample_rate % audio_rate == 0, "IQ rate must be exact multiple of audio rate"
        self.decimation = iq_sample_rate // audio_rate
        self.bandwidth = bandwidth
        self.mode = "AM"

        # Lock protecting state accessed from both UI and IQ threads
        self._lock = threading.Lock()

        # Pre-decimation lowpass FIR filter
        self._lp_taps, self._lp_zi_i = _make_filter(127, bandwidth, iq_sample_rate)
        _, self._lp_zi_q = _make_filter(127, bandwidth, iq_sample_rate)

        # DC removal
        self._dc_avg = 0.0

        # AGC
        self._agc_gain = _AGC_INITIAL_GAIN
        self._agc_enabled = True

        # Volume
        self.volume = 0.5
        self.muted = False

        # PLL for synchronous AM
        self._pll_phase = 0.0
        self._pll_freq = 0.0

        # BFO for CW
        self._bfo_offset = _CW_BFO_HZ
        self._bfo_phase = 0.0

        # Post-decimation CW filter
        self._cw_taps = None
        self._cw_zi_i = None
        self._cw_zi_q = None
        self._cw_peak_hz = 0.0
        self._cw_tone_present = False
        self._cw_snr_db = 0.0
        self._cw_buf = np.zeros(_CW_FFT_SIZE, dtype=np.float32)
        self._cw_buf_pos = 0  # ring buffer write position (avoids np.roll)

        # CW keying/speed
        self._cw_env = 0.0
        self._cw_env_peak = 0.0
        self._cw_key_down = False
        self._cw_edge_sample = 0
        self._cw_sample_count = 0
        self._cw_element_ms = []
        self._cw_wpm = 0.0

        # CW decoder
        self._cw_current_char = []
        self._cw_decoded_text = ""
        self._cw_last_keyup_sample = 0
        self._cw_dit_ms = 0.0

    def _update_cw_filter(self):
        """Rebuild the post-decimation audio-rate lowpass for CW modes."""
        self._cw_taps, self._cw_zi_i = _make_filter(255, self.bandwidth, self.audio_rate)
        _, self._cw_zi_q = _make_filter(255, self.bandwidth, self.audio_rate)

    def set_bandwidth(self, bandwidth):
        """Update the demodulation bandwidth (Hz)."""
        if bandwidth == self.bandwidth:
            return
        self.bandwidth = max(100, min(bandwidth, self.iq_sample_rate // 2 - 1))
        if self.mode in ("CW+", "CW-"):
            self._lp_taps, self._lp_zi_i = _make_filter(127, _CW_PREFILTER_HZ, self.iq_sample_rate)
            _, self._lp_zi_q = _make_filter(127, _CW_PREFILTER_HZ, self.iq_sample_rate)
            self._update_cw_filter()
        else:
            self._lp_taps, self._lp_zi_i = _make_filter(127, self.bandwidth, self.iq_sample_rate)
            _, self._lp_zi_q = _make_filter(127, self.bandwidth, self.iq_sample_rate)

    def process(self, iq_samples):
        """Demodulate AM/SSB from complex IQ samples.

        Returns float32 audio array at audio_rate, or empty array if input too short.
        """
        if len(iq_samples) < self.decimation:
            return np.array([], dtype=np.float32)

        # Separate I and Q
        i_in = iq_samples.real.astype(np.float32)
        q_in = iq_samples.imag.astype(np.float32)

        # Lowpass filter (anti-alias)
        i_filt, self._lp_zi_i = lfilter(self._lp_taps, 1.0, i_in, zi=self._lp_zi_i)
        q_filt, self._lp_zi_q = lfilter(self._lp_taps, 1.0, q_in, zi=self._lp_zi_q)

        # Decimate
        i_dec = i_filt[::self.decimation]
        q_dec = q_filt[::self.decimation]

        # Detection
        if self.mode in ("CW+", "CW-"):
            detected = self._detect_cw(i_dec, q_dec)
        elif self.mode in ("USB", "LSB"):
            detected = i_dec.astype(np.float32)
        elif self.mode in ("SAM", "SAM-U", "SAM-L"):
            detected = self._pll_detect(i_dec, q_dec, self.mode)
        else:
            detected = np.sqrt(i_dec ** 2 + q_dec ** 2)

        # DC removal
        block_mean = np.mean(detected)
        self._dc_avg = _DC_ALPHA * self._dc_avg + (1 - _DC_ALPHA) * block_mean
        audio = detected - self._dc_avg

        # AGC
        with self._lock:
            agc_on = self._agc_enabled
        if agc_on:
            audio = self._apply_agc(audio)

        # Volume and mute
        with self._lock:
            vol = self.volume
            muted = self.muted
        if muted:
            return np.zeros(len(audio), dtype=np.float32)
        audio = audio * vol
        np.clip(audio, -1.0, 1.0, out=audio)
        return audio.astype(np.float32)

    def _detect_cw(self, i_dec, q_dec):
        """CW detection: narrow filter, BFO mix, tone analysis, speed measurement."""
        if self._cw_taps is not None:
            i_dec, self._cw_zi_i = lfilter(self._cw_taps, 1.0, i_dec, zi=self._cw_zi_i)
            q_dec, self._cw_zi_q = lfilter(self._cw_taps, 1.0, q_dec, zi=self._cw_zi_q)

        sign = 1.0 if self.mode == "CW+" else -1.0
        n = len(i_dec)
        phase_inc = 2.0 * np.pi * sign * self._bfo_offset / self.audio_rate
        phases = self._bfo_phase + phase_inc * np.arange(n)
        self._bfo_phase = (phases[-1] + phase_inc) % (2.0 * np.pi)
        complex_dec = i_dec + 1j * q_dec
        detected = np.real(complex_dec * np.exp(1j * phases)).astype(np.float32)

        # Ring buffer for CW FFT (avoids np.roll copy)
        buf = self._cw_buf
        fft_n = _CW_FFT_SIZE
        pos = self._cw_buf_pos
        if n >= fft_n:
            buf[:] = detected[-fft_n:]
            self._cw_buf_pos = 0
        else:
            end = pos + n
            if end <= fft_n:
                buf[pos:end] = detected
            else:
                first = fft_n - pos
                buf[pos:] = detected[:first]
                buf[:n - first] = detected[first:]
            self._cw_buf_pos = end % fft_n

        # Reorder ring buffer for FFT (newest samples at end)
        p = self._cw_buf_pos
        ordered = np.concatenate((buf[p:], buf[:p])) if p > 0 else buf.copy()

        self._cw_analyze_tone(ordered, detected)
        self._cw_measure_speed(detected)
        return detected

    def _cw_analyze_tone(self, ordered_buf, detected):
        """Measure peak audio frequency and SNR for CW tuning indicator."""
        fft_n = _CW_FFT_SIZE
        bin_hz = self.audio_rate / fft_n
        win = np.hanning(fft_n).astype(np.float32)
        spec = np.abs(np.fft.rfft(ordered_buf * win)) ** 2

        lo = max(1, int((self._bfo_offset - self.bandwidth) / bin_hz))
        hi = min(len(spec) - 1, int((self._bfo_offset + self.bandwidth) / bin_hz))
        passband = spec[lo:hi + 1]
        pk = np.argmax(passband)
        pk_abs = lo + pk

        total = np.sum(passband)
        tone = False
        if total > 0 and len(passband) > 2:
            tone = passband[pk] / total > _CW_TONE_CONCENTRATION
        self._cw_tone_present = tone

        if tone and 0 < pk_abs < len(spec) - 1:
            tone_bins = set(range(max(pk - 1, 0), min(pk + 2, len(passband))))
            noise = np.array([passband[i] for i in range(len(passband)) if i not in tone_bins])
            if len(noise) > 0:
                noise_mean = np.mean(noise)
                if noise_mean > 0:
                    snr = 10.0 * np.log10(passband[pk] / noise_mean)
                    self._cw_snr_db = _CW_SNR_SMOOTH * self._cw_snr_db + (1 - _CW_SNR_SMOOTH) * snr
            # Parabolic interpolation for sub-bin accuracy
            a, b, c = spec[pk_abs - 1], spec[pk_abs], spec[pk_abs + 1]
            denom = a - 2.0 * b + c
            delta = 0.5 * (a - c) / denom if abs(denom) > 1e-20 else 0.0
            peak_hz = (pk_abs + delta) * bin_hz
            if self._cw_peak_hz == 0.0:
                self._cw_peak_hz = peak_hz
            else:
                self._cw_peak_hz = _CW_PEAK_HZ_SMOOTH * self._cw_peak_hz + (1 - _CW_PEAK_HZ_SMOOTH) * peak_hz
        else:
            self._cw_snr_db *= _CW_SNR_SMOOTH

    def _apply_agc(self, audio):
        """Apply block-based automatic gain control."""
        rms = np.sqrt(np.mean(audio ** 2))
        if rms < 1e-15:
            return audio * self._agc_gain
        desired_gain = _AGC_TARGET / rms
        if desired_gain < self._agc_gain:
            self._agc_gain += _AGC_ATTACK * (desired_gain - self._agc_gain)
        else:
            self._agc_gain += _AGC_DECAY * (desired_gain - self._agc_gain)
        self._agc_gain = max(0.001, min(self._agc_gain, _AGC_MAX_GAIN))
        return audio * self._agc_gain

    def get_agc_gain_db(self):
        """Return current AGC gain in dB."""
        if self._agc_gain > 0:
            return 20.0 * np.log10(self._agc_gain)
        return -120.0

    @property
    def agc_enabled(self):
        with self._lock:
            return self._agc_enabled

    @agc_enabled.setter
    def agc_enabled(self, value):
        with self._lock:
            self._agc_enabled = value

    @property
    def bfo_offset(self):
        return self._bfo_offset

    def _cw_measure_speed(self, audio):
        """Detect CW keying envelope, estimate speed, and decode Morse."""
        abs_audio = np.abs(audio)
        env = self._cw_env
        env_peak = self._cw_env_peak
        key_down = self._cw_key_down
        edge_sample = self._cw_edge_sample
        base_count = self._cw_sample_count

        for i in range(len(abs_audio)):
            s = abs_audio[i]
            if s > env:
                env += _CW_ENV_ATTACK * (s - env)
            else:
                env += _CW_ENV_DECAY * (s - env)
            if env > env_peak:
                env_peak = env
            else:
                env_peak *= _CW_PEAK_DECAY
            threshold = env_peak * _CW_THRESHOLD_FRAC
            key_now = env > threshold and env_peak > 1e-8
            sample_pos = base_count + i

            if key_down and not key_now:
                dur_ms = (sample_pos - edge_sample) * 1000.0 / self.audio_rate
                if 15 < dur_ms < 2000:
                    self._cw_element_ms.append(dur_ms)
                    if len(self._cw_element_ms) > 60:
                        self._cw_element_ms = self._cw_element_ms[-60:]
                    self._cw_update_wpm()
                    if self._cw_dit_ms > 0:
                        if dur_ms < self._cw_dit_ms * 2.0:
                            self._cw_current_char.append(".")
                        else:
                            self._cw_current_char.append("-")
                self._cw_last_keyup_sample = sample_pos
                edge_sample = sample_pos
            elif not key_down and key_now:
                if self._cw_dit_ms > 0 and self._cw_last_keyup_sample > 0:
                    off_ms = (sample_pos - self._cw_last_keyup_sample) * 1000.0 / self.audio_rate
                    if off_ms > self._cw_dit_ms * 5.0 and self._cw_current_char:
                        self._cw_decode_char()
                        self._cw_append_text(" ")
                    elif off_ms > self._cw_dit_ms * 2.5 and self._cw_current_char:
                        self._cw_decode_char()
                edge_sample = sample_pos
            key_down = key_now

        self._cw_env = env
        self._cw_env_peak = env_peak
        self._cw_key_down = key_down
        self._cw_edge_sample = edge_sample
        self._cw_sample_count = base_count + len(audio)

        # Flush pending character on long silence
        if (not self._cw_key_down and self._cw_dit_ms > 0
                and self._cw_current_char and self._cw_last_keyup_sample > 0):
            silence_ms = (self._cw_sample_count - self._cw_last_keyup_sample) * 1000.0 / self.audio_rate
            if silence_ms > self._cw_dit_ms * 5.0:
                self._cw_decode_char()
                self._cw_append_text(" ")

    def _cw_update_wpm(self):
        """Estimate WPM from collected element durations."""
        if len(self._cw_element_ms) < 4:
            return
        sorted_ms = sorted(self._cw_element_ms)
        boundary = sorted_ms[len(sorted_ms) // 2]
        for _ in range(5):
            dits = [d for d in sorted_ms if d < boundary]
            dahs = [d for d in sorted_ms if d >= boundary]
            if not dits or not dahs:
                break
            dit_med = sorted(dits)[len(dits) // 2]
            dah_med = sorted(dahs)[len(dahs) // 2]
            boundary = (dit_med + dah_med) / 2.0
        dits = [d for d in sorted_ms if d < boundary]
        if not dits:
            dits = sorted_ms[:len(sorted_ms) // 2]
        if dits:
            dit_ms = sorted(dits)[len(dits) // 2]
            if dit_ms > 0:
                new_wpm = 1200.0 / dit_ms
                if self._cw_wpm == 0.0:
                    self._cw_wpm = new_wpm
                else:
                    self._cw_wpm = _CW_WPM_SMOOTH * self._cw_wpm + (1 - _CW_WPM_SMOOTH) * new_wpm
                if self._cw_dit_ms == 0.0:
                    self._cw_dit_ms = dit_ms
                else:
                    self._cw_dit_ms = _CW_DIT_SMOOTH * self._cw_dit_ms + (1 - _CW_DIT_SMOOTH) * dit_ms

    def _cw_decode_char(self):
        """Decode the current element buffer into a character."""
        code = "".join(self._cw_current_char)
        self._cw_current_char.clear()
        if code:
            ch = _MORSE_TABLE.get(code, "\u2423")
            self._cw_append_text(ch)

    def _cw_append_text(self, text):
        """Append text to the decoded buffer, keeping last 120 chars."""
        self._cw_decoded_text += text
        if len(self._cw_decoded_text) > 120:
            self._cw_decoded_text = self._cw_decoded_text[-120:]

    def get_cw_text(self):
        """Return the decoded CW text buffer."""
        with self._lock:
            return self._cw_decoded_text

    def clear_cw_text(self):
        """Clear the decoded CW text buffer (thread-safe)."""
        with self._lock:
            self._cw_decoded_text = ""

    def clear_cw_timing(self):
        """Clear CW speed/element state (thread-safe, called from UI)."""
        with self._lock:
            self._cw_wpm = 0.0
            self._cw_element_ms = []
            self._cw_dit_ms = 0.0

    def get_cw_peak_hz(self):
        """Return the smoothed peak audio frequency in CW mode."""
        return self._cw_peak_hz

    def get_cw_tone_present(self):
        """Return whether a CW tone is detected above the noise floor."""
        return self._cw_tone_present

    def get_cw_snr_db(self):
        """Return CW tone SNR in dB."""
        return self._cw_snr_db

    def get_cw_wpm(self):
        """Return estimated CW speed in words per minute."""
        with self._lock:
            return self._cw_wpm

    def get_pll_offset_hz(self):
        """Return PLL tracking offset in Hz (0.0 for non-SAM modes)."""
        if self.mode not in ("SAM", "SAM-U", "SAM-L"):
            return 0.0
        return self._pll_freq * self.audio_rate / (2.0 * np.pi)

    def _pll_detect(self, i_samples, q_samples, mode="SAM"):
        """PLL-based synchronous AM detection using math (not numpy) for scalars."""
        n = len(i_samples)
        out = np.empty(n, dtype=np.float32)
        phase = self._pll_phase
        freq = self._pll_freq
        alpha = _PLL_ALPHA
        beta = _PLL_BETA

        for k in range(n):
            cos_p = math.cos(phase)
            sin_p = math.sin(phase)
            dot = i_samples[k] * cos_p + q_samples[k] * sin_p
            cross = -i_samples[k] * sin_p + q_samples[k] * cos_p
            if mode == "SAM-U":
                out[k] = dot + cross
            elif mode == "SAM-L":
                out[k] = dot - cross
            else:
                out[k] = dot
            error = math.atan2(cross, dot)
            freq += beta * error
            phase += freq + alpha * error

        self._pll_phase = (phase + math.pi) % (2 * math.pi) - math.pi
        self._pll_freq = max(-0.5, min(0.5, freq))
        return out

    def reset(self):
        """Reset all filter, AGC, and PLL state."""
        self._lp_taps, self._lp_zi_i = _make_filter(127, self.bandwidth, self.iq_sample_rate)
        _, self._lp_zi_q = _make_filter(127, self.bandwidth, self.iq_sample_rate)
        self._dc_avg = 0.0
        self._agc_gain = _AGC_INITIAL_GAIN
        self._pll_phase = 0.0
        self._pll_freq = 0.0
        self._bfo_phase = 0.0
        self._cw_buf[:] = 0.0
        self._cw_buf_pos = 0
        self._cw_peak_hz = 0.0
        self._cw_tone_present = False
        self._cw_snr_db = 0.0
        self._cw_env = 0.0
        self._cw_env_peak = 0.0
        self._cw_key_down = False
        self._cw_edge_sample = 0
        self._cw_sample_count = 0
        with self._lock:
            self._cw_element_ms = []
            self._cw_wpm = 0.0
            self._cw_decoded_text = ""
        self._cw_current_char = []
        self._cw_last_keyup_sample = 0
        self._cw_dit_ms = 0.0
        if self._cw_taps is not None:
            self._cw_taps, self._cw_zi_i = _make_filter(255, self.bandwidth, self.audio_rate)
            _, self._cw_zi_q = _make_filter(255, self.bandwidth, self.audio_rate)
