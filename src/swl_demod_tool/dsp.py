"""DSP functions for spectrum computation and demodulation."""

import math
import threading
import numpy as np
from scipy.signal import firwin, lfilter

# ---------------------------------------------------------------------------
# Optional accelerators: pyfftw (faster FFT) and numba (JIT for hot loops)
# Both fall back gracefully if not installed.
# ---------------------------------------------------------------------------
try:
    import pyfftw
    pyfftw.interfaces.cache.enable()
    pyfftw.interfaces.cache.set_keepalive_time(60)
    _fft = pyfftw.interfaces.numpy_fft.fft
    _ifft = pyfftw.interfaces.numpy_fft.ifft
    _rfft = pyfftw.interfaces.numpy_fft.rfft
    _irfft = pyfftw.interfaces.numpy_fft.irfft
    _fftshift = np.fft.fftshift  # fftshift is just index reordering, no speedup
except ImportError:
    _fft = np.fft.fft
    _ifft = np.fft.ifft
    _rfft = np.fft.rfft
    _irfft = np.fft.irfft
    _fftshift = np.fft.fftshift

try:
    import numba
    _HAS_NUMBA = True
except ImportError:
    _HAS_NUMBA = False

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

# ITA2 (Baudot) code tables: 5-bit code -> character
# LTRS shift (letters mode)
_BAUDOT_LTRS = {
    0b00000: '\0',  # Null
    0b00100: ' ',   # Space
    0b01000: '\n',  # Line feed
    0b00010: '\r',  # Carriage return
    0b11000: 'A', 0b10011: 'B', 0b01110: 'C', 0b10010: 'D',
    0b10000: 'E', 0b10110: 'F', 0b01011: 'G', 0b00101: 'H',
    0b01100: 'I', 0b11010: 'J', 0b11110: 'K', 0b01001: 'L',
    0b00111: 'M', 0b00110: 'N', 0b00011: 'O', 0b01101: 'P',
    0b11101: 'Q', 0b01010: 'R', 0b10100: 'S', 0b00001: 'T',
    0b11100: 'U', 0b01111: 'V', 0b11001: 'W', 0b10111: 'X',
    0b10101: 'Y', 0b10001: 'Z',
}
# FIGS shift (figures mode)
_BAUDOT_FIGS = {
    0b00000: '\0',  # Null
    0b00100: ' ',   # Space
    0b01000: '\n',  # Line feed
    0b00010: '\r',  # Carriage return
    0b11000: '-', 0b10011: '?', 0b01110: ':', 0b10010: '$',
    0b10000: '3', 0b10110: '!', 0b01011: '&', 0b00101: '#',
    0b01100: '8', 0b11010: '\'', 0b11110: '(', 0b01001: ')',
    0b00111: '.', 0b00110: ',', 0b00011: '9', 0b01101: '0',
    0b11101: '1', 0b01010: '4', 0b10100: '\x07',  # Bell
    0b00001: '5', 0b11100: '7', 0b01111: ';', 0b11001: '2',
    0b10111: '/', 0b10101: '6', 0b10001: '"',
}
_BAUDOT_LTRS_SHIFT = 0b11111  # Switch to letters
_BAUDOT_FIGS_SHIFT = 0b11011  # Switch to figures

# PSK31 Varicode table: character -> bit pattern (MSB first, no trailing 00)
# Each character is a variable-length code; characters separated by two+ zeros
_VARICODE_ENC = {
    '\x00': '1010101011', '\x01': '1011011011', '\x02': '1011101101',
    '\x03': '1101110111', '\x04': '1011101011', '\x05': '1101011111',
    '\x06': '1011101111', '\x07': '1011111101', '\x08': '1011111111', # BS
    '\x09': '11101111',   '\x0a': '11101',      # LF
    '\x0b': '1101101111', '\x0c': '1011011101',
    '\x0d': '11111',       # CR
    '\x0e': '1101110101', '\x0f': '1110101011',
    '\x10': '1011110111', '\x11': '1011110101', '\x12': '1110101101',
    '\x13': '1110101111', '\x14': '1101011011', '\x15': '1101101011',
    '\x16': '1101101101', '\x17': '1101010111', '\x18': '1101111011',
    '\x19': '1101111101', '\x1a': '1110110111', '\x1b': '1101010101',
    '\x1c': '1101011101', '\x1d': '1110111011', '\x1e': '1011111011',
    '\x1f': '1101111111',
    ' ': '1',          '!': '111111111',  '"': '101011111',
    '#': '111110101',  '$': '111011011',  '%': '1011010101',
    '&': '1010111011', "'": '101111111',  '(': '11111011',
    ')': '11110111',   '*': '101101111',  '+': '111011111',
    ',': '1110101',    '-': '110101',     '.': '1010111',
    '/': '110101111',  '0': '10110111',   '1': '10111101',
    '2': '11101101',   '3': '11111111',   '4': '101110111',
    '5': '101011011',  '6': '101101011',  '7': '110101101',
    '8': '110101011',  '9': '110110111',  ':': '11110101',
    ';': '110111101',  '<': '111101101',  '=': '1010101',
    '>': '111010111',  '?': '1010101111', '@': '1010111101',
    'A': '1111101',    'B': '11101011',   'C': '10101101',
    'D': '10110101',   'E': '1110111',    'F': '11011011',
    'G': '11111101',   'H': '101010101',  'I': '1111111',
    'J': '111111101',  'K': '101111101',  'L': '11010111',
    'M': '10111011',   'N': '11011101',   'O': '10101011',
    'P': '11010101',   'Q': '111011101',  'R': '10101111',
    'S': '1101111',    'T': '1101101',    'U': '101010111',
    'V': '110110101',  'W': '101011101',  'X': '101110101',
    'Y': '101111011',  'Z': '1010101101',
    '[': '111110111',  '\\': '111101111', ']': '111111011',
    '^': '1010111111', '_': '101101101',  '`': '1011011111',
    'a': '1011',       'b': '1011111',    'c': '101111',
    'd': '101101',     'e': '11',         'f': '111101',
    'g': '1011011',    'h': '101011',     'i': '1101',
    'j': '111101011',  'k': '10111111',   'l': '11011',
    'm': '111011',     'n': '1111',       'o': '111',
    'p': '111111',     'q': '110111111',  'r': '10101',
    's': '10111',      't': '101',        'u': '110111',
    'v': '1111011',    'w': '1101011',    'x': '11011111',
    'y': '1011101',    'z': '111010101',
    '{': '1010110111', '|': '110111011',  '}': '1010110101',
    '~': '1011010111', '\x7f': '1110110101',
}
# Build reverse lookup: bit string -> character
_VARICODE_DEC = {v: k for k, v in _VARICODE_ENC.items()}

# PSK31 constants
_PSK31_CARRIER_HZ = 1000.0    # Nominal audio carrier frequency
_PSK31_BAUD = 31.25            # Symbol rate
_PSK31_FILTER_BW = 100.0       # Bandpass filter half-bandwidth (Hz)
_PSK31_PLL_ALPHA = 0.03        # Carrier PLL proportional gain
_PSK31_PLL_BETA = 0.002        # Carrier PLL integrator gain

# RTTY constants
_RTTY_MARK_HZ = 2125.0        # Mark tone frequency (Hz)
_RTTY_SHIFT_HZ = 170.0        # Frequency shift (Hz), space = mark + shift
_RTTY_BAUD = 45.45             # Standard amateur RTTY baud rate
_RTTY_FILTER_BW = 80.0         # Bandpass filter bandwidth per tone (Hz)
_RTTY_BIT_SMOOTH = 0.3         # EMA smoothing for mark/space discriminator

