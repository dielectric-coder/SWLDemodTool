/* main.c — GTK4 application for HFDemodGTK.
 *
 * Layout: GtkGLArea (spectrum+waterfall) on top, GTK4 control panel on bottom.
 * Bottom panel mimics the TUI status layout with monospace level bars. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libgen.h>
#include <limits.h>
#include <unistd.h>
#include <math.h>
#include <time.h>
#include <epoxy/gl.h>
#include <gtk/gtk.h>
#include <pango/pangocairo.h>

#include "app_state.h"
#include "config.h"
#include "text.h"

#define PANEL_MIN_HEIGHT 280

static AppState app_state;

/* ── Bar rendering helper ─────────────────────────────────────── */

/* Build a level bar string like [████████        ] using Unicode blocks.
 * fraction: 0.0-1.0, width: total character width inside brackets. */
/* Check at startup if MesloLGS NF is available (supports block chars) */
static int use_block_chars = -1; /* -1=unknown, 0=ascii, 1=unicode */

static void detect_block_chars(void) {
    if (use_block_chars >= 0) return;
    PangoFontMap *fm = pango_cairo_font_map_get_default();
    PangoFontDescription *desc = pango_font_description_from_string("MesloLGS NF 12");
    PangoContext *ctx = pango_font_map_create_context(fm);
    PangoFont *font = pango_font_map_load_font(fm, ctx, desc);
    use_block_chars = (font != NULL) ? 1 : 0;
    if (font) g_object_unref(font);
    g_object_unref(ctx);
    pango_font_description_free(desc);
    if (use_block_chars)
        fprintf(stderr, "UI: Using MesloLGS NF with Unicode block chars\n");
    else
        fprintf(stderr, "UI: MesloLGS NF not found, using ASCII bars\n");
}

static void make_bar(char *out, int out_size, float fraction, int width) {
    if (fraction < 0.0f) fraction = 0.0f;
    if (fraction > 1.0f) fraction = 1.0f;

    detect_block_chars();

    if (use_block_chars) {
        /* Unicode eighth blocks: ▏▎▍▌▋▊▉█ (U+258F..U+2588) */
        static const char *eighths[] = {
            " ",
            "\xe2\x96\x8f", "\xe2\x96\x8e", "\xe2\x96\x8d",
            "\xe2\x96\x8c", "\xe2\x96\x8b", "\xe2\x96\x8a",
            "\xe2\x96\x89", "\xe2\x96\x88"
        };
        float steps = fraction * width;
        int full = (int)steps;
        int partial = (int)((steps - full) * 8.0f);
        if (partial > 8) partial = 8;

        int pos = 0;
        out[pos++] = '[';
        for (int i = 0; i < width && pos < out_size - 5; i++) {
            const char *b;
            if (i < full)
                b = eighths[8];
            else if (i == full && partial > 0)
                b = eighths[partial];
            else
                b = " ";
            int blen = (int)strlen(b);
            if (pos + blen < out_size - 2) {
                memcpy(out + pos, b, blen);
                pos += blen;
            }
        }
        out[pos++] = ']';
        out[pos] = '\0';
    } else {
        /* ASCII fallback */
        int filled = (int)(fraction * width + 0.5f);
        if (filled > width) filled = width;
        int pos = 0;
        out[pos++] = '[';
        for (int i = 0; i < width && pos < out_size - 3; i++)
            out[pos++] = (i < filled) ? '#' : ' ';
        out[pos++] = ']';
        out[pos] = '\0';
    }
}

/* S-meter conversion: raw 0-30 value to S-unit string */
static const char *s_meter_str(int raw) {
    if (raw <= 0) return "S0";
    if (raw <= 3) return "S1";
    if (raw <= 6) return "S2";
    if (raw <= 9) return "S3";
    if (raw <= 12) return "S4";
    if (raw <= 15) return "S5";
    if (raw <= 18) return "S6";
    if (raw <= 21) return "S7";
    if (raw <= 24) return "S8";
    if (raw <= 27) return "S9";
    if (raw <= 30) return "S9+10";
    return "S9+20";
}

/* ── IQ data callback (called from IQ receive thread) ─────────── */

static void on_iq_data(const uint8_t *data, int length, void *user) {
    AppState *s = (AppState *)user;

    /* Feed FFT for spectrum display */
    fft_process(&s->fft, data, length);

    if (s->demod.mode == MODE_DRM) {
        /* DRM mode: pipe IQ to Dream subprocess */
        int num_samples = length / 8;
        float *iq_float = malloc(num_samples * 2 * sizeof(float));
        if (iq_float) {
            for (int i = 0; i < num_samples; i++) {
                const uint8_t *p = data + i * 8;
                int32_t i_raw = (int32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24));
                int32_t q_raw = (int32_t)(p[4] | (p[5] << 8) | (p[6] << 16) | (p[7] << 24));
                iq_float[i * 2]     = (float)i_raw / 2147483648.0f;
                iq_float[i * 2 + 1] = (float)q_raw / 2147483648.0f;
            }
            drm_write_iq(&s->drm, iq_float, num_samples);
            free(iq_float);
        }
    } else {
        /* Normal demod: process IQ -> audio */
        float audio_buf[16384];
        int audio_count = demod_process(&s->demod, data, length,
                                        audio_buf, sizeof(audio_buf) / sizeof(float));
        if (audio_count > 0 && s->audio_open)
            audio_write(&s->audio, audio_buf, audio_count);
    }
}

/* ── DRM audio callback (called from Dream reader thread) ─────── */

static void on_drm_audio(const float *samples, int count, void *user) {
    AppState *s = (AppState *)user;
    if (s->audio_open)
        audio_write(&s->audio, samples, count);
}

/* ── GL callbacks ─────────────────────────────────────────────── */

static void on_realize(GtkGLArea *area, gpointer data) {
    (void)data;
    AppState *s = &app_state;

    gtk_gl_area_make_current(area);
    if (gtk_gl_area_get_error(area) != NULL) return;

    if (renderer_init(&s->renderer, s->shader_dir) != 0) {
        fprintf(stderr, "Failed to initialize renderer\n");
        return;
    }

    s->split = 0.4f;
    spectrum_init(&s->spectrum, &s->renderer, s->split);
    waterfall_init(&s->waterfall, &s->renderer);
    fft_init(&s->fft, FFT_SIZE);
    text_init();

    s->gl_initialized = 1;

    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glEnable(GL_LINE_SMOOTH);
}

