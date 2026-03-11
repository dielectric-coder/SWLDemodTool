/* dsp.c — Demodulation pipeline.
 *
 * Ported from SWLDemodTool Python dsp.py to C.
 * Pipeline: IQ -> noise blanker -> FIR lowpass -> decimate -> detect ->
 *           DNR -> auto notch -> DC removal -> AGC
 *
 * Improvements over initial port:
 * - Spectral DNR (STFT spectral gate with percentile noise floor)
 * - Auto notch (STFT tone detection and removal)
 * - SNR estimator (spectral, median-based noise floor)
 * - Block-based AGC (RMS instead of per-sample magnitude) */

#include "dsp.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <fftw3.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* ── Morse code lookup table ──────────────────────────────────── */

static const struct { const char *code; char ch; } morse_table[] __attribute__((used)) = {
    {".-",    'A'}, {"-...",  'B'}, {"-.-.",  'C'}, {"-..",   'D'},
    {".",     'E'}, {"..-.",  'F'}, {"--.",   'G'}, {"....",  'H'},
    {"..",    'I'}, {".---",  'J'}, {"-.-",   'K'}, {".-..",  'L'},
    {"--",    'M'}, {"-.",    'N'}, {"---",   'O'}, {".--.",  'P'},
    {"--.-",  'Q'}, {".-.",   'R'}, {"...",   'S'}, {"-",     'T'},
    {"..-",   'U'}, {"...-",  'V'}, {".--",   'W'}, {"-..-",  'X'},
    {"-.--",  'Y'}, {"--..",  'Z'},
    {"-----", '0'}, {".----", '1'}, {"..---", '2'}, {"...--", '3'},
    {"....-", '4'}, {".....", '5'}, {"-....", '6'}, {"--...", '7'},
    {"---..", '8'}, {"----.", '9'},
    {".-.-.-",'.'}, {"--..--",','}, {"..--..",'?'}, {".----.",'\''},
    {"-.-.--",'!'}, {"-..-.", '/'}, {"-.--.", '('}, {"-.--.-",')'},
    {".-...", '&'}, {"---...",':'}, {"-.-.-.",';'}, {"-...-", '='},
    {".-.-.", '+'}, {"-....-",'-'}, {"..--.-",'_'}, {".-..-.",'"'},
    {NULL, 0}
};

/* ── ITA2 / Baudot tables for RTTY ───────────────────────────── */

static const char ita2_letters[32] = {
    '\0', 'E', '\n', 'A', ' ', 'S', 'I', 'U',
    '\r', 'D', 'R', 'J', 'N', 'F', 'C', 'K',
    'T', 'Z', 'L', 'W', 'H', 'Y', 'P', 'Q',
    'O', 'B', 'G', '\x0E', 'M', 'X', 'V', '\x0F'
};

static const char ita2_figures[32] = {
    '\0', '3', '\n', '-', ' ', '\a', '8', '7',
    '\r', '$', '4', '\'', ',', '!', ':', '(',
    '5', '"', ')', '2', '#', '6', '0', '1',
    '9', '?', '&', '\x0E', '.', '/', ';', '\x0F'
};

/* ── PSK31 Varicode table ────────────────────────────────────── */

static const unsigned int varicode[128] = {
    0x0355, 0x036D, 0x02DD, 0x03BB, 0x035D, 0x03EB, 0x03DD, 0x02FD,
    0x03FD, 0x00F7, 0x0017, 0x03DB, 0x02ED, 0x001F, 0x02BB, 0x0357,
    0x03BD, 0x02BD, 0x02D7, 0x02EB, 0x036B, 0x035B, 0x02DB, 0x03AB,
    0x02F7, 0x02F5, 0x03AD, 0x03AF, 0x037B, 0x037D, 0x03B5, 0x03B7,
    0x0001, 0x01FF, 0x01F5, 0x015F, 0x01B7, 0x02AD, 0x0375, 0x01FD,
    0x00DF, 0x00EF, 0x01ED, 0x01F7, 0x0057, 0x002B, 0x0075, 0x01EB,
    0x00ED, 0x00BD, 0x00B7, 0x00FF, 0x01DD, 0x01B5, 0x01AD, 0x016B,
    0x01AB, 0x01DB, 0x00AF, 0x0177, 0x015B, 0x01EF, 0x01BF, 0x01D5,
    0x02F3, 0x003D, 0x017B, 0x00FB, 0x00BB, 0x0055, 0x00D5, 0x015D,
    0x0157, 0x001D, 0x01DF, 0x017D, 0x00EB, 0x00DD, 0x00DB, 0x006F,
    0x016F, 0x01BF, 0x00D7, 0x006D, 0x0035, 0x00AB, 0x0175, 0x00B5,
    0x01AF, 0x017F, 0x02AB, 0x00F5, 0x01BD, 0x00FD, 0x02D5, 0x016D,
    0x03B3, 0x000F, 0x005F, 0x002F, 0x002D, 0x0003, 0x005B, 0x003B,
    0x006B, 0x000D, 0x01D7, 0x00BF, 0x001B, 0x003F, 0x000B, 0x0007,
    0x003D, 0x01D5, 0x0015, 0x0011, 0x0005, 0x0037, 0x007B, 0x0027,
    0x00B5, 0x007D, 0x01B3, 0x00F7, 0x01AB, 0x00DB, 0x02D5, 0x036B,
};

/* Reverse lookup: code bits -> ASCII character. */
static char varicode_reverse[4096];
static int varicode_initialized = 0;

static void varicode_init(void) {
    if (varicode_initialized) return;
    varicode_initialized = 1;
    memset(varicode_reverse, 0, sizeof(varicode_reverse));
    for (int i = 0; i < 128; i++) {
        unsigned int code = varicode[i];
        if (code < 4096)
            varicode_reverse[code] = (char)i;
    }
}

/* ── Filter design ────────────────────────────────────────────── */

static void blackman_window(float *w, int n) {
    for (int i = 0; i < n; i++) {
        float x = (float)i / (float)(n - 1);
        w[i] = 0.42f - 0.5f * cosf(2.0f * (float)M_PI * x)
                      + 0.08f * cosf(4.0f * (float)M_PI * x);
    }
}

static void hann_window(float *w, int n) {
    for (int i = 0; i < n; i++)
        w[i] = 0.5f * (1.0f - cosf(2.0f * (float)M_PI * (float)i / (float)(n - 1)));
}

void demod_design_fir(float *taps, int num_taps, float cutoff_hz, float sample_rate) {
    float fc = cutoff_hz / sample_rate;
    int M = num_taps - 1;
    float window[num_taps]; /* VLA */
    blackman_window(window, num_taps);

    float sum = 0.0f;
    for (int i = 0; i < num_taps; i++) {
        float n = (float)i - (float)M / 2.0f;
        if (fabsf(n) < 1e-6f)
            taps[i] = 2.0f * fc;
        else
            taps[i] = sinf(2.0f * (float)M_PI * fc * n) / ((float)M_PI * n);
        taps[i] *= window[i];
        sum += taps[i];
    }
    for (int i = 0; i < num_taps; i++)
        taps[i] /= sum;
}

void demod_design_bandpass(float *taps, int num_taps,
                           float low_hz, float high_hz, float sample_rate) {
    float fl = low_hz / sample_rate;
    float fh = high_hz / sample_rate;
    int M = num_taps - 1;
    float window[num_taps]; /* VLA */
    blackman_window(window, num_taps);

    for (int i = 0; i < num_taps; i++) {
        float n = (float)i - (float)M / 2.0f;
        float h;
        if (fabsf(n) < 1e-6f)
            h = 2.0f * (fh - fl);
        else
            h = (sinf(2.0f * (float)M_PI * fh * n) -
                 sinf(2.0f * (float)M_PI * fl * n)) / ((float)M_PI * n);
        taps[i] = h * window[i];
    }
}

/* ── APF biquad design ────────────────────────────────────────── */