# Cached Blackman window for spectrum computation
_blackman_cache = {}


# ---------------------------------------------------------------------------
# Numba-accelerated inner loops (fall back to plain Python if unavailable)
# ---------------------------------------------------------------------------

def _nb_loop_py(mag, iq_samples, out, threshold, avg, holdoff, delay_buf,
                lookahead, ema_alpha, holdoff_ext):
    """Pure-Python noise blanker inner loop."""
    n = len(mag)
    for i in range(n):
        if mag[i] < threshold * avg or avg < 1e-15:
            avg += ema_alpha * (mag[i] - avg)
        if avg > 1e-15 and mag[i] > threshold * avg:
            holdoff = holdoff_ext + lookahead
        elif holdoff > 0:
            holdoff -= 1
        oldest = delay_buf[0]
        if holdoff > 0:
            out[i] = 0.0
        else:
            out[i] = oldest
        delay_buf[:-1] = delay_buf[1:]
        delay_buf[-1] = iq_samples[i]
    return avg, holdoff


def _pll_loop_py(i_samples, q_samples, out, phase, freq, alpha, beta, mode_code):
    """Pure-Python PLL inner loop. mode_code: 0=SAM, 1=SAM-U, 2=SAM-L."""
    n = len(i_samples)
    for k in range(n):
        mag2 = i_samples[k] * i_samples[k] + q_samples[k] * q_samples[k]
        if mag2 < 1e-20:
            # Near-zero input (e.g. blanked by NB) — coast: hold phase/freq, output zero
            out[k] = 0.0
            phase += freq
            continue
        cos_p = math.cos(phase)
        sin_p = math.sin(phase)
        dot = i_samples[k] * cos_p + q_samples[k] * sin_p
        cross = -i_samples[k] * sin_p + q_samples[k] * cos_p
        if mode_code == 1:
            out[k] = dot + cross
        elif mode_code == 2:
            out[k] = dot - cross
        else:
            out[k] = dot
        error = math.atan2(cross, dot)
        freq += beta * error
        phase += freq + alpha * error
    return phase, freq


def _cw_env_loop_py(abs_audio, env, env_peak, key_down, edge_sample,
                     base_count, min_edge_samples, attack, decay, peak_decay,
                     thresh_up, thresh_dn, min_peak, audio_rate,
                     last_keyup_sample):
    """Pure-Python CW envelope tracker. Returns state + list of (event, dur_ms, gap_ms, sample_pos)."""
    events = []
    for i in range(len(abs_audio)):
        s = abs_audio[i]
        if s > env:
            env += attack * (s - env)
        else:
            env += decay * (s - env)
        if env > env_peak:
            env_peak = env
        else:
            env_peak *= peak_decay

        sample_pos = base_count + i
        if key_down:
            key_now = env > env_peak * thresh_dn and env_peak > min_peak
        else:
            key_now = env > env_peak * thresh_up and env_peak > min_peak

        if key_down != key_now:
            edge_dur = sample_pos - edge_sample
            if edge_dur < min_edge_samples:
                continue

        if key_down and not key_now:
            dur_ms = (sample_pos - edge_sample) * 1000.0 / audio_rate
            gap_ms = 0.0
            if last_keyup_sample > 0:
                gap_ms = (edge_sample - last_keyup_sample) * 1000.0 / audio_rate
            if 15 < dur_ms < 2000:
                events.append((dur_ms, gap_ms))
            last_keyup_sample = sample_pos
            edge_sample = sample_pos
        elif not key_down and key_now:
            edge_sample = sample_pos
        key_down = key_now

    return env, env_peak, key_down, edge_sample, last_keyup_sample, events


if _HAS_NUMBA:
    @numba.njit(cache=True)
    def _nb_loop_jit(mag, iq_real, iq_imag, out_real, out_imag,
                     threshold, avg, holdoff, delay_real, delay_imag,
                     lookahead, ema_alpha, holdoff_ext):
        """Numba-accelerated noise blanker inner loop."""
        n = len(mag)
        for i in range(n):
            if mag[i] < threshold * avg or avg < 1e-15:
                avg += ema_alpha * (mag[i] - avg)
            if avg > 1e-15 and mag[i] > threshold * avg:
                holdoff = holdoff_ext + lookahead
            elif holdoff > 0:
                holdoff -= 1
            if holdoff > 0:
                out_real[i] = 0.0
                out_imag[i] = 0.0
            else:
                out_real[i] = delay_real[0]
                out_imag[i] = delay_imag[0]
            # Shift delay buffer
            for j in range(len(delay_real) - 1):
                delay_real[j] = delay_real[j + 1]
                delay_imag[j] = delay_imag[j + 1]
            delay_real[-1] = iq_real[i]
            delay_imag[-1] = iq_imag[i]
        return avg, holdoff

    @numba.njit(cache=True)
    def _pll_loop_jit(i_samples, q_samples, out, phase, freq, alpha, beta,
                      mode_code):
        """Numba-accelerated PLL inner loop."""
        n = len(i_samples)
        for k in range(n):
            mag2 = i_samples[k] * i_samples[k] + q_samples[k] * q_samples[k]
            if mag2 < 1e-20:
                out[k] = 0.0
                phase += freq
                continue
            cos_p = math.cos(phase)
            sin_p = math.sin(phase)
            dot = i_samples[k] * cos_p + q_samples[k] * sin_p
            cross = -i_samples[k] * sin_p + q_samples[k] * cos_p
            if mode_code == 1:
                out[k] = dot + cross
            elif mode_code == 2:
                out[k] = dot - cross
            else:
                out[k] = dot
            error = math.atan2(cross, dot)
            freq += beta * error
            phase += freq + alpha * error
        return phase, freq

    @numba.njit(cache=True)
    def _cw_env_loop_jit(abs_audio, env, env_peak, key_down, edge_sample,
                          base_count, min_edge_samples, attack, decay,
                          peak_decay, thresh_up, thresh_dn, min_peak,
                          audio_rate, last_keyup_sample):
        """Numba-accelerated CW envelope tracker.

        Returns (env, env_peak, key_down, edge_sample, last_keyup_sample,
                 event_dur_ms, event_gap_ms, event_count).
        Events are stored in pre-allocated arrays; event_count says how many are valid.
        """
        max_events = len(abs_audio)
        event_dur = np.empty(max_events, dtype=np.float64)
        event_gap = np.empty(max_events, dtype=np.float64)
        ec = 0

        for i in range(len(abs_audio)):
            s = abs_audio[i]
            if s > env:
                env += attack * (s - env)
            else:
                env += decay * (s - env)
            if env > env_peak:
                env_peak = env
            else:
                env_peak *= peak_decay

            sample_pos = base_count + i
            if key_down:
                key_now = env > env_peak * thresh_dn and env_peak > min_peak
            else:
                key_now = env > env_peak * thresh_up and env_peak > min_peak

            if key_down != key_now:
                edge_dur_samp = sample_pos - edge_sample
                if edge_dur_samp < min_edge_samples:
                    continue

            if key_down and not key_now:
                dur_ms = (sample_pos - edge_sample) * 1000.0 / audio_rate
                gap_ms = 0.0
                if last_keyup_sample > 0:
                    gap_ms = (edge_sample - last_keyup_sample) * 1000.0 / audio_rate
                if 15 < dur_ms < 2000:
                    event_dur[ec] = dur_ms
                    event_gap[ec] = gap_ms
                    ec += 1
                last_keyup_sample = sample_pos
                edge_sample = sample_pos
            elif not key_down and key_now:
                edge_sample = sample_pos
            key_down = key_now

        return (env, env_peak, key_down, edge_sample, last_keyup_sample,
                event_dur, event_gap, ec)


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
    spectrum = _fftshift(_fft(windowed))
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

