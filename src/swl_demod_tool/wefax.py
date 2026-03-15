"""WEFAX (Weather Fax) decoder — FM subcarrier demodulation and image assembly.

HF Weather Fax uses an FM subcarrier at 1900 Hz center (black=1500 Hz,
white=2300 Hz) to transmit grayscale images line-by-line.  IOC 576 standard:
1810 pixels/line (576*pi), 120 RPM = 2 lines/sec.

State machine:
  IDLE -> START_TONE (300 Hz for ~3s) -> PHASING (B/W sync pulses) ->
  RECEIVING (pixel assembly) -> STOP_TONE (450 Hz) -> save image -> IDLE

The decoder writes intermediate data to a temp directory for the external
GTK4 viewer to poll:
  - image.raw: flat uint8 grayscale pixel data (width * height bytes)
  - meta.json: {"width": N, "height": N, "ioc": 576, "rpm": 120,
                "state": "RECEIVING", "line_count": N}
"""

import json
import logging
import math
import os
import shutil
import tempfile
import threading
import time
import numpy as np
from scipy.signal import firwin, lfilter

log = logging.getLogger(__name__)

# WEFAX protocol constants
_CENTER_HZ = 1900.0       # FM subcarrier center frequency
_BLACK_HZ = 1500.0        # Black level frequency
_WHITE_HZ = 2300.0        # White level frequency
_DEV_HZ = _WHITE_HZ - _BLACK_HZ  # Full deviation range (800 Hz)

_START_TONE_HZ = 300.0    # Start tone frequency
_STOP_TONE_HZ = 450.0     # Stop tone frequency
_PHASING_HZ = 675.0       # Phasing pulse alternation (approximate)

_START_TONE_MIN_S = 2.5   # Minimum duration to confirm start tone
_STOP_TONE_MIN_S = 2.5    # Minimum duration to confirm stop tone
_PHASING_MIN_LINES = 15   # Minimum stable phasing lines before lock


