"""Audio output via sounddevice with a lock-free ring buffer."""

import numpy as np
import sounddevice as sd


class AudioOutput:
    """Continuous audio output stream with a lock-free ring buffer.

    Audio chunks are pushed via write(). The stream callback pulls from the buffer.
    If the buffer underruns, silence is output.

    The ring buffer reserves one slot to distinguish full from empty:
    capacity = buf_len - 1.  No lock is needed because there is exactly one
    writer (the DSP thread) and one reader (the audio callback thread), and
    the position indices are only updated by their owning side.
    """

    def __init__(self, sample_rate=48000, block_size=1024, buffer_seconds=1.0):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self._stream = None

        # Ring buffer (one extra slot so full != empty)
        buf_len = int(sample_rate * buffer_seconds) + 1
        self._buffer = np.zeros(buf_len, dtype=np.float32)
        self._write_pos = 0
        self._read_pos = 0
        self._buf_len = buf_len
        self._underrun_count = 0
        self._overflow_count = 0

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

    @property
    def _capacity(self):
        """Usable capacity (one slot reserved to distinguish full/empty)."""
        return self._buf_len - 1

    def write(self, samples):
        """Push audio samples into the ring buffer (single-writer, lock-free)."""
        n = len(samples)
        if n == 0:
            return

        available = self._capacity - self._buffered()
        if n > available:
            # Overflow: advance read pointer to make room, dropping oldest
            # samples.  Then write what fits (up to capacity).
            if n > self._capacity:
                samples = samples[-self._capacity:]
                n = self._capacity
            # Advance read pointer to free exactly n slots
            drop = n - available
            self._read_pos = (self._read_pos + drop) % self._buf_len
            self._overflow_count += 1

        wp = self._write_pos
        if wp + n <= self._buf_len:
            self._buffer[wp:wp + n] = samples
        else:
            first = self._buf_len - wp
            self._buffer[wp:] = samples[:first]
            self._buffer[:n - first] = samples[first:]
        self._write_pos = (wp + n) % self._buf_len

    def _buffered(self):
        """Return number of samples in the buffer (safe from any thread)."""
        diff = self._write_pos - self._read_pos
        if diff < 0:
            diff += self._buf_len
        return diff

    def _audio_callback(self, outdata, frames, time_info, status):
        """sounddevice callback — pull from ring buffer (lock-free)."""
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
            outdata[:, 0] = 0.0
            self._underrun_count += 1

    @property
    def is_running(self):
        return self._stream is not None and self._stream.active

    @property
    def buffer_fill(self):
        """Return buffer fill level as fraction (0..1)."""
        return self._buffered() / self._capacity

    @property
    def underruns(self):
        return self._underrun_count