static void design_apf_biquad(demodulator_t *d, float center_hz, float Q) {
    float w0 = 2.0f * (float)M_PI * center_hz / (float)AUDIO_SAMPLE_RATE;
    float alpha = sinf(w0) / (2.0f * Q);
    float b0 = alpha;
    float b2 = -alpha;
    float a0 = 1.0f + alpha;
    float a1 = -2.0f * cosf(w0);
    float a2 = 1.0f - alpha;

    d->apf_b0 = b0 / a0;
    d->apf_b1 = 0.0f;
    d->apf_b2 = b2 / a0;
    d->apf_a1 = a1 / a0;
    d->apf_a2 = a2 / a0;
}

/* ── Synthesis window for overlap-add reconstruction ──────────── */

static void compute_synth_window(float *synth, const float *analysis, int fft_size, int hop) {
    /* synth = analysis / ola_sum, where ola_sum accounts for overlap */
    float ola_sum[fft_size];
    memset(ola_sum, 0, sizeof(float) * fft_size);
    /* Two overlapping frames at 50% overlap */
    for (int i = 0; i < fft_size; i++) {
        float a = analysis[i];
        ola_sum[i] += a * a;
    }
    /* Add contribution from adjacent frame shifted by hop */
    for (int i = 0; i < hop; i++) {
        ola_sum[i] += analysis[i + hop] * analysis[i + hop];
    }
    for (int i = hop; i < fft_size; i++) {
        ola_sum[i] += analysis[i - hop] * analysis[i - hop];
    }
    for (int i = 0; i < fft_size; i++) {
        synth[i] = analysis[i] / fmaxf(ola_sum[i], 1e-10f);
    }
}

/* ── Demodulator init ─────────────────────────────────────────── */

static const char *mode_names[MODE_COUNT] = {
    "AM", "SAM", "SAM-U", "SAM-L", "USB", "LSB", "CW+", "CW-", "RTTY", "PSK31", "DRM"
};

const char *demod_mode_name(demod_mode_t mode) {
    if (mode >= 0 && mode < MODE_COUNT)
        return mode_names[mode];
    return "???";
}

void demod_init(demodulator_t *d) {
    memset(d, 0, sizeof(*d));
    pthread_mutex_init(&d->lock, NULL);

    d->mode = MODE_AM;
    d->bandwidth_hz = 5000;
    d->agc_gain = 100.0f;
    d->agc_enabled = true;
    d->volume = 0.5f;
    d->muted = false;
    d->dc_avg = 0.0f;
    d->snr_noise_floor = 1e-10f;

    varicode_init();

    /* Design initial FIR */
    demod_design_fir(d->fir_taps, DEMOD_FIR_TAPS,
                     (float)d->bandwidth_hz, (float)IQ_SAMPLE_RATE);

    /* CW post-filter */
    demod_design_fir(d->cw_fir_taps, DEMOD_CW_FIR_TAPS,
                     400.0f, (float)AUDIO_SAMPLE_RATE);

    /* CW tone analysis FFT plan (cached for reuse) */
    int cw_spec_len = CW_FFT_SIZE / 2 + 1;
    d->cw_fft_out = fftwf_alloc_complex(cw_spec_len);
    d->cw_fft_plan = fftwf_plan_dft_r2c_1d(CW_FFT_SIZE, d->cw_tone_buf,
                                            d->cw_fft_out, FFTW_ESTIMATE);

    /* APF biquad */
    design_apf_biquad(d, CW_BFO_HZ, 15.0f);

    /* RTTY bandpass filters */
    float rtty_bw = 80.0f;
    demod_design_bandpass(d->rtty_mark_fir, DEMOD_CW_FIR_TAPS,
                          RTTY_MARK_HZ - rtty_bw/2, RTTY_MARK_HZ + rtty_bw/2,
                          (float)AUDIO_SAMPLE_RATE);
    demod_design_bandpass(d->rtty_space_fir, DEMOD_CW_FIR_TAPS,
                          RTTY_SPACE_HZ - rtty_bw/2, RTTY_SPACE_HZ + rtty_bw/2,
                          (float)AUDIO_SAMPLE_RATE);

    /* PSK31 lowpass */
    demod_design_fir(d->psk_lp_taps, 127, 100.0f, (float)AUDIO_SAMPLE_RATE);

    /* DNR: Hann analysis window + synthesis window for overlap-add */
    hann_window(d->dnr_window, DNR_FFT_SIZE);
    compute_synth_window(d->dnr_synth_window, d->dnr_window, DNR_FFT_SIZE, DNR_HOP);
    for (int i = 0; i < DNR_BINS; i++)
        d->dnr_prev_gain[i] = 1.0f;

    /* Auto notch: Hann analysis window + synthesis window */
    hann_window(d->an_window, AN_FFT_SIZE);
    compute_synth_window(d->an_synth_window, d->an_window, AN_FFT_SIZE, AN_HOP);
    for (int i = 0; i < AN_BINS; i++)
        d->an_prev_gain[i] = 1.0f;
}

void demod_set_mode(demodulator_t *d, demod_mode_t mode) {
    pthread_mutex_lock(&d->lock);
    d->mode = mode;

    /* Set default bandwidth for mode (one-sided cutoff, matches Python TUI) */
    switch (mode) {
    case MODE_AM:
    case MODE_SAM:
    case MODE_SAM_U:
    case MODE_SAM_L:    d->bandwidth_hz = 5000; break;
    case MODE_USB:
    case MODE_LSB:      d->bandwidth_hz = 2400; break;
    case MODE_CW_PLUS:
    case MODE_CW_MINUS: d->bandwidth_hz = 500;  break;
    case MODE_RTTY:     d->bandwidth_hz = 2400; break;
    case MODE_PSK31:    d->bandwidth_hz = 500;  break;
    case MODE_DRM:      d->bandwidth_hz = 10000; break;
    default: break;
    }

    /* Redesign FIR for new bandwidth.
     * For CW: wide IQ pre-filter (2400 Hz) to pass BFO tone;
     *         the narrow CW post-filter uses bandwidth_hz. */
    if (mode == MODE_CW_PLUS || mode == MODE_CW_MINUS) {
        demod_design_fir(d->fir_taps, DEMOD_FIR_TAPS,
                         CW_PREFILTER_HZ, (float)IQ_SAMPLE_RATE);
        demod_design_fir(d->cw_fir_taps, DEMOD_CW_FIR_TAPS,
                         (float)d->bandwidth_hz, (float)AUDIO_SAMPLE_RATE);
    } else {
        demod_design_fir(d->fir_taps, DEMOD_FIR_TAPS,
                         (float)d->bandwidth_hz, (float)IQ_SAMPLE_RATE);
    }

    /* Reset mode-specific state */
    d->pll_phase = 0.0;
    d->pll_freq = 0.0;
    d->cw_bfo_phase = 0.0;
    d->cw_fir_pos = 0;
    memset(d->cw_fir_buf, 0, sizeof(d->cw_fir_buf));
    d->cw_peak_hz = 0.0f;
    d->cw_snr = 0.0f;
    d->cw_wpm = 0.0f;
    d->cw_tone_present = false;
    d->cw_analyze_counter = 0;
    d->cw_envelope = 0.0f;
    d->cw_peak_env = 0.0f;
    d->cw_key_state = false;
    d->cw_edge_sample = 0;
    d->cw_sample_count = 0;
    d->cw_last_keyup_sample = 0;
    d->cw_dit_ms = 0.0f;
    d->cw_element_count = 0;
    d->cw_char_len = 0;
    d->morse_text_pos = 0;
    d->morse_text[0] = '\0';
    memset(d->cw_tone_buf, 0, sizeof(d->cw_tone_buf));
    d->cw_tone_pos = 0;
    d->rtty_fir_pos = 0;
    d->rtty_state = 0;
    d->rtty_discrim = 0.0f;
    d->psk_lo_phase = 0.0;
    d->psk_sample_count = 0;
    d->psk_acc_i = d->psk_acc_q = 0.0f;

    /* Reset DNR/notch state on mode switch */
    d->dnr_in_len = 0;
    d->dnr_frame_count = 0;
    d->dnr_noise_est = 0.0f;
    memset(d->dnr_prev_tail, 0, sizeof(d->dnr_prev_tail));
    for (int i = 0; i < DNR_BINS; i++)
        d->dnr_prev_gain[i] = 1.0f;

    d->an_in_len = 0;
    d->an_frame_count = 0;
    memset(d->an_persist, 0, sizeof(d->an_persist));
    memset(d->an_prev_tail, 0, sizeof(d->an_prev_tail));
    for (int i = 0; i < AN_BINS; i++)
        d->an_prev_gain[i] = 1.0f;

    /* Reset SNR */
    d->snr_buf_pos = 0;
    d->snr_signal_power = 0.0f;
    d->snr_noise_floor = 1e-10f;
    d->snr_db = 0.0f;

    pthread_mutex_unlock(&d->lock);
}