class WEFAXDecoder:
    """Standalone WEFAX decoder fed 48 kHz float32 audio samples."""

    def __init__(self, sample_rate=48000, ioc=576, rpm=120,
                 save_dir="~/Pictures/fax", auto_save=True):
        self.sample_rate = sample_rate
        self.ioc = ioc
        self.rpm = rpm
        self.save_dir = os.path.expanduser(save_dir)
        self.auto_save = auto_save

        # Derived constants
        self.pixels_per_line = round(ioc * math.pi)  # 1810 for IOC 576
        self.samples_per_line = int(sample_rate * 60 / rpm)  # 24000 for 120 RPM

        # Thread safety
        self._lock = threading.Lock()

        # State machine
        self._state = "IDLE"
        self._line_count = 0

        # Temp directory for shared data with viewer
        self._temp_dir = tempfile.mkdtemp(prefix="swl_wefax_")
        log.info("WEFAX temp dir: %s", self._temp_dir)

        # Image buffer (list of row arrays, stacked only at save/write time)
        self._image_rows = []
        self._completed_images = []

        # FM discriminator state
        self._prev_sample = 0.0 + 0.0j  # Previous analytic sample for phase diff

        # Bandpass filter around 1900 Hz (width ~1200 Hz to capture full deviation)
        bp_low = (_CENTER_HZ - _DEV_HZ * 0.75) / (sample_rate / 2)
        bp_high = (_CENTER_HZ + _DEV_HZ * 0.75) / (sample_rate / 2)
        bp_low = max(bp_low, 0.001)
        bp_high = min(bp_high, 0.999)
        self._bp_taps = firwin(127, [bp_low, bp_high], pass_zero=False)
        self._bp_zi = np.zeros(126, dtype=np.float64)

        # Goertzel tone detection state
        self._tone_buf = np.zeros(0, dtype=np.float32)
        self._tone_block_size = int(sample_rate * 0.25)  # 250ms blocks for tone detection
        self._start_tone_count = 0.0  # Accumulated seconds of start tone
        self._stop_tone_count = 0.0   # Accumulated seconds of stop tone

        # Line assembly state
        self._line_buf = np.zeros(self.samples_per_line, dtype=np.float32)
        self._line_pos = 0  # Current position within line buffer

        # Phasing detection state
        self._phasing_count = 0  # Number of stable phasing lines detected
        self._phasing_edge_pos = -1  # Sample position of last B->W transition

        # Hilbert transform FIR for analytic signal (31-tap)
        n_hilbert = 31
        self._hilbert_len = n_hilbert
        half = n_hilbert // 2
        h = np.zeros(n_hilbert, dtype=np.float64)
        for i in range(n_hilbert):
            k = i - half
            if k % 2 != 0:
                h[i] = 2.0 / (math.pi * k)
        h *= np.hamming(n_hilbert)
        self._hilbert_taps = h
        self._hilbert_zi = np.zeros(n_hilbert - 1, dtype=np.float64)
        self._delay_zi = np.zeros(half, dtype=np.float64)
        self._hilbert_delay = half

    def get_state(self):
        with self._lock:
            return self._state

    def get_line_count(self):
        with self._lock:
            return self._line_count

    def get_ioc(self):
        return self.ioc

    def get_rpm(self):
        return self.rpm

    def get_completed_images(self):
        """Return and clear list of saved file paths."""
        with self._lock:
            imgs = self._completed_images[:]
            self._completed_images.clear()
            return imgs

    def get_temp_dir(self):
        return self._temp_dir

    def reset(self, save_partial=True):
        """Clear state, optionally save in-progress image."""
        with self._lock:
            if save_partial and self._state == "RECEIVING" and self._line_count > 10:
                self._save_image()
            self._state = "IDLE"
            self._line_count = 0
            self._image_rows = []
            self._start_tone_count = 0.0
            self._stop_tone_count = 0.0
            self._line_pos = 0
            self._phasing_count = 0
            self._phasing_edge_pos = -1
            self._prev_sample = 0.0 + 0.0j
            self._tone_buf = np.zeros(0, dtype=np.float32)
            self._write_meta()

    def cleanup(self):
        """Remove temp directory."""
        try:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass

    def feed(self, audio):
        """Feed float32 audio samples to the decoder."""
        if len(audio) == 0:
            return

        # FM discriminator: bandpass -> analytic signal -> phase diff -> freq
        pixel_values = self._fm_discriminate(audio)

        # Tone detection on raw audio (not bandpass filtered)
        self._detect_tones(audio)

        with self._lock:
            if self._state == "IDLE":
                if self._start_tone_count >= _START_TONE_MIN_S:
                    self._state = "START_TONE"
                    log.info("WEFAX: start tone detected")
                    self._start_tone_count = 0.0
                    self._phasing_count = 0

            elif self._state == "START_TONE":
                # Wait for start tone to end, then enter phasing
                if self._start_tone_count < 0.1:
                    # Start tone ended, look for phasing
                    self._state = "PHASING"
                    self._line_pos = 0
                    log.info("WEFAX: entering phasing detection")

            elif self._state == "PHASING":
                self._process_phasing(pixel_values)

            elif self._state == "RECEIVING":
                if self._stop_tone_count >= _STOP_TONE_MIN_S:
                    log.info("WEFAX: stop tone detected, saving image (%d lines)",
                             self._line_count)
                    self._save_image()
                    self._state = "IDLE"
                    self._stop_tone_count = 0.0
                    self._image_rows = []
                    self._line_count = 0
                    self._line_pos = 0
                    self._write_meta()
                else:
                    self._assemble_pixels(pixel_values)

    def _fm_discriminate(self, audio):
        """Bandpass filter and FM discriminate audio to pixel values [0, 1]."""
        # Bandpass filter around 1900 Hz
        filtered, self._bp_zi = lfilter(self._bp_taps, 1.0, audio, zi=self._bp_zi)
        filtered = filtered.astype(np.float64)

        # Build analytic signal using Hilbert transform FIR
        # Imaginary part from Hilbert filter
        imag_part, self._hilbert_zi = lfilter(
            self._hilbert_taps, 1.0, filtered, zi=self._hilbert_zi)
        # Real part delayed to match Hilbert group delay
        real_part, self._delay_zi = lfilter(
            np.concatenate([np.zeros(self._hilbert_delay), [1.0]]),
            1.0, filtered, zi=self._delay_zi)

        analytic = real_part + 1j * imag_part

        # Phase difference (instantaneous frequency)
        # freq = d(phase)/dt / (2*pi) * sample_rate
        prev = self._prev_sample
        if abs(prev) < 1e-15:
            prev = analytic[0] if len(analytic) > 0 else 1.0 + 0.0j
        all_samples = np.concatenate([[prev], analytic])
        phase_diff = np.angle(all_samples[1:] * np.conj(all_samples[:-1]))
        self._prev_sample = analytic[-1] if len(analytic) > 0 else prev

        # Convert phase diff to instantaneous frequency
        inst_freq = phase_diff * self.sample_rate / (2.0 * math.pi)

        # Normalize: black (1500 Hz) = 0, white (2300 Hz) = 1
        pixel_values = (inst_freq - _BLACK_HZ) / _DEV_HZ
        pixel_values = np.clip(pixel_values, 0.0, 1.0).astype(np.float32)

        return pixel_values

    def _goertzel_mag(self, samples, target_hz):
        """Compute Goertzel magnitude for a single frequency."""
        n = len(samples)
        if n == 0:
            return 0.0
        k = round(target_hz * n / self.sample_rate)
        w = 2.0 * math.pi * k / n
        coeff = 2.0 * math.cos(w)
        s0 = 0.0
        s1 = 0.0
        s2 = 0.0
        for sample in samples:
            s0 = sample + coeff * s1 - s2
            s2 = s1
            s1 = s0
        power = s1 * s1 + s2 * s2 - coeff * s1 * s2
        return math.sqrt(max(power, 0.0)) / n

    def _detect_tones(self, audio):
        """Detect 300 Hz start tone and 450 Hz stop tone using Goertzel."""
        self._tone_buf = np.concatenate([self._tone_buf, audio])

        while len(self._tone_buf) >= self._tone_block_size:
            block = self._tone_buf[:self._tone_block_size]
            self._tone_buf = self._tone_buf[self._tone_block_size:]

            block_dur = self._tone_block_size / self.sample_rate

            mag_300 = self._goertzel_mag(block, _START_TONE_HZ)
            mag_450 = self._goertzel_mag(block, _STOP_TONE_HZ)
            mag_1900 = self._goertzel_mag(block, _CENTER_HZ)

            # RMS of block for threshold
            rms = float(np.sqrt(np.mean(block ** 2)))
            if rms < 1e-6:
                continue

            # Normalize magnitudes
            norm_300 = mag_300 / rms
            norm_450 = mag_450 / rms
            norm_1900 = mag_1900 / rms

            # Start tone: 300 Hz dominant
            if norm_300 > 0.3 and norm_300 > norm_450 * 2 and norm_300 > norm_1900 * 1.5:
                self._start_tone_count += block_dur
                self._stop_tone_count = 0.0
            # Stop tone: 450 Hz dominant
            elif norm_450 > 0.3 and norm_450 > norm_300 * 2 and norm_450 > norm_1900 * 1.5:
                self._stop_tone_count += block_dur
                self._start_tone_count = 0.0
            else:
                # Decay counters slowly (tolerate brief dropouts)
                self._start_tone_count = max(0.0, self._start_tone_count - block_dur * 0.5)
                self._stop_tone_count = max(0.0, self._stop_tone_count - block_dur * 0.5)

    def _process_phasing(self, pixel_values):
        """Detect phasing pulses (alternating B/W with narrow sync pulse)."""
        # Accumulate into line buffer
        for pv in pixel_values:
            self._line_buf[self._line_pos] = pv
            self._line_pos += 1

            if self._line_pos >= self.samples_per_line:
                # Analyze this line for phasing pattern
                line = self._line_buf[:self.samples_per_line]

                # Look for the characteristic phasing pattern:
                # ~95% black + ~5% white pulse at line boundary
                threshold = 0.5
                is_white = line > threshold
                white_frac = np.mean(is_white)

                if 0.02 < white_frac < 0.15:
                    # Find the white-to-black transition (end of sync pulse)
                    # This marks the start of the next line
                    transitions = np.diff(is_white.astype(np.int8))
                    wb_edges = np.where(transitions == -1)[0]  # white -> black

                    if len(wb_edges) > 0:
                        edge = wb_edges[-1]  # Last W->B transition
                        if self._phasing_edge_pos >= 0:
                            drift = abs(edge - self._phasing_edge_pos)
                            if drift < self.samples_per_line * 0.02:
                                self._phasing_count += 1
                            else:
                                self._phasing_count = max(0, self._phasing_count - 1)
                        self._phasing_edge_pos = edge
                else:
                    self._phasing_count = max(0, self._phasing_count - 1)

                self._line_pos = 0

                if self._phasing_count >= _PHASING_MIN_LINES:
                    log.info("WEFAX: phasing locked after %d lines, starting reception",
                             self._phasing_count)
                    self._state = "RECEIVING"
                    self._line_count = 0
                    self._image_rows = []
                    # Shift line start by phasing edge position
                    if self._phasing_edge_pos > 0:
                        self._line_pos = 0
                    self._write_meta()
                    return

    def _assemble_pixels(self, pixel_values):
        """Map discriminator samples to pixel positions and build image lines."""
        for pv in pixel_values:
            self._line_buf[self._line_pos] = pv
            self._line_pos += 1

            if self._line_pos >= self.samples_per_line:
                # Downsample line to pixel width
                line = self._line_buf[:self.samples_per_line]
                # Use linear interpolation for clean downsampling
                indices = np.linspace(0, len(line) - 1, self.pixels_per_line)
                pixel_line = np.interp(indices, np.arange(len(line)), line)
                pixel_line_u8 = (pixel_line * 255.0).astype(np.uint8)

                # Append to image buffer
                self._image_rows.append(pixel_line_u8.copy())
                self._line_count += 1
                self._line_pos = 0

                # Write shared data every line
                self._write_shared_data()

    def _write_shared_data(self):
        """Write image data and metadata to temp directory for viewer."""
        try:
            raw_path = os.path.join(self._temp_dir, "image.raw")
            meta_path = os.path.join(self._temp_dir, "meta.json")

            # Write raw pixel data
            if self._image_rows:
                np.vstack(self._image_rows).tofile(raw_path)

            # Write metadata
            self._write_meta()
        except Exception as e:
            log.warning("WEFAX: failed to write shared data: %s", e)

    def _write_meta(self):
        """Write metadata JSON to temp directory."""
        try:
            meta_path = os.path.join(self._temp_dir, "meta.json")
            meta = {
                "width": self.pixels_per_line,
                "height": self._line_count,
                "ioc": self.ioc,
                "rpm": self.rpm,
                "state": self._state,
                "line_count": self._line_count,
            }
            # Write atomically via temp file
            tmp = meta_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(meta, f)
            os.replace(tmp, meta_path)
        except Exception as e:
            log.warning("WEFAX: failed to write meta: %s", e)

    def _save_image(self):
        """Save the current image buffer as a grayscale PNG."""
        if self._line_count < 1:
            return

        try:
            from PIL import Image
        except ImportError:
            log.warning("WEFAX: Pillow not installed, cannot save image")
            return

        try:
            os.makedirs(self.save_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"wefax_{timestamp}.png"
            path = os.path.join(self.save_dir, filename)

            img = Image.fromarray(np.vstack(self._image_rows), mode='L')
            img.save(path)
            log.info("WEFAX: saved %s (%d lines)", path, self._line_count)

            if self.auto_save:
                self._completed_images.append(path)
        except Exception as e:
            log.error("WEFAX: failed to save image: %s", e)