static gboolean on_render(GtkGLArea *area, GdkGLContext *context, gpointer data) {
    (void)area; (void)context; (void)data;
    AppState *s = &app_state;

    if (!s->gl_initialized) return TRUE;

    int w = gtk_widget_get_width(GTK_WIDGET(area));
    int h = gtk_widget_get_height(GTK_WIDGET(area));
    int scale = gtk_widget_get_scale_factor(GTK_WIDGET(area));
    int fb_w = w * scale;
    int fb_h = h * scale;

    glViewport(0, 0, fb_w, fb_h);
    glClearColor(0.05f, 0.05f, 0.08f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);

    fft_swap(&s->fft);

    s->spectrum.center_freq_hz = s->frequency_hz;
    s->spectrum.sample_rate = s->iq_client.sample_rate ? s->iq_client.sample_rate : 192000;
    s->waterfall.center_freq_hz = s->frequency_hz;
    s->waterfall.sample_rate = s->spectrum.sample_rate;
    s->waterfall.ref_level = s->spectrum.ref_level;
    s->waterfall.dynamic_range = s->spectrum.dynamic_range;

    int hud_h = 30 * scale;
    int content_h = fb_h - hud_h;
    if (content_h < 1) content_h = 1;
    int spec_h = (int)(content_h * s->split);

    glViewport(0, content_h - spec_h, fb_w, spec_h);
    spectrum_render(&s->spectrum, s->fft.front_buf, s->fft.fft_size, fb_w, spec_h);

    waterfall_push_line(&s->waterfall, s->fft.front_buf, s->fft.fft_size);

    glViewport(0, 0, fb_w, content_h - spec_h);
    waterfall_render(&s->waterfall, 0.0f, fb_w, content_h - spec_h);

    /* HUD strip (centered, above spectrum) */
    {
        glViewport(0, content_h, fb_w, hud_h);
        glUseProgram(s->renderer.spectrum_program);
        float mvp[16];
        memset(mvp, 0, sizeof(mvp));
        mvp[0]  = 2.0f / fb_w;
        mvp[5]  = -2.0f / hud_h;
        mvp[10] = -1.0f;
        mvp[12] = -1.0f;
        mvp[13] = 1.0f;
        mvp[15] = 1.0f;
        glUniformMatrix4fv(s->renderer.u_mvp, 1, GL_FALSE, mvp);

        float text_verts[2048];
        char hud[128];
        float font_size = 20.0f;

        double freq_mhz = s->frequency_hz / 1e6;
        if (s->frequency_hz >= 1e6)
            snprintf(hud, sizeof(hud), "%.6f MHz  %s  BW:%d Hz",
                     freq_mhz, demod_mode_name(s->demod.mode), s->demod.bandwidth_hz);
        else if (s->frequency_hz > 0)
            snprintf(hud, sizeof(hud), "%.3f kHz  %s  BW:%d Hz",
                     s->frequency_hz / 1e3, demod_mode_name(s->demod.mode), s->demod.bandwidth_hz);
        else
            snprintf(hud, sizeof(hud), "%s  BW:%d Hz",
                     demod_mode_name(s->demod.mode), s->demod.bandwidth_hz);

        float tw = text_width(hud, font_size);
        float hud_x = ((float)fb_w - tw) * 0.5f;
        float hud_y = ((float)hud_h - font_size) * 0.5f;
        int tv = text_build(hud, hud_x, hud_y, font_size, text_verts, 1024);

        if (tv > 0) {
            glBindVertexArray(s->renderer.text_vao);
            glBindBuffer(GL_ARRAY_BUFFER, s->renderer.text_vbo);
            glBufferData(GL_ARRAY_BUFFER, tv * 2 * sizeof(float),
                         text_verts, GL_DYNAMIC_DRAW);
            glEnableVertexAttribArray(0);
            glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE,
                                  2 * sizeof(float), (void *)0);
            glDisableVertexAttribArray(1);
            glVertexAttrib1f(1, 1.0f);
            glUniform4f(s->renderer.u_color, 0.9f, 0.9f, 0.95f, 1.0f);
            glDrawArrays(GL_LINES, 0, tv);
            glBindVertexArray(0);
        }
    }

    return TRUE;
}

static void on_resize(GtkGLArea *area, int width, int height, gpointer data) {
    (void)area; (void)width; (void)height; (void)data;
}

/* ── Bandwidth limits per mode (min, max, step) ──────────────── */

static void bw_limits(demod_mode_t mode, int *bw_min, int *bw_max, int *step) {
    switch (mode) {
    case MODE_AM:
    case MODE_SAM:
    case MODE_SAM_U:
    case MODE_SAM_L:
        *bw_min = 4000; *bw_max = 10000; *step = 1000; break;
    case MODE_USB:
    case MODE_LSB:
        *bw_min = 1200; *bw_max = 3200; *step = 100; break;
    case MODE_CW_PLUS:
    case MODE_CW_MINUS:
        *bw_min = 100; *bw_max = 1000; *step = 50; break;
    case MODE_RTTY:
        *bw_min = 1200; *bw_max = 3200; *step = 100; break;
    case MODE_PSK31:
        *bw_min = 200; *bw_max = 1000; *step = 50; break;
    default:
        *bw_min = 100; *bw_max = 24000; *step = 500; break;
    }
}

/* ── GL area keyboard input ───────────────────────────────────── */

static gboolean on_key_pressed(GtkEventControllerKey *ctrl,
                                guint keyval, guint keycode,
                                GdkModifierType state, gpointer data) {
    (void)ctrl; (void)keycode; (void)data;
    AppState *s = &app_state;

    /* Escape: unfocus any entry widget */
    if (keyval == GDK_KEY_Escape) {
        GtkWidget *focus = gtk_window_get_focus(GTK_WINDOW(s->window));
        if (focus && GTK_IS_ENTRY(focus)) {
            gtk_widget_grab_focus(s->gl_area);
            return TRUE;
        }
        return FALSE;
    }

    /* Skip keyboard shortcuts when an entry has focus */
    GtkWidget *focus = gtk_window_get_focus(GTK_WINDOW(s->window));
    if (focus && GTK_IS_ENTRY(focus))
        return FALSE;

    switch (keyval) {
    case GDK_KEY_q:
    case GDK_KEY_Q:
        g_application_quit(G_APPLICATION(s->app));
        return TRUE;
    case GDK_KEY_Up:
        if (state & GDK_SHIFT_MASK)
            s->spectrum.ref_level += 5.0f;
        break;
    case GDK_KEY_Down:
        if (state & GDK_SHIFT_MASK)
            s->spectrum.ref_level -= 5.0f;
        break;
    case GDK_KEY_bracketright:
        s->split += 0.05f;
        if (s->split > 0.9f) s->split = 0.9f;
        s->spectrum.split = s->split;
        break;
    case GDK_KEY_bracketleft:
        s->split -= 0.05f;
        if (s->split < 0.1f) s->split = 0.1f;
        s->spectrum.split = s->split;
        break;
    case GDK_KEY_p:
    case GDK_KEY_P:
        s->spectrum.peak_hold_enabled = !s->spectrum.peak_hold_enabled;
        break;
    case GDK_KEY_N:  /* Shift+N: cycle NB OFF -> Low -> Med -> High -> OFF */
        s->demod.nb_threshold = (s->demod.nb_threshold + 1) % 4;
        gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(s->btn_nb),
                                     s->demod.nb_threshold != NB_OFF);
        break;
    case GDK_KEY_r:  /* r: toggle RIT on/off */
        s->rit_enabled = !s->rit_enabled;
        s->demod.rit_offset_hz = s->rit_enabled ? s->rit_offset_hz : 0.0;
        break;
    case GDK_KEY_plus:
    case GDK_KEY_equal:  /* +/= : RIT up 10 Hz */
        s->rit_offset_hz += 10.0;
        if (s->rit_enabled) s->demod.rit_offset_hz = s->rit_offset_hz;
        break;
    case GDK_KEY_minus:  /* - : RIT down 10 Hz */
        if (!(state & GDK_SHIFT_MASK)) {
            s->rit_offset_hz -= 10.0;
            if (s->rit_enabled) s->demod.rit_offset_hz = s->rit_offset_hz;
        } else {
            return FALSE;
        }
        break;
    case GDK_KEY_0:  /* 0: clear RIT offset */
        s->rit_offset_hz = 0.0;
        s->demod.rit_offset_hz = 0.0;
        break;
    case GDK_KEY_w: {  /* w: BW down, W (Shift+w): BW up */
        int bmin, bmax, bstep;
        bw_limits(s->demod.mode, &bmin, &bmax, &bstep);
        if (state & GDK_SHIFT_MASK) {
            int new_bw = s->demod.bandwidth_hz + bstep;
            if (new_bw > bmax) new_bw = bmax;
            demod_set_bandwidth(&s->demod, new_bw);
        } else {
            int new_bw = s->demod.bandwidth_hz - bstep;
            if (new_bw < bmin) new_bw = bmin;
            demod_set_bandwidth(&s->demod, new_bw);
        }
        break;
    }
    default:
        return FALSE;
    }

    gtk_widget_queue_draw(s->gl_area);
    return TRUE;
}

