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

In `audio.py`, a lock-free ring buffer (single-writer/single-reader) sits between the DSP thread and the `sounddevice` output callback. When the callback finds the buffer empty, it fills the output array with zeros (silence). This is graceful degradation: silence instead of a crash, but the user hears a brief dropout.

### Why a Ring Buffer Helps

The ring buffer acts as a shock absorber. If it holds, say, 50 ms of audio, the DSP thread can be up to 50 ms late without causing an audible glitch. A larger buffer increases tolerance for jitter at the cost of higher latency.

## HFDemodGTK-Specific Notes

### DRM Binary Path Resolution

The C/GTK4 port cannot use CWD-relative paths reliably because the working directory depends on how the binary is launched. Instead, `drm_find_binary()` resolves the executable's own path via `/proc/self/exe` + `realpath()`, then searches relative to it:

1. `<exe_dir>/../DRM/dream-2.2/dream`
2. `<exe_dir>/../../DRM/dream-2.2/dream`
3. `<exe_dir>/../../../DRM/dream-2.2/dream`
4. `PATH` lookup via `which dream`

This handles the common development layout where the binary is in `HFDemodGTK/build/` and Dream is in a sibling `DRM/` directory at the repository root or above.

### DRM FIR Decimation Buffer

The decimation filter uses a 128-element circular buffer (not 127) with modulo-128 indexing. Using a power-of-two buffer size avoids off-by-one aliasing where position 0 would collide with the last tap in a 127-element buffer with `% 127`.

### Font Detection for Bar Characters

The GTK4 UI uses PangoCairo to detect whether the MesloLGS NF font family is available at runtime. If found, Unicode block characters (▏▎▍▌▋▊▉█) are used for volume/audio/S-meter bars. Otherwise, ASCII `#` characters are used as a fallback to avoid rendering artifacts with fonts that lack these glyphs.

### PulseAudio vs sounddevice

HFDemodGTK uses the PulseAudio simple API (`libpulse-simple`) directly instead of sounddevice/PortAudio. A dedicated pthread pulls samples from the ring buffer and writes to PulseAudio in blocking mode. This avoids the callback-based model and simplifies error handling.
