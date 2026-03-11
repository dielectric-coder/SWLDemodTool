/* audio.c — Audio output with lock-free ring buffer and PulseAudio. */

#include "audio.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <pthread.h>
#include <pulse/simple.h>
#include <pulse/error.h>

static void *audio_thread_func(void *arg) {
    audio_output_t *a = (audio_output_t *)arg;
    float block[AUDIO_BLOCK_SIZE];

    while (a->running) {
        int rd = atomic_load(&a->read_pos);
        int wr = atomic_load(&a->write_pos);

        int avail = (wr - rd + a->buf_len) % a->buf_len;

        if (avail < AUDIO_BLOCK_SIZE) {
            /* Underrun: output silence */
            memset(block, 0, sizeof(block));
            atomic_fetch_add(&a->underruns, 1);
        } else {
            /* Read from ring buffer */
            float rms_sum = 0.0f;
            for (int i = 0; i < AUDIO_BLOCK_SIZE; i++) {
                block[i] = a->buffer[(rd + i) % a->buf_len];
                rms_sum += block[i] * block[i];
            }
            int new_rd = (rd + AUDIO_BLOCK_SIZE) % a->buf_len;
            atomic_store(&a->read_pos, new_rd);
            a->level = sqrtf(rms_sum / AUDIO_BLOCK_SIZE);
        }

        /* Write to PulseAudio */
        int error;
        pa_simple_write(a->pa, block, sizeof(block), &error);
    }

    return NULL;
}

int audio_open(audio_output_t *a, const char *device) {
    memset(a, 0, sizeof(*a));

    a->buf_len = AUDIO_RATE * AUDIO_BUF_SECS + 1;
    a->buffer = calloc(a->buf_len, sizeof(float));
    if (!a->buffer) return -1;

    /* PulseAudio setup */
    pa_sample_spec ss = {
        .format = PA_SAMPLE_FLOAT32LE,
        .rate = AUDIO_RATE,
        .channels = 1
    };

    pa_buffer_attr attr = {
        .maxlength = (uint32_t)-1,
        .tlength = AUDIO_BLOCK_SIZE * sizeof(float) * 2,
        .prebuf = (uint32_t)-1,
        .minreq = (uint32_t)-1,
        .fragsize = (uint32_t)-1
    };

    int error;
    a->pa = pa_simple_new(NULL, "HFDemodGTK", PA_STREAM_PLAYBACK,
                           device, "Demodulated Audio",
                           &ss, NULL, &attr, &error);
    if (!a->pa) {
        fprintf(stderr, "PulseAudio: %s\n", pa_strerror(error));
        free(a->buffer);
        a->buffer = NULL;
        return -1;
    }

    a->running = true;
    if (pthread_create(&a->thread, NULL, audio_thread_func, a) != 0) {
        pa_simple_free(a->pa);
        free(a->buffer);
        return -1;
    }

    return 0;
}

void audio_write(audio_output_t *a, const float *samples, int count) {
    if (!a->buffer || !a->running) return;

    int wr = atomic_load(&a->write_pos);
    int rd = atomic_load(&a->read_pos);
    int capacity = a->buf_len - 1;
    int used = (wr - rd + a->buf_len) % a->buf_len;
    int space = capacity - used;

    if (count > space) {
        /* Overflow: advance read pointer to make room */
        int drop = count - space;
        int new_rd = (rd + drop) % a->buf_len;
        atomic_store(&a->read_pos, new_rd);
    }

    for (int i = 0; i < count; i++) {
        a->buffer[(wr + i) % a->buf_len] = samples[i];
    }
    atomic_store(&a->write_pos, (wr + count) % a->buf_len);
}

float audio_fill(audio_output_t *a) {
    if (!a->buffer) return 0.0f;
    int wr = atomic_load(&a->write_pos);
    int rd = atomic_load(&a->read_pos);
    int used = (wr - rd + a->buf_len) % a->buf_len;
    return (float)used / (float)(a->buf_len - 1);
}

void audio_close(audio_output_t *a) {
    if (!a->running) return;
    a->running = false;
    pthread_join(a->thread, NULL);

    if (a->pa) {
        pa_simple_drain(a->pa, NULL);
        pa_simple_free(a->pa);
        a->pa = NULL;
    }

    free(a->buffer);
    a->buffer = NULL;
}