void demod_set_bandwidth(demodulator_t *d, int bw_hz) {
    pthread_mutex_lock(&d->lock);
    d->bandwidth_hz = bw_hz;
    if (d->mode == MODE_CW_PLUS || d->mode == MODE_CW_MINUS) {
        demod_design_fir(d->fir_taps, DEMOD_FIR_TAPS,
                         CW_PREFILTER_HZ, (float)IQ_SAMPLE_RATE);
        demod_design_fir(d->cw_fir_taps, DEMOD_CW_FIR_TAPS,
                         (float)bw_hz, (float)AUDIO_SAMPLE_RATE);
    } else {
        demod_design_fir(d->fir_taps, DEMOD_FIR_TAPS,
                         (float)bw_hz, (float)IQ_SAMPLE_RATE);
    }
    pthread_mutex_unlock(&d->lock);
}

/* ── FIR filter application ───────────────────────────────────── */

static iq_sample_t apply_fir_iq(demodulator_t *d, iq_sample_t sample) {
    d->fir_buf[d->fir_pos] = sample;
    iq_sample_t out = {0.0f, 0.0f};
    for (int j = 0; j < DEMOD_FIR_TAPS; j++) {
        int idx = (d->fir_pos - j + DEMOD_FIR_TAPS) % DEMOD_FIR_TAPS;
        out.i += d->fir_taps[j] * d->fir_buf[idx].i;
        out.q += d->fir_taps[j] * d->fir_buf[idx].q;
    }
    d->fir_pos = (d->fir_pos + 1) % DEMOD_FIR_TAPS;
    return out;
}

static float apply_fir_real(float *taps, float *buf, int *pos, int ntaps, float sample) {
    buf[*pos] = sample;
    float out = 0.0f;
    for (int j = 0; j < ntaps; j++) {
        int idx = (*pos - j + ntaps) % ntaps;
        out += taps[j] * buf[idx];
    }
    *pos = (*pos + 1) % ntaps;
    return out;
}

/* ── Noise blanker ────────────────────────────────────────────── */

static iq_sample_t noise_blank(demodulator_t *d, iq_sample_t s) {
    if (d->nb_threshold == NB_OFF) return s;

    float mag = sqrtf(s.i * s.i + s.q * s.q);
    d->nb_ema = d->nb_ema * 0.999f + mag * 0.001f;

    float thresh_mult[] = {0, 10.0f, 20.0f, 40.0f};
    float threshold = d->nb_ema * thresh_mult[d->nb_threshold];

    /* Delay line */
    int delayed_pos = (d->nb_delay_pos + 16 - 8) % 16;
    iq_sample_t delayed = d->nb_delay[delayed_pos];
    d->nb_delay[d->nb_delay_pos] = s;
    d->nb_delay_pos = (d->nb_delay_pos + 1) % 16;

    if (mag > threshold && threshold > 0.0f) {
        d->nb_holdoff = 4;
    }

    if (d->nb_holdoff > 0) {
        d->nb_holdoff--;
        delayed.i = 0.0f;
        delayed.q = 0.0f;
    }

    return delayed;
}

/* Forward declarations for CW helpers */
static void append_morse_char(demodulator_t *d, char ch);
static void cw_decode_char(demodulator_t *d);
static void cw_add_element(demodulator_t *d, char elem);
static void cw_update_wpm(demodulator_t *d);

/* ── Detection modes ──────────────────────────────────────────── */

static float detect_am(iq_sample_t s) {
    return sqrtf(s.i * s.i + s.q * s.q);
}

static float detect_usb(iq_sample_t s) {
    return s.i;
}

static float detect_lsb(iq_sample_t s) {
    return s.i;
}

/* PLL constants */
#define PLL_ALPHA 0.005
#define PLL_BETA  1.5e-5

static float detect_sam(demodulator_t *d, iq_sample_t s, demod_mode_t mode) {
    double cos_p = cos(d->pll_phase);
    double sin_p = sin(d->pll_phase);

    double dot   =  s.i * cos_p + s.q * sin_p;
    double cross = -s.i * sin_p + s.q * cos_p;

    float out;
    if (mode == MODE_SAM_U)
        out = (float)(dot + cross);
    else if (mode == MODE_SAM_L)
        out = (float)(dot - cross);
    else
        out = (float)dot;

    double error = atan2(cross, dot);
    d->pll_freq += PLL_BETA * error;
    d->pll_phase += d->pll_freq + PLL_ALPHA * error;
    d->pll_phase = fmod(d->pll_phase + M_PI, 2.0 * M_PI) - M_PI;
    if (d->pll_freq > 0.5) d->pll_freq = 0.5;
    if (d->pll_freq < -0.5) d->pll_freq = -0.5;

    return out;
}

static float detect_cw(demodulator_t *d, float audio_sample, int is_minus) {
    float bfo_offset = is_minus ? -CW_BFO_HZ : CW_BFO_HZ;
    float phase_inc = 2.0f * (float)M_PI * bfo_offset / (float)AUDIO_SAMPLE_RATE;
    d->cw_bfo_phase += phase_inc;
    if (d->cw_bfo_phase > 2.0 * M_PI) d->cw_bfo_phase -= 2.0 * M_PI;
    if (d->cw_bfo_phase < -2.0 * M_PI) d->cw_bfo_phase += 2.0 * M_PI;

    float mixed = audio_sample * cosf((float)d->cw_bfo_phase);

    /* Narrow CW filter */
    float filtered = apply_fir_real(d->cw_fir_taps, d->cw_fir_buf,
                                    &d->cw_fir_pos, DEMOD_CW_FIR_TAPS, mixed);

    /* APF */
    if (d->apf_enabled) {
        float y = d->apf_b0 * filtered + d->apf_b1 * d->apf_x1 + d->apf_b2 * d->apf_x2
                - d->apf_a1 * d->apf_y1 - d->apf_a2 * d->apf_y2;
        d->apf_x2 = d->apf_x1; d->apf_x1 = filtered;
        d->apf_y2 = d->apf_y1; d->apf_y1 = y;
        filtered = y;
    }

    /* Tone analysis buffer */
    d->cw_tone_buf[d->cw_tone_pos] = filtered;
    d->cw_tone_pos = (d->cw_tone_pos + 1) % CW_FFT_SIZE;

    /* CW keying envelope tracker (per-sample) */
    float abs_s = fabsf(filtered);
    if (abs_s > d->cw_envelope)
        d->cw_envelope += CW_ENV_ATTACK * (abs_s - d->cw_envelope);
    else
        d->cw_envelope += CW_ENV_DECAY * (abs_s - d->cw_envelope);
    if (d->cw_envelope > d->cw_peak_env)
        d->cw_peak_env = d->cw_envelope;
    else
        d->cw_peak_env *= CW_PEAK_DECAY;

    /* Per-sample keying state machine */
    {
        int64_t sample_pos = d->cw_sample_count;
        int min_edge_samples = (int)(CW_MIN_EDGE_MS * AUDIO_SAMPLE_RATE / 1000.0f);
        bool key_now;
        if (d->cw_key_state)
            key_now = d->cw_envelope > d->cw_peak_env * CW_THRESHOLD_DN && d->cw_peak_env > CW_MIN_PEAK;
        else
            key_now = d->cw_envelope > d->cw_peak_env * CW_THRESHOLD_UP && d->cw_peak_env > CW_MIN_PEAK;

        if (d->cw_key_state != key_now) {
            int64_t edge_dur = sample_pos - d->cw_edge_sample;
            if (edge_dur >= min_edge_samples) {
                if (d->cw_key_state && !key_now) {
                    /* Key-up: measure element duration */
                    float dur_ms = edge_dur * 1000.0f / AUDIO_SAMPLE_RATE;
                    if (dur_ms > 15.0f && dur_ms < 2000.0f) {
                        float gap_ms = 0.0f;
                        if (d->cw_last_keyup_sample > 0)
                            gap_ms = (d->cw_edge_sample - d->cw_last_keyup_sample) * 1000.0f / AUDIO_SAMPLE_RATE;
                        if (d->cw_element_count < 64)
                            d->cw_element_ms[d->cw_element_count++] = dur_ms;
                        else {
                            memmove(d->cw_element_ms, d->cw_element_ms + 1, 63 * sizeof(float));
                            d->cw_element_ms[63] = dur_ms;
                        }
                        cw_update_wpm(d);
                        if (d->cw_dit_ms > 0) {
                            if (gap_ms > d->cw_dit_ms * 4.0f && d->cw_char_len > 0) {
                                cw_decode_char(d);
                                append_morse_char(d, ' ');
                            } else if (gap_ms > d->cw_dit_ms * 2.0f && d->cw_char_len > 0) {
                                cw_decode_char(d);
                            }
                            cw_add_element(d, dur_ms < d->cw_dit_ms * 2.0f ? '.' : '-');
                        }
                    }
                    d->cw_last_keyup_sample = sample_pos;
                }
                d->cw_edge_sample = sample_pos;
                d->cw_key_state = key_now;
            }
        }
        d->cw_sample_count++;
    }

    return filtered;
}