static gboolean on_scroll(GtkEventControllerScroll *ctrl,
                           double dx, double dy, gpointer data) {
    (void)ctrl; (void)dx; (void)data;
    AppState *s = &app_state;
    s->spectrum.dynamic_range += (float)dy * 5.0f;
    if (s->spectrum.dynamic_range < 20.0f) s->spectrum.dynamic_range = 20.0f;
    if (s->spectrum.dynamic_range > 200.0f) s->spectrum.dynamic_range = 200.0f;
    gtk_widget_queue_draw(s->gl_area);
    return TRUE;
}

/* ── Display update timer (100ms) ─────────────────────────────── */

static gboolean on_display_update(gpointer data) {
    AppState *s = (AppState *)data;

    if (s->gl_area)
        gtk_widget_queue_draw(s->gl_area);

    char buf[512];
    char bar[128];

    /* ── VFO bar ── */
    double freq_mhz = s->frequency_hz / 1e6;
    char rit_str[48] = "";
    if (s->rit_enabled)
        snprintf(rit_str, sizeof(rit_str), "    RIT: %+.0f Hz", s->rit_offset_hz);
    snprintf(buf, sizeof(buf), "  VFO: %s    Frequency: %.6f MHz    Mode: %s    BW: %d Hz%s",
             s->active_vfo == 0 ? "A" : "B",
             freq_mhz,
             demod_mode_name(s->demod.mode),
             s->demod.bandwidth_hz,
             rit_str);
    gtk_label_set_text(GTK_LABEL(s->lbl_vfo_bar), buf);

    /* ── Connection info ── */
    {
        const char *iq_dot  = s->iq_connected  ? "●" : "○";
        const char *cat_dot = s->cat_connected ? "●" : "○";
        const char *aud_dot = s->audio_open    ? "●" : "○";
        int sr = s->iq_client.sample_rate ? s->iq_client.sample_rate : 192000;
        snprintf(buf, sizeof(buf),
                 "  IQ %s %s:%d  %d Hz  32-bit IQ    CAT %s %s:%d    Audio %s %d Hz",
                 iq_dot, s->host, s->iq_port, sr,
                 cat_dot, s->host, s->cat_port,
                 aud_dot, AUDIO_RATE);
        gtk_label_set_text(GTK_LABEL(s->lbl_conn_info), buf);
    }

    /* ── Status lines — fixed columns: label at 6 chars right-padded ── */
    /* Col positions: 0..22 (label+bar+val), 24..33 (mid), 36.. (right) */
    {
        /* Line 1: Vol + NB + AGC */
        float vol_pct = s->demod.volume * 100.0f;
        make_bar(bar, sizeof(bar), s->demod.volume, 10);
        const char *nb_str;
        switch (s->demod.nb_threshold) {
        case NB_LOW:  nb_str = "Low";  break;
        case NB_MED:  nb_str = "Med";  break;
        case NB_HIGH: nb_str = "High"; break;
        default:      nb_str = "OFF";  break;
        }
        float agc_db = 20.0f * log10f(s->demod.agc_gain > 0 ? s->demod.agc_gain : 1.0f);
        snprintf(buf, sizeof(buf),
                 "   Vol: %s %3.0f%%    NB: %-4s    AGC: %s (%+.0f dB)",
                 bar, vol_pct, nb_str,
                 s->demod.agc_enabled ? "ON " : "OFF", agc_db);
        gtk_label_set_text(GTK_LABEL(s->lbl_status_1), buf);

        /* Line 2: Audio + DNR + Buf */
        float level_db = -120.0f;
        if (s->audio.level > 1e-10f)
            level_db = 20.0f * log10f(s->audio.level);
        float level_frac = (level_db + 60.0f) / 60.0f;
        make_bar(bar, sizeof(bar), level_frac, 10);
        const char *dnr_str;
        switch (s->demod.dnr_level) {
        case DNR_1: dnr_str = "1";   break;
        case DNR_2: dnr_str = "2";   break;
        case DNR_3: dnr_str = "3";   break;
        default:    dnr_str = "OFF"; break;
        }
        float buf_fill = audio_fill(&s->audio);
        char buf_bar[64];
        make_bar(buf_bar, sizeof(buf_bar), buf_fill, 10);
        snprintf(buf, sizeof(buf),
                 " Audio: %s %+4.0fdB   DNR: %-4s   Buf: %s %3.0f%%  Ur: %d",
                 bar, level_db, dnr_str,
                 buf_bar, buf_fill * 100.0f, atomic_load(&s->audio.underruns));
        gtk_label_set_text(GTK_LABEL(s->lbl_status_2), buf);

        /* Line 3: DNF + APF + S-meter + SNR */
        float s_frac = (float)s->s_meter_raw / 30.0f;
        char s_bar[64];
        make_bar(s_bar, sizeof(s_bar), s_frac, 10);
        snprintf(buf, sizeof(buf),
                 "   DNF: %-3s APF: %-4s          S: %s %-4s  SNR: %.1f dB",
                 s->demod.auto_notch ? "ON" : "OFF",
                 s->demod.apf_enabled ? "ON" : "OFF",
                 s_bar, s_meter_str(s->s_meter_raw), s->demod.snr_db);
        gtk_label_set_text(GTK_LABEL(s->lbl_status_3), buf);
    }

    /* ── Mode-specific info ── */
    {
        int show_mode_info = 1;
        switch (s->demod.mode) {
        case MODE_SAM:
        case MODE_SAM_U:
        case MODE_SAM_L: {
            double pll_hz = s->demod.pll_freq * AUDIO_SAMPLE_RATE / (2.0 * M_PI);
            snprintf(buf, sizeof(buf),
                     "   PLL Offset: %+7.1f Hz    SNR: %.0f dB",
                     pll_hz, s->demod.snr_db);
            break;
        }
        case MODE_USB:
        case MODE_LSB:
            snprintf(buf, sizeof(buf),
                     "   SNR: %.1f dB", s->demod.snr_db);
            break;
        case MODE_CW_PLUS:
        case MODE_CW_MINUS: {
            /* CW tuning bar: ±150 Hz range, 20 chars wide */
            float dev = s->demod.cw_peak_hz - CW_BFO_HZ;
            char tune_bar[32];
            int bar_w = 20;
            int center = bar_w / 2;
            int peak_pos = center + (int)(dev / 150.0f * center);
            if (peak_pos < 0) peak_pos = 0;
            if (peak_pos >= bar_w) peak_pos = bar_w - 1;
            int tpos = 0;
            tune_bar[tpos++] = '[';
            for (int i = 0; i < bar_w; i++) {
                if (i == center)
                    tune_bar[tpos++] = '|';
                else if (i == peak_pos && s->demod.cw_peak_hz > 0)
                    tune_bar[tpos++] = '#';
                else
                    tune_bar[tpos++] = '.';
            }
            tune_bar[tpos++] = ']';
            tune_bar[tpos] = '\0';

            if (s->demod.cw_peak_hz > 0)
                snprintf(buf, sizeof(buf),
                         "   Tune: %s %+7.1f Hz    SNR: %.0f dB    %.0f WPM",
                         tune_bar, dev, s->demod.cw_snr, s->demod.cw_wpm);
            else
                snprintf(buf, sizeof(buf),
                         "   Tune: %s  ---.- Hz    SNR: -- dB    -- WPM",
                         tune_bar);
            break;
        }
        case MODE_RTTY:
            snprintf(buf, sizeof(buf),
                     "   RTTY 45.45 Bd / 170 Hz shift    SNR: %.1f dB",
                     s->demod.snr_db);
            break;
        case MODE_PSK31:
            snprintf(buf, sizeof(buf),
                     "   BPSK31 31.25 Bd    SNR: %.1f dB",
                     s->demod.snr_db);
            break;
        default:
            show_mode_info = 0;
            break;
        }
        /* Append RIT info if enabled */
        if (s->rit_enabled) {
            char rit_info[64];
            snprintf(rit_info, sizeof(rit_info), "    RIT: %+.0f Hz", s->rit_offset_hz);
            if (show_mode_info) {
                size_t len = strlen(buf);
                snprintf(buf + len, sizeof(buf) - len, "%s", rit_info);
            } else {
                snprintf(buf, sizeof(buf), "   %s", rit_info);
                show_mode_info = 1;
            }
        }
        if (show_mode_info) {
            gtk_label_set_text(GTK_LABEL(s->lbl_mode_info), buf);
            gtk_widget_set_visible(s->lbl_mode_info, TRUE);
        } else {
            gtk_widget_set_visible(s->lbl_mode_info, FALSE);
        }
    }

    /* ── Decoded text ── */
    if (s->demod.mode == MODE_RTTY || s->demod.mode == MODE_PSK31) {
        gtk_label_set_text(GTK_LABEL(s->lbl_decoded_text), s->demod.decoded_text);
        gtk_widget_set_visible(s->lbl_decoded_text, TRUE);
    } else if (s->demod.mode == MODE_CW_PLUS || s->demod.mode == MODE_CW_MINUS) {
        gtk_label_set_text(GTK_LABEL(s->lbl_decoded_text), s->demod.morse_text);
        gtk_widget_set_visible(s->lbl_decoded_text, TRUE);
    } else {
        gtk_widget_set_visible(s->lbl_decoded_text, FALSE);
    }

    /* ── DRM status ── */
    if (s->demod.mode == MODE_DRM) {
        if (s->drm.status_valid) {
            /* Sync detail chars: 0=O(synced), 1/2=*(tracking), else - */
            char sync_chars[7];
            int sv[] = { s->drm.sync_io, s->drm.sync_time, s->drm.sync_frame,
                         s->drm.sync_fac, s->drm.sync_sdc, s->drm.sync_msc };
            for (int i = 0; i < 6; i++)
                sync_chars[i] = sv[i] == 0 ? 'O' : (sv[i] == 1 || sv[i] == 2) ? '*' : '-';
            sync_chars[6] = '\0';

            const char rob_chars[] = "ABCD";
            char rob = (s->drm.robustness >= 0 && s->drm.robustness <= 3)
                       ? rob_chars[s->drm.robustness] : '?';

            static const char *qam_names[] = { "4-QAM", "16-QAM", "64-QAM" };
            const char *sdc_qam = (s->drm.sdc_qam >= 0 && s->drm.sdc_qam <= 2)
                                  ? qam_names[s->drm.sdc_qam] : "?";
            const char *msc_qam = (s->drm.msc_qam >= 0 && s->drm.msc_qam <= 2)
                                  ? qam_names[s->drm.msc_qam] : "?";

            /* Line 1: Sync + SNR + Mode + QAM + Codec */
            int pos = snprintf(buf, sizeof(buf),
                     " Sync: io:%c time:%c frame:%c fac:%c sdc:%c msc:%c"
                     "    SNR: %.1f dB    Mode: %c    SDC: %s  MSC: %s",
                     sync_chars[0], sync_chars[1], sync_chars[2],
                     sync_chars[3], sync_chars[4], sync_chars[5],
                     s->drm.snr, rob, sdc_qam, msc_qam);
            if (s->drm.audio_codec[0])
                pos += snprintf(buf + pos, sizeof(buf) - pos,
                         "    Codec: %s", s->drm.audio_codec);
            /* Line 2: Station + bitrate + audio mode + country */
            if (s->drm.service_label[0])
                pos += snprintf(buf + pos, sizeof(buf) - pos,
                         "\n Station: %s", s->drm.service_label);
            if (s->drm.bitrate_kbps > 0)
                pos += snprintf(buf + pos, sizeof(buf) - pos,
                         "    %.1f kbps", s->drm.bitrate_kbps);
            if (s->drm.audio_mode[0])
                pos += snprintf(buf + pos, sizeof(buf) - pos,
                         "  %s", s->drm.audio_mode);
            if (s->drm.country[0])
                pos += snprintf(buf + pos, sizeof(buf) - pos,
                         "    %s", s->drm.country);
            if (s->drm.language[0])
                pos += snprintf(buf + pos, sizeof(buf) - pos,
                         "  (%s)", s->drm.language);
            /* Line 3: text message */
            if (s->drm.text_message[0])
                snprintf(buf + pos, sizeof(buf) - pos,
                         "\n %s", s->drm.text_message);

            gtk_label_set_text(GTK_LABEL(s->lbl_drm_status), buf);
            gtk_widget_set_visible(s->lbl_drm_status, TRUE);
        } else {
            gtk_label_set_text(GTK_LABEL(s->lbl_drm_status), " DRM: Waiting for Dream decoder...");
            gtk_widget_set_visible(s->lbl_drm_status, TRUE);
        }
    } else {
        gtk_widget_set_visible(s->lbl_drm_status, FALSE);
    }

    return G_SOURCE_CONTINUE;
}

