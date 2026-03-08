"""DRM decoder integration — wraps the Dream DRM decoder as a subprocess.

Uses Dream's stdin/stdout pipe mode (-I - / -O -) following the same
approach as openwebrx.  IQ data is written to Dream's stdin as raw
int16 interleaved stereo (I=left, Q=right).  Decoded audio is read
from Dream's stdout as raw int16 mono and fed into the app's audio
ring buffer.  Status information is parsed from stderr.
"""

import os
import shutil
import subprocess
import threading
import numpy as np

# Default path to Dream binary (relative to this project)
_DEFAULT_DREAM_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "DRM",
                 "dream-2.1.1-svn808", "dream", "dream")
)

# Status field names for the DRM|... line from patched Dream
_STATUS_FIELDS = ("sync", "signal", "snr", "label", "bitrate", "mode",
                  "audio_ratio")


def find_dream_binary(configured_path=None):
    """Locate the Dream binary. Returns path or None."""
    candidates = []
    if configured_path:
        candidates.append(configured_path)
    candidates.append(_DEFAULT_DREAM_PATH)
    path_dream = shutil.which("dream")
    if path_dream:
        candidates.append(path_dream)
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


class DRMDecoder:
    """Manages a Dream DRM decoder subprocess using stdin/stdout pipes.

    Dream reads raw int16 stereo IQ from stdin (-I -) and writes
    decoded int16 audio to stdout (-O -).  A patched Dream also emits
    periodic status lines to stderr in the format:
        DRM|SYNC|signal|snr|label|bitrate|mode|audiook/total
    """

    def __init__(self, iq_sample_rate=48000, audio_rate=48000,
                 dream_path=None):
        self.iq_sample_rate = iq_sample_rate
        self.audio_rate = audio_rate
        self.dream_path = find_dream_binary(dream_path)
        self._process = None
        self._reader_thread = None
        self._stderr_thread = None
        self._audio_callback = None
        self._lock = threading.Lock()

        # Latest status from Dream's stderr
        self.status = {
            "sync": "------",   # 6 chars: IO,Time,Frame,FAC,SDC,MSC
            "signal": False,    # DRM signal acquired
            "snr": 0.0,         # Signal-to-noise ratio in dB
            "label": "",        # Service label
            "bitrate": 0.0,     # Audio bitrate kbps
            "mode": "?",        # Robustness mode A/B/C/D
            "audio_ok": 0,      # Audio frames OK
            "audio_total": 0,   # Audio frames total
        }

    @property
    def running(self):
        return self._process is not None and self._process.poll() is None

    def start(self, audio_callback=None):
        """Start the Dream subprocess.

        audio_callback: callable(np.ndarray float32) to receive decoded audio.
        """
        if self.running:
            return True
        if not self.dream_path:
            return False

        self._audio_callback = audio_callback

        cmd = [
            self.dream_path,
            "-c", "6",  # IQ positive, zero IF
            "--sigsrate", str(self.iq_sample_rate),
            "--audsrate", str(self.audio_rate),
            "-I", "-",   # Read IQ from stdin
            "-O", "-",   # Write decoded audio to stdout
        ]
        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Start audio reader thread (reads decoded int16 from stdout)
        self._reader_thread = threading.Thread(
            target=self._read_audio, daemon=True)
        self._reader_thread.start()

        # Start stderr parser thread (reads status lines)
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

        return True

    def write_iq(self, iq_samples):
        """Write IQ samples to Dream's stdin.

        iq_samples: complex64 numpy array (normalised to [-1, 1]).
        """
        if not self.running or self._process.stdin is None:
            return

        # Convert complex64 → interleaved int16 stereo (I, Q)
        scale = 32767.0
        interleaved = np.empty(len(iq_samples) * 2, dtype=np.int16)
        interleaved[0::2] = np.clip(
            np.real(iq_samples) * scale, -32768, 32767).astype(np.int16)
        interleaved[1::2] = np.clip(
            np.imag(iq_samples) * scale, -32768, 32767).astype(np.int16)

        try:
            self._process.stdin.write(interleaved.tobytes())
            self._process.stdin.flush()
        except (OSError, BrokenPipeError):
            pass

    def _read_audio(self):
        """Read decoded audio from Dream's stdout and deliver via callback."""
        # Dream outputs int16 stereo at audio_rate
        chunk_bytes = 4096  # read in chunks
        try:
            while self.running:
                data = self._process.stdout.read(chunk_bytes)
                if not data:
                    break
                # Convert int16 stereo to float32 mono (downmix)
                samples = np.frombuffer(data, dtype=np.int16)
                if len(samples) < 2:
                    continue
                # Stereo → mono: average L and R
                stereo = samples.reshape(-1, 2)
                mono = stereo.mean(axis=1).astype(np.float32) / 32768.0
                if self._audio_callback:
                    self._audio_callback(mono)
        except (OSError, ValueError):
            pass

    def _read_stderr(self):
        """Parse status lines from Dream's stderr."""
        try:
            for raw_line in self._process.stderr:
                line = raw_line.decode(errors="replace").strip()
                if not line.startswith("DRM|"):
                    continue
                parts = line.split("|")
                if len(parts) < 8:
                    continue
                try:
                    audio_parts = parts[7].split("/")
                    with self._lock:
                        self.status["sync"] = parts[1]
                        self.status["signal"] = parts[2] == "1"
                        self.status["snr"] = float(parts[3])
                        self.status["label"] = parts[4]
                        self.status["bitrate"] = float(parts[5])
                        self.status["mode"] = parts[6]
                        self.status["audio_ok"] = int(audio_parts[0])
                        self.status["audio_total"] = int(audio_parts[1]) \
                            if len(audio_parts) > 1 else 0
                except (ValueError, IndexError):
                    pass
        except (OSError, ValueError):
            pass

    def get_status(self):
        """Return a copy of the current DRM status dict."""
        with self._lock:
            return dict(self.status)

    def stop(self):
        """Stop the Dream subprocess and clean up."""
        if self._process is not None:
            # Close stdin to signal Dream to stop
            if self._process.stdin:
                try:
                    self._process.stdin.close()
                except OSError:
                    pass
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._process.kill()
                except OSError:
                    pass
            self._process = None
        self._audio_callback = None
        # Reset status
        with self._lock:
            self.status = {
                "sync": "------", "signal": False, "snr": 0.0,
                "label": "", "bitrate": 0.0, "mode": "?",
                "audio_ok": 0, "audio_total": 0,
            }