/* ── CW Morse text append ─────────────────────────────────────── */

static void append_morse_char(demodulator_t *d, char ch) {
    if (ch == '\0') return;
    if (d->morse_text_pos >= DECODED_TEXT_LEN) {
        memmove(d->morse_text, d->morse_text + 1, DECODED_TEXT_LEN - 1);
        d->morse_text_pos = DECODED_TEXT_LEN - 1;
    }
    d->morse_text[d->morse_text_pos++] = ch;
    d->morse_text[d->morse_text_pos] = '\0';
}

static char morse_lookup(const char *code) {
    for (int i = 0; morse_table[i].code; i++) {
        if (strcmp(morse_table[i].code, code) == 0)
            return morse_table[i].ch;
    }
    return '?';
}

static void cw_decode_char(demodulator_t *d) {
    if (d->cw_char_len == 0) return;
    d->cw_current_char[d->cw_char_len] = '\0';
    char ch = morse_lookup(d->cw_current_char);
    append_morse_char(d, ch);
    d->cw_char_len = 0;
}

static void cw_add_element(demodulator_t *d, char elem) {
    if (d->cw_char_len < (int)sizeof(d->cw_current_char) - 1)
        d->cw_current_char[d->cw_char_len++] = elem;
}

/* ── CW WPM estimation (k-means dit/dah separation) ──────────── */

static int float_cmp(const void *a, const void *b) {
    float fa = *(const float *)a, fb = *(const float *)b;
    return (fa > fb) - (fa < fb);
}

static void cw_update_wpm(demodulator_t *d) {
    if (d->cw_element_count < 4) return;

    float sorted[64];
    int n = d->cw_element_count;
    memcpy(sorted, d->cw_element_ms, n * sizeof(float));
    qsort(sorted, n, sizeof(float), float_cmp);

    float boundary = sorted[n / 2];
    for (int iter = 0; iter < 5; iter++) {
        float dit_sum = 0; int dit_n = 0;
        float dah_sum = 0; int dah_n = 0;
        for (int i = 0; i < n; i++) {
            if (sorted[i] < boundary) { dit_sum += sorted[i]; dit_n++; }
            else { dah_sum += sorted[i]; dah_n++; }
        }
        if (!dit_n || !dah_n) break;
        /* Use medians for robustness */
        float dit_med = sorted[dit_n / 2];
        float dah_med = sorted[dit_n + (dah_n > 1 ? dah_n / 2 : 0)];
        boundary = (dit_med + dah_med) / 2.0f;
    }

    /* Extract dit duration from short elements */
    int dit_count = 0;
    for (int i = 0; i < n; i++)
        if (sorted[i] < boundary) dit_count++;
    if (dit_count == 0) dit_count = n / 2;
    if (dit_count > 0) {
        float dit_ms = sorted[dit_count / 2];
        if (dit_ms > 0) {
            float new_wpm = 1200.0f / dit_ms;
            if (d->cw_wpm == 0.0f)
                d->cw_wpm = new_wpm;
            else
                d->cw_wpm = CW_WPM_SMOOTH * d->cw_wpm + (1.0f - CW_WPM_SMOOTH) * new_wpm;
            if (d->cw_dit_ms == 0.0f)
                d->cw_dit_ms = dit_ms;
            else
                d->cw_dit_ms = CW_DIT_SMOOTH * d->cw_dit_ms + (1.0f - CW_DIT_SMOOTH) * dit_ms;
        }
    }
}

/* ── CW tone analysis (FFT-based peak + SNR) ─────────────────── */

static void cw_analyze_tone(demodulator_t *d) {
    /* Reorder ring buffer for FFT */
    float ordered[CW_FFT_SIZE];
    int p = d->cw_tone_pos;
    if (p > 0) {
        memcpy(ordered, d->cw_tone_buf + p, (CW_FFT_SIZE - p) * sizeof(float));
        memcpy(ordered + (CW_FFT_SIZE - p), d->cw_tone_buf, p * sizeof(float));
    } else {
        memcpy(ordered, d->cw_tone_buf, CW_FFT_SIZE * sizeof(float));
    }

    /* Apply Hanning window */
    for (int i = 0; i < CW_FFT_SIZE; i++) {
        float w = 0.5f * (1.0f - cosf(2.0f * (float)M_PI * i / (CW_FFT_SIZE - 1)));
        ordered[i] *= w;
    }

    /* Real FFT using cached FFTW plan */
    int spec_len = CW_FFT_SIZE / 2 + 1;
    fftwf_execute_dft_r2c(d->cw_fft_plan, ordered, d->cw_fft_out);

    /* Power spectrum */
    float spec[spec_len];
    for (int i = 0; i < spec_len; i++)
        spec[i] = d->cw_fft_out[i][0] * d->cw_fft_out[i][0]
                + d->cw_fft_out[i][1] * d->cw_fft_out[i][1];

    /* Search passband around BFO frequency */
    float bin_hz = (float)AUDIO_SAMPLE_RATE / CW_FFT_SIZE;
    int lo = (int)((CW_BFO_HZ - d->bandwidth_hz) / bin_hz);
    int hi = (int)((CW_BFO_HZ + d->bandwidth_hz) / bin_hz);
    if (lo < 1) lo = 1;
    if (hi >= spec_len - 1) hi = spec_len - 2;
    int pb_len = hi - lo + 1;
    if (pb_len < 3) return;

    /* Find peak in passband */
    int pk = 0;
    float pk_val = 0;
    float total = 0;
    for (int i = 0; i < pb_len; i++) {
        float v = spec[lo + i];
        total += v;
        if (v > pk_val) { pk_val = v; pk = i; }
    }

    /* Tone detection: check energy concentration */
    bool tone = (total > 0 && pb_len > 2) ? (pk_val / total > CW_TONE_CONCENTRATION) : false;
    d->cw_tone_present = tone;

    int pk_abs = lo + pk;

    if (tone && pk_abs > 0 && pk_abs < spec_len - 1) {
        /* SNR: peak vs noise floor */
        float noise_sum = 0;
        int noise_n = 0;
        for (int i = 0; i < pb_len; i++) {
            if (i < pk - 1 || i > pk + 1) {
                noise_sum += spec[lo + i];
                noise_n++;
            }
        }
        if (noise_n > 0) {
            float noise_mean = noise_sum / noise_n;
            if (noise_mean > 0) {
                float snr = 10.0f * log10f(pk_val / noise_mean);
                d->cw_snr = CW_SNR_SMOOTH * d->cw_snr + (1.0f - CW_SNR_SMOOTH) * snr;
            }
        }

        /* Parabolic interpolation for sub-bin accuracy */
        float a = spec[pk_abs - 1], b = spec[pk_abs], c = spec[pk_abs + 1];
        float denom = a - 2.0f * b + c;
        float delta = (fabsf(denom) > 1e-20f) ? 0.5f * (a - c) / denom : 0.0f;
        float peak_hz = (pk_abs + delta) * bin_hz;

        if (d->cw_peak_hz == 0.0f)
            d->cw_peak_hz = peak_hz;
        else
            d->cw_peak_hz = CW_PEAK_HZ_SMOOTH * d->cw_peak_hz + (1.0f - CW_PEAK_HZ_SMOOTH) * peak_hz;
    } else {
        d->cw_snr *= CW_SNR_SMOOTH;
    }
}

