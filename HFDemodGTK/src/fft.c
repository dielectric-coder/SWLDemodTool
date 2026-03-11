#include "fft.h"
#include <fftw3.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

static void generate_window(float *window, int size) {
    const float a0 = 0.35875f;
    const float a1 = 0.48829f;
    const float a2 = 0.14128f;
    const float a3 = 0.01168f;
    const float pi = 3.14159265358979323846f;

    for (int i = 0; i < size; i++) {
        float x = (float)i / (float)(size - 1);
        window[i] = a0 - a1 * cosf(2.0f * pi * x)
                       + a2 * cosf(4.0f * pi * x)
                       - a3 * cosf(6.0f * pi * x);
    }
}

int fft_init(fft_state_t *f, int fft_size) {
    memset(f, 0, sizeof(*f));
    f->fft_size = fft_size;

    f->fft_in  = fftwf_malloc(sizeof(float) * fft_size * 2);
    f->fft_out = fftwf_malloc(sizeof(float) * fft_size * 2);
    if (!f->fft_in || !f->fft_out) return -1;

    f->plan = fftwf_plan_dft_1d(fft_size,
                                 (fftwf_complex *)f->fft_in,
                                 (fftwf_complex *)f->fft_out,
                                 FFTW_FORWARD, FFTW_MEASURE);
    if (!f->plan) return -1;

    f->window = malloc(sizeof(float) * fft_size);
    if (!f->window) return -1;
    generate_window(f->window, fft_size);

    f->spectrum_db    = calloc(fft_size, sizeof(float));
    f->spectrum_accum = calloc(fft_size, sizeof(float));
    f->front_buf      = calloc(fft_size, sizeof(float));
    f->back_buf       = calloc(fft_size, sizeof(float));
    if (!f->spectrum_db || !f->spectrum_accum || !f->front_buf || !f->back_buf)
        return -1;

    pthread_mutex_init(&f->mutex, NULL);
    return 0;
}

/* Convert 32-bit signed integer to float [-1.0, 1.0] */
static inline float convert_sample(const uint8_t *data) {
    int32_t value = (int32_t)(data[0] | (data[1] << 8) |
                              (data[2] << 16) | (data[3] << 24));
    return (float)value / 2147483648.0f;
}

void fft_process(fft_state_t *f, const uint8_t *data, int length) {
    const int bytes_per_sample = 8; /* 4 bytes I + 4 bytes Q */
    int num_samples = length / bytes_per_sample;

    for (int i = 0; i < num_samples; i++) {
        const uint8_t *s = data + i * bytes_per_sample;
        float i_val = convert_sample(s);
        float q_val = convert_sample(s + 4);

        int idx = f->sample_count;
        float w = f->window[idx];
        f->fft_in[idx * 2]     = i_val * w;
        f->fft_in[idx * 2 + 1] = q_val * w;

        f->sample_count++;

        if (f->sample_count >= f->fft_size) {
            /* Execute FFT */
            fftwf_execute((fftwf_plan)f->plan);

            /* Convert to dB with DC-center shift and accumulate */
            int half = f->fft_size / 2;
            for (int j = 0; j < f->fft_size; j++) {
                int src = (j + half) % f->fft_size;
                float re = f->fft_out[src * 2];
                float im = f->fft_out[src * 2 + 1];
                float mag = sqrtf(re * re + im * im) / f->fft_size;
                if (mag < 1e-10f) mag = 1e-10f;
                f->spectrum_accum[j] += 20.0f * log10f(mag);
            }
            f->avg_count++;

            if (f->avg_count >= SPECTRUM_AVERAGING) {
                for (int j = 0; j < f->fft_size; j++) {
                    f->spectrum_db[j] = f->spectrum_accum[j] / SPECTRUM_AVERAGING;
                    f->spectrum_accum[j] = 0.0f;
                }
                f->avg_count = 0;

                /* Publish to back buffer */
                pthread_mutex_lock(&f->mutex);
                memcpy(f->back_buf, f->spectrum_db, sizeof(float) * f->fft_size);
                f->new_frame = true;
                pthread_mutex_unlock(&f->mutex);
            }

            f->sample_count = 0;
        }
    }
}

bool fft_swap(fft_state_t *f) {
    pthread_mutex_lock(&f->mutex);
    if (!f->new_frame) {
        pthread_mutex_unlock(&f->mutex);
        return false;
    }
    /* Swap front and back */
    float *tmp = f->front_buf;
    f->front_buf = f->back_buf;
    f->back_buf = tmp;
    f->new_frame = false;
    pthread_mutex_unlock(&f->mutex);
    return true;
}

void fft_destroy(fft_state_t *f) {
    if (f->plan) fftwf_destroy_plan((fftwf_plan)f->plan);
    if (f->fft_in)  fftwf_free(f->fft_in);
    if (f->fft_out) fftwf_free(f->fft_out);
    free(f->window);
    free(f->spectrum_db);
    free(f->spectrum_accum);
    free(f->front_buf);
    free(f->back_buf);
    pthread_mutex_destroy(&f->mutex);
}