/* ── CAT polling timer (1s) ───────────────────────────────────── */

static gboolean on_cat_poll(gpointer data) {
    AppState *s = (AppState *)data;

    if (!s->cat_connected) return G_SOURCE_CONTINUE;

    double freq;
    char mode[8];
    int bw;
    if (cat_client_read(&s->cat_client, &freq, mode, sizeof(mode), &bw)) {
        s->frequency_hz = freq;
        strncpy(s->mode, mode, sizeof(s->mode) - 1);
        if (bw > 0) s->bandwidth_hz = bw;
    }

    return G_SOURCE_CONTINUE;
}

/* ── Button callbacks ─────────────────────────────────────────── */

static void on_connect_clicked(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;

    const char *host = gtk_editable_get_text(GTK_EDITABLE(s->entry_host));
    int iq_port = atoi(gtk_editable_get_text(GTK_EDITABLE(s->entry_iq_port)));
    int cat_port = atoi(gtk_editable_get_text(GTK_EDITABLE(s->entry_cat_port)));

    if (iq_port <= 0) iq_port = 4533;
    if (cat_port <= 0) cat_port = 4532;

    if (!s->audio_open) {
        if (audio_open(&s->audio, NULL) == 0)
            s->audio_open = true;
    }

    if (!s->iq_connected) {
        if (iq_client_connect(&s->iq_client, host, iq_port) == 0) {
            s->iq_connected = true;
            iq_client_start(&s->iq_client, on_iq_data, s);
        }
    }

    if (!s->cat_connected) {
        if (cat_client_connect(&s->cat_client, host, cat_port) == 0) {
            s->cat_connected = true;
            cat_client_start(&s->cat_client);
        }
    }

    if (s->demod.mode == MODE_DRM && !s->drm.running) {
        drm_start(&s->drm, on_drm_audio, s);
    }
}