static void append_decoded_char(demodulator_t *d, char ch) {
    if (ch == '\0' || ch == '\r') return;
    if (d->decoded_text_pos >= DECODED_TEXT_LEN) {
        memmove(d->decoded_text, d->decoded_text + 1, DECODED_TEXT_LEN - 1);
        d->decoded_text_pos = DECODED_TEXT_LEN - 1;
    }
    d->decoded_text[d->decoded_text_pos++] = ch;
    d->decoded_text[d->decoded_text_pos] = '\0';
}

static float detect_rtty(demodulator_t *d, float audio_sample) {
    float mark = apply_fir_real(d->rtty_mark_fir, d->rtty_mark_buf,
                                &d->rtty_fir_pos, DEMOD_CW_FIR_TAPS, audio_sample);
    float space = apply_fir_real(d->rtty_space_fir, d->rtty_space_buf,
                                 &d->rtty_fir_pos, DEMOD_CW_FIR_TAPS, audio_sample);

    float mark_env = fabsf(mark);
    float space_env = fabsf(space);

    float raw_discrim = mark_env - space_env;
    d->rtty_discrim = d->rtty_discrim * 0.7f + raw_discrim * 0.3f;

    int samples_per_bit = (int)((float)AUDIO_SAMPLE_RATE / RTTY_BAUD);
    d->rtty_bit_phase++;

    switch (d->rtty_state) {
    case 0:
        if (d->rtty_discrim < 0.0f) {
            d->rtty_state = 1;
            d->rtty_bit_phase = 0;
        }
        break;
    case 1:
        if (d->rtty_bit_phase >= samples_per_bit / 2) {
            if (d->rtty_discrim < 0.0f) {
                d->rtty_state = 2;
                d->rtty_bit_count = 0;
                d->rtty_shift_reg = 0;
                d->rtty_bit_phase = 0;
            } else {
                d->rtty_state = 0;
            }
        }
        break;
    case 2:
        if (d->rtty_bit_phase >= samples_per_bit) {
            d->rtty_bit_phase = 0;
            int bit = (d->rtty_discrim > 0.0f) ? 1 : 0;
            d->rtty_shift_reg |= (bit << d->rtty_bit_count);
            d->rtty_bit_count++;
            if (d->rtty_bit_count >= 5) {
                d->rtty_state = 3;
            }
        }
        break;
    case 3:
        if (d->rtty_bit_phase >= samples_per_bit) {
            uint8_t code = d->rtty_shift_reg & 0x1F;
            if (code == 0x1F) {
                d->rtty_figs_mode = false;
            } else if (code == 0x1B) {
                d->rtty_figs_mode = true;
            } else {
                char ch = d->rtty_figs_mode ? ita2_figures[code] : ita2_letters[code];
                if (ch >= ' ' || ch == '\n')
                    append_decoded_char(d, ch);
            }
            d->rtty_state = 0;
        }
        break;
    }

    return audio_sample;
}

static float detect_psk31(demodulator_t *d, float audio_sample) {
    float phase_inc = 2.0f * (float)M_PI * PSK31_CARRIER_HZ / (float)AUDIO_SAMPLE_RATE;
    d->psk_lo_phase += phase_inc;
    if (d->psk_lo_phase > 2.0 * M_PI) d->psk_lo_phase -= 2.0 * M_PI;

    float lo_i = cosf((float)d->psk_lo_phase);
    float lo_q = -sinf((float)d->psk_lo_phase);

    float bb_i = audio_sample * lo_i;
    float bb_q = audio_sample * lo_q;

    d->psk_acc_i += bb_i;
    d->psk_acc_q += bb_q;

    int samples_per_sym = (int)((float)AUDIO_SAMPLE_RATE / PSK31_BAUD);
    d->psk_sample_count++;

    if (d->psk_sample_count >= samples_per_sym) {
        d->psk_sample_count = 0;

        float mag = sqrtf(d->psk_acc_i * d->psk_acc_i + d->psk_acc_q * d->psk_acc_q);
        if (mag > 1e-10f) {
            d->psk_acc_i /= mag;
            d->psk_acc_q /= mag;
        }

        float dot = d->psk_acc_i * d->psk_prev_i + d->psk_acc_q * d->psk_prev_q;
        int bit = (dot > 0.0f) ? 1 : 0;

        d->psk_prev_i = d->psk_acc_i;
        d->psk_prev_q = d->psk_acc_q;
        d->psk_acc_i = 0.0f;
        d->psk_acc_q = 0.0f;

        d->psk_bit_buf = (d->psk_bit_buf << 1) | bit;
        d->psk_bit_count++;

        if ((d->psk_bit_buf & 0x3) == 0 && d->psk_bit_count > 2) {
            unsigned int code = d->psk_bit_buf >> 2;
            if (code > 0 && code < 4096) {
                char ch = varicode_reverse[code];
                if (ch >= ' ' && ch < 127)
                    append_decoded_char(d, ch);
            }
            d->psk_bit_buf = 0;
            d->psk_bit_count = 0;
        }

        if (d->psk_bit_count > 20) {
            d->psk_bit_buf = 0;
            d->psk_bit_count = 0;
        }
    }

    return audio_sample;
}

/* ── Spectral DNR (STFT spectral gate) ────────────────────────── */

/* DNR level presets: (gate_threshold, gain_floor) */
static const float dnr_gate_thresh[] = { 0.0f, 2.0f, 3.0f, 5.0f };
static const float dnr_gain_floor[]  = { 0.0f, 0.15f, 0.08f, 0.03f };

static int compare_floats(const void *a, const void *b) {
    float fa = *(const float *)a;
    float fb = *(const float *)b;
    if (fa < fb) return -1;
    if (fa > fb) return 1;
    return 0;
}

static float percentile(float *data, int n, int pct) {
    /* Sort a copy and return the pct-th percentile */
    float sorted[n]; /* VLA */
    memcpy(sorted, data, n * sizeof(float));
    qsort(sorted, n, sizeof(float), compare_floats);
    int idx = n * pct / 100;
    if (idx >= n) idx = n - 1;
    return sorted[idx];
}

/* Apply spectral DNR to audio buffer in-place.
 * Returns the number of output samples (may be less than input due to STFT framing). */