# SNR estimator constants
_SNR_SMOOTH = 0.85          # Smoothing for signal power estimate
_SNR_NOISE_SMOOTH = 0.95    # Smoothing for noise floor (slow-tracking)
_SNR_NOISE_UP = 0.005       # Noise floor rises very slowly
_SNR_NOISE_DOWN = 0.1       # Noise floor drops moderately fast
_SNR_FFT_SIZE = 1024        # FFT size for in-band SNR measurement
_CW_BFO_HZ = 700.0
_CW_FFT_SIZE = 8192
_CW_PREFILTER_HZ = 2400
_CW_ENV_ATTACK = 0.06    # ~0.35 ms at 48 kHz
_CW_ENV_DECAY = 0.003    # ~7 ms at 48 kHz
_CW_PEAK_DECAY = 0.99998 # ~1 s at 48 kHz
_CW_THRESHOLD_UP = 0.4   # Hysteresis: key-down when envelope rises above this fraction of peak
_CW_THRESHOLD_DN = 0.2   # Hysteresis: key-up when envelope drops below this fraction of peak
_CW_MIN_EDGE_MS = 8.0    # Ignore transitions shorter than this (anti-chatter debounce)
_CW_MIN_PEAK = 1e-6      # Minimum envelope peak before trusting key detection
_CW_TONE_CONCENTRATION = 0.25
_CW_SNR_SMOOTH = 0.8
_CW_PEAK_HZ_SMOOTH = 0.85
_CW_WPM_SMOOTH = 0.8
_CW_DIT_SMOOTH = 0.8

# Audio Peak Filter (APF) — narrow bandpass IIR centered on BFO tone
_APF_Q = 15.0               # Quality factor (higher = narrower, ~50 Hz BW at Q=15)

# Noise blanker constants
_NB_EMA_ALPHA = 0.001       # EMA smoothing for magnitude average (~slow)
_NB_LOOKAHEAD = 8           # Samples of lookahead for blanking window
_NB_HOLDOFF = 4             # Extend blanking window by this many samples after impulse
_NB_THRESHOLD_PRESETS = {"Low": 10.0, "Med": 20.0, "High": 40.0}

# Spectral DNR constants — spectral gate with percentile noise estimation
_DNR_FFT_SIZE = 512
_DNR_HOP = 256              # 50% overlap
_DNR_NOISE_PERCENTILE = 30  # Noise floor from 30th percentile of passband bins
_DNR_NOISE_SMOOTH = 0.90    # Smooth noise estimate across frames
_DNR_GAIN_SMOOTH = 0.5      # Temporal gain smoothing per bin
_DNR_RAMP_FRAMES = 5        # Frames to ramp gain from 1.0 to computed value
# Level presets: (gate_threshold, gain_floor)
# gate_threshold: bins above this × noise_floor pass through
# gain_floor: attenuation for noise-only bins
_DNR_LEVEL_PRESETS = {
    1: (2.0, 0.15),   # Gentle: only attenuate clearly-noise bins
    2: (3.0, 0.08),   # Moderate
    3: (5.0, 0.03),   # Aggressive: strong gating, deep suppression
}

# Auto notch constants — detect and null persistent narrow tonal interference
_AN_FFT_SIZE = 1024
_AN_HOP = 512               # 50% overlap
_AN_PEAK_THRESH = 10.0      # Bin must exceed median of neighbors by this factor
_AN_NEIGHBOR_BINS = 8       # Half-width of neighborhood for local median
_AN_NOTCH_HALFWIDTH = 2     # Null this many bins on each side of detected peak
_AN_PERSIST_SMOOTH = 0.85   # Smoothing for persistent tone tracker (higher = slower adapt)
_AN_GAIN_SMOOTH = 0.6       # Temporal gain smoothing per bin
_AN_RAMP_FRAMES = 5         # Frames to ramp gain from 1.0 to computed value