static void on_disconnect_clicked(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;

    if (s->drm.running) drm_stop(&s->drm);

    if (s->iq_connected) {
        iq_client_stop(&s->iq_client);
        s->iq_connected = false;
    }
    if (s->cat_connected) {
        cat_client_stop(&s->cat_client);
        s->cat_connected = false;
    }
    if (s->audio_open) {
        audio_close(&s->audio);
        s->audio_open = false;
    }
}

static void set_mode_button_active(AppState *s, demod_mode_t mode) {
    GtkWidget *buttons[] = {
        s->btn_am, s->btn_sam, s->btn_sam_u, s->btn_sam_l,
        s->btn_usb, s->btn_lsb,
        s->btn_cw_plus, s->btn_cw_minus,
        s->btn_rtty, s->btn_psk31, s->btn_drm
    };
    for (int i = 0; i < MODE_COUNT; i++) {
        g_signal_handlers_block_matched(buttons[i], G_SIGNAL_MATCH_DATA,
                                         0, 0, NULL, NULL, s);
        gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(buttons[i]), i == (int)mode);
        g_signal_handlers_unblock_matched(buttons[i], G_SIGNAL_MATCH_DATA,
                                           0, 0, NULL, NULL, s);
    }
}

static void on_mode_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    if (!gtk_toggle_button_get_active(btn)) return;

    demod_mode_t mode = MODE_AM;
    if (GTK_WIDGET(btn) == s->btn_am)        mode = MODE_AM;
    else if (GTK_WIDGET(btn) == s->btn_sam)       mode = MODE_SAM;
    else if (GTK_WIDGET(btn) == s->btn_sam_u)     mode = MODE_SAM_U;
    else if (GTK_WIDGET(btn) == s->btn_sam_l)     mode = MODE_SAM_L;
    else if (GTK_WIDGET(btn) == s->btn_usb)       mode = MODE_USB;
    else if (GTK_WIDGET(btn) == s->btn_lsb)       mode = MODE_LSB;
    else if (GTK_WIDGET(btn) == s->btn_cw_plus)   mode = MODE_CW_PLUS;
    else if (GTK_WIDGET(btn) == s->btn_cw_minus)  mode = MODE_CW_MINUS;
    else if (GTK_WIDGET(btn) == s->btn_rtty)      mode = MODE_RTTY;
    else if (GTK_WIDGET(btn) == s->btn_psk31)     mode = MODE_PSK31;
    else if (GTK_WIDGET(btn) == s->btn_drm)       mode = MODE_DRM;

    if (s->demod.mode == MODE_DRM && mode != MODE_DRM && s->drm.running)
        drm_stop(&s->drm);

    demod_set_mode(&s->demod, mode);

    if (mode == MODE_DRM && s->iq_connected && !s->drm.running)
        drm_start(&s->drm, on_drm_audio, s);

    set_mode_button_active(s, mode);
}

static void on_mute_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    s->demod.muted = gtk_toggle_button_get_active(btn);
}

static void on_agc_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    s->demod.agc_enabled = gtk_toggle_button_get_active(btn);
}

static void on_nb_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    if (gtk_toggle_button_get_active(btn)) {
        s->demod.nb_threshold = (s->demod.nb_threshold + 1) % 4;
        if (s->demod.nb_threshold == NB_OFF)
            s->demod.nb_threshold = NB_LOW;
    } else {
        s->demod.nb_threshold = NB_OFF;
    }
}

static void on_dnr_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    if (gtk_toggle_button_get_active(btn)) {
        s->demod.dnr_level = (s->demod.dnr_level + 1) % 4;
        if (s->demod.dnr_level == DNR_OFF)
            s->demod.dnr_level = DNR_1;
    } else {
        s->demod.dnr_level = DNR_OFF;
    }
}

static void on_notch_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    s->demod.auto_notch = gtk_toggle_button_get_active(btn);
}

static void on_peak_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    s->spectrum.peak_hold_enabled = gtk_toggle_button_get_active(btn);
    gtk_widget_queue_draw(s->gl_area);
}

static void on_apf_toggled(GtkToggleButton *btn, gpointer data) {
    AppState *s = (AppState *)data;
    s->demod.apf_enabled = gtk_toggle_button_get_active(btn);
}

static void on_volume_changed(GtkRange *range, gpointer data) {
    AppState *s = (AppState *)data;
    s->demod.volume = (float)gtk_range_get_value(range);
}

static void set_frequency(AppState *s, double freq_hz) {
    if (freq_hz < 100.0) return;
    s->frequency_hz = freq_hz;
    if (s->cat_connected)
        cat_client_set_frequency(&s->cat_client, freq_hz);
}

static void on_tune_up(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;
    set_frequency(s, s->frequency_hz + 1000.0);
}

static void on_tune_down(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;
    set_frequency(s, s->frequency_hz - 1000.0);
}

static void on_mid_up(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;
    set_frequency(s, s->frequency_hz + 100.0);
}

static void on_mid_down(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;
    set_frequency(s, s->frequency_hz - 100.0);
}

static void on_fine_up(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;
    set_frequency(s, s->frequency_hz + 10.0);
}

static void on_fine_down(GtkButton *btn, gpointer data) {
    (void)btn;
    AppState *s = (AppState *)data;
    set_frequency(s, s->frequency_hz - 10.0);
}

static void on_freq_entry_activate(GtkEntry *entry, gpointer data) {
    AppState *s = (AppState *)data;
    const char *text = gtk_editable_get_text(GTK_EDITABLE(entry));
    if (!text || !text[0]) return;

    char *endptr;
    double freq_khz = strtod(text, &endptr);
    if (endptr == text || freq_khz <= 0) return;

    set_frequency(s, freq_khz * 1000.0);

    char buf[32];
    snprintf(buf, sizeof(buf), "%.3f", freq_khz);
    gtk_editable_set_text(GTK_EDITABLE(entry), buf);
}

/* ── CSS styling ──────────────────────────────────────────────── */