static int apply_dnr(demodulator_t *d, float *audio, int n_audio,
                     float *out_buf, int max_out) {
    if (d->dnr_level == DNR_OFF) {
        /* Pass through */
        int copy = n_audio < max_out ? n_audio : max_out;
        memcpy(out_buf, audio, copy * sizeof(float));
        return copy;
    }

    float gate_thresh = dnr_gate_thresh[d->dnr_level];
    float gain_floor = dnr_gain_floor[d->dnr_level];
    int out_pos = 0;

    /* Append input to accumulation buffer */
    int space = DNR_FFT_SIZE * 2 - d->dnr_in_len;
    int to_copy = n_audio < space ? n_audio : space;
    memcpy(d->dnr_in_buf + d->dnr_in_len, audio, to_copy * sizeof(float));
    d->dnr_in_len += to_copy;

    /* Process complete frames */
    while (d->dnr_in_len >= DNR_FFT_SIZE && out_pos + DNR_HOP <= max_out) {
        float frame[DNR_FFT_SIZE];
        for (int i = 0; i < DNR_FFT_SIZE; i++)
            frame[i] = d->dnr_in_buf[i] * d->dnr_window[i];

        /* FFT (real -> complex) */
        fftwf_complex fft_out[DNR_BINS];
        fftwf_plan p = fftwf_plan_dft_r2c_1d(DNR_FFT_SIZE, frame, fft_out, FFTW_ESTIMATE);
        fftwf_execute(p);
        fftwf_destroy_plan(p);

        /* Compute power spectrum */
        float power[DNR_BINS];
        for (int i = 0; i < DNR_BINS; i++)
            power[i] = fft_out[i][0] * fft_out[i][0] + fft_out[i][1] * fft_out[i][1];

        d->dnr_frame_count++;

        /* Noise floor: percentile of passband bins (skip DC) */
        float bin_hz = (float)AUDIO_SAMPLE_RATE / (float)DNR_FFT_SIZE;
        int bw_bins = (int)((float)d->bandwidth_hz / bin_hz);
        if (bw_bins < 4) bw_bins = 4;
        if (bw_bins >= DNR_BINS) bw_bins = DNR_BINS - 1;

        float passband[bw_bins];
        memcpy(passband, power + 1, bw_bins * sizeof(float));
        float frame_noise = percentile(passband, bw_bins, DNR_NOISE_PERCENTILE);

        if (d->dnr_noise_est == 0.0f)
            d->dnr_noise_est = frame_noise;
        else
            d->dnr_noise_est = DNR_NOISE_SMOOTH * d->dnr_noise_est
                             + (1.0f - DNR_NOISE_SMOOTH) * frame_noise;

        float noise_floor = d->dnr_noise_est;
        if (noise_floor < 1e-20f) noise_floor = 1e-20f;

        /* Spectral gate: smooth transition from floor to 1.0 */
        float gain[DNR_BINS];
        for (int i = 0; i < DNR_BINS; i++) {
            float snr_bin = power[i] / noise_floor;
            if (snr_bin >= gate_thresh)
                gain[i] = 1.0f;
            else if (snr_bin <= 1.0f)
                gain[i] = gain_floor;
            else
                gain[i] = gain_floor + (1.0f - gain_floor) * (snr_bin - 1.0f) / (gate_thresh - 1.0f);
        }
        /* Always pass DC (carrier in AM) */
        gain[0] = 1.0f;

        /* Temporal smoothing */
        for (int i = 0; i < DNR_BINS; i++)
            gain[i] = DNR_GAIN_SMOOTH * d->dnr_prev_gain[i] + (1.0f - DNR_GAIN_SMOOTH) * gain[i];
        memcpy(d->dnr_prev_gain, gain, sizeof(float) * DNR_BINS);

        /* Ramp during initial frames */
        if (d->dnr_frame_count <= DNR_RAMP_FRAMES) {
            float ramp = (float)d->dnr_frame_count / (float)DNR_RAMP_FRAMES;
            for (int i = 0; i < DNR_BINS; i++)
                gain[i] = 1.0f - ramp * (1.0f - gain[i]);
        }

        /* Apply gain to spectrum */
        for (int i = 0; i < DNR_BINS; i++) {
            fft_out[i][0] *= gain[i];
            fft_out[i][1] *= gain[i];
        }

        /* Inverse FFT */
        float ifft_out[DNR_FFT_SIZE];
        fftwf_plan ip = fftwf_plan_dft_c2r_1d(DNR_FFT_SIZE, fft_out, ifft_out, FFTW_ESTIMATE);
        fftwf_execute(ip);
        fftwf_destroy_plan(ip);

        /* Normalize FFTW output and apply synthesis window */
        float norm = 1.0f / (float)DNR_FFT_SIZE;
        for (int i = 0; i < DNR_FFT_SIZE; i++)
            ifft_out[i] *= norm * d->dnr_synth_window[i];

        /* Overlap-add: add previous tail to first half, save second half as tail */
        for (int i = 0; i < DNR_HOP; i++)
            out_buf[out_pos + i] = ifft_out[i] + d->dnr_prev_tail[i];
        memcpy(d->dnr_prev_tail, ifft_out + DNR_HOP, DNR_HOP * sizeof(float));
        out_pos += DNR_HOP;

        /* Shift input buffer by hop */
        d->dnr_in_len -= DNR_HOP;
        memmove(d->dnr_in_buf, d->dnr_in_buf + DNR_HOP, d->dnr_in_len * sizeof(float));
    }

    return out_pos;
}

/* ── Auto Notch (STFT tone detection & removal) ──────────────── */

static float median_of(float *data, int n) {
    if (n <= 0) return 0.0f;
    float sorted[n]; /* VLA */
    memcpy(sorted, data, n * sizeof(float));
    qsort(sorted, n, sizeof(float), compare_floats);
    return sorted[n / 2];
}

