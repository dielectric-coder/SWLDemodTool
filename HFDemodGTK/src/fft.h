#ifndef FFT_H
#define FFT_H

#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>

#define FFT_SIZE 4096
#define SPECTRUM_AVERAGING 3

typedef struct {
    int fft_size;
    int sample_count;

    /* FFTW data (float precision) */
    float *fft_in;      /* interleaved complex: [re,im,re,im,...] */
    float *fft_out;     /* interleaved complex output */
    void  *plan;        /* fftwf_plan */

    /* Blackman-Harris window */
    float *window;

    /* Output spectrum in dB (DC-centered, fft_size floats) */
    float *spectrum_db;

    /* Averaging accumulator */
    float *spectrum_accum;
    int avg_count;

    /* Double buffer for thread-safe handoff */
    float *front_buf;   /* read by main thread */
    float *back_buf;    /* written by FFT thread */
    pthread_mutex_t mutex;
    bool new_frame;     /* set when back_buf has new data */
} fft_state_t;

/* Initialize FFT processor. Returns 0 on success. */
int fft_init(fft_state_t *f, int fft_size);

/* Process raw IQ data (32-bit signed int pairs).
 * Called from receive thread. */
void fft_process(fft_state_t *f, const uint8_t *data, int length);

/* Swap buffers. Call from main thread. Returns true if new data available.
 * After return, front_buf contains the latest spectrum. */
bool fft_swap(fft_state_t *f);

/* Clean up. */
void fft_destroy(fft_state_t *f);

#endif