static void load_css(void) {
    GtkCssProvider *provider = gtk_css_provider_new();
    gtk_css_provider_load_from_string(provider,
        "window { background-color: #0d0d1e; }"
        ".panel { background-color: #10101a; padding: 6px 8px; }"
        ".panel label { color: #b3cce6; font-family: 'MesloLGS NF', monospace; font-size: 14px; }"

        /* VFO info bar — mimics TUI's cyan-highlighted tuning bar */
        ".vfo-bar {"
        "  background-color: #0a1a2a; border: 1px solid #1a3a5a;"
        "  border-radius: 3px; padding: 4px 8px; margin: 2px 0;"
        "  font-size: 16px; font-weight: bold; color: #00ccdd;"
        "  font-family: 'MesloLGS NF', monospace;"
        "}"

        /* Connection info — dim, like TUI header area */
        ".conn-info { font-size: 13px; color: #778899; font-family: 'MesloLGS NF', monospace; }"

        /* Status lines — monospace, exact TUI-style */
        ".status-line {"
        "  font-size: 14px; color: #a0b8d0; font-family: 'MesloLGS NF', monospace;"
        "  padding: 1px 0;"
        "}"

        ".panel .mode-label { font-size: 18px; font-weight: bold; color: #ffcc00; }"
        ".panel .info-label { font-size: 13px; color: #99bbdd; font-family: 'MesloLGS NF', monospace; }"
        ".panel .decoded-text {"
        "  font-size: 15px; color: #66ff66; font-family: 'MesloLGS NF', monospace;"
        "  background-color: #0a1510; border: 1px solid #1a3a2a;"
        "  border-radius: 3px; padding: 4px 8px; margin: 2px 0;"
        "}"
        ".panel .drm-status {"
        "  font-size: 14px; color: #66ccff; font-family: 'MesloLGS NF', monospace;"
        "  background-color: #0a1520; border: 1px solid #1a3050;"
        "  border-radius: 3px; padding: 4px 8px; margin: 2px 0;"
        "}"
        ".panel .mode-info {"
        "  font-size: 14px; color: #ccdd88; font-family: 'MesloLGS NF', monospace;"
        "  padding: 2px 0;"
        "}"
        ".panel .status-label { font-size: 13px; color: #667788; }"

        ".panel button { "
        "  background-color: #1a1a2e; color: #ccc; border: 1px solid #334; "
        "  border-radius: 3px; padding: 3px 8px; font-size: 13px; "
        "  font-family: 'MesloLGS NF', monospace; min-height: 24px; }"
        ".panel button:hover { background-color: #252540; border-color: #558; }"
        ".panel button:checked { background-color: #2a4570; border-color: #4a7ab5; color: #fff; }"

        ".panel entry { "
        "  background-color: #1a1a2e; color: #ddeeff; border: 1px solid #334; "
        "  border-radius: 4px; font-family: 'MesloLGS NF', monospace; font-size: 13px; "
        "  padding: 2px 6px; min-height: 24px; }"

        ".panel scale { margin: 0 4px; }"
        ".panel scale trough { background-color: #1a1a2e; min-height: 8px; }"
        ".panel scale slider { background-color: #4a7ab5; padding: 6px; }"

        ".btn-sep { background-color: #1a2a3a; min-height: 1px; margin: 3px 0; }"
        ".section-label { font-size: 12px; color: #556677; margin-top: 4px; }"

        ".gl-area { background-color: #0d0d1e; }"
    );
    gtk_style_context_add_provider_for_display(
        gdk_display_get_default(),
        GTK_STYLE_PROVIDER(provider),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    g_object_unref(provider);
}

/* ── Helper to create a labeled section separator ─────────────── */

static void add_section_sep(GtkWidget *box) {
    GtkWidget *sep = gtk_separator_new(GTK_ORIENTATION_HORIZONTAL);
    gtk_widget_add_css_class(sep, "btn-sep");
    gtk_box_append(GTK_BOX(box), sep);
}

/* ── Application activate ─────────────────────────────────────── */

static void activate(GtkApplication *app, gpointer user_data) {
    (void)user_data;
    AppState *s = &app_state;

    load_css();

    /* Main window */
    s->window = gtk_application_window_new(app);
    gtk_window_set_title(GTK_WINDOW(s->window), "HFDemod GTK v" APP_VERSION);
    gtk_window_set_default_size(GTK_WINDOW(s->window), DEFAULT_WIDTH, DEFAULT_HEIGHT);

    /* Main vertical layout: GL area (top) + panel (bottom) */
    GtkWidget *vbox = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_window_set_child(GTK_WINDOW(s->window), vbox);

    /* GL Area for spectrum + waterfall */
    s->gl_area = gtk_gl_area_new();
    gtk_gl_area_set_required_version(GTK_GL_AREA(s->gl_area), 3, 3);
    gtk_widget_set_hexpand(s->gl_area, TRUE);
    gtk_widget_set_vexpand(s->gl_area, TRUE);
    gtk_widget_add_css_class(s->gl_area, "gl-area");
    gtk_widget_set_focusable(s->gl_area, TRUE);

    g_signal_connect(s->gl_area, "realize", G_CALLBACK(on_realize), NULL);
    g_signal_connect(s->gl_area, "render", G_CALLBACK(on_render), NULL);
    g_signal_connect(s->gl_area, "resize", G_CALLBACK(on_resize), NULL);

    gtk_box_append(GTK_BOX(vbox), s->gl_area);

    /* ── Bottom Panel ─────────────────────────────────────────── */

    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    gtk_widget_add_css_class(panel, "panel");
    gtk_widget_set_size_request(panel, -1, PANEL_MIN_HEIGHT);
    gtk_box_append(GTK_BOX(vbox), panel);

    /* ── Connection info (IQ/CAT/Audio) ── */
    s->lbl_conn_info = gtk_label_new("  IQ ○ --  CAT ○ --  Audio ○ --");
    gtk_widget_add_css_class(s->lbl_conn_info, "conn-info");
    gtk_widget_set_halign(s->lbl_conn_info, GTK_ALIGN_START);
    gtk_box_append(GTK_BOX(panel), s->lbl_conn_info);

    /* ── Host connection row ── */
    GtkWidget *host_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_widget_set_halign(host_row, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(panel), host_row);

    GtkWidget *host_label = gtk_label_new("HOST");
    gtk_widget_add_css_class(host_label, "section-label");
    gtk_box_append(GTK_BOX(host_row), host_label);

    s->entry_host = gtk_entry_new();
    gtk_editable_set_text(GTK_EDITABLE(s->entry_host), s->host);
    gtk_widget_set_size_request(s->entry_host, 100, -1);
    gtk_entry_set_placeholder_text(GTK_ENTRY(s->entry_host), "Host");
    gtk_box_append(GTK_BOX(host_row), s->entry_host);

    s->entry_iq_port = gtk_entry_new();
    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%d", s->iq_port);
    gtk_editable_set_text(GTK_EDITABLE(s->entry_iq_port), port_str);
    gtk_widget_set_size_request(s->entry_iq_port, 60, -1);
    gtk_box_append(GTK_BOX(host_row), s->entry_iq_port);

    s->entry_cat_port = gtk_entry_new();
    snprintf(port_str, sizeof(port_str), "%d", s->cat_port);
    gtk_editable_set_text(GTK_EDITABLE(s->entry_cat_port), port_str);
    gtk_widget_set_size_request(s->entry_cat_port, 60, -1);
    gtk_box_append(GTK_BOX(host_row), s->entry_cat_port);

    s->btn_connect    = gtk_button_new_with_label("CONNECT");
    s->btn_disconnect = gtk_button_new_with_label("DISCONNECT");
    gtk_box_append(GTK_BOX(host_row), s->btn_connect);
    gtk_box_append(GTK_BOX(host_row), s->btn_disconnect);

    g_signal_connect(s->btn_connect, "clicked", G_CALLBACK(on_connect_clicked), s);
    g_signal_connect(s->btn_disconnect, "clicked", G_CALLBACK(on_disconnect_clicked), s);

    /* ── VFO bar (Frequency/Mode/BW) ── */
    s->lbl_vfo_bar = gtk_label_new("  VFO: A    Frequency: 0.000000 MHz    Mode: AM    BW: 5000 Hz");
    gtk_widget_add_css_class(s->lbl_vfo_bar, "vfo-bar");
    gtk_widget_set_halign(s->lbl_vfo_bar, GTK_ALIGN_FILL);
    gtk_label_set_xalign(GTK_LABEL(s->lbl_vfo_bar), 0.5f);
    gtk_box_append(GTK_BOX(panel), s->lbl_vfo_bar);

    /* ── Tune controls ── */
    GtkWidget *tune_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_widget_set_halign(tune_row, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(panel), tune_row);

    GtkWidget *tune_label = gtk_label_new("TUNE");
    gtk_widget_add_css_class(tune_label, "section-label");
    gtk_box_append(GTK_BOX(tune_row), tune_label);

    s->btn_tune_down = gtk_button_new_with_label("-1k");
    s->btn_mid_down  = gtk_button_new_with_label("-100");
    s->btn_fine_down = gtk_button_new_with_label("-10");
    s->entry_freq    = gtk_entry_new();
    s->btn_fine_up   = gtk_button_new_with_label("+10");
    s->btn_mid_up    = gtk_button_new_with_label("+100");
    s->btn_tune_up   = gtk_button_new_with_label("+1k");

    gtk_entry_set_placeholder_text(GTK_ENTRY(s->entry_freq), "kHz");
    gtk_widget_set_size_request(s->entry_freq, 80, -1);

    GtkWidget *tune_btns[] = {
        s->btn_tune_down, s->btn_mid_down, s->btn_fine_down, s->entry_freq,
        s->btn_fine_up, s->btn_mid_up, s->btn_tune_up
    };
    for (int i = 0; i < 7; i++) {
        gtk_widget_set_size_request(tune_btns[i], 60, -1);
        gtk_box_append(GTK_BOX(tune_row), tune_btns[i]);
    }

    g_signal_connect(s->btn_tune_up, "clicked", G_CALLBACK(on_tune_up), s);
    g_signal_connect(s->btn_tune_down, "clicked", G_CALLBACK(on_tune_down), s);
    g_signal_connect(s->btn_mid_up, "clicked", G_CALLBACK(on_mid_up), s);
    g_signal_connect(s->btn_mid_down, "clicked", G_CALLBACK(on_mid_down), s);
    g_signal_connect(s->btn_fine_up, "clicked", G_CALLBACK(on_fine_up), s);
    g_signal_connect(s->btn_fine_down, "clicked", G_CALLBACK(on_fine_down), s);
    g_signal_connect(s->entry_freq, "activate", G_CALLBACK(on_freq_entry_activate), s);

    /* ── Mode buttons + Volume ── */
    GtkWidget *mode_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_widget_set_halign(mode_row, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(panel), mode_row);

    GtkWidget *mode_label = gtk_label_new("MODE");
    gtk_widget_add_css_class(mode_label, "section-label");
    gtk_box_append(GTK_BOX(mode_row), mode_label);

    s->btn_am       = gtk_toggle_button_new_with_label("AM");
    s->btn_sam      = gtk_toggle_button_new_with_label("SAM");
    s->btn_sam_u    = gtk_toggle_button_new_with_label("SAM-U");
    s->btn_sam_l    = gtk_toggle_button_new_with_label("SAM-L");
    s->btn_usb      = gtk_toggle_button_new_with_label("USB");
    s->btn_lsb      = gtk_toggle_button_new_with_label("LSB");
    s->btn_cw_plus  = gtk_toggle_button_new_with_label("CW+");
    s->btn_cw_minus = gtk_toggle_button_new_with_label("CW-");
    s->btn_rtty     = gtk_toggle_button_new_with_label("RTTY");
    s->btn_psk31    = gtk_toggle_button_new_with_label("PSK31");
    s->btn_drm      = gtk_toggle_button_new_with_label("DRM");

    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(s->btn_am), TRUE);

    GtkWidget *mode_btns[] = {
        s->btn_am, s->btn_sam, s->btn_sam_u, s->btn_sam_l,
        s->btn_usb, s->btn_lsb, s->btn_cw_plus,
        s->btn_cw_minus, s->btn_rtty, s->btn_psk31, s->btn_drm
    };
    for (int i = 0; i < MODE_COUNT; i++) {
        gtk_widget_set_size_request(mode_btns[i], 60, -1);
        gtk_box_append(GTK_BOX(mode_row), mode_btns[i]);
        g_signal_connect(mode_btns[i], "toggled", G_CALLBACK(on_mode_toggled), s);
    }

    GtkWidget *spacer_mode = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_hexpand(spacer_mode, TRUE);
    gtk_box_append(GTK_BOX(mode_row), spacer_mode);

    GtkWidget *vol_label = gtk_label_new("VOL");
    gtk_widget_add_css_class(vol_label, "section-label");
    gtk_box_append(GTK_BOX(mode_row), vol_label);

    s->scale_volume = gtk_scale_new_with_range(GTK_ORIENTATION_HORIZONTAL, 0.0, 1.0, 0.05);
    gtk_range_set_value(GTK_RANGE(s->scale_volume), 0.5);
    gtk_widget_set_size_request(s->scale_volume, 120, -1);
    g_signal_connect(s->scale_volume, "value-changed", G_CALLBACK(on_volume_changed), s);
    gtk_box_append(GTK_BOX(mode_row), s->scale_volume);

    add_section_sep(panel);

    /* ── Control buttons + Server connection ── */
    GtkWidget *ctrl_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 4);
    gtk_widget_set_halign(ctrl_row, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(panel), ctrl_row);

    GtkWidget *ctrl_label = gtk_label_new("CTRL");
    gtk_widget_add_css_class(ctrl_label, "section-label");
    gtk_box_append(GTK_BOX(ctrl_row), ctrl_label);

    s->btn_mute  = gtk_toggle_button_new_with_label("MUTE");
    s->btn_agc   = gtk_toggle_button_new_with_label("AGC");
    s->btn_nb    = gtk_toggle_button_new_with_label("NB");
    s->btn_dnr   = gtk_toggle_button_new_with_label("DNR");
    s->btn_notch = gtk_toggle_button_new_with_label("NOTCH");
    s->btn_peak  = gtk_toggle_button_new_with_label("PEAK");
    s->btn_apf   = gtk_toggle_button_new_with_label("APF");

    gtk_toggle_button_set_active(GTK_TOGGLE_BUTTON(s->btn_agc), TRUE);

    GtkWidget *ctrl_btns[] = {
        s->btn_mute, s->btn_agc, s->btn_nb, s->btn_dnr,
        s->btn_notch, s->btn_peak, s->btn_apf
    };
    for (int i = 0; i < 7; i++) {
        gtk_widget_set_size_request(ctrl_btns[i], 60, -1);
        gtk_box_append(GTK_BOX(ctrl_row), ctrl_btns[i]);
    }

    g_signal_connect(s->btn_mute, "toggled", G_CALLBACK(on_mute_toggled), s);
    g_signal_connect(s->btn_agc, "toggled", G_CALLBACK(on_agc_toggled), s);
    g_signal_connect(s->btn_nb, "toggled", G_CALLBACK(on_nb_toggled), s);
    g_signal_connect(s->btn_dnr, "toggled", G_CALLBACK(on_dnr_toggled), s);
    g_signal_connect(s->btn_notch, "toggled", G_CALLBACK(on_notch_toggled), s);
    g_signal_connect(s->btn_peak, "toggled", G_CALLBACK(on_peak_toggled), s);
    g_signal_connect(s->btn_apf, "toggled", G_CALLBACK(on_apf_toggled), s);


    add_section_sep(panel);

    /* ── Status lines (TUI-style monospace bars) ── */
    s->lbl_status_1 = gtk_label_new("");
    gtk_widget_add_css_class(s->lbl_status_1, "status-line");
    gtk_widget_set_halign(s->lbl_status_1, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(panel), s->lbl_status_1);

    s->lbl_status_2 = gtk_label_new("");
    gtk_widget_add_css_class(s->lbl_status_2, "status-line");
    gtk_widget_set_halign(s->lbl_status_2, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(panel), s->lbl_status_2);

    s->lbl_status_3 = gtk_label_new("");
    gtk_widget_add_css_class(s->lbl_status_3, "status-line");
    gtk_widget_set_halign(s->lbl_status_3, GTK_ALIGN_CENTER);
    gtk_box_append(GTK_BOX(panel), s->lbl_status_3);

    add_section_sep(panel);

    /* ── CW info + DRM status + decoded text ── */
    s->lbl_mode_info = gtk_label_new("");
    gtk_widget_add_css_class(s->lbl_mode_info, "mode-info");
    gtk_widget_set_visible(s->lbl_mode_info, FALSE);
    gtk_widget_set_halign(s->lbl_mode_info, GTK_ALIGN_START);
    gtk_box_append(GTK_BOX(panel), s->lbl_mode_info);

    s->lbl_drm_status = gtk_label_new("");
    gtk_widget_add_css_class(s->lbl_drm_status, "drm-status");
    gtk_widget_set_visible(s->lbl_drm_status, FALSE);
    gtk_widget_set_halign(s->lbl_drm_status, GTK_ALIGN_FILL);
    gtk_label_set_xalign(GTK_LABEL(s->lbl_drm_status), 0.0f);
    gtk_box_append(GTK_BOX(panel), s->lbl_drm_status);

    s->lbl_decoded_text = gtk_label_new("");
    gtk_widget_add_css_class(s->lbl_decoded_text, "decoded-text");
    gtk_widget_set_visible(s->lbl_decoded_text, FALSE);
    gtk_widget_set_halign(s->lbl_decoded_text, GTK_ALIGN_FILL);
    gtk_widget_set_hexpand(s->lbl_decoded_text, TRUE);
    gtk_label_set_xalign(GTK_LABEL(s->lbl_decoded_text), 0.0f);
    gtk_label_set_ellipsize(GTK_LABEL(s->lbl_decoded_text), PANGO_ELLIPSIZE_START);
    gtk_box_append(GTK_BOX(panel), s->lbl_decoded_text);

    /* ── Input controllers on GL area ─────────────────────────── */

    GtkEventController *key = gtk_event_controller_key_new();
    g_signal_connect(key, "key-pressed", G_CALLBACK(on_key_pressed), NULL);
    gtk_widget_add_controller(s->window, key);

    GtkEventController *scroll = gtk_event_controller_scroll_new(
        GTK_EVENT_CONTROLLER_SCROLL_VERTICAL);
    g_signal_connect(scroll, "scroll", G_CALLBACK(on_scroll), NULL);
    gtk_widget_add_controller(s->gl_area, scroll);

    /* ── Timers ───────────────────────────────────────────────── */

    s->display_timer_id = g_timeout_add(100, on_display_update, s);
    s->cat_timer_id = g_timeout_add_seconds(1, on_cat_poll, s);

    gtk_window_present(GTK_WINDOW(s->window));
}