class Demodulator:
    """AM/SSB/SAM/CW/RTTY demodulator with decimation, DC removal, AGC, and noise reduction.

    Pipeline: IQ (192 kHz) -> [NB] -> lowpass -> decimate (÷4) -> detect -> [DNR] -> [Auto Notch] -> DC remove -> AGC -> audio (48 kHz)
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

        # Noise blanker state
        self._nb_enabled = False
        self._nb_threshold = 20.0
        self._nb_threshold_name = "Med"
        self._nb_avg_mag = 0.0
        self._nb_delay_buf = np.zeros(_NB_LOOKAHEAD, dtype=np.complex64)
        self._nb_holdoff_count = 0

        # Spectral DNR state (spectral gate)
        self._dnr_level = 0  # 0=off, 1/2/3
        self._dnr_in_buf = np.zeros(0, dtype=np.float32)
        self._dnr_prev_frame = np.zeros(_DNR_HOP, dtype=np.float32)
        n_bins = _DNR_FFT_SIZE // 2 + 1
        self._dnr_noise_floor = 0.0      # Scalar noise floor estimate
        self._dnr_prev_gain = np.ones(n_bins, dtype=np.float32)
        self._dnr_frame_count = 0
        self._dnr_window = np.hanning(_DNR_FFT_SIZE).astype(np.float32)
        # Synthesis window for proper overlap-add reconstruction
        self._dnr_synth_window = self._dnr_window.copy()
        ola_sum = np.zeros(_DNR_FFT_SIZE, dtype=np.float32)
        ola_sum[:_DNR_HOP] += self._dnr_window[:_DNR_HOP] ** 2
        ola_sum[_DNR_HOP:] += self._dnr_window[_DNR_HOP:] ** 2
        ola_sum[:_DNR_HOP] += self._dnr_window[_DNR_HOP:] ** 2
        ola_sum[_DNR_HOP:] += self._dnr_window[:_DNR_HOP] ** 2
        self._dnr_synth_window /= np.maximum(ola_sum, 1e-10)

        # Auto notch state (detect and null persistent tones)
        self._an_enabled = False
        self._an_in_buf = np.zeros(0, dtype=np.float32)
        self._an_prev_frame = np.zeros(_AN_HOP, dtype=np.float32)
        an_bins = _AN_FFT_SIZE // 2 + 1
        self._an_persist = np.zeros(an_bins, dtype=np.float32)  # Persistent tone tracker
        self._an_prev_gain = np.ones(an_bins, dtype=np.float32)
        self._an_frame_count = 0
        self._an_window = np.hanning(_AN_FFT_SIZE).astype(np.float32)
        self._an_synth_window = self._an_window.copy()
        an_ola_sum = np.zeros(_AN_FFT_SIZE, dtype=np.float32)
        an_ola_sum[:_AN_HOP] += self._an_window[:_AN_HOP] ** 2
        an_ola_sum[_AN_HOP:] += self._an_window[_AN_HOP:] ** 2
        an_ola_sum[:_AN_HOP] += self._an_window[_AN_HOP:] ** 2
        an_ola_sum[_AN_HOP:] += self._an_window[:_AN_HOP] ** 2
        self._an_synth_window /= np.maximum(an_ola_sum, 1e-10)

        # SNR estimator state
        self._snr_db = 0.0
        self._snr_signal_power = 0.0
        self._snr_noise_floor = 0.0
        self._snr_buf = np.zeros(0, dtype=np.complex64)

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
        # Pending edges buffer: stores (element_dur_ms, gap_before_ms) tuples
        # collected before _cw_dit_ms is established, replayed once WPM locks
        self._cw_pending_edges = []

        # Audio Peak Filter (APF) — narrow IIR bandpass on BFO tone
        self._apf_enabled = False
        self._apf_b, self._apf_a = self._make_apf_coeffs()
        self._apf_zi = np.zeros(2, dtype=np.float64)

        # RTTY demodulator state
        self._rtty_mark_phase = 0.0
        self._rtty_space_phase = 0.0
        self._rtty_mark_hz = _RTTY_MARK_HZ
        self._rtty_space_hz = _RTTY_MARK_HZ + _RTTY_SHIFT_HZ
        self._rtty_baud = _RTTY_BAUD
        self._rtty_samples_per_bit = self.audio_rate / self._rtty_baud
        self._rtty_bit_acc = 0.0       # Smoothed mark-space discriminator
        self._rtty_bit_phase = 0.0     # Phase within current bit (0..1)
        self._rtty_shift_reg = 0       # 5-bit shift register
        self._rtty_bit_count = 0       # Bits received in current character
        self._rtty_state = "IDLE"      # IDLE, DATA, STOP
        self._rtty_figs_mode = False   # True = figures shift active
        self._rtty_decoded_text = ""
        # Mark/space bandpass filters (built on first use or mode switch)
        self._rtty_mark_bp = None
        self._rtty_space_bp = None
        self._rtty_mark_zi = None
        self._rtty_space_zi = None

        # PSK31 demodulator state
        self._psk_carrier_hz = _PSK31_CARRIER_HZ
        self._psk_lo_phase = 0.0       # Local oscillator phase
        self._psk_lo_freq = 2.0 * np.pi * _PSK31_CARRIER_HZ / self.audio_rate
        self._psk_samples_per_sym = self.audio_rate / _PSK31_BAUD
        self._psk_sym_phase = 0.0      # Phase within current symbol (0..samples_per_sym)
        self._psk_prev_symbol = 1.0 + 0j  # Previous symbol's complex value
        self._psk_bit_buf = ""         # Accumulated Varicode bits
        self._psk_decoded_text = ""
        # Matched filter: raised-cosine pulse for one symbol period
        sym_len = int(self._psk_samples_per_sym)
        self._psk_matched = (1.0 - np.cos(2.0 * np.pi * np.arange(sym_len) / sym_len)).astype(np.float32) / sym_len
        # IIR lowpass for I/Q after downconversion
        self._psk_lp_taps = firwin(127, _PSK31_FILTER_BW, fs=self.audio_rate).astype(np.float32)
        self._psk_lp_zi_i = np.zeros(126, dtype=np.float32)
        self._psk_lp_zi_q = np.zeros(126, dtype=np.float32)
        # Symbol accumulator
        self._psk_i_acc = 0.0
        self._psk_q_acc = 0.0
        self._psk_acc_count = 0

    def _update_cw_filter(self):
        """Rebuild the post-decimation audio-rate lowpass for CW modes."""
        self._cw_taps, self._cw_zi_i = _make_filter(255, self.bandwidth, self.audio_rate)
        _, self._cw_zi_q = _make_filter(255, self.bandwidth, self.audio_rate)

    def _make_apf_coeffs(self):
        """Compute biquad bandpass coefficients centered on BFO frequency."""
        w0 = 2.0 * np.pi * self._bfo_offset / self.audio_rate
        alpha = np.sin(w0) / (2.0 * _APF_Q)
        b0 = alpha
        b1 = 0.0
        b2 = -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * np.cos(w0)
        a2 = 1.0 - alpha
        b = np.array([b0 / a0, b1 / a0, b2 / a0])
        a = np.array([1.0, a1 / a0, a2 / a0])
        return b, a

    def _apply_apf(self, audio):
        """Apply the audio peak filter (narrow bandpass at BFO frequency)."""
        filtered, self._apf_zi = lfilter(self._apf_b, self._apf_a,
                                         audio, zi=self._apf_zi)
        return filtered.astype(np.float32)

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

    def _measure_snr(self, i_dec, q_dec):
        """Estimate in-band SNR from decimated IQ using spectral analysis.

        Analyzes only the passband bins. Uses the median bin power as
        noise floor estimate (robust to narrowband signals like carriers
        and tones) and total passband power as signal+noise.
        SNR = (signal+noise) / noise - 1, clamped to [0, 60] dB.
        """
        # Accumulate decimated IQ into buffer
        iq = (i_dec + 1j * q_dec).astype(np.complex64)
        self._snr_buf = np.concatenate((self._snr_buf, iq))
        fft_size = _SNR_FFT_SIZE

        if len(self._snr_buf) < fft_size:
            return

        # Use the most recent fft_size samples
        frame = self._snr_buf[-fft_size:]
        self._snr_buf = self._snr_buf[-fft_size // 2:]  # keep overlap

        # Power spectrum
        window = np.hanning(fft_size).astype(np.float32)
        spec = _fft(frame * window)
        spec_power = np.abs(spec) ** 2

        # Select only passband bins (±bandwidth around DC)
        bin_hz = self.audio_rate / fft_size
        bw_bins = max(2, int(self.bandwidth / bin_hz))
        # DC-centered: bins 0..bw_bins and (fft_size-bw_bins)..fft_size
        passband = np.concatenate((spec_power[:bw_bins], spec_power[-bw_bins:]))

        if len(passband) < 4:
            return

        # Total passband power
        total_power = np.mean(passband)

        # Noise floor: median of passband bins
        # Median is robust to carrier and tonal components
        noise_floor = np.median(passband)

        # Smooth estimates
        if self._snr_signal_power == 0.0:
            self._snr_signal_power = total_power
            self._snr_noise_floor = noise_floor
        else:
            self._snr_signal_power = (
                _SNR_SMOOTH * self._snr_signal_power
                + (1 - _SNR_SMOOTH) * total_power
            )
            # Asymmetric noise tracking
            if noise_floor > self._snr_noise_floor:
                rate = _SNR_NOISE_UP
            else:
                rate = _SNR_NOISE_DOWN
            self._snr_noise_floor += rate * (noise_floor - self._snr_noise_floor)

        if self._snr_noise_floor > 1e-20:
            # SNR = (S+N)/N - 1 = S/N, in dB
            ratio = self._snr_signal_power / self._snr_noise_floor
            if ratio > 1.0:
                self._snr_db = max(0.0, min(60.0, 10.0 * np.log10(ratio - 1.0)))
            else:
                self._snr_db = 0.0

    def _noise_blank(self, iq_samples):
        """Apply impulse noise blanking on raw IQ samples.

        Detects impulses that exceed threshold * running average magnitude,
        and replaces them with zeros. Uses a small lookahead delay buffer.
        """
        mag = np.abs(iq_samples).astype(np.float64)
        n = len(mag)
        threshold = self._nb_threshold
        avg = float(self._nb_avg_mag)
        holdoff = int(self._nb_holdoff_count)
        delay_buf = self._nb_delay_buf
        lookahead = _NB_LOOKAHEAD

        if _HAS_NUMBA:
            delay_real = delay_buf.real.copy().astype(np.float64)
            delay_imag = delay_buf.imag.copy().astype(np.float64)
            out_real = np.empty(n, dtype=np.float64)
            out_imag = np.empty(n, dtype=np.float64)
            avg, holdoff = _nb_loop_jit(
                mag, iq_samples.real.astype(np.float64),
                iq_samples.imag.astype(np.float64),
                out_real, out_imag, threshold, avg, holdoff,
                delay_real, delay_imag, lookahead,
                _NB_EMA_ALPHA, _NB_HOLDOFF)
            out = (out_real + 1j * out_imag).astype(np.complex64)
            delay_buf[:] = (delay_real + 1j * delay_imag).astype(np.complex64)
        else:
            out = np.empty(n, dtype=np.complex64)
            avg, holdoff = _nb_loop_py(
                mag, iq_samples, out, threshold, avg, holdoff,
                delay_buf, lookahead, _NB_EMA_ALPHA, _NB_HOLDOFF)

        self._nb_avg_mag = avg
        self._nb_holdoff_count = int(holdoff)
        return out

    def _apply_dnr(self, audio):
        """Spectral gate noise reduction.

        Uses percentile-based noise floor estimation from passband bins.
        Bins above the noise floor pass through; bins at or below are
        attenuated. Temporal gain smoothing prevents flutter.
        """
        level = self._dnr_level
        if level == 0:
            return audio

        gate_thresh, gain_floor = _DNR_LEVEL_PRESETS[level]
        fft_size = _DNR_FFT_SIZE
        hop = _DNR_HOP

        # Accumulate input
        self._dnr_in_buf = np.concatenate((self._dnr_in_buf, audio))
        output_pieces = []

        while len(self._dnr_in_buf) >= fft_size:
            frame = self._dnr_in_buf[:fft_size]
            self._dnr_in_buf = self._dnr_in_buf[hop:]

            # Analysis
            spectrum = _rfft(frame * self._dnr_window)
            power = np.abs(spectrum) ** 2

            self._dnr_frame_count += 1

            # --- Noise floor: percentile of passband bins ---
            bw_bins = max(4, int(self.bandwidth / (self.audio_rate / fft_size)))
            passband_power = power[1:bw_bins + 1]  # skip DC bin
            frame_noise = np.percentile(passband_power, _DNR_NOISE_PERCENTILE)

            if self._dnr_noise_floor == 0.0:
                self._dnr_noise_floor = frame_noise
            else:
                self._dnr_noise_floor = (
                    _DNR_NOISE_SMOOTH * self._dnr_noise_floor
                    + (1 - _DNR_NOISE_SMOOTH) * frame_noise
                )

            noise_floor = max(self._dnr_noise_floor, 1e-20)

            # --- Spectral gate: smooth transition from floor to 1.0 ---
            # snr_bin = power / noise_floor
            # gain = floor when snr_bin <= 1
            # gain = 1.0  when snr_bin >= gate_thresh
            # smooth interpolation between
            snr_bin = power / noise_floor
            gain = np.where(
                snr_bin >= gate_thresh,
                1.0,
                np.where(
                    snr_bin <= 1.0,
                    gain_floor,
                    gain_floor + (1.0 - gain_floor) * (snr_bin - 1.0) / (gate_thresh - 1.0)
                )
            )
            # Always pass DC bin (carrier in AM)
            gain[0] = 1.0

            # Temporal smoothing to prevent flutter
            gain = _DNR_GAIN_SMOOTH * self._dnr_prev_gain + (1 - _DNR_GAIN_SMOOTH) * gain
            self._dnr_prev_gain = gain.copy()

            # Ramp gain from 1.0 during first few frames
            if self._dnr_frame_count <= _DNR_RAMP_FRAMES:
                ramp = self._dnr_frame_count / _DNR_RAMP_FRAMES
                gain = 1.0 - ramp * (1.0 - gain)

            # Apply gain and synthesize
            filtered = spectrum * gain
            out_frame = (
                _irfft(filtered, n=fft_size) * self._dnr_synth_window
            ).astype(np.float32)

            # Overlap-add
            out_frame[:hop] += self._dnr_prev_frame
            self._dnr_prev_frame = out_frame[hop:].copy()
            output_pieces.append(out_frame[:hop])

        if output_pieces:
            return np.concatenate(output_pieces)
        return np.array([], dtype=np.float32)

    def _apply_auto_notch(self, audio):
        """Auto notch filter — detect and null persistent narrow tonal interference.

        Uses STFT to find bins whose power significantly exceeds their local
        neighborhood median.  Persistent peaks (tracked across frames) are
        nulled with a narrow notch.
        """
        fft_size = _AN_FFT_SIZE
        hop = _AN_HOP

        self._an_in_buf = np.concatenate((self._an_in_buf, audio))
        output_pieces = []

        while len(self._an_in_buf) >= fft_size:
            frame = self._an_in_buf[:fft_size]
            self._an_in_buf = self._an_in_buf[hop:]

            spectrum = _rfft(frame * self._an_window)
            power = np.abs(spectrum) ** 2
            n_bins = len(power)

            self._an_frame_count += 1

            # Detect peaks: compare each bin to local median of neighbors
            gain = np.ones(n_bins, dtype=np.float32)
            for b in range(1, n_bins - 1):  # skip DC and Nyquist
                lo = max(1, b - _AN_NEIGHBOR_BINS)
                hi = min(n_bins - 1, b + _AN_NEIGHBOR_BINS + 1)
                # Exclude the center notch region from the median calculation
                notch_lo = max(1, b - _AN_NOTCH_HALFWIDTH)
                notch_hi = min(n_bins - 1, b + _AN_NOTCH_HALFWIDTH + 1)
                neighbors = np.concatenate((power[lo:notch_lo], power[notch_hi:hi]))
                if len(neighbors) == 0:
                    continue
                local_med = np.median(neighbors)
                if local_med > 0 and power[b] > local_med * _AN_PEAK_THRESH:
                    # This bin is a tonal peak — mark for notching
                    gain[b] = 0.0

            # Expand notch to halfwidth around detected peaks
            notch_mask = gain == 0.0
            expanded_gain = gain.copy()
            for b in np.where(notch_mask)[0]:
                lo = max(1, b - _AN_NOTCH_HALFWIDTH)
                hi = min(n_bins, b + _AN_NOTCH_HALFWIDTH + 1)
                expanded_gain[lo:hi] = 0.0

            # Track persistent tones: smooth detection across frames
            self._an_persist = (
                _AN_PERSIST_SMOOTH * self._an_persist
                + (1 - _AN_PERSIST_SMOOTH) * (1.0 - expanded_gain)
            )

            # Apply notch only where persistence exceeds threshold
            notch_gain = np.where(self._an_persist > 0.3, 0.01, 1.0).astype(np.float32)
            # Always pass DC
            notch_gain[0] = 1.0

            # Temporal smoothing
            notch_gain = (
                _AN_GAIN_SMOOTH * self._an_prev_gain
                + (1 - _AN_GAIN_SMOOTH) * notch_gain
            )
            self._an_prev_gain = notch_gain.copy()

            # Ramp during initial frames
            if self._an_frame_count <= _AN_RAMP_FRAMES:
                ramp = self._an_frame_count / _AN_RAMP_FRAMES
                notch_gain = 1.0 - ramp * (1.0 - notch_gain)

            # Apply and synthesize
            filtered = spectrum * notch_gain
            out_frame = (
                _irfft(filtered, n=fft_size) * self._an_synth_window
            ).astype(np.float32)

            # Overlap-add
            out_frame[:hop] += self._an_prev_frame
            self._an_prev_frame = out_frame[hop:].copy()
            output_pieces.append(out_frame[:hop])

        if output_pieces:
            return np.concatenate(output_pieces)
        return np.array([], dtype=np.float32)

    def process(self, iq_samples):
        """Demodulate AM/SSB from complex IQ samples.

        Returns float32 audio array at audio_rate, or empty array if input too short.
        """
        if len(iq_samples) < self.decimation:
            return np.array([], dtype=np.float32)

        # Noise blanker (pre-filter, on raw IQ at full sample rate)
        with self._lock:
            nb_on = self._nb_enabled
        if nb_on:
            iq_samples = self._noise_blank(iq_samples)

        # Separate I and Q
        i_in = iq_samples.real.astype(np.float32)
        q_in = iq_samples.imag.astype(np.float32)

        # Lowpass filter (anti-alias)
        i_filt, self._lp_zi_i = lfilter(self._lp_taps, 1.0, i_in, zi=self._lp_zi_i)
        q_filt, self._lp_zi_q = lfilter(self._lp_taps, 1.0, q_in, zi=self._lp_zi_q)

        # Decimate
        i_dec = i_filt[::self.decimation]
        q_dec = q_filt[::self.decimation]

        # SNR measurement (from filtered, decimated IQ)
        self._measure_snr(i_dec, q_dec)

        # Detection
        if self.mode in ("CW+", "CW-"):
            detected = self._detect_cw(i_dec, q_dec)
        elif self.mode == "RTTY":
            detected = self._detect_rtty(i_dec, q_dec)
        elif self.mode == "PSK31":
            detected = self._detect_psk31(i_dec, q_dec)
        elif self.mode in ("USB", "LSB"):
            detected = i_dec.astype(np.float32)
        elif self.mode in ("SAM", "SAM-U", "SAM-L"):
            detected = self._pll_detect(i_dec, q_dec, self.mode)
        else:
            detected = np.sqrt(i_dec ** 2 + q_dec ** 2)

        # Spectral DNR (post-detection, pre-DC removal)
        with self._lock:
            dnr_level = self._dnr_level
        if dnr_level > 0:
            detected = self._apply_dnr(detected)
            if len(detected) == 0:
                return np.array([], dtype=np.float32)

        # Auto notch (post-detection, after DNR)
        with self._lock:
            an_on = self._an_enabled
        if an_on:
            detected = self._apply_auto_notch(detected)
            if len(detected) == 0:
                return np.array([], dtype=np.float32)

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

        # Audio Peak Filter — narrow bandpass centered on BFO tone
        with self._lock:
            apf_on = self._apf_enabled
        if apf_on:
            detected = self._apply_apf(detected)

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
        spec = np.abs(_rfft(ordered_buf * win)) ** 2

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
    def nb_enabled(self):
        with self._lock:
            return self._nb_enabled

    @nb_enabled.setter
    def nb_enabled(self, value):
        with self._lock:
            self._nb_enabled = value

    @property
    def nb_threshold_name(self):
        with self._lock:
            return self._nb_threshold_name

    def cycle_nb_threshold(self):
        """Cycle NB threshold through Low -> Med -> High -> Low."""
        names = ["Low", "Med", "High"]
        with self._lock:
            idx = names.index(self._nb_threshold_name) if self._nb_threshold_name in names else 0
            self._nb_threshold_name = names[(idx + 1) % len(names)]
            self._nb_threshold = _NB_THRESHOLD_PRESETS[self._nb_threshold_name]

    @property
    def dnr_level(self):
        with self._lock:
            return self._dnr_level

    @dnr_level.setter
    def dnr_level(self, value):
        with self._lock:
            self._dnr_level = value

    def cycle_dnr_level(self):
        """Cycle DNR level: 0 -> 1 -> 2 -> 3 -> 0."""
        with self._lock:
            self._dnr_level = (self._dnr_level + 1) % 4

    @property
    def auto_notch(self):
        with self._lock:
            return self._an_enabled

    @auto_notch.setter
    def auto_notch(self, value):
        with self._lock:
            self._an_enabled = value

    def toggle_auto_notch(self):
        """Toggle auto notch on/off."""
        with self._lock:
            self._an_enabled = not self._an_enabled

    @property
    def apf_enabled(self):
        with self._lock:
            return self._apf_enabled

    @apf_enabled.setter
    def apf_enabled(self, value):
        with self._lock:
            self._apf_enabled = value

    def toggle_apf(self):
        """Toggle audio peak filter on/off."""
        with self._lock:
            self._apf_enabled = not self._apf_enabled
            if self._apf_enabled:
                # Reset filter state when enabling
                self._apf_b, self._apf_a = self._make_apf_coeffs()
                self._apf_zi = np.zeros(2, dtype=np.float64)

    def get_snr_db(self):
        """Return estimated in-band SNR in dB."""
        return self._snr_db

    @property
    def bfo_offset(self):
        return self._bfo_offset

    def _cw_measure_speed(self, audio):
        """Detect CW keying envelope, estimate speed, and decode Morse.

        Uses rectified magnitude with hysteresis thresholds to prevent
        chatter on noisy signals, and a minimum edge duration debounce
        to reject noise glitches.
        """
        abs_audio = np.abs(audio).astype(np.float64)
        base_count = self._cw_sample_count
        min_edge_samples = int(_CW_MIN_EDGE_MS * self.audio_rate / 1000.0)

        if _HAS_NUMBA:
            (env, env_peak, key_down, edge_sample, last_keyup,
             ev_dur, ev_gap, ec) = _cw_env_loop_jit(
                abs_audio, float(self._cw_env), float(self._cw_env_peak),
                self._cw_key_down, self._cw_edge_sample, base_count,
                min_edge_samples, _CW_ENV_ATTACK, _CW_ENV_DECAY,
                _CW_PEAK_DECAY, _CW_THRESHOLD_UP, _CW_THRESHOLD_DN,
                _CW_MIN_PEAK, float(self.audio_rate),
                self._cw_last_keyup_sample)
            events = [(ev_dur[i], ev_gap[i]) for i in range(ec)]
        else:
            (env, env_peak, key_down, edge_sample, last_keyup,
             events) = _cw_env_loop_py(
                abs_audio, self._cw_env, self._cw_env_peak,
                self._cw_key_down, self._cw_edge_sample, base_count,
                min_edge_samples, _CW_ENV_ATTACK, _CW_ENV_DECAY,
                _CW_PEAK_DECAY, _CW_THRESHOLD_UP, _CW_THRESHOLD_DN,
                _CW_MIN_PEAK, self.audio_rate,
                self._cw_last_keyup_sample)

        self._cw_env = env
        self._cw_env_peak = env_peak
        self._cw_key_down = bool(key_down)
        self._cw_edge_sample = int(edge_sample)
        self._cw_last_keyup_sample = int(last_keyup)
        self._cw_sample_count = base_count + len(audio)

        # Process detected events (Morse decode logic stays in Python)
        for dur_ms, gap_ms in events:
            self._cw_element_ms.append(dur_ms)
            if len(self._cw_element_ms) > 60:
                self._cw_element_ms = self._cw_element_ms[-60:]
            self._cw_update_wpm()

            if self._cw_dit_ms > 0:
                if self._cw_pending_edges:
                    self._cw_replay_pending()
                if gap_ms > self._cw_dit_ms * 4.0 and self._cw_current_char:
                    self._cw_decode_char()
                    self._cw_append_text(" ")
                elif gap_ms > self._cw_dit_ms * 2.0 and self._cw_current_char:
                    self._cw_decode_char()
                if dur_ms < self._cw_dit_ms * 2.0:
                    self._cw_current_char.append(".")
                else:
                    self._cw_current_char.append("-")
            else:
                self._cw_pending_edges.append((dur_ms, gap_ms))

        # Flush pending character on long silence
        if (not self._cw_key_down and self._cw_dit_ms > 0
                and self._cw_current_char and self._cw_last_keyup_sample > 0):
            silence_ms = (self._cw_sample_count - self._cw_last_keyup_sample) * 1000.0 / self.audio_rate
            if silence_ms > self._cw_dit_ms * 4.0:
                self._cw_decode_char()
                self._cw_append_text(" ")

    def _cw_replay_pending(self):
        """Replay buffered edges now that _cw_dit_ms is established."""
        pending = self._cw_pending_edges
        self._cw_pending_edges = []
        for dur_ms, gap_ms in pending:
            # Check gap for character/word boundary
            if gap_ms > self._cw_dit_ms * 4.0 and self._cw_current_char:
                self._cw_decode_char()
                self._cw_append_text(" ")
            elif gap_ms > self._cw_dit_ms * 2.0 and self._cw_current_char:
                self._cw_decode_char()
            # Classify element
            if dur_ms < self._cw_dit_ms * 2.0:
                self._cw_current_char.append(".")
            else:
                self._cw_current_char.append("-")

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

    def _init_rtty_filters(self):
        """Build bandpass filters for RTTY mark and space tones."""
        mark = self._rtty_mark_hz
        space = self._rtty_space_hz
        bw = _RTTY_FILTER_BW
        sr = self.audio_rate
        ntaps = 255
        # Mark bandpass
        lo = max(1.0, mark - bw / 2)
        hi = min(sr / 2 - 1, mark + bw / 2)
        self._rtty_mark_bp = firwin(ntaps, [lo, hi], pass_zero=False, fs=sr).astype(np.float32)
        self._rtty_mark_zi = np.zeros(ntaps - 1, dtype=np.float32)
        # Space bandpass
        lo = max(1.0, space - bw / 2)
        hi = min(sr / 2 - 1, space + bw / 2)
        self._rtty_space_bp = firwin(ntaps, [lo, hi], pass_zero=False, fs=sr).astype(np.float32)
        self._rtty_space_zi = np.zeros(ntaps - 1, dtype=np.float32)

    def _detect_rtty(self, i_dec, q_dec):
        """RTTY FSK detection: bandpass mark/space, envelope compare, bit clock recovery, Baudot decode.

        Takes decimated IQ (audio-rate), outputs audio (the mark/space mixed tone for monitoring)
        and decodes Baudot characters as a side effect.
        """
        # Initialize filters on first call
        if self._rtty_mark_bp is None:
            self._init_rtty_filters()

        # Use I channel as real audio (USB demod equivalent)
        audio = i_dec.astype(np.float32)

        # Bandpass filter for mark and space tones
        mark_sig, self._rtty_mark_zi = lfilter(
            self._rtty_mark_bp, 1.0, audio, zi=self._rtty_mark_zi)
        space_sig, self._rtty_space_zi = lfilter(
            self._rtty_space_bp, 1.0, audio, zi=self._rtty_space_zi)

        # Envelope detection (magnitude)
        mark_env = np.abs(mark_sig)
        space_env = np.abs(space_sig)

        # Process each sample: discriminate mark vs space, clock recovery, decode
        samples_per_bit = self._rtty_samples_per_bit
        bit_acc = self._rtty_bit_acc
        bit_phase = self._rtty_bit_phase

        for k in range(len(audio)):
            # Smoothed discriminator: positive = mark, negative = space
            diff = mark_env[k] - space_env[k]
            bit_acc = _RTTY_BIT_SMOOTH * bit_acc + (1 - _RTTY_BIT_SMOOTH) * diff
            is_mark = bit_acc > 0

            # Advance bit clock
            bit_phase += 1.0 / samples_per_bit

            if self._rtty_state == "IDLE":
                # Wait for start bit (space = 0)
                if not is_mark:
                    self._rtty_state = "START"
                    bit_phase = 0.0
            elif self._rtty_state == "START":
                # Sample at mid-bit to confirm start bit
                if bit_phase >= 0.5:
                    if not is_mark:
                        # Valid start bit, begin data
                        self._rtty_state = "DATA"
                        self._rtty_shift_reg = 0
                        self._rtty_bit_count = 0
                        bit_phase = 0.0
                    else:
                        # False start
                        self._rtty_state = "IDLE"
            elif self._rtty_state == "DATA":
                if bit_phase >= 1.0:
                    bit_phase -= 1.0
                    # Sample data bit (LSB first)
                    if is_mark:
                        self._rtty_shift_reg |= (1 << self._rtty_bit_count)
                    self._rtty_bit_count += 1
                    if self._rtty_bit_count >= 5:
                        self._rtty_state = "STOP"
            elif self._rtty_state == "STOP":
                if bit_phase >= 1.0:
                    # Stop bit(s) received — decode character
                    self._rtty_decode_char(self._rtty_shift_reg)
                    self._rtty_state = "IDLE"
                    bit_phase = 0.0

        self._rtty_bit_acc = bit_acc
        self._rtty_bit_phase = bit_phase
        return audio

    def _rtty_decode_char(self, code):
        """Decode a 5-bit Baudot code and append to text buffer."""
        if code == _BAUDOT_LTRS_SHIFT:
            self._rtty_figs_mode = False
            return
        if code == _BAUDOT_FIGS_SHIFT:
            self._rtty_figs_mode = True
            return

        table = _BAUDOT_FIGS if self._rtty_figs_mode else _BAUDOT_LTRS
        ch = table.get(code)
        if ch is None or ch == '\0':
            return
        if ch == '\x07':  # Bell
            return
        if ch == '\r':
            return  # Ignore CR, LF handles newlines
        if ch == '\n':
            ch = ' '  # Display newlines as spaces in the text buffer

        self._rtty_decoded_text += ch
        if len(self._rtty_decoded_text) > 120:
            self._rtty_decoded_text = self._rtty_decoded_text[-120:]

    def get_rtty_text(self):
        """Return the decoded RTTY text buffer."""
        with self._lock:
            return self._rtty_decoded_text

    def clear_rtty_text(self):
        """Clear the decoded RTTY text buffer (thread-safe)."""
        with self._lock:
            self._rtty_decoded_text = ""

    def _detect_psk31(self, i_dec, q_dec):
        """BPSK31 detection: downconvert, lowpass, symbol clock recovery, differential decode, Varicode.

        Returns audio (real part of baseband) for monitoring.
        """
        audio = i_dec.astype(np.float64)
        n = len(audio)

        # Downconvert to baseband using local oscillator
        lo_phases = self._psk_lo_phase + self._psk_lo_freq * np.arange(n)
        self._psk_lo_phase = (lo_phases[-1] + self._psk_lo_freq) % (2.0 * np.pi)
        bb_i = audio * np.cos(lo_phases)
        bb_q = audio * (-np.sin(lo_phases))

        # Lowpass filter baseband I and Q
        bb_i, self._psk_lp_zi_i = lfilter(
            self._psk_lp_taps, 1.0, bb_i.astype(np.float32), zi=self._psk_lp_zi_i)
        bb_q, self._psk_lp_zi_q = lfilter(
            self._psk_lp_taps, 1.0, bb_q.astype(np.float32), zi=self._psk_lp_zi_q)

        # Process samples: accumulate over symbol period, then decide
        samples_per_sym = self._psk_samples_per_sym
        sym_phase = self._psk_sym_phase
        i_acc = self._psk_i_acc
        q_acc = self._psk_q_acc
        acc_count = self._psk_acc_count

        for k in range(n):
            i_acc += bb_i[k]
            q_acc += bb_q[k]
            acc_count += 1
            sym_phase += 1.0

            if sym_phase >= samples_per_sym:
                sym_phase -= samples_per_sym
                # Normalize accumulated symbol
                if acc_count > 0:
                    sym_i = i_acc / acc_count
                    sym_q = q_acc / acc_count
                else:
                    sym_i = 0.0
                    sym_q = 0.0

                # Differential detection: compare phase to previous symbol
                # BPSK: 0 = phase reversal (180°), 1 = same phase (0°)
                current = complex(sym_i, sym_q)
                prev = self._psk_prev_symbol

                if abs(prev) > 1e-10 and abs(current) > 1e-10:
                    # Dot product of normalized symbols gives cos(phase_diff)
                    dot = (current.real * prev.real + current.imag * prev.imag)
                    dot /= (abs(current) * abs(prev))
                    bit = 1 if dot > 0 else 0
                else:
                    bit = 1  # Idle

                self._psk_prev_symbol = current

                # Varicode decode: accumulate bits, look for "00" separator
                self._psk_bit_buf += str(bit)

                if self._psk_bit_buf.endswith("00"):
                    # Character complete — strip trailing "00"
                    code = self._psk_bit_buf[:-2]
                    self._psk_bit_buf = ""
                    if code and code in _VARICODE_DEC:
                        ch = _VARICODE_DEC[code]
                        if ch == '\r' or ch == '\n':
                            ch = ' '
                        elif ord(ch) < 32 and ch != '\t':
                            pass  # Skip non-printable control chars
                        else:
                            self._psk_decoded_text += ch
                            if len(self._psk_decoded_text) > 120:
                                self._psk_decoded_text = self._psk_decoded_text[-120:]
                elif len(self._psk_bit_buf) > 20:
                    # Too long without separator — noise, reset
                    self._psk_bit_buf = ""

                i_acc = 0.0
                q_acc = 0.0
                acc_count = 0

        self._psk_sym_phase = sym_phase
        self._psk_i_acc = i_acc
        self._psk_q_acc = q_acc
        self._psk_acc_count = acc_count

        return i_dec.astype(np.float32)

    def get_psk_text(self):
        """Return the decoded PSK31 text buffer."""
        with self._lock:
            return self._psk_decoded_text

    def clear_psk_text(self):
        """Clear the decoded PSK31 text buffer (thread-safe)."""
        with self._lock:
            self._psk_decoded_text = ""

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
            self._cw_pending_edges = []

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
        """PLL-based synchronous AM detection."""
        n = len(i_samples)
        out = np.empty(n, dtype=np.float64)
        phase = float(self._pll_phase)
        freq = float(self._pll_freq)
        alpha = _PLL_ALPHA
        beta = _PLL_BETA
        mode_code = {"SAM": 0, "SAM-U": 1, "SAM-L": 2}.get(mode, 0)

        i_f64 = i_samples.astype(np.float64)
        q_f64 = q_samples.astype(np.float64)

        if _HAS_NUMBA:
            phase, freq = _pll_loop_jit(i_f64, q_f64, out, phase, freq,
                                        alpha, beta, mode_code)
        else:
            phase, freq = _pll_loop_py(i_f64, q_f64, out, phase, freq,
                                       alpha, beta, mode_code)

        self._pll_phase = (phase + math.pi) % (2 * math.pi) - math.pi
        self._pll_freq = max(-0.5, min(0.5, freq))
        return out.astype(np.float32)

    def reset(self):
        """Reset all filter, AGC, PLL, and noise reduction state."""
        self._lp_taps, self._lp_zi_i = _make_filter(127, self.bandwidth, self.iq_sample_rate)
        _, self._lp_zi_q = _make_filter(127, self.bandwidth, self.iq_sample_rate)
        # Reset SNR estimator
        self._snr_db = 0.0
        self._snr_signal_power = 0.0
        self._snr_noise_floor = 0.0
        self._snr_buf = np.zeros(0, dtype=np.complex64)
        # Reset noise blanker
        self._nb_avg_mag = 0.0
        self._nb_delay_buf[:] = 0.0
        self._nb_holdoff_count = 0
        # Reset spectral DNR
        n_bins = _DNR_FFT_SIZE // 2 + 1
        self._dnr_in_buf = np.zeros(0, dtype=np.float32)
        self._dnr_prev_frame = np.zeros(_DNR_HOP, dtype=np.float32)
        self._dnr_noise_floor = 0.0
        self._dnr_prev_gain = np.ones(n_bins, dtype=np.float32)
        self._dnr_frame_count = 0
        # Reset auto notch
        an_bins = _AN_FFT_SIZE // 2 + 1
        self._an_in_buf = np.zeros(0, dtype=np.float32)
        self._an_prev_frame = np.zeros(_AN_HOP, dtype=np.float32)
        self._an_persist = np.zeros(an_bins, dtype=np.float32)
        self._an_prev_gain = np.ones(an_bins, dtype=np.float32)
        self._an_frame_count = 0
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
        self._cw_pending_edges = []
        # Reset APF state
        self._apf_b, self._apf_a = self._make_apf_coeffs()
        self._apf_zi = np.zeros(2, dtype=np.float64)
        # Reset RTTY state
        self._rtty_mark_phase = 0.0
        self._rtty_space_phase = 0.0
        self._rtty_bit_acc = 0.0
        self._rtty_bit_phase = 0.0
        self._rtty_shift_reg = 0
        self._rtty_bit_count = 0
        self._rtty_state = "IDLE"
        self._rtty_figs_mode = False
        self._rtty_mark_bp = None  # Rebuilt on next use
        self._rtty_space_bp = None
        self._rtty_mark_zi = None
        self._rtty_space_zi = None
        # Reset PSK31 state
        self._psk_lo_phase = 0.0
        self._psk_lo_freq = 2.0 * np.pi * _PSK31_CARRIER_HZ / self.audio_rate
        self._psk_sym_phase = 0.0
        self._psk_prev_symbol = 1.0 + 0j
        self._psk_bit_buf = ""
        self._psk_i_acc = 0.0
        self._psk_q_acc = 0.0
        self._psk_acc_count = 0
        self._psk_lp_zi_i = np.zeros(126, dtype=np.float32)
        self._psk_lp_zi_q = np.zeros(126, dtype=np.float32)
        with self._lock:
            self._rtty_decoded_text = ""
            self._psk_decoded_text = ""
        if self._cw_taps is not None:
            self._cw_taps, self._cw_zi_i = _make_filter(255, self.bandwidth, self.audio_rate)
            _, self._cw_zi_q = _make_filter(255, self.bandwidth, self.audio_rate)
