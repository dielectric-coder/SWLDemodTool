"""ZeroMQ SUB client for receiving IQ samples from Elad Spectrum IQ server."""

import logging
import queue
import struct
import threading
import numpy as np
import zmq

log = logging.getLogger(__name__)

HEADER_SIZE = 16
HEADER_MAGIC = b"ELAD"

# Max queued chunks before dropping (bounds memory usage)
_QUEUE_MAX = 256  # ~16s of data at 12288 B/chunk

# SUB high-water mark — matches server PUB HWM
_SUB_HWM = 64

# Poll timeout for shutdown checks (ms)
_POLL_TIMEOUT_MS = 2000

# Consecutive poll timeouts before declaring "disconnected"
_DISCONNECT_TIMEOUTS = 5

# IQ chunk size (must match USB_BUFFER_SIZE on server)
_CHUNK_SIZE = 12288


class IQClient:
    def __init__(self, host="localhost", port=4533):
        self.host = host
        self.port = port
        self.connected = False
        self.sample_rate = 0
        self.format_bits = 0
        self._zmq_ctx = zmq.Context()
        self._zmq_sub = None
        self._recv_thread = None
        self._proc_thread = None
        self._running = False
        self._callback = None
        self._queue = queue.Queue(maxsize=_QUEUE_MAX)

    def connect(self):
        """Connect to IQ server via ZeroMQ SUB."""
        if self._zmq_sub:
            try:
                self._zmq_sub.close()
            except zmq.ZMQError:
                pass

        self._zmq_sub = self._zmq_ctx.socket(zmq.SUB)
        self._zmq_sub.setsockopt(zmq.SUBSCRIBE, b"")
        self._zmq_sub.setsockopt(zmq.RCVHWM, _SUB_HWM)
        self._zmq_sub.setsockopt(zmq.LINGER, 0)
        self._zmq_sub.setsockopt(zmq.RECONNECT_IVL, 1000)

        endpoint = f"tcp://{self.host}:{self.port}"
        try:
            self._zmq_sub.connect(endpoint)
        except zmq.ZMQError as e:
            log.error("ZMQ connect failed: %s", e)
            self._zmq_sub.close()
            self._zmq_sub = None
            return False

        # ZMQ connect is async — wait briefly for first message to get header
        poller = zmq.Poller()
        poller.register(self._zmq_sub, zmq.POLLIN)
        for _ in range(5):  # Up to 10 seconds
            events = poller.poll(timeout=2000)
            if events:
                try:
                    frames = self._zmq_sub.recv_multipart(zmq.NOBLOCK)
                    if len(frames) >= 1 and len(frames[0]) == HEADER_SIZE:
                        header = frames[0]
                        if header[:4] == HEADER_MAGIC:
                            magic, self.sample_rate, self.format_bits, _ = \
                                struct.unpack("<4sIII", header)
                            if self.sample_rate == 0:
                                log.error("Server reported zero sample rate")
                                continue
                            self.connected = True
                            return True
                except zmq.ZMQError:
                    continue

        log.error("Timeout waiting for IQ server")
        return False

    def disconnect(self):
        self._running = False
        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=3)
        if self._proc_thread and self._proc_thread.is_alive():
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            self._proc_thread.join(timeout=2)
        if self._zmq_sub:
            try:
                self._zmq_sub.close()
            except zmq.ZMQError:
                pass
            self._zmq_sub = None
        self.connected = False

    def start_streaming(self, callback):
        """Start receiving IQ data in background threads.

        callback(iq_array): called with numpy complex64 array of IQ samples.
        Receive and processing run on separate threads so DSP never stalls
        the ZMQ socket.
        """
        if not self.connected:
            return
        if self._recv_thread is not None and self._recv_thread.is_alive():
            return
        self._callback = callback
        self._running = True
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
        """Receive IQ chunks from ZMQ and enqueue; never blocks on DSP."""
        poller = zmq.Poller()
        poller.register(self._zmq_sub, zmq.POLLIN)
        timeouts = 0

        while self._running:
            events = poller.poll(timeout=_POLL_TIMEOUT_MS)
            if not events:
                timeouts += 1
                if timeouts >= _DISCONNECT_TIMEOUTS and self.connected:
                    self.connected = False
                    log.warning("IQ client: no data, disconnected")
                continue

            timeouts = 0

            try:
                frames = self._zmq_sub.recv_multipart(zmq.NOBLOCK)
            except zmq.ZMQError:
                continue

            if len(frames) < 2:
                continue
            header_frame, data_frame = frames[0], frames[1]

            if len(header_frame) != HEADER_SIZE:
                continue
            if header_frame[:4] != HEADER_MAGIC:
                continue
            if len(data_frame) != _CHUNK_SIZE:
                continue

            # Update sample rate from header
            _, sr, fmt, _ = struct.unpack("<4sIII", header_frame)
            if sr > 0:
                self.sample_rate = sr
                self.format_bits = fmt

            if not self.connected:
                self.connected = True
                log.info("IQ client: receiving (rate=%d Hz)", sr)

            try:
                self._queue.put_nowait(bytes(data_frame))
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(bytes(data_frame))
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
