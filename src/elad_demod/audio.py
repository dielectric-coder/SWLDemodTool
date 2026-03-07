"""Audio output via sounddevice."""

import threading
import numpy as np
import sounddevice as sd


class AudioOutput:
    """Continuous audio output stream with a ring buffer.

    Audio chunks are pushed via write(). The stream callback pulls from the buffer.
    If the buffer underruns, silence is output.
    """

    def __init__(self, sample_rate=48000, block_size=1024, buffer_seconds=1.0):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self._stream = None

        # Ring buffer
        buf_len = int(sample_rate * buffer_seconds)
        self._buffer = np.zeros(buf_len, dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0
        self._buf_len = buf_len
        self._lock = threading.Lock()
        self._underrun_count = 0

    def start(self, device=None):
        """Start the audio output stream."""
        if self._stream is not None:
            return

        dev = None if device == "default" else device
        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            blocksize=self.block_size,
            channels=1,
            dtype="float32",
            device=dev,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self):
        """Stop the audio output stream."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def write(self, samples):
        """Push audio samples into the ring buffer."""
        n = len(samples)
        if n == 0:
            return

        with self._lock:
            # Check available space
            available = self._buf_len - self._buffered()
            if n > available:
                # Drop oldest to make room
                drop = n - available
                self._read_pos = (self._read_pos + drop) % self._buf_len

            # Write into ring buffer
            wp = self._write_pos
            if wp + n <= self._buf_len:
                self._buffer[wp:wp + n] = samples
            else:
                first = self._buf_len - wp
                self._buffer[wp:] = samples[:first]
                self._buffer[:n - first] = samples[first:]
            self._write_pos = (wp + n) % self._buf_len

    def _buffered(self):
        """Return number of samples in the buffer. Caller must hold lock."""
        diff = self._write_pos - self._read_pos
        if diff < 0:
            diff += self._buf_len
        return diff

    def _audio_callback(self, outdata, frames, time_info, status):
        """sounddevice callback — pull from ring buffer."""
        with self._lock:
            available = self._buffered()
            if available >= frames:
                rp = self._read_pos
                if rp + frames <= self._buf_len:
                    outdata[:, 0] = self._buffer[rp:rp + frames]
                else:
                    first = self._buf_len - rp
                    outdata[:first, 0] = self._buffer[rp:]
                    outdata[first:, 0] = self._buffer[:frames - first]
                self._read_pos = (rp + frames) % self._buf_len
            else:
                # Underrun: output silence
                outdata[:, 0] = 0.0
                self._underrun_count += 1

    @property
    def is_running(self):
        return self._stream is not None and self._stream.active

    @property
    def buffer_fill(self):
        """Return buffer fill level as fraction (0..1)."""
        with self._lock:
            return self._buffered() / self._buf_len

    @property
    def underruns(self):
        return self._underrun_count
