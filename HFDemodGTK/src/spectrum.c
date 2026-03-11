#include "spectrum.h"
#include "text.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

void spectrum_init(spectrum_state_t *s, Renderer *r, float split) {
    memset(s, 0, sizeof(*s));
    s->r = r;
    s->split = split;
    s->ref_level = -30.0f;
    s->dynamic_range = 120.0f;
    s->center_freq_hz = 0.0;
    s->sample_rate = 192000;
    s->peak_hold_enabled = false;
    s->peak_decay = 0.5f;
}

/* Build orthographic projection matrix (column-major) */
static void ortho(float *m, float l, float r, float b, float t) {
    memset(m, 0, 16 * sizeof(float));
    m[0]  = 2.0f / (r - l);
    m[5]  = 2.0f / (t - b);
    m[10] = -1.0f;
    m[12] = -(r + l) / (r - l);
    m[13] = -(t + b) / (t - b);
    m[15] = 1.0f;
}

/* Upload vertex data to a VAO/VBO with pos(2f) + alpha(1f) stride */
static void upload_lines(GLuint vao, GLuint vbo, const float *data, int count) {
    glBindVertexArray(vao);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, count * 3 * sizeof(float), data, GL_DYNAMIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 3 * sizeof(float), (void *)0);
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 1, GL_FLOAT, GL_FALSE, 3 * sizeof(float),
                          (void *)(2 * sizeof(float)));
    glBindVertexArray(0);
}

/* Upload text vertices (pos only, 2f stride) */
static void upload_text(GLuint vao, GLuint vbo, const float *data, int verts) {
    glBindVertexArray(vao);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, verts * 2 * sizeof(float), data, GL_DYNAMIC_DRAW);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 2 * sizeof(float), (void *)0);
    /* Set alpha attribute to constant 1.0 */
    glDisableVertexAttribArray(1);
    glVertexAttrib1f(1, 1.0f);
    glBindVertexArray(0);
}

