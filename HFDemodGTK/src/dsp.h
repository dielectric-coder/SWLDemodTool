/* dsp.h — Demodulation pipeline for HFDemodGTK.
 *
 * Implements: FIR lowpass, decimation, AM/SSB/CW/RTTY/PSK31 detection,
 * noise blanker, spectral DNR, auto notch, DC removal, AGC. */

#ifndef DSP_H
#define DSP_H

#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>

/* IQ sample as two floats (I, Q) */
typedef struct { float i, q; } iq_sample_t;

/* Mode enumeration */
typedef enum {
    MODE_AM = 0,
    MODE_SAM,
    MODE_SAM_U,
    MODE_SAM_L,
    MODE_USB,
    MODE_LSB,
    MODE_CW_PLUS,
    MODE_CW_MINUS,
    MODE_RTTY,
    MODE_PSK31,
    MODE_DRM,
    MODE_COUNT
} demod_mode_t;

/* Noise blanker threshold presets */
typedef enum {
    NB_OFF = 0,
    NB_LOW,       /* 10x */
    NB_MED,       /* 20x */
    NB_HIGH       /* 40x */
} nb_threshold_t;

/* DNR level */
typedef enum {
    DNR_OFF = 0,
    DNR_1,
    DNR_2,
    DNR_3
} dnr_level_t;

#define DEMOD_FIR_TAPS      127
#define DEMOD_CW_FIR_TAPS   255
#define DEMOD_DECIMATE       4
#define IQ_SAMPLE_RATE       192000
#define AUDIO_SAMPLE_RATE    48000
#define AGC_TARGET           0.3f
#define CW_BFO_HZ           700.0f
#define CW_FFT_SIZE          8192
#define RTTY_MARK_HZ         2125.0f
#define RTTY_SPACE_HZ        2295.0f
#define RTTY_BAUD            45.45f
#define PSK31_CARRIER_HZ     1000.0f
#define PSK31_BAUD           31.25f

#define DECODED_TEXT_LEN     120

/* DNR constants */
#define DNR_FFT_SIZE         512
#define DNR_HOP              256     /* 50% overlap */
#define DNR_BINS             (DNR_FFT_SIZE / 2 + 1)  /* 257 */
#define DNR_NOISE_PERCENTILE 30
#define DNR_NOISE_SMOOTH     0.90f
#define DNR_GAIN_SMOOTH      0.5f
#define DNR_RAMP_FRAMES      5

/* Auto notch constants */
#define AN_FFT_SIZE          1024
#define AN_HOP               512     /* 50% overlap */
#define AN_BINS              (AN_FFT_SIZE / 2 + 1)  /* 513 */
#define AN_PEAK_THRESH       10.0f
#define AN_NEIGHBOR_BINS     8
#define AN_NOTCH_HALFWIDTH   2
#define AN_PERSIST_SMOOTH    0.85f
#define AN_GAIN_SMOOTH       0.6f
#define AN_RAMP_FRAMES       5

/* SNR estimator constants */
#define SNR_FFT_SIZE         1024
#define SNR_SMOOTH           0.85f
#define SNR_NOISE_UP         0.005f
#define SNR_NOISE_DOWN       0.1f

