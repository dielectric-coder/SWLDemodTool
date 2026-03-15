"""DRM decoder integration — wraps the Dream 2.2 decoder as a subprocess.

Uses Dream's stdin/stdout pipe mode (-I - / -O -) following the same
approach as openwebrx.  IQ data is decimated to 48 kHz and written to
Dream's stdin as raw int16 interleaved stereo (I=left, Q=right).
Decoded audio is read from Dream's stdout as raw int16 stereo and
mixed to mono for the app's audio ring buffer.  Status information is
read from a Unix domain socket via Dream's --status-socket option
(JSON format).
"""

import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import numpy as np
from scipy.signal import firwin, lfilter

log = logging.getLogger(__name__)

# Default path to Dream 2.2 binary (relative to this project)
_DEFAULT_DREAM_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "DRM",
                 "dream-2.2", "dream"))

_ROBUSTNESS_MODES = {0: "A", 1: "B", 2: "C", 3: "D"}
_SYNC_KEYS = ("io", "time", "frame", "fac", "sdc", "msc")
_QAM_MODES = {0: "4-QAM", 1: "16-QAM", 2: "64-QAM"}

# Maximum bytes to buffer when waiting for a newline in status socket
_MAX_STATUS_BUF = 65536


def _default_status():
    """Return a fresh default status dict."""
    return {
        "sync": "------",
        "signal": False,
        "snr": 0.0,
        "label": "",
        "text": "",
        "bitrate": 0.0,
        "mode": "?",
        "country": "",
        "language": "",
        "audio_mode": "",
        "sync_detail": {"io": "-", "time": "-", "frame": "-",
                        "fac": "-", "sdc": "-", "msc": "-"},
        "sdc_qam": "",
        "msc_qam": "",
    }


def _make_decim_filter(num_taps, cutoff, fs):
    """Build a decimation FIR and zero initial conditions."""
    taps = firwin(num_taps, cutoff, fs=fs).astype(np.float32)
    zi = np.zeros(len(taps) - 1, dtype=np.float32)
    return taps, zi


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


def _extract_service_info(svc):
    """Extract label, text, bitrate, audio_mode, language, country from a service dict."""
    label = svc.get("label", "").strip()
    text = svc.get("text", "").strip()
    bitrate = svc.get("bitrate_kbps", 0.0)
    audio_mode = svc.get("audio_mode", "")
    lang = svc.get("language", {})
    language = lang.get("name", "") if lang else ""
    ctry = svc.get("country", {})
    country = ctry.get("name", "") if ctry else ""
    return label, text, bitrate, audio_mode, language, country


