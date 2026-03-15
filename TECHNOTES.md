# Technical Notes

## Buffer Underruns

A buffer underrun (also called underflow) occurs when the audio output device requests the next chunk of samples to play, but the ring buffer is empty.

### Audio Playback Pipeline

1. The DSP thread decodes IQ data into audio samples and writes them into a ring buffer
2. The sound card's hardware timer fires at a fixed rate and invokes the audio callback
3. The callback must supply N samples immediately — the sound card will not wait

An underrun occurs when step 3 fires but step 1 has not produced enough data. The result is a click, pop, or brief silence.

### Common Causes

- **DSP too slow**: CPU spike, garbage collection pause, or thread scheduling delay
- **Network jitter**: IQ data arrives late from the TCP stream
- **Thread starvation**: the producer thread is preempted by other work

### How This Project Handles It

In `audio.py`, a lock-free ring buffer (single-writer/single-reader) sits between the DSP thread and the `sounddevice` output callback. When the callback finds the buffer empty, it fills the output array with zeros (silence). This is graceful degradation: silence instead of a crash, but the user hears a brief dropout. When the buffer is full on write, the oldest *input* samples are dropped (rather than advancing the reader position), which preserves the lock-free single-writer/single-reader invariant.

### Why a Ring Buffer Helps

The ring buffer acts as a shock absorber. If it holds, say, 50 ms of audio, the DSP thread can be up to 50 ms late without causing an audible glitch. A larger buffer increases tolerance for jitter at the cost of higher latency.