typedef struct {
    pthread_mutex_t lock;

    /* Current mode */
    demod_mode_t mode;

    /* FIR lowpass filter (pre-decimation) */
    float       fir_taps[DEMOD_FIR_TAPS];
    iq_sample_t fir_buf[DEMOD_FIR_TAPS];
    int         fir_pos;
    int         bandwidth_hz;

    /* Decimation */
    int     decim_counter;

    /* AGC (block-based RMS) */
    float   agc_gain;
    bool    agc_enabled;

    /* Volume / mute */
    float   volume;
    bool    muted;

    /* DC removal */
    float   dc_avg;

    /* PLL for SAM modes */
    double  pll_phase;
    double  pll_freq;

    /* Noise blanker */
    nb_threshold_t nb_threshold;
    float       nb_ema;
    iq_sample_t nb_delay[16];
    int         nb_delay_pos;
    int         nb_holdoff;

    /* Spectral DNR (STFT-based spectral gate) */
    dnr_level_t dnr_level;
    float   dnr_window[DNR_FFT_SIZE];
    float   dnr_synth_window[DNR_FFT_SIZE];
    float   dnr_prev_gain[DNR_BINS];
    float   dnr_noise_est;          /* scalar noise floor estimate */
    float   dnr_prev_tail[DNR_HOP]; /* overlap-add tail from previous frame */
    float   dnr_in_buf[DNR_FFT_SIZE * 2]; /* input accumulation buffer */
    int     dnr_in_len;
    int     dnr_frame_count;

    /* Auto notch (STFT-based tone detection) */
    bool    auto_notch;
    float   an_window[AN_FFT_SIZE];
    float   an_synth_window[AN_FFT_SIZE];
    float   an_persist[AN_BINS];
    float   an_prev_gain[AN_BINS];
    float   an_prev_tail[AN_HOP];
    float   an_in_buf[AN_FFT_SIZE * 2];
    int     an_in_len;
    int     an_frame_count;

    /* SNR estimator */
    float   snr_db;
    float   snr_signal_power;
    float   snr_noise_floor;
    float   snr_buf_i[SNR_FFT_SIZE];
    float   snr_buf_q[SNR_FFT_SIZE];
    int     snr_buf_pos;

    /* CW state */
    float   cw_fir_taps[DEMOD_CW_FIR_TAPS];
    float   cw_fir_buf[DEMOD_CW_FIR_TAPS];
    int     cw_fir_pos;
    double  cw_bfo_phase;
    float   cw_tone_buf[CW_FFT_SIZE];
    int     cw_tone_pos;
    float   cw_peak_hz;
    float   cw_snr;
    float   cw_wpm;
    bool    apf_enabled;
    /* APF biquad state */
    float   apf_x1, apf_x2, apf_y1, apf_y2;
    float   apf_b0, apf_b1, apf_b2, apf_a1, apf_a2;
    /* CW keying */
    float   cw_envelope;
    float   cw_peak_env;
    bool    cw_key_state;
    int     cw_debounce;
    float   cw_dit_ms;
    char    cw_element_buf[64];
    int     cw_element_count;
    int     cw_gap_samples;

    /* RTTY state */
    float   rtty_mark_fir[DEMOD_CW_FIR_TAPS];
    float   rtty_space_fir[DEMOD_CW_FIR_TAPS];
    float   rtty_mark_buf[DEMOD_CW_FIR_TAPS];
    float   rtty_space_buf[DEMOD_CW_FIR_TAPS];
    int     rtty_fir_pos;
    float   rtty_discrim;
    int     rtty_bit_phase;
    int     rtty_state;         /* 0=idle, 1=start, 2=data, 3=stop */
    int     rtty_bit_count;
    uint8_t rtty_shift_reg;
    bool    rtty_figs_mode;

    /* PSK31 state */
    double  psk_lo_phase;
    float   psk_acc_i, psk_acc_q;
    float   psk_prev_i, psk_prev_q;
    float   psk_lp_taps[127];
    float   psk_lp_buf_i[127];
    float   psk_lp_buf_q[127];
    int     psk_lp_pos;
    int     psk_sample_count;
    int     psk_bit_buf;
    int     psk_bit_count;

    /* Decoded text buffer */
    char    decoded_text[DECODED_TEXT_LEN + 1];
    int     decoded_text_pos;

    /* Morse decoded text */
    char    morse_text[DECODED_TEXT_LEN + 1];
    int     morse_text_pos;
} demodulator_t;

/* Initialize demodulator. */
void demod_init(demodulator_t *d);

/* Set demodulation mode and bandwidth. */
void demod_set_mode(demodulator_t *d, demod_mode_t mode);
void demod_set_bandwidth(demodulator_t *d, int bw_hz);

/* Process IQ samples. Outputs audio samples to out_audio.
 * iq_data: raw bytes from IQ client (int32 pairs).
 * Returns number of audio samples written. */
int demod_process(demodulator_t *d, const uint8_t *iq_data, int iq_bytes,
                  float *out_audio, int max_audio);

/* Design FIR lowpass filter (windowed sinc). */
void demod_design_fir(float *taps, int num_taps, float cutoff_hz, float sample_rate);

/* Design bandpass filter. */
void demod_design_bandpass(float *taps, int num_taps,
                           float low_hz, float high_hz, float sample_rate);

/* Clean up. */
void demod_destroy(demodulator_t *d);

/* Get mode name string. */
const char *demod_mode_name(demod_mode_t mode);

#endif
