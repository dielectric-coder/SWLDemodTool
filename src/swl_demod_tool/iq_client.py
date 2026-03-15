"""TCP client for receiving IQ samples from Elad Spectrum IQ server."""

import logging
import socket
import struct
import threading
import numpy as np

log = logging.getLogger(__name__)

HEADER_SIZE = 16
HEADER_MAGIC = b"ELAD"


class IQClient:
    def __init__(self, host="localhost", port=4533):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        self.sample_rate = 0
        self.format_bits = 0
        self._thread = None
        self._running = False
        self._callback = None
        self._lock = threading.Lock()

    def connect(self):
        """Connect to IQ server and read header."""
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
            self.sock.settimeout(10.0)
            # Read 16-byte header
            header = self._recv_exact(HEADER_SIZE)
            if header is None or header[:4] != HEADER_MAGIC:
                self.disconnect()
                return False
            magic, self.sample_rate, self.format_bits, _ = struct.unpack(
                "<4sIII", header
            )
            if self.sample_rate == 0:
                log.error("Server reported zero sample rate")
                self.disconnect()
                return False
            self.connected = True
            return True
        except (OSError, TimeoutError):
            self.connected = False
            return False

    def disconnect(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._lock:
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
                self.sock = None
            self.connected = False

    def start_streaming(self, callback):
        """Start receiving IQ data in a background thread.

        callback(iq_array): called with numpy complex64 array of IQ samples.
        """
        if not self.connected:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._callback = callback
        self._running = True
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()

    def _receive_loop(self):
        # Read in chunks matching the USB buffer: 12288 bytes = 1536 IQ pairs
        chunk_size = 12288
        while self._running:
            data = self._recv_exact(chunk_size)
            if data is None:
                self.connected = False
                break
            if self._callback:
                # Convert 32-bit signed int IQ pairs to normalized complex64
                samples = np.frombuffer(data, dtype=np.int32)
                n_pairs = len(samples) // 2
                scale = np.float32(1.0 / 2147483648.0)  # 1 / 2^31
                i_samples = samples[0::2].astype(np.float32) * scale
                q_samples = samples[1::2].astype(np.float32) * scale
                iq = i_samples + 1j * q_samples
                self._callback(iq[:n_pairs])

    def _recv_exact(self, n):
        """Receive exactly n bytes from socket."""
        data = bytearray()
        while len(data) < n:
            with self._lock:
                if not self.sock:
                    return None
                sock = self.sock
            try:
                chunk = sock.recv(n - len(data))
                if not chunk:
                    return None
                data.extend(chunk)
            except (OSError, TimeoutError):
                return None
        return bytes(data)
