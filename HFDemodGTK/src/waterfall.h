#ifndef WATERFALL_H
#define WATERFALL_H

#include "renderer.h"
#include <stdbool.h>

#define WATERFALL_LINES 512

typedef struct {
    Renderer *r;
    int fft_size;
    int num_lines;
    int current_row;        /* circular buffer write pointer */
    bool texture_ready;

    /* Display range (must match spectrum) */
    float ref_level;
    float dynamic_range;

    /* Frequency info */
    double center_freq_hz;
    uint32_t sample_rate;
} waterfall_state_t;

/* Initialize waterfall renderer. */
void waterfall_init(waterfall_state_t *w, Renderer *r);

/* Push a new spectrum line into the waterfall.
 * Normalizes dB values and uploads a row to the texture. */
void waterfall_push_line(waterfall_state_t *w, const float *spectrum_db, int fft_size);

/* Render the waterfall quad.
 * split: fraction of window height used by spectrum (waterfall starts below). */
void waterfall_render(waterfall_state_t *w, float split,
                      int win_width, int win_height);

void waterfall_destroy(waterfall_state_t *w);

#endif
