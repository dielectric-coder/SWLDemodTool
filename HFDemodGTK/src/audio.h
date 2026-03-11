/* audio.h — Audio output with lock-free ring buffer. */

#ifndef AUDIO_H
#define AUDIO_H

#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pulse/simple.h>

#define AUDIO_RATE       48000
#define AUDIO_BLOCK_SIZE 1024
#define AUDIO_BUF_SECS   1

typedef struct {
    /* Ring buffer */
    float      *buffer;
    int         buf_len;
    atomic_int  write_pos;
    atomic_int  read_pos;

    /* PulseAudio */
    pa_simple  *pa;
    bool        running;
    pthread_t   thread;

    /* Stats */
    atomic_int  underruns;
    float       level;          /* recent RMS level */
} audio_output_t;

/* Open audio output. device can be NULL for default. Returns 0 on success. */
int audio_open(audio_output_t *a, const char *device);

/* Write samples to ring buffer. Called from IQ/DSP thread. */
void audio_write(audio_output_t *a, const float *samples, int count);

/* Get buffer fill fraction (0.0 - 1.0). */
float audio_fill(audio_output_t *a);

/* Close audio output. */
void audio_close(audio_output_t *a);

#endif