void spectrum_render(spectrum_state_t *s, const float *spectrum_db, int fft_size,
                     int win_width, int win_height) {
    Renderer *r = s->r;
    s->win_width = win_width;
    s->win_height = win_height;

    /* win_height is already the spectrum area height (caller passes win_height * split) */
    float plot_left   = SPEC_MARGIN_LEFT;
    float plot_right  = win_width - SPEC_MARGIN_RIGHT;
    float plot_top    = SPEC_MARGIN_TOP;
    float plot_bottom = win_height - SPEC_MARGIN_BOTTOM;
    float plot_w = plot_right - plot_left;
    float plot_h = plot_bottom - plot_top;

    if (plot_w <= 0 || plot_h <= 0) return;

    glUseProgram(r->spectrum_program);

    /* Orthographic projection: pixel coordinates, y-down */
    float mvp[16];
    ortho(mvp, 0, (float)win_width, (float)win_height, 0);
    glUniformMatrix4fv(r->u_mvp, 1, GL_FALSE, mvp);

    /* --- Grid lines --- */
    {
        float grid[512 * 3]; /* pos(2) + alpha(1) per vertex */
        int gc = 0;

        /* Horizontal dB lines */
        float db_step = 10.0f;
        if (s->dynamic_range > 80) db_step = 20.0f;

        float min_db = s->ref_level - s->dynamic_range;
        for (float db = min_db; db <= s->ref_level; db += db_step) {
            float y = plot_top + (1.0f - (db - min_db) / s->dynamic_range) * plot_h;
            if (gc + 2 <= 512) {
                grid[gc * 3] = plot_left;  grid[gc * 3 + 1] = y; grid[gc * 3 + 2] = 1.0f; gc++;
                grid[gc * 3] = plot_right; grid[gc * 3 + 1] = y; grid[gc * 3 + 2] = 1.0f; gc++;
            }
        }

        /* Vertical frequency lines (10 divisions) */
        for (int i = 0; i <= 10; i++) {
            float x = plot_left + (float)i / 10.0f * plot_w;
            if (gc + 2 <= 512) {
                grid[gc * 3] = x; grid[gc * 3 + 1] = plot_top;    grid[gc * 3 + 2] = 1.0f; gc++;
                grid[gc * 3] = x; grid[gc * 3 + 1] = plot_bottom; grid[gc * 3 + 2] = 1.0f; gc++;
            }
        }

        upload_lines(r->grid_vao, r->grid_vbo, grid, gc);
        glUniform4f(r->u_color, 0.25f, 0.25f, 0.3f, 1.0f);
        glBindVertexArray(r->grid_vao);
        glDrawArrays(GL_LINES, 0, gc);
    }

    /* --- Spectrum trace --- */
    if (spectrum_db && fft_size > 0) {
        float *trace = malloc(fft_size * 3 * sizeof(float));
        float *smoothed = malloc(fft_size * sizeof(float));
        if (trace && smoothed) {
            float min_db = s->ref_level - s->dynamic_range;

            /* 5-tap moving average to smooth the trace */
            const int radius = 2;
            for (int i = 0; i < fft_size; i++) {
                float sum = 0.0f;
                int count = 0;
                for (int j = i - radius; j <= i + radius; j++) {
                    if (j >= 0 && j < fft_size) {
                        sum += spectrum_db[j];
                        count++;
                    }
                }
                smoothed[i] = sum / count;
            }

            for (int i = 0; i < fft_size; i++) {
                float x = plot_left + (float)i / (fft_size - 1) * plot_w;
                float db = smoothed[i];
                if (db < min_db) db = min_db;
                if (db > s->ref_level) db = s->ref_level;
                float y = plot_top + (1.0f - (db - min_db) / s->dynamic_range) * plot_h;

                trace[i * 3]     = x;
                trace[i * 3 + 1] = y;
                trace[i * 3 + 2] = 1.0f;
            }

            /* Gradient fill under the trace (TRIANGLE_STRIP: top=0.35 alpha, bottom=0) */
            float *fill = malloc(fft_size * 2 * 3 * sizeof(float));
            if (fill) {
                for (int i = 0; i < fft_size; i++) {
                    float x = trace[i * 3];
                    float y_top = trace[i * 3 + 1];
                    /* Top vertex: at trace line, semi-transparent */
                    fill[i * 6]     = x;
                    fill[i * 6 + 1] = y_top;
                    fill[i * 6 + 2] = 0.35f;
                    /* Bottom vertex: at plot bottom, fully transparent */
                    fill[i * 6 + 3] = x;
                    fill[i * 6 + 4] = plot_bottom;
                    fill[i * 6 + 5] = 0.0f;
                }
                upload_lines(r->spectrum_vao, r->spectrum_vbo, fill, fft_size * 2);
                glUniform4f(r->u_color, 0.0f, 0.9f, 1.0f, 1.0f); /* cyan */
                glBindVertexArray(r->spectrum_vao);
                glDrawArrays(GL_TRIANGLE_STRIP, 0, fft_size * 2);
                free(fill);
            }

            /* Spectrum trace line on top */
            upload_lines(r->spectrum_vao, r->spectrum_vbo, trace, fft_size);
            glUniform4f(r->u_color, 0.0f, 0.9f, 1.0f, 1.0f); /* cyan */
            glBindVertexArray(r->spectrum_vao);
            glDrawArrays(GL_LINE_STRIP, 0, fft_size);

            free(trace);
            free(smoothed);
        } else {
            free(trace);
            free(smoothed);
        }

        /* Peak hold */
        if (s->peak_hold_enabled) {
            if (!s->peak_hold || s->peak_hold_size != fft_size) {
                free(s->peak_hold);
                s->peak_hold = malloc(fft_size * sizeof(float));
                s->peak_hold_size = fft_size;
                if (s->peak_hold)
                    memcpy(s->peak_hold, spectrum_db, fft_size * sizeof(float));
            }
            if (s->peak_hold) {
                float *ptrace = malloc(fft_size * 3 * sizeof(float));
                if (ptrace) {
                    float min_db = s->ref_level - s->dynamic_range;
                    for (int i = 0; i < fft_size; i++) {
                        if (spectrum_db[i] > s->peak_hold[i])
                            s->peak_hold[i] = spectrum_db[i];
                        else
                            s->peak_hold[i] -= s->peak_decay;

                        float db = s->peak_hold[i];
                        if (db < min_db) db = min_db;
                        if (db > s->ref_level) db = s->ref_level;
                        float x = plot_left + (float)i / (fft_size - 1) * plot_w;
                        float y = plot_top + (1.0f - (db - min_db) / s->dynamic_range) * plot_h;
                        ptrace[i * 3]     = x;
                        ptrace[i * 3 + 1] = y;
                        ptrace[i * 3 + 2] = 1.0f;
                    }
                    upload_lines(r->spectrum_vao, r->spectrum_vbo, ptrace, fft_size);
                    glUniform4f(r->u_color, 1.0f, 1.0f, 0.0f, 0.6f); /* yellow */
                    glBindVertexArray(r->spectrum_vao);
                    glDrawArrays(GL_LINE_STRIP, 0, fft_size);
                    free(ptrace);
                }
            }
        }
    }

    /* --- Axis labels --- */
    {
        float text_verts[4096];
        int tv = 0;
        char label[32];
        float font_size = 16.0f;
        float min_db = s->ref_level - s->dynamic_range;

        /* dB labels on left */
        float db_step = 10.0f;
        if (s->dynamic_range > 80) db_step = 20.0f;

        for (float db = min_db; db <= s->ref_level; db += db_step) {
            float y = plot_top + (1.0f - (db - min_db) / s->dynamic_range) * plot_h;
            snprintf(label, sizeof(label), "%d", (int)db);
            float tw = text_width(label, font_size);
            int n = text_build(label, plot_left - tw - 5, y - font_size * 0.5f,
                               font_size, text_verts + tv * 2, 4096 - tv);
            tv += n;
        }

        /* Frequency labels on bottom */
        double half_bw = (double)s->sample_rate / 2.0;
        for (int i = 0; i <= 10; i++) {
            float x = plot_left + (float)i / 10.0f * plot_w;
            double freq = (s->center_freq_hz - half_bw) +
                          (double)i / 10.0 * (double)s->sample_rate;

            if (s->center_freq_hz > 0) {
                /* Absolute frequency in kHz (standard for HF/SWL) */
                double freq_khz = freq / 1e3;
                snprintf(label, sizeof(label), "%.1f", freq_khz);
            } else {
                /* No tuned frequency: show relative offset in kHz */
                double offset_khz = (freq - s->center_freq_hz) / 1e3;
                if (offset_khz > 0)
                    snprintf(label, sizeof(label), "+%.0f", offset_khz);
                else
                    snprintf(label, sizeof(label), "%.0f", offset_khz);
            }

            float tw = text_width(label, font_size);
            int n = text_build(label, x - tw * 0.5f, plot_bottom + 16,
                               font_size, text_verts + tv * 2, 4096 - tv);
            tv += n;
        }

        if (tv > 0) {
            upload_text(r->text_vao, r->text_vbo, text_verts, tv);
            glUniform4f(r->u_color, 0.7f, 0.7f, 0.75f, 1.0f);
            glBindVertexArray(r->text_vao);
            glDrawArrays(GL_LINES, 0, tv);
        }
    }

    /* --- Plot border --- */
    {
        float border[] = {
            plot_left,  plot_top,    1.0f,
            plot_right, plot_top,    1.0f,
            plot_right, plot_top,    1.0f,
            plot_right, plot_bottom, 1.0f,
            plot_right, plot_bottom, 1.0f,
            plot_left,  plot_bottom, 1.0f,
            plot_left,  plot_bottom, 1.0f,
            plot_left,  plot_top,    1.0f,
        };
        upload_lines(r->grid_vao, r->grid_vbo, border, 8);
        glUniform4f(r->u_color, 0.4f, 0.4f, 0.5f, 1.0f);
        glBindVertexArray(r->grid_vao);
        glDrawArrays(GL_LINES, 0, 8);
    }

    glBindVertexArray(0);
}

void spectrum_destroy(spectrum_state_t *s) {
    free(s->peak_hold);
    s->peak_hold = NULL;
}
