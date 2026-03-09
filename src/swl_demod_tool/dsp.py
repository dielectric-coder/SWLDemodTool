"""DSP functions for spectrum computation and demodulation."""

import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi

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
    """AM/SSB/SAM/CW demodulator with decimation, DC removal, and AGC.

    Pipeline: IQ (192 kHz) → lowpass → decimate (÷4) → detect → DC remove → AGC → audio (48 kHz)
    Detection: AM = envelope (magnitude), USB/LSB = product detector (real part),
               SAM = PLL synchronous detection, CW = product detector + BFO tone.
    """

    def __init__(self, iq_sample_rate=192000, audio_rate=48000, bandwidth=5000):
        self.iq_sample_rate = iq_sample_rate
        self.audio_rate = audio_rate
        self.decimation = iq_sample_rate // audio_rate  # 4
        self.bandwidth = bandwidth
        self.mode = "AM"  # "AM", "SAM", "SAM-U", "SAM-L", "USB", "LSB", "CW+", "CW-"

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

        # BFO state for CW modes
        self._bfo_offset = 700.0    # BFO tone frequency in Hz
        self._bfo_phase = 0.0       # Phase accumulator (radians)

        # Post-decimation audio-rate filter for CW (narrow BW at 48 kHz is effective)
        self._cw_taps = None
        self._cw_zi_i = None
        self._cw_zi_q = None
        self._cw_peak_hz = 0.0      # Smoothed peak audio frequency (Hz)
        self._cw_tone_present = False  # Whether a tone is detected above noise
        self._cw_snr_db = 0.0         # Tone SNR in dB
        self._cw_fft_size = 8192
        self._cw_buf = np.zeros(self._cw_fft_size, dtype=np.float32)

        # CW speed measurement (envelope-based keying detector)
        self._cw_env = 0.0           # Smoothed envelope level
        self._cw_env_peak = 0.0      # Peak envelope (for adaptive threshold)
        self._cw_key_down = False     # Current key state
        self._cw_edge_sample = 0     # Sample count at last edge
        self._cw_sample_count = 0    # Running sample counter
        self._cw_element_ms = []     # Recent on-duration measurements (ms)
        self._cw_wpm = 0.0           # Estimated speed in WPM

        # CW decoder state
        self._cw_current_char = []   # Current element sequence ('.' and '-')
        self._cw_decoded_text = ""   # Decoded text buffer (last ~120 chars)
        self._cw_last_keyup_sample = 0  # Sample at last key-up edge
        self._cw_dit_ms = 0.0        # Current dit duration estimate (ms)

    def _update_cw_filter(self):
        """Rebuild the post-decimation audio-rate lowpass for CW modes."""
        num_taps = 255
        self._cw_taps = firwin(num_taps, self.bandwidth, fs=self.audio_rate).astype(np.float32)
        self._cw_zi_i = lfilter_zi(self._cw_taps, 1.0).astype(np.float32) * 0
        self._cw_zi_q = lfilter_zi(self._cw_taps, 1.0).astype(np.float32) * 0

    def set_bandwidth(self, bandwidth):
        """Update the demodulation bandwidth (Hz)."""
        if bandwidth == self.bandwidth:
            return
        self.bandwidth = max(100, min(bandwidth, self.iq_sample_rate // 2 - 1))
        if self.mode in ("CW+", "CW-"):
            # CW: wide pre-decimation anti-alias filter, narrow post-decimation audio filter
            cw_prefilter = 2400
            num_taps = 127
            self._lp_taps = firwin(num_taps, cw_prefilter, fs=self.iq_sample_rate).astype(np.float32)
            self._lp_zi_i = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
            self._lp_zi_q = lfilter_zi(self._lp_taps, 1.0).astype(np.float32) * 0
            self._update_cw_filter()
        else:
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
        if self.mode in ("CW+", "CW-"):
            # CW: narrow audio-rate lowpass on I/Q, then BFO mix to audible tone
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
            # Accumulate samples for tone measurement
            self._cw_buf = np.roll(self._cw_buf, -n)
            self._cw_buf[-n:] = detected[:n]
            # Measure peak audio frequency for tuning indicator
            fft_n = self._cw_fft_size
            bin_hz = self.audio_rate / fft_n  # ~5.9 Hz/bin
            win = np.hanning(fft_n).astype(np.float32)
            spec = np.abs(np.fft.rfft(self._cw_buf * win)) ** 2
            # Only look within the passband (BFO ± bandwidth)
            lo = max(1, int((self._bfo_offset - self.bandwidth) / bin_hz))
            hi = min(len(spec) - 1, int((self._bfo_offset + self.bandwidth) / bin_hz))
            passband = spec[lo:hi + 1]
            pk = np.argmax(passband)
            pk_abs = lo + pk  # absolute bin index
            # Spectral concentration within passband only
            total = np.sum(passband)
            tone = False
            if total > 0 and len(passband) > 2:
                tone = passband[pk] / total > 0.25
            self._cw_tone_present = tone
            if tone and pk_abs > 0 and pk_abs < len(spec) - 1:
                # SNR: tone power (peak ± 1 bin) vs noise (rest of passband)
                tone_bins = set(range(max(pk - 1, 0), min(pk + 2, len(passband))))
                noise = np.array([passband[i] for i in range(len(passband)) if i not in tone_bins])
                if len(noise) > 0:
                    noise_mean = np.mean(noise)
                    if noise_mean > 0:
                        snr = 10.0 * np.log10(passband[pk] / noise_mean)
                        self._cw_snr_db = 0.8 * self._cw_snr_db + 0.2 * snr
                # Parabolic interpolation for sub-bin accuracy
                a = spec[pk_abs - 1]
                b = spec[pk_abs]
                c = spec[pk_abs + 1]
                denom = a - 2.0 * b + c
                delta = 0.5 * (a - c) / denom if abs(denom) > 1e-20 else 0.0
                peak_hz = (pk_abs + delta) * bin_hz
                if self._cw_peak_hz == 0.0:
                    self._cw_peak_hz = peak_hz
                else:
                    self._cw_peak_hz = 0.85 * self._cw_peak_hz + 0.15 * peak_hz
            else:
                self._cw_snr_db *= 0.8  # decay toward 0 when no tone
            # Speed measurement: envelope-based keying detector
            self._cw_measure_speed(detected)
        elif self.mode in ("USB", "LSB"):
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

    def _cw_measure_speed(self, audio):
        """Detect CW keying envelope, estimate speed, and decode Morse.

        Uses sample-level envelope detection for precise edge timing,
        median-based dit estimation, exponential WPM smoothing, and
        element-to-character decoding with space detection.
        """
        abs_audio = np.abs(audio)
        # Envelope attack/decay constants (per-sample at 48 kHz)
        attack = 0.04    # ~0.5 ms attack
        decay = 0.002    # ~10 ms decay
        env = self._cw_env
        for i in range(len(abs_audio)):
            s = abs_audio[i]
            if s > env:
                env += attack * (s - env)
            else:
                env += decay * (s - env)
            # Track peak with slow decay
            if env > self._cw_env_peak:
                self._cw_env_peak = env
            else:
                self._cw_env_peak *= 0.99998  # ~1 second decay at 48 kHz
            # Adaptive threshold at 40% of peak
            threshold = self._cw_env_peak * 0.4
            key_now = env > threshold and self._cw_env_peak > 1e-8
            sample_pos = self._cw_sample_count + i
            if self._cw_key_down and not key_now:
                # Key-up edge: measure on-duration, classify as dit or dah
                dur_ms = (sample_pos - self._cw_edge_sample) * 1000.0 / self.audio_rate
                if 15 < dur_ms < 2000:
                    self._cw_element_ms.append(dur_ms)
                    if len(self._cw_element_ms) > 60:
                        self._cw_element_ms = self._cw_element_ms[-60:]
                    self._cw_update_wpm()
                    # Classify element and append to current character
                    if self._cw_dit_ms > 0:
                        if dur_ms < self._cw_dit_ms * 2.0:
                            self._cw_current_char.append(".")
                        else:
                            self._cw_current_char.append("-")
                self._cw_last_keyup_sample = sample_pos
                self._cw_edge_sample = sample_pos
            elif not self._cw_key_down and key_now:
                # Key-down edge: check off-duration for char/word space
                if self._cw_dit_ms > 0 and self._cw_last_keyup_sample > 0:
                    off_ms = (sample_pos - self._cw_last_keyup_sample) * 1000.0 / self.audio_rate
                    if off_ms > self._cw_dit_ms * 5.0 and self._cw_current_char:
                        # Word space: decode char, then add space
                        self._cw_decode_char()
                        self._cw_append_text(" ")
                    elif off_ms > self._cw_dit_ms * 2.5 and self._cw_current_char:
                        # Character space: decode accumulated elements
                        self._cw_decode_char()
                self._cw_edge_sample = sample_pos
            self._cw_key_down = key_now
        self._cw_env = env
        self._cw_sample_count += len(audio)
        # Flush pending character if silence has been long enough
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
        # Iterative split: start with median as boundary, refine
        boundary = sorted_ms[len(sorted_ms) // 2]
        for _ in range(5):
            dits = [d for d in sorted_ms if d < boundary]
            dahs = [d for d in sorted_ms if d >= boundary]
            if not dits or not dahs:
                break
            dit_med = sorted(dits)[len(dits) // 2]
            dah_med = sorted(dahs)[len(dahs) // 2]
            boundary = (dit_med + dah_med) / 2.0
        # Final dit estimate: median of elements below boundary
        dits = [d for d in sorted_ms if d < boundary]
        if not dits:
            dits = sorted_ms[:len(sorted_ms) // 2]
        if dits:
            dit_ms = sorted(dits)[len(dits) // 2]  # median
            if dit_ms > 0:
                new_wpm = 1200.0 / dit_ms
                # Exponential smoothing on WPM
                if self._cw_wpm == 0.0:
                    self._cw_wpm = new_wpm
                else:
                    self._cw_wpm = 0.8 * self._cw_wpm + 0.2 * new_wpm
                # Update dit duration for decoder thresholds
                if self._cw_dit_ms == 0.0:
                    self._cw_dit_ms = dit_ms
                else:
                    self._cw_dit_ms = 0.8 * self._cw_dit_ms + 0.2 * dit_ms

    def _cw_decode_char(self):
        """Decode the current element buffer into a character."""
        code = "".join(self._cw_current_char)
        self._cw_current_char.clear()
        if code:
            ch = _MORSE_TABLE.get(code, "\u2423")  # ␣ for unknown
            self._cw_append_text(ch)

    def _cw_append_text(self, text):
        """Append text to the decoded buffer, keeping last 120 chars."""
        self._cw_decoded_text += text
        if len(self._cw_decoded_text) > 120:
            self._cw_decoded_text = self._cw_decoded_text[-120:]

    def get_cw_text(self):
        """Return the decoded CW text buffer."""
        return self._cw_decoded_text

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
        return self._cw_wpm

    def get_pll_offset_hz(self):
        """Return PLL tracking offset in Hz (0.0 for non-SAM modes)."""
        if self.mode not in ("SAM", "SAM-U", "SAM-L"):
            return 0.0
        return self._pll_freq * self.audio_rate / (2.0 * np.pi)

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
        self._bfo_phase = 0.0
        self._cw_buf[:] = 0.0
        self._cw_peak_hz = 0.0
        self._cw_tone_present = False
        self._cw_snr_db = 0.0
        self._cw_env = 0.0
        self._cw_env_peak = 0.0
        self._cw_key_down = False
        self._cw_edge_sample = 0
        self._cw_sample_count = 0
        self._cw_element_ms = []
        self._cw_wpm = 0.0
        self._cw_current_char = []
        self._cw_decoded_text = ""
        self._cw_last_keyup_sample = 0
        self._cw_dit_ms = 0.0
        if self._cw_taps is not None:
            self._cw_zi_i = lfilter_zi(self._cw_taps, 1.0).astype(np.float32) * 0
            self._cw_zi_q = lfilter_zi(self._cw_taps, 1.0).astype(np.float32) * 0
