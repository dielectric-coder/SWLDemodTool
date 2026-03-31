"""TCP client for receiving IQ samples from Elad Spectrum IQ server."""

import logging
import queue
import socket
import struct
import threading
import numpy as np

log = logging.getLogger(__name__)

HEADER_SIZE = 16
HEADER_MAGIC = b"ELAD"

# Receive buffer: 4 MB absorbs ~2.7s at 1.5 MB/s (192 kHz IQ)
_SO_RCVBUF = 4 * 1024 * 1024

# Max queued chunks before dropping (bounds memory usage)
_QUEUE_MAX = 256  # ~16s of data at 12288 B/chunk


class IQClient:
    def __init__(self, host="localhost", port=4533):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        self.sample_rate = 0
        self.format_bits = 0
        self._recv_thread = None
        self._proc_thread = None
        self._running = False
        self._callback = None
        self._lock = threading.Lock()
        self._queue = queue.Queue(maxsize=_QUEUE_MAX)

    def connect(self):
        """Connect to IQ server and read header."""
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, _SO_RCVBUF)
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
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2)
        if self._proc_thread and self._proc_thread.is_alive():
            # Unblock process loop waiting on queue
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            self._proc_thread.join(timeout=2)
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
        """Start receiving IQ data in background threads.

        callback(iq_array): called with numpy complex64 array of IQ samples.
        Receive and processing run on separate threads so DSP never stalls
        the socket, preventing TCP backpressure disconnects.
        """
        if not self.connected:
            return
        if self._recv_thread is not None and self._recv_thread.is_alive():
            return
        self._callback = callback
        self._running = True
        # Clear any stale data
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._proc_thread = threading.Thread(target=self._process_loop, daemon=True)
        self._recv_thread.start()
        self._proc_thread.start()

    def _receive_loop(self):
        """Read IQ chunks from socket and enqueue; never blocks on DSP."""
        chunk_size = 12288
        while self._running:
            data = self._recv_exact(chunk_size)
            if data is None:
                self.connected = False
                break
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                # Processing can't keep up — drop oldest chunk
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(data)
                except queue.Full:
                    pass
        # Signal processing thread to stop
        self._queue.put(None)

    def _process_loop(self):
        """Dequeue IQ chunks and invoke callback (DSP runs here)."""
        scale = np.float32(1.0 / 2147483648.0)  # 1 / 2^31
        while self._running:
            try:
                data = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if data is None:
                break
            if self._callback:
                samples = np.frombuffer(data, dtype=np.int32)
                n_pairs = len(samples) // 2
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
