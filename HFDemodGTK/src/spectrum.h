#ifndef SPECTRUM_H
#define SPECTRUM_H

#include "renderer.h"
#include <stdbool.h>
#include <stdint.h>

/* Layout margins in pixels */
#define SPEC_MARGIN_LEFT   60
#define SPEC_MARGIN_RIGHT  10
#define SPEC_MARGIN_TOP    10
#define SPEC_MARGIN_BOTTOM 45

typedef struct {
    Renderer *r;
    int win_width, win_height;
    float split;              /* 0.0-1.0: fraction of window for spectrum (top) */

    /* Display range */
    float ref_level;          /* top of scale in dB (e.g., -30) */
    float dynamic_range;      /* dB range displayed (e.g., 120) */

    /* Frequency info */
    double center_freq_hz;
    uint32_t sample_rate;

    /* Peak hold */
    float *peak_hold;
    int peak_hold_size;
    bool peak_hold_enabled;
    float peak_decay;         /* dB per frame decay */
} spectrum_state_t;

/* Initialize spectrum renderer. split = fraction for spectrum area. */
void spectrum_init(spectrum_state_t *s, Renderer *r, float split);

/* Update and render spectrum plot.
 * spectrum_db: FFT_SIZE floats of dB values (DC-centered).
 * Called each frame from main thread. */
void spectrum_render(spectrum_state_t *s, const float *spectrum_db, int fft_size,
                     int win_width, int win_height);

void spectrum_destroy(spectrum_state_t *s);

#endif