static int apply_auto_notch(demodulator_t *d, float *audio, int n_audio,
                            float *out_buf, int max_out) {
    if (!d->auto_notch) {
        int copy = n_audio < max_out ? n_audio : max_out;
        memcpy(out_buf, audio, copy * sizeof(float));
        return copy;
    }

    int out_pos = 0;

    /* Append input to accumulation buffer */
    int space = AN_FFT_SIZE * 2 - d->an_in_len;
    int to_copy = n_audio < space ? n_audio : space;
    memcpy(d->an_in_buf + d->an_in_len, audio, to_copy * sizeof(float));
    d->an_in_len += to_copy;

    while (d->an_in_len >= AN_FFT_SIZE && out_pos + AN_HOP <= max_out) {
        float frame[AN_FFT_SIZE];
        for (int i = 0; i < AN_FFT_SIZE; i++)
            frame[i] = d->an_in_buf[i] * d->an_window[i];

        /* FFT */
        fftwf_complex fft_out[AN_BINS];
        fftwf_plan p = fftwf_plan_dft_r2c_1d(AN_FFT_SIZE, frame, fft_out, FFTW_ESTIMATE);
        fftwf_execute(p);
        fftwf_destroy_plan(p);

        float power[AN_BINS];
        for (int i = 0; i < AN_BINS; i++)
            power[i] = fft_out[i][0] * fft_out[i][0] + fft_out[i][1] * fft_out[i][1];

        d->an_frame_count++;

        /* Detect peaks: compare each bin to local median of neighbors */
        float bin_gain[AN_BINS];
        for (int i = 0; i < AN_BINS; i++)
            bin_gain[i] = 1.0f;

        for (int b = 1; b < AN_BINS - 1; b++) {
            int lo = b - AN_NEIGHBOR_BINS;
            if (lo < 1) lo = 1;
            int hi = b + AN_NEIGHBOR_BINS + 1;
            if (hi > AN_BINS - 1) hi = AN_BINS - 1;

            int notch_lo = b - AN_NOTCH_HALFWIDTH;
            if (notch_lo < 1) notch_lo = 1;
            int notch_hi = b + AN_NOTCH_HALFWIDTH + 1;
            if (notch_hi > AN_BINS - 1) notch_hi = AN_BINS - 1;

            /* Collect neighbor bins excluding the notch region */
            float neighbors[AN_NEIGHBOR_BINS * 2 + 2];
            int nn = 0;
            for (int j = lo; j < notch_lo; j++)
                neighbors[nn++] = power[j];
            for (int j = notch_hi; j < hi; j++)
                neighbors[nn++] = power[j];

            if (nn == 0) continue;
            float local_med = median_of(neighbors, nn);
            if (local_med > 0 && power[b] > local_med * AN_PEAK_THRESH)
                bin_gain[b] = 0.0f;
        }

        /* Expand notch to halfwidth around detected peaks */
        float expanded_gain[AN_BINS];
        memcpy(expanded_gain, bin_gain, sizeof(float) * AN_BINS);
        for (int b = 1; b < AN_BINS - 1; b++) {
            if (bin_gain[b] == 0.0f) {
                int lo = b - AN_NOTCH_HALFWIDTH;
                if (lo < 1) lo = 1;
                int hi = b + AN_NOTCH_HALFWIDTH + 1;
                if (hi > AN_BINS) hi = AN_BINS;
                for (int j = lo; j < hi; j++)
                    expanded_gain[j] = 0.0f;
            }
        }

        /* Track persistent tones across frames */
        for (int i = 0; i < AN_BINS; i++) {
            d->an_persist[i] = AN_PERSIST_SMOOTH * d->an_persist[i]
                             + (1.0f - AN_PERSIST_SMOOTH) * (1.0f - expanded_gain[i]);
        }

        /* Apply notch only where persistence exceeds threshold */
        float notch_gain[AN_BINS];
        for (int i = 0; i < AN_BINS; i++)
            notch_gain[i] = (d->an_persist[i] > 0.3f) ? 0.01f : 1.0f;
        notch_gain[0] = 1.0f; /* Always pass DC */

        /* Temporal smoothing */
        for (int i = 0; i < AN_BINS; i++)
            notch_gain[i] = AN_GAIN_SMOOTH * d->an_prev_gain[i]
                          + (1.0f - AN_GAIN_SMOOTH) * notch_gain[i];
        memcpy(d->an_prev_gain, notch_gain, sizeof(float) * AN_BINS);

        /* Ramp during initial frames */
        if (d->an_frame_count <= AN_RAMP_FRAMES) {
            float ramp = (float)d->an_frame_count / (float)AN_RAMP_FRAMES;
            for (int i = 0; i < AN_BINS; i++)
                notch_gain[i] = 1.0f - ramp * (1.0f - notch_gain[i]);
        }

        /* Apply gain */
        for (int i = 0; i < AN_BINS; i++) {
            fft_out[i][0] *= notch_gain[i];
            fft_out[i][1] *= notch_gain[i];
        }

        /* Inverse FFT */
        float ifft_out[AN_FFT_SIZE];
        fftwf_plan ip = fftwf_plan_dft_c2r_1d(AN_FFT_SIZE, fft_out, ifft_out, FFTW_ESTIMATE);
        fftwf_execute(ip);
        fftwf_destroy_plan(ip);

        float norm = 1.0f / (float)AN_FFT_SIZE;
        for (int i = 0; i < AN_FFT_SIZE; i++)
            ifft_out[i] *= norm * d->an_synth_window[i];

        /* Overlap-add */
        for (int i = 0; i < AN_HOP; i++)
            out_buf[out_pos + i] = ifft_out[i] + d->an_prev_tail[i];
        memcpy(d->an_prev_tail, ifft_out + AN_HOP, AN_HOP * sizeof(float));
        out_pos += AN_HOP;

        /* Shift input buffer */
        d->an_in_len -= AN_HOP;
        memmove(d->an_in_buf, d->an_in_buf + AN_HOP, d->an_in_len * sizeof(float));
    }

    return out_pos;
}

/* ── SNR estimator ────────────────────────────────────────────── */

static void measure_snr(demodulator_t *d, iq_sample_t sample) {
    /* Accumulate decimated IQ into ring buffer */
    d->snr_buf_i[d->snr_buf_pos] = sample.i;
    d->snr_buf_q[d->snr_buf_pos] = sample.q;
    d->snr_buf_pos++;

    if (d->snr_buf_pos < SNR_FFT_SIZE)
        return;

    /* Buffer full — compute SNR */
    d->snr_buf_pos = SNR_FFT_SIZE / 2; /* keep overlap */
    memmove(d->snr_buf_i, d->snr_buf_i + SNR_FFT_SIZE / 2, (SNR_FFT_SIZE / 2) * sizeof(float));
    memmove(d->snr_buf_q, d->snr_buf_q + SNR_FFT_SIZE / 2, (SNR_FFT_SIZE / 2) * sizeof(float));

    /* Window and compute complex FFT via two real FFTs:
     * Pack I in real, Q in imaginary, do complex FFT */
    fftwf_complex fft_in[SNR_FFT_SIZE];
    fftwf_complex fft_out[SNR_FFT_SIZE];

    float hann[SNR_FFT_SIZE];
    hann_window(hann, SNR_FFT_SIZE);

    /* Use the second half of the original buffer (before we shifted) */
    for (int i = 0; i < SNR_FFT_SIZE; i++) {
        /* We need the full original buffer — reconstruct from shifted data.
         * After memmove, positions 0..N/2-1 have the overlap, but we already
         * overwrote. Instead, let's just use a simpler approach: FFT the
         * I and Q channels separately as a real-valued power spectrum. */
        fft_in[i][0] = d->snr_buf_i[i] * hann[i]; /* Use what we have */
        fft_in[i][1] = d->snr_buf_q[i] * hann[i];
    }

    fftwf_plan p = fftwf_plan_dft_1d(SNR_FFT_SIZE, fft_in, fft_out, FFTW_FORWARD, FFTW_ESTIMATE);
    fftwf_execute(p);
    fftwf_destroy_plan(p);

    /* Power spectrum */
    float spec_power[SNR_FFT_SIZE];
    for (int i = 0; i < SNR_FFT_SIZE; i++)
        spec_power[i] = fft_out[i][0] * fft_out[i][0] + fft_out[i][1] * fft_out[i][1];

    /* Select passband bins (±bandwidth around DC) */
    float bin_hz = (float)AUDIO_SAMPLE_RATE / (float)SNR_FFT_SIZE;
    int bw_bins = (int)((float)d->bandwidth_hz / bin_hz);
    if (bw_bins < 2) bw_bins = 2;
    if (bw_bins > SNR_FFT_SIZE / 2) bw_bins = SNR_FFT_SIZE / 2;

    /* DC-centered: bins 0..bw_bins and (N-bw_bins)..N */
    int pb_len = bw_bins * 2;
    float passband[pb_len];
    memcpy(passband, spec_power, bw_bins * sizeof(float));
    memcpy(passband + bw_bins, spec_power + SNR_FFT_SIZE - bw_bins, bw_bins * sizeof(float));

    if (pb_len < 4) return;

    /* Total passband power */
    float total_power = 0.0f;
    for (int i = 0; i < pb_len; i++)
        total_power += passband[i];
    total_power /= (float)pb_len;

    /* Noise floor: median (robust to carriers/tones) */
    float noise_floor = median_of(passband, pb_len);

    /* Smooth estimates */
    if (d->snr_signal_power == 0.0f) {
        d->snr_signal_power = total_power;
        d->snr_noise_floor = noise_floor;
    } else {
        d->snr_signal_power = SNR_SMOOTH * d->snr_signal_power
                            + (1.0f - SNR_SMOOTH) * total_power;
        /* Asymmetric noise tracking */
        float rate = (noise_floor > d->snr_noise_floor) ? SNR_NOISE_UP : SNR_NOISE_DOWN;
        d->snr_noise_floor += rate * (noise_floor - d->snr_noise_floor);
    }

    if (d->snr_noise_floor > 1e-20f) {
        float ratio = d->snr_signal_power / d->snr_noise_floor;
        if (ratio > 1.0f) {
            d->snr_db = 10.0f * log10f(ratio - 1.0f);
            if (d->snr_db < 0.0f) d->snr_db = 0.0f;
            if (d->snr_db > 60.0f) d->snr_db = 60.0f;
        } else {
            d->snr_db = 0.0f;
        }
    }
}

