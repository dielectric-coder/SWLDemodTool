#include "waterfall.h"
#include "spectrum.h"  /* SPEC_MARGIN_LEFT, SPEC_MARGIN_RIGHT */
#include <stdlib.h>
#include <string.h>
#include <math.h>

void waterfall_init(waterfall_state_t *w, Renderer *r) {
    memset(w, 0, sizeof(*w));
    w->r = r;
    w->num_lines = WATERFALL_LINES;
    w->ref_level = -30.0f;
    w->dynamic_range = 120.0f;
    w->sample_rate = 192000;
}

static void ensure_texture(waterfall_state_t *w, int fft_size) {
    if (w->texture_ready && w->fft_size == fft_size) return;

    w->fft_size = fft_size;
    w->current_row = 0;

    /* Create R32F texture for dB values */
    glBindTexture(GL_TEXTURE_2D, w->r->waterfall_texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R32F, fft_size, w->num_lines,
                 0, GL_RED, GL_FLOAT, NULL);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_REPEAT);

    /* Clear texture to zero */
    float *zeros = calloc(fft_size * w->num_lines, sizeof(float));
    if (zeros) {
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, fft_size, w->num_lines,
                        GL_RED, GL_FLOAT, zeros);
        free(zeros);
    }

    glBindTexture(GL_TEXTURE_2D, 0);
    w->texture_ready = true;
}

void waterfall_push_line(waterfall_state_t *w, const float *spectrum_db, int fft_size) {
    ensure_texture(w, fft_size);

    /* Normalize dB values to 0.0-1.0 range */
    float *normalized = malloc(fft_size * sizeof(float));
    if (!normalized) return;

    float min_db = w->ref_level - w->dynamic_range;
    float range = w->dynamic_range;
    if (range < 1.0f) range = 1.0f;

    for (int i = 0; i < fft_size; i++) {
        float db = spectrum_db[i];
        if (db < min_db) db = min_db;
        if (db > w->ref_level) db = w->ref_level;
        normalized[i] = (db - min_db) / range;
    }

    /* Upload one row to the circular texture */
    glBindTexture(GL_TEXTURE_2D, w->r->waterfall_texture);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, w->current_row, fft_size, 1,
                    GL_RED, GL_FLOAT, normalized);
    glBindTexture(GL_TEXTURE_2D, 0);

    free(normalized);

    w->current_row = (w->current_row + 1) % w->num_lines;
}

void waterfall_render(waterfall_state_t *w, float split,
                      int win_width, int win_height) {
    if (!w->texture_ready) return;

    Renderer *r = w->r;

    /* Compute NDC coordinates for the waterfall area.
     * Align horizontally with spectrum plot margins.
     * NDC: x from -1 (left) to +1 (right), y from -1 (bottom) to +1 (top). */
    float wf_left_ndc  = 2.0f * SPEC_MARGIN_LEFT / win_width - 1.0f;
    float wf_right_ndc = 1.0f - 2.0f * SPEC_MARGIN_RIGHT / win_width;
    float wf_top_ndc   = 1.0f - 2.0f * split;
    float wf_bot_ndc   = -1.0f;

    /* Quad: pos(2) + uv(2) per vertex, 6 vertices (2 triangles) */
    float quad[] = {
        /* pos x,y              uv u,v */
        wf_left_ndc,  wf_top_ndc, 0.0f, 0.0f,
        wf_right_ndc, wf_top_ndc, 1.0f, 0.0f,
        wf_right_ndc, wf_bot_ndc, 1.0f, 1.0f,
        wf_left_ndc,  wf_top_ndc, 0.0f, 0.0f,
        wf_right_ndc, wf_bot_ndc, 1.0f, 1.0f,
        wf_left_ndc,  wf_bot_ndc, 0.0f, 1.0f,
    };

    glBindVertexArray(r->waterfall_vao);
    glBindBuffer(GL_ARRAY_BUFFER, r->waterfall_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof(quad), quad, GL_DYNAMIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float), (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * sizeof(float),
                          (void *)(2 * sizeof(float)));

    glUseProgram(r->waterfall_program);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, r->waterfall_texture);
    glUniform1i(r->u_waterfall_tex, 0);

    /* Row offset: point to the newest row (just written, before increment) */
    float row_offset = (float)(w->current_row - 1) / (float)w->num_lines;
    glUniform1f(r->u_row_offset, row_offset);

    glDrawArrays(GL_TRIANGLES, 0, 6);

    glBindTexture(GL_TEXTURE_2D, 0);
    glBindVertexArray(0);

    (void)win_height;
}

void waterfall_destroy(waterfall_state_t *w) {
    (void)w;
}
