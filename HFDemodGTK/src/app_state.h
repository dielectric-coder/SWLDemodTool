/* app_state.h — Central application state for HFDemodGTK. */

#ifndef APP_STATE_H
#define APP_STATE_H

#include <gtk/gtk.h>
#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <limits.h>

#include "renderer.h"
#include "spectrum.h"
#include "waterfall.h"
#include "fft.h"
#include "iq_client.h"
#include "cat_client.h"
#include "dsp.h"
#include "audio.h"
#include "drm.h"

#define PANEL_HEIGHT     280
#define DEFAULT_WIDTH    1024
#define DEFAULT_HEIGHT   768
#define APP_VERSION      "0.1.0"

typedef struct {
    GtkApplication *app;
    GtkWidget      *window;
    GtkWidget      *gl_area;

    /* Bottom panel widgets */
    GtkWidget *lbl_vfo_bar;         /* VFO/Freq/Mode/BW info bar */
    GtkWidget *lbl_conn_info;       /* Connection status (IQ/CAT/Audio) */
    GtkWidget *lbl_status_1;        /* Vol + NB + AGC line */
    GtkWidget *lbl_status_2;        /* Audio + DNR + Buf + Underruns line */
    GtkWidget *lbl_status_3;        /* Peak + DNF + APF + S-meter + SNR line */
    GtkWidget *lbl_decoded_text;
    GtkWidget *lbl_drm_status;
    GtkWidget *lbl_mode_info;

    /* Connection widgets */
    GtkWidget *entry_host;
    GtkWidget *entry_iq_port;
    GtkWidget *entry_cat_port;
    GtkWidget *btn_connect;
    GtkWidget *btn_disconnect;

    /* Mode buttons */
    GtkWidget *btn_am;
    GtkWidget *btn_sam;
    GtkWidget *btn_sam_u;
    GtkWidget *btn_sam_l;
    GtkWidget *btn_usb;
    GtkWidget *btn_lsb;
    GtkWidget *btn_cw_plus;
    GtkWidget *btn_cw_minus;
    GtkWidget *btn_rtty;
    GtkWidget *btn_psk31;
    GtkWidget *btn_drm;

    /* Control buttons */
    GtkWidget *btn_mute;
    GtkWidget *btn_agc;
    GtkWidget *btn_nb;
    GtkWidget *btn_dnr;
    GtkWidget *btn_notch;
    GtkWidget *btn_peak;
    GtkWidget *btn_apf;
    GtkWidget *btn_vfo;
    GtkWidget *scale_volume;

    /* Tune buttons */
    GtkWidget *btn_tune_up;
    GtkWidget *btn_tune_down;
    GtkWidget *btn_mid_up;
    GtkWidget *btn_mid_down;
    GtkWidget *btn_fine_up;
    GtkWidget *btn_fine_down;
    GtkWidget *entry_freq;

    /* OpenGL rendering */
    Renderer        renderer;
    spectrum_state_t spectrum;
    waterfall_state_t waterfall;
    fft_state_t     fft;
    int             gl_initialized;
    float           split;          /* spectrum/waterfall split ratio */

    /* Network clients */
    iq_client_t     iq_client;
    cat_client_t    cat_client;
    bool            iq_connected;
    bool            cat_connected;

    /* DSP */
    demodulator_t   demod;

    /* Audio */
    audio_output_t  audio;
    bool            audio_open;

    /* DRM */
    drm_decoder_t   drm;

    /* Radio state */
    double          frequency_hz;
    char            mode[8];
    int             bandwidth_hz;
    int             active_vfo;     /* 0=A, 1=B */
    int             s_meter_raw;

    /* RIT (Receiver Incremental Tuning) */
    bool            rit_enabled;
    double          rit_offset_hz;  /* ±offset applied to receive frequency */

    /* Config */
    char            host[128];
    int             iq_port;
    int             cat_port;
    char            audio_device[64];

    /* Paths */
    char            shader_dir[PATH_MAX];

    /* Timers */
    guint           display_timer_id;
    guint           cat_timer_id;
} AppState;

#endif
