/* drm.h — DRM decoder integration via Dream 2.2 subprocess. */

#ifndef DRM_H
#define DRM_H

#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <sys/types.h>

#define DRM_IQ_RATE 48000

typedef void (*drm_audio_callback_t)(const float *samples, int count, void *user);

typedef struct {
    pthread_mutex_t lock;

    /* Dream subprocess */
    pid_t           pid;
    int             stdin_fd;
    int             stdout_fd;
    int             stderr_fd;
    int             status_fd;

    /* Threads */
    pthread_t       audio_thread;
    pthread_t       status_thread;
    pthread_t       stderr_thread;
    bool            running;

    /* Decimation filter for IQ 192k -> 48k (FIR anti-alias + decimate by 4) */
    float           decim_taps[127];
    float           decim_buf_i[128];   /* FIR state for I channel */
    float           decim_buf_q[128];   /* FIR state for Q channel */
    int             decim_pos;
    int             decim_counter;

    /* Audio callback */
    drm_audio_callback_t audio_cb;
    void            *audio_cb_user;

    /* Status */
    char            service_label[64];
    char            text_message[256];
    char            audio_codec[32];
    char            audio_mode[16];     /* "Mono", "Stereo", etc. */
    char            country[64];
    char            language[64];
    float           snr;
    float           bitrate_kbps;
    int             sync_state;     /* 0=no sync, 1=timing, 2=FAC, 3=full */
    int             robustness;     /* 0=A, 1=B, 2=C, 3=D */
    int             sdc_qam;        /* 0=4QAM, 1=16QAM, 2=64QAM */
    int             msc_qam;        /* 0=4QAM, 1=16QAM, 2=64QAM */
    bool            status_valid;
    /* Per-field sync detail: 0=synced(O), 1/2=tracking(*), -1=no sync(-) */
    int             sync_io;
    int             sync_time;
    int             sync_frame;
    int             sync_fac;
    int             sync_sdc;
    int             sync_msc;

    /* Dream binary path */
    char            dream_path[256];
    char            socket_path[128];
} drm_decoder_t;

/* Initialize DRM decoder state. */
void drm_init(drm_decoder_t *d);

/* Start Dream subprocess. Returns 0 on success. */
int drm_start(drm_decoder_t *d, drm_audio_callback_t cb, void *user);

/* Write IQ data to Dream (will be decimated to 48k internally).
 * iq: complex float samples at 192 kHz. */
void drm_write_iq(drm_decoder_t *d, const float *iq_interleaved, int num_samples);

/* Stop Dream subprocess. */
void drm_stop(drm_decoder_t *d);

/* Clean up. */
void drm_destroy(drm_decoder_t *d);

/* Find Dream binary. Returns true if found. */
bool drm_find_binary(drm_decoder_t *d);

#endif