/* ── Shutdown ─────────────────────────────────────────────────── */

static void on_shutdown(GtkApplication *app, gpointer data) {
    (void)app; (void)data;
    AppState *s = &app_state;

    if (s->display_timer_id) g_source_remove(s->display_timer_id);
    if (s->cat_timer_id) g_source_remove(s->cat_timer_id);

    if (s->drm.running) drm_stop(&s->drm);
    if (s->iq_connected) iq_client_stop(&s->iq_client);
    if (s->cat_connected) cat_client_stop(&s->cat_client);
    if (s->audio_open) audio_close(&s->audio);

    if (s->gl_initialized) {
        spectrum_destroy(&s->spectrum);
        waterfall_destroy(&s->waterfall);
        renderer_destroy(&s->renderer);
    }
    fft_destroy(&s->fft);
    demod_destroy(&s->demod);
    drm_destroy(&s->drm);
}

/* ── Main ─────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    AppState *s = &app_state;
    memset(s, 0, sizeof(*s));

    /* Load config */
    hf_config_t cfg;
    hf_config_load(&cfg);
    strncpy(s->host, cfg.host, sizeof(s->host) - 1);
    s->iq_port = cfg.iq_port;
    s->cat_port = cfg.cat_port;

    /* Parse CLI flags and strip them so GTK doesn't reject them */
    int new_argc = 1; /* keep argv[0] */
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--host") == 0 && i + 1 < argc) {
            strncpy(s->host, argv[++i], sizeof(s->host) - 1);
            s->host[sizeof(s->host) - 1] = '\0';
        } else {
            argv[new_argc++] = argv[i];
        }
    }
    argc = new_argc;
    argv[argc] = NULL;

    /* Initialize DSP */
    demod_init(&s->demod);
    drm_init(&s->drm);

    if (cfg.dream_path[0])
        strncpy(s->drm.dream_path, cfg.dream_path, sizeof(s->drm.dream_path) - 1);

    if (cfg.nb_threshold > 0 && cfg.nb_threshold <= 3)
        s->demod.nb_threshold = (nb_threshold_t)cfg.nb_threshold;
    if (cfg.dnr_level > 0 && cfg.dnr_level <= 3)
        s->demod.dnr_level = (dnr_level_t)cfg.dnr_level;

    /* Resolve shader directory */
    char exe_path[PATH_MAX];
    ssize_t len = readlink("/proc/self/exe", exe_path, sizeof(exe_path) - 1);
    if (len == -1) {
        strncpy(exe_path, argv[0], PATH_MAX - 1);
        exe_path[PATH_MAX - 1] = '\0';
    } else {
        exe_path[len] = '\0';
    }
    char exe_copy[PATH_MAX];
    strncpy(exe_copy, exe_path, PATH_MAX - 1);
    exe_copy[PATH_MAX - 1] = '\0';
    char *dir = dirname(exe_copy);

    snprintf(s->shader_dir, sizeof(s->shader_dir), "%s/shaders", dir);
    if (access(s->shader_dir, F_OK) != 0)
        snprintf(s->shader_dir, sizeof(s->shader_dir),
                 "%s/../share/hfdemod-gtk/shaders", dir);

    /* GTK Application */
    s->app = gtk_application_new("com.hfdemod.gtk", G_APPLICATION_DEFAULT_FLAGS);
    g_signal_connect(s->app, "activate", G_CALLBACK(activate), NULL);
    g_signal_connect(s->app, "shutdown", G_CALLBACK(on_shutdown), NULL);

    int status = g_application_run(G_APPLICATION(s->app), argc, argv);
    g_object_unref(s->app);

    return status;
}