class DRMDecoder:
    """Manages a Dream 2.2 DRM decoder subprocess using stdin/stdout pipes."""

    DREAM_IQ_RATE = 48000

    def __init__(self, iq_sample_rate=48000, audio_rate=48000,
                 dream_path=None):
        self.iq_sample_rate = iq_sample_rate
        self.audio_rate = audio_rate
        self.dream_path = find_dream_binary(dream_path)
        self._process = None
        self._reader_thread = None
        self._stderr_thread = None
        self._socket_thread = None
        self._audio_callback = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._socket_dir = None
        self._socket_path = None

        # Decimation filter state
        self._decim = 1
        self._decim_fir = None
        self._decim_zi_i = None
        self._decim_zi_q = None

        self.status = _default_status()

    @property
    def running(self):
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, audio_callback=None):
        """Start the Dream subprocess."""
        if self.running:
            return True
        if not self.dream_path:
            return False

        self._audio_callback = audio_callback
        self._stop_event.clear()

        # Build decimation filter: IQ sample rate -> 48 kHz
        self._decim = max(1, self.iq_sample_rate // self.DREAM_IQ_RATE)
        if self._decim > 1:
            self._decim_fir, self._decim_zi_i = _make_decim_filter(
                127, self.DREAM_IQ_RATE / 2.0, self.iq_sample_rate)
            _, self._decim_zi_q = _make_decim_filter(
                127, self.DREAM_IQ_RATE / 2.0, self.iq_sample_rate)
        else:
            self._decim_fir = None

        # Use a private temp directory for the socket (prevents symlink attacks)
        self._socket_dir = tempfile.mkdtemp(prefix="swl_drm_")
        self._socket_path = os.path.join(self._socket_dir, "status.sock")

        cmd = [
            self.dream_path,
            "-c", "6",
            "--sigsrate", str(self.DREAM_IQ_RATE),
            "--audsrate", str(self.audio_rate),
            "-I", "-",
            "-O", "-",
            "--status-socket", self._socket_path,
        ]
        log.info("Starting Dream: %s", " ".join(cmd))

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self._reader_thread = threading.Thread(
            target=self._read_audio, daemon=True)
        self._reader_thread.start()

        self._socket_thread = threading.Thread(
            target=self._read_status_socket, daemon=True)
        self._socket_thread.start()

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

        return True

    def write_iq(self, iq_samples):
        """Write IQ samples to Dream's stdin, decimating to 48 kHz first."""
        with self._lock:
            proc = self._process
        if proc is None or proc.poll() is not None or proc.stdin is None:
            return

        # Decimate from iq_sample_rate to 48 kHz
        if self._decim > 1 and self._decim_fir is not None:
            i_in = np.real(iq_samples).astype(np.float32)
            q_in = np.imag(iq_samples).astype(np.float32)
            i_filt, self._decim_zi_i = lfilter(
                self._decim_fir, 1.0, i_in, zi=self._decim_zi_i)
            q_filt, self._decim_zi_q = lfilter(
                self._decim_fir, 1.0, q_in, zi=self._decim_zi_q)
            i_dec = i_filt[::self._decim]
            q_dec = q_filt[::self._decim]
        else:
            i_dec = np.real(iq_samples)
            q_dec = np.imag(iq_samples)

        scale = 32767.0
        interleaved = np.empty(len(i_dec) * 2, dtype=np.int16)
        interleaved[0::2] = np.clip(
            i_dec * scale, -32768, 32767).astype(np.int16)
        interleaved[1::2] = np.clip(
            q_dec * scale, -32768, 32767).astype(np.int16)

        try:
            proc.stdin.write(interleaved.tobytes())
            proc.stdin.flush()
        except (OSError, BrokenPipeError) as e:
            log.debug("write_iq error: %s", e)

    def _read_audio(self):
        """Read decoded audio from Dream's stdout and deliver via callback."""
        chunk_bytes = 4096
        frame_bytes = 4  # 2 channels x 2 bytes per int16 sample
        remainder = b""
        try:
            while not self._stop_event.is_set():
                data = self._process.stdout.read(chunk_bytes)
                if not data:
                    break
                data = remainder + data
                usable = len(data) - (len(data) % frame_bytes)
                if usable < frame_bytes:
                    remainder = data
                    continue
                remainder = data[usable:]
                samples = np.frombuffer(data[:usable], dtype=np.int16)
                stereo = samples.reshape(-1, 2)
                mono = stereo.mean(axis=1).astype(np.float32) / 32768.0
                if self._audio_callback:
                    self._audio_callback(mono)
        except (OSError, ValueError) as e:
            log.debug("_read_audio error: %s", e)

    def _read_status_socket(self):
        """Read JSON status from Dream's Unix domain socket."""
        for _ in range(50):
            if self._stop_event.is_set():
                return
            if os.path.exists(self._socket_path):
                break
            time.sleep(0.1)
        else:
            log.warning("Dream status socket did not appear: %s",
                        self._socket_path)
            return

        sock = None
        for _ in range(10):
            if self._stop_event.is_set():
                return
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self._socket_path)
                break
            except (OSError, ConnectionRefusedError) as e:
                log.debug("Status socket connect attempt failed: %s", e)
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                time.sleep(0.5)

        if sock is None:
            log.warning("Could not connect to Dream status socket")
            return

        log.info("Connected to Dream status socket")
        try:
            sock.settimeout(2.0)
            buf = b""
            while not self._stop_event.is_set():
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                # Cap buffer to prevent unbounded growth
                if len(buf) > _MAX_STATUS_BUF:
                    buf = buf[-_MAX_STATUS_BUF:]
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._parse_json_status(line)
        except OSError as e:
            log.debug("Status socket error: %s", e)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _parse_json_status(self, raw):
        """Parse a JSON status line from Dream 2.2."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return

        log.debug("Dream raw JSON: %s", raw.strip())

        st = data.get("status", {})
        sync_chars = []
        sync_detail = {}
        for key in _SYNC_KEYS:
            val = st.get(key, -1)
            if val == 0:
                sync_chars.append("O")
                sync_detail[key] = "O"
            elif val in (1, 2):
                sync_chars.append("*")
                sync_detail[key] = "*"
            else:
                sync_chars.append("-")
                sync_detail[key] = "-"
        sync = "".join(sync_chars)

        signal_info = data.get("signal", {})
        snr = signal_info.get("snr_db", 0.0)
        signal = st.get("fac", -1) == 0

        mode_info = data.get("mode", {})
        mode = _ROBUSTNESS_MODES.get(mode_info.get("robustness", -1), "?")
        sdc_qam = _QAM_MODES.get(mode_info.get("sdc_qam"), "")
        msc_qam = _QAM_MODES.get(mode_info.get("msc_qam"), "")

        # Extract service info — prefer first audio service, fall back to first service
        label = text = audio_mode = country = language = ""
        bitrate = 0.0
        services = data.get("service_list", [])
        chosen = None
        for svc in services:
            if svc.get("is_audio", False):
                chosen = svc
                break
        if chosen is None and services:
            chosen = services[0]
        if chosen is not None:
            label, text, bitrate, audio_mode, language, country = _extract_service_info(chosen)

        with self._lock:
            self.status["sync"] = sync
            self.status["signal"] = signal
            self.status["snr"] = snr
            self.status["label"] = label
            self.status["text"] = text
            self.status["bitrate"] = bitrate
            self.status["mode"] = mode
            self.status["country"] = country
            self.status["language"] = language
            self.status["audio_mode"] = audio_mode
            self.status["sync_detail"] = sync_detail
            self.status["sdc_qam"] = sdc_qam
            self.status["msc_qam"] = msc_qam

    def _drain_stderr(self):
        """Drain stderr to prevent pipe blocking."""
        try:
            for raw_line in self._process.stderr:
                log.debug("Dream stderr: %s",
                          raw_line.decode(errors="replace").rstrip())
        except (OSError, ValueError):
            pass

    def get_status(self):
        """Return a copy of the current DRM status dict."""
        with self._lock:
            return dict(self.status)

    def stop(self):
        """Stop the Dream subprocess and clean up."""
        self._stop_event.set()
        with self._lock:
            proc = self._process
            self._process = None
        if proc is not None:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        for t in (self._reader_thread, self._stderr_thread, self._socket_thread):
            if t is not None:
                t.join(timeout=2)
        self._reader_thread = None
        self._stderr_thread = None
        self._socket_thread = None
        self._audio_callback = None
        if self._socket_path:
            try:
                os.unlink(self._socket_path)
            except OSError:
                pass
            self._socket_path = None
        if self._socket_dir:
            try:
                os.rmdir(self._socket_dir)
            except OSError:
                pass
            self._socket_dir = None
        with self._lock:
            self.status = _default_status()