/* ── Block-based AGC (RMS) ────────────────────────────────────── */

static void apply_agc_block(demodulator_t *d, float *audio, int n) {
    if (!d->agc_enabled || n == 0) return;

    /* Compute block RMS */
    float sum_sq = 0.0f;
    for (int i = 0; i < n; i++)
        sum_sq += audio[i] * audio[i];
    float rms = sqrtf(sum_sq / (float)n);

    if (rms < 1e-15f) {
        /* Signal is essentially zero — apply current gain */
        for (int i = 0; i < n; i++)
            audio[i] *= d->agc_gain;
        return;
    }

    float desired_gain = AGC_TARGET / rms;
    float rate;
    if (desired_gain < d->agc_gain)
        rate = 0.1f;    /* Fast attack */
    else
        rate = 0.005f;  /* Slow decay */

    d->agc_gain += rate * (desired_gain - d->agc_gain);

    if (d->agc_gain > 100000.0f) d->agc_gain = 100000.0f;
    if (d->agc_gain < 0.001f) d->agc_gain = 0.001f;

    for (int i = 0; i < n; i++)
        audio[i] *= d->agc_gain;
}

/* ── Block-based DC removal ───────────────────────────────────── */

static void dc_remove_block(demodulator_t *d, float *audio, int n) {
    if (n == 0) return;

    /* Compute block mean */
    float sum = 0.0f;
    for (int i = 0; i < n; i++)
        sum += audio[i];
    float block_mean = sum / (float)n;

    /* Smooth DC estimate */
    d->dc_avg = 0.99f * d->dc_avg + 0.01f * block_mean;

    /* Remove DC */
    for (int i = 0; i < n; i++)
        audio[i] -= d->dc_avg;
}

/* ── Main processing ──────────────────────────────────────────── */

int demod_process(demodulator_t *d, const uint8_t *iq_data, int iq_bytes,
                  float *out_audio, int max_audio) {
    pthread_mutex_lock(&d->lock);

    int bytes_per_sample = 8; /* 4 bytes I + 4 bytes Q */
    int num_iq = iq_bytes / bytes_per_sample;

    /* First pass: detect into a temp buffer (pre-DNR/notch/AGC) */
    /* Max audio samples from decimation */
    int max_raw = num_iq / DEMOD_DECIMATE + 1;
    float raw_audio[max_raw]; /* VLA */
    int raw_count = 0;

    for (int i = 0; i < num_iq; i++) {
        const uint8_t *p = iq_data + i * bytes_per_sample;

        int32_t i_raw = (int32_t)(p[0] | (p[1] << 8) | (p[2] << 16) | (p[3] << 24));
        int32_t q_raw = (int32_t)(p[4] | (p[5] << 8) | (p[6] << 16) | (p[7] << 24));
        iq_sample_t sample = {
            .i = (float)i_raw / 2147483648.0f,
            .q = (float)q_raw / 2147483648.0f
        };

        /* RIT frequency shift (NCO mixer) */
        if (d->rit_offset_hz != 0.0) {
            double phase_inc = 2.0 * M_PI * d->rit_offset_hz / IQ_SAMPLE_RATE;
            float cos_p = (float)cos(d->rit_phase);
            float sin_p = (float)sin(d->rit_phase);
            float si = sample.i * cos_p - sample.q * sin_p;
            float sq = sample.i * sin_p + sample.q * cos_p;
            sample.i = si;
            sample.q = sq;
            d->rit_phase += phase_inc;
            if (d->rit_phase > 2.0 * M_PI) d->rit_phase -= 2.0 * M_PI;
            if (d->rit_phase < -2.0 * M_PI) d->rit_phase += 2.0 * M_PI;
        }

        /* Noise blanker */
        sample = noise_blank(d, sample);

        /* FIR lowpass + decimation */
        iq_sample_t filtered = apply_fir_iq(d, sample);
        d->decim_counter++;
        if (d->decim_counter < DEMOD_DECIMATE)
            continue;
        d->decim_counter = 0;

        /* SNR measurement (on decimated IQ) */
        measure_snr(d, filtered);

        /* Detection */
        float audio;
        switch (d->mode) {
        case MODE_AM:
            audio = detect_am(filtered);
            break;
        case MODE_SAM:
        case MODE_SAM_U:
        case MODE_SAM_L:
            audio = detect_sam(d, filtered, d->mode);
            break;
        case MODE_USB:
            audio = detect_usb(filtered);
            break;
        case MODE_LSB:
            audio = detect_lsb(filtered);
            break;
        case MODE_CW_PLUS:
            audio = detect_cw(d, filtered.i, 0);
            break;
        case MODE_CW_MINUS:
            audio = detect_cw(d, filtered.i, 1);
            break;
        case MODE_RTTY:
            audio = detect_rtty(d, filtered.i);
            break;
        case MODE_PSK31:
            audio = detect_psk31(d, filtered.i);
            break;
        case MODE_DRM:
            audio = 0.0f;
            break;
        default:
            audio = 0.0f;
            break;
        }

        if (raw_count < max_raw)
            raw_audio[raw_count++] = audio;
    }

    if (raw_count == 0) {
        pthread_mutex_unlock(&d->lock);
        return 0;
    }

    /* CW tone analysis (periodic FFT) + silence flush */
    if (d->mode == MODE_CW_PLUS || d->mode == MODE_CW_MINUS) {
        d->cw_analyze_counter += raw_count;
        if (d->cw_analyze_counter >= CW_ANALYZE_INTERVAL) {
            d->cw_analyze_counter = 0;
            cw_analyze_tone(d);
        }
        /* Flush character on long silence */
        if (!d->cw_key_state && d->cw_dit_ms > 0 && d->cw_char_len > 0
            && d->cw_last_keyup_sample > 0) {
            float silence_ms = (d->cw_sample_count - d->cw_last_keyup_sample)
                               * 1000.0f / AUDIO_SAMPLE_RATE;
            if (silence_ms > d->cw_dit_ms * 4.0f) {
                cw_decode_char(d);
                append_morse_char(d, ' ');
            }
        }
    }

    /* Post-detection processing pipeline (block-based) */

    /* 1. Spectral DNR */
    float dnr_buf[raw_count + DNR_FFT_SIZE]; /* VLA, generous size */
    int dnr_count = apply_dnr(d, raw_audio, raw_count, dnr_buf, raw_count + DNR_FFT_SIZE);

    if (dnr_count == 0) {
        /* DNR hasn't accumulated enough for a frame yet — no output */
        pthread_mutex_unlock(&d->lock);
        return 0;
    }

    /* 2. Auto notch */
    float notch_buf[dnr_count + AN_FFT_SIZE]; /* VLA */
    int notch_count = apply_auto_notch(d, dnr_buf, dnr_count, notch_buf, dnr_count + AN_FFT_SIZE);

    if (notch_count == 0) {
        pthread_mutex_unlock(&d->lock);
        return 0;
    }

    /* 3. DC removal (block-based) */
    dc_remove_block(d, notch_buf, notch_count);

    /* 4. AGC (block-based RMS) */
    apply_agc_block(d, notch_buf, notch_count);

    /* 5. Volume, mute, clip */
    int audio_out = notch_count < max_audio ? notch_count : max_audio;
    for (int i = 0; i < audio_out; i++) {
        float s = notch_buf[i];
        if (d->muted)
            s = 0.0f;
        else
            s *= d->volume;
        if (s > 1.0f) s = 1.0f;
        if (s < -1.0f) s = -1.0f;
        out_audio[i] = s;
    }

    pthread_mutex_unlock(&d->lock);
    return audio_out;
}

void demod_destroy(demodulator_t *d) {
    if (d->cw_fft_plan) fftwf_destroy_plan(d->cw_fft_plan);
    if (d->cw_fft_out)  fftwf_free(d->cw_fft_out);
    pthread_mutex_destroy(&d->lock);
}
