/* drm.c — DRM decoder integration via Dream 2.2 subprocess. */

#include "drm.h"
#include "dsp.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <errno.h>
#include <sys/wait.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <math.h>
#include <limits.h>
#include <libgen.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

void drm_init(drm_decoder_t *d) {
    memset(d, 0, sizeof(*d));
    pthread_mutex_init(&d->lock, NULL);
    d->pid = -1;
    d->stdin_fd = -1;
    d->stdout_fd = -1;
    d->stderr_fd = -1;
    d->status_fd = -1;

    /* Decimation filter: 192k -> 48k (factor 4) */
    demod_design_fir(d->decim_taps, 127, 24000.0f, 192000.0f);
}

bool drm_find_binary(drm_decoder_t *d) {
    /* Resolve executable directory via /proc/self/exe */
    char exe_path[PATH_MAX];
    ssize_t elen = readlink("/proc/self/exe", exe_path, sizeof(exe_path) - 1);
    char exe_dir[PATH_MAX] = "";
    if (elen > 0) {
        exe_path[elen] = '\0';
        char *dir = dirname(exe_path);
        snprintf(exe_dir, sizeof(exe_dir), "%s", dir);
    }

    /* Try relative to executable: exe_dir/../DRM/dream-2.2/dream etc. */
    if (exe_dir[0]) {
        const char *suffixes[] = {
            "/../DRM/dream-2.2/dream",
            "/../DRM/dream",
            "/../../DRM/dream-2.2/dream",
            "/../../DRM/dream",
            "/../../../DRM/dream-2.2/dream",
            "/../../../DRM/dream",
            NULL
        };
        for (int i = 0; suffixes[i]; i++) {
            char candidate[PATH_MAX];
            snprintf(candidate, sizeof(candidate), "%s%s", exe_dir, suffixes[i]);
            /* Resolve to absolute path */
            char resolved[PATH_MAX];
            if (realpath(candidate, resolved) && access(resolved, X_OK) == 0) {
                snprintf(d->dream_path, sizeof(d->dream_path), "%s", resolved);
                fprintf(stderr, "DRM: Found Dream at %s\n", d->dream_path);
                return true;
            }
        }
    }

    /* Try PATH */
    FILE *fp = popen("which dream 2>/dev/null", "r");
    if (fp) {
        char found = 0;
        if (fgets(d->dream_path, sizeof(d->dream_path), fp)) {
            char *nl = strchr(d->dream_path, '\n');
            if (nl) *nl = '\0';
            if (d->dream_path[0]) found = 1;
        }
        pclose(fp);
        if (found) {
            fprintf(stderr, "DRM: Found Dream in PATH at %s\n", d->dream_path);
            return true;
        }
    }

    fprintf(stderr, "DRM: Dream binary not found (exe_dir=%s)\n", exe_dir);
    return false;
}

/* Audio reader thread: reads Dream stdout */
static void *audio_reader(void *arg) {
    drm_decoder_t *d = (drm_decoder_t *)arg;
    int16_t buf[2048];
    float audio[1024];

    while (d->running) {
        ssize_t n = read(d->stdout_fd, buf, sizeof(buf));
        if (n <= 0) break;

        int frames = (int)(n / (2 * sizeof(int16_t))); /* stereo int16 */
        for (int i = 0; i < frames && i < 1024; i++) {
            /* Mono: average L+R */
            audio[i] = ((float)buf[i*2] + (float)buf[i*2+1]) / 32768.0f;
        }

        if (d->audio_cb)
            d->audio_cb(audio, frames, d->audio_cb_user);
    }

    return NULL;
}

/* Status reader thread: connects to Unix socket, reads JSON */
static void *status_reader(void *arg) {
    drm_decoder_t *d = (drm_decoder_t *)arg;

    /* Wait for socket to appear */
    for (int i = 0; i < 50 && d->running; i++) {
        usleep(100000);
        if (access(d->socket_path, F_OK) == 0)
            break;
    }

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return NULL;

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, d->socket_path, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return NULL;
    }

    d->status_fd = fd;
    char line[4096];
    int line_pos = 0;
    char rbuf[4096];
    int rbuf_pos = 0, rbuf_len = 0;

    while (d->running) {
        /* Buffered read */
        if (rbuf_pos >= rbuf_len) {
            ssize_t n = read(fd, rbuf, sizeof(rbuf));
            if (n <= 0) break;
            rbuf_pos = 0;
            rbuf_len = (int)n;
        }
        char c = rbuf[rbuf_pos++];

        if (c == '\n') {
            line[line_pos] = '\0';
            /* Simple JSON parsing for key fields */
            pthread_mutex_lock(&d->lock);

            char *p;

            /* SNR (field is "snr_db" inside "signal" object) */
            if ((p = strstr(line, "\"snr_db\":")))
                d->snr = strtof(p + 9, NULL);

            /* Robustness mode */
            if ((p = strstr(line, "\"robustness\":")))
                d->robustness = atoi(p + 13);

            /* QAM modes (inside "mode" object) */
            if ((p = strstr(line, "\"sdc_qam\":")))
                d->sdc_qam = atoi(p + 10);
            if ((p = strstr(line, "\"msc_qam\":")))
                d->msc_qam = atoi(p + 10);

            /* Audio codec (inside service, e.g. "audio_codec":"xHE-AAC") */
            if ((p = strstr(line, "\"audio_codec\":\""))) {
                p += 15;
                char *end = strchr(p, '"');
                if (end) {
                    int len = (int)(end - p);
                    if (len > 31) len = 31;
                    memcpy(d->audio_codec, p, len);
                    d->audio_codec[len] = '\0';
                }
            }

            /* Bitrate */
            if ((p = strstr(line, "\"bitrate_kbps\":")))
                d->bitrate_kbps = strtof(p + 15, NULL);

            /* Per-field sync detail (io, time, frame, fac, sdc, msc) */
            if ((p = strstr(line, "\"io\":")))
                d->sync_io = atoi(p + 5);
            if ((p = strstr(line, "\"time\":")))
                d->sync_time = atoi(p + 7);
            if ((p = strstr(line, "\"frame\":")))
                d->sync_frame = atoi(p + 8);
            if ((p = strstr(line, "\"fac\":")))
                d->sync_fac = atoi(p + 6);
            if ((p = strstr(line, "\"sdc\":")))
                d->sync_sdc = atoi(p + 6);
            if ((p = strstr(line, "\"msc\":")))
                d->sync_msc = atoi(p + 6);

            /* Compute overall sync_state from fac field */
            if (d->sync_fac == 0)
                d->sync_state = 3; /* Full sync */
            else if (d->sync_time == 0)
                d->sync_state = 1; /* Timing */
            else
                d->sync_state = 0; /* No sync */

            /* String fields: service_label */
            if ((p = strstr(line, "\"label\":\""))) {
                p += 9;
                char *end = strchr(p, '"');
                if (end) {
                    int len = (int)(end - p);
                    if (len > 63) len = 63;
                    memcpy(d->service_label, p, len);
                    d->service_label[len] = '\0';
                }
            }

            /* text_message */
            if ((p = strstr(line, "\"text\":\""))) {
                p += 8;
                char *end = strchr(p, '"');
                if (end) {
                    int len = (int)(end - p);
                    if (len > 255) len = 255;
                    memcpy(d->text_message, p, len);
                    d->text_message[len] = '\0';
                }
            }

            /* audio_mode */
            if ((p = strstr(line, "\"audio_mode\":\""))) {
                p += 14;
                char *end = strchr(p, '"');
                if (end) {
                    int len = (int)(end - p);
                    if (len > 15) len = 15;
                    memcpy(d->audio_mode, p, len);
                    d->audio_mode[len] = '\0';
                }
            }

            /* country name (nested: "country":{"name":"..."}) */
            if ((p = strstr(line, "\"country\""))) {
                char *np = strstr(p, "\"name\":");
                if (np) {
                    np = strchr(np + 7, '"');
                    if (np) {
                        np++;
                        char *end = strchr(np, '"');
                        if (end) {
                            int len = (int)(end - np);
                            if (len > 63) len = 63;
                            memcpy(d->country, np, len);
                            d->country[len] = '\0';
                        }
                    }
                }
            }

            /* language name (nested: "language":{"name":"..."}) */
            if ((p = strstr(line, "\"language\""))) {
                char *np = strstr(p, "\"name\":");
                if (np) {
                    np = strchr(np + 7, '"');
                    if (np) {
                        np++;
                        char *end = strchr(np, '"');
                        if (end) {
                            int len = (int)(end - np);
                            if (len > 63) len = 63;
                            memcpy(d->language, np, len);
                            d->language[len] = '\0';
                        }
                    }
                }
            }

            d->status_valid = true;

            pthread_mutex_unlock(&d->lock);
            line_pos = 0;
        } else if (line_pos < (int)sizeof(line) - 1) {
            line[line_pos++] = c;
        }
    }

    close(fd);
    return NULL;
}

/* Stderr drain thread */
static void *stderr_drain(void *arg) {
    drm_decoder_t *d = (drm_decoder_t *)arg;
    char buf[256];
    while (d->running) {
        ssize_t n = read(d->stderr_fd, buf, sizeof(buf) - 1);
        if (n <= 0) break;
        buf[n] = '\0';
        fprintf(stderr, "Dream: %s", buf);
    }
    return NULL;
}

int drm_start(drm_decoder_t *d, drm_audio_callback_t cb, void *user) {
    if (d->running) {
        fprintf(stderr, "DRM: already running\n");
        return -1;
    }
    fprintf(stderr, "DRM: dream_path='%s'\n", d->dream_path);
    if (!d->dream_path[0] && !drm_find_binary(d)) {
        fprintf(stderr, "DRM: Dream binary not found\n");
        return -1;
    }
    fprintf(stderr, "DRM: Using Dream binary: %s\n", d->dream_path);

    d->audio_cb = cb;
    d->audio_cb_user = user;

    /* Create temp directory for status socket */
    char tmpdir[] = "/tmp/hfdemod_drm_XXXXXX";
    if (!mkdtemp(tmpdir)) return -1;
    snprintf(d->socket_path, sizeof(d->socket_path), "%s/status.sock", tmpdir);

    /* Create pipes */
    int stdin_pipe[2], stdout_pipe[2], stderr_pipe[2];
    if (pipe(stdin_pipe) < 0 || pipe(stdout_pipe) < 0 || pipe(stderr_pipe) < 0)
        return -1;

    d->pid = fork();
    if (d->pid < 0) return -1;

    if (d->pid == 0) {
        /* Child */
        dup2(stdin_pipe[0], STDIN_FILENO);
        dup2(stdout_pipe[1], STDOUT_FILENO);
        dup2(stderr_pipe[1], STDERR_FILENO);
        close(stdin_pipe[1]);
        close(stdout_pipe[0]);
        close(stderr_pipe[0]);

        execlp(d->dream_path, d->dream_path,
               "-c", "6",
               "--sigsrate", "48000",
               "--audsrate", "48000",
               "-I", "-",
               "-O", "-",
               "--status-socket", d->socket_path,
               NULL);
        _exit(1);
    }

    /* Parent */
    close(stdin_pipe[0]);
    close(stdout_pipe[1]);
    close(stderr_pipe[1]);
    d->stdin_fd = stdin_pipe[1];
    d->stdout_fd = stdout_pipe[0];
    d->stderr_fd = stderr_pipe[0];

    d->running = true;

    pthread_create(&d->audio_thread, NULL, audio_reader, d);
    pthread_create(&d->status_thread, NULL, status_reader, d);
    pthread_create(&d->stderr_thread, NULL, stderr_drain, d);

    fprintf(stderr, "DRM: Dream started (pid=%d)\n", d->pid);
    return 0;
}

void drm_write_iq(drm_decoder_t *d, const float *iq_interleaved, int num_samples) {
    pthread_mutex_lock(&d->lock);
    if (!d->running || d->stdin_fd < 0) {
        pthread_mutex_unlock(&d->lock);
        return;
    }

    /* FIR lowpass anti-alias filter + decimate by 4: 192k -> 48k */
    int16_t out_buf[4096]; /* max output samples * 2 channels */
    int out_count = 0;

    for (int i = 0; i < num_samples; i++) {
        float si = iq_interleaved[i * 2];
        float sq = iq_interleaved[i * 2 + 1];

        /* Insert into FIR circular buffer */
        d->decim_buf_i[d->decim_pos] = si;
        d->decim_buf_q[d->decim_pos] = sq;

        d->decim_counter++;
        if (d->decim_counter >= 4) {
            d->decim_counter = 0;

            /* Apply FIR filter at decimation point */
            float fi = 0.0f, fq = 0.0f;
            for (int j = 0; j < 127; j++) {
                int idx = (d->decim_pos - j + 128) % 128;
                fi += d->decim_taps[j] * d->decim_buf_i[idx];
                fq += d->decim_taps[j] * d->decim_buf_q[idx];
            }

            /* Convert to int16 stereo (I=left, Q=right) with clipping */
            float si16 = fi * 32767.0f;
            float sq16 = fq * 32767.0f;
            if (si16 > 32767.0f) si16 = 32767.0f;
            else if (si16 < -32768.0f) si16 = -32768.0f;
            if (sq16 > 32767.0f) sq16 = 32767.0f;
            else if (sq16 < -32768.0f) sq16 = -32768.0f;
            int16_t i16 = (int16_t)si16;
            int16_t q16 = (int16_t)sq16;

            if (out_count < 2048) {
                out_buf[out_count * 2] = i16;
                out_buf[out_count * 2 + 1] = q16;
                out_count++;
            }
        }

        d->decim_pos = (d->decim_pos + 1) % 128;
    }

    if (out_count > 0) {
        ssize_t bytes = out_count * 2 * sizeof(int16_t);
        write(d->stdin_fd, out_buf, bytes);
    }
    pthread_mutex_unlock(&d->lock);
}

void drm_stop(drm_decoder_t *d) {
    if (!d->running) return;
    d->running = false;

    if (d->stdin_fd >= 0) { close(d->stdin_fd); d->stdin_fd = -1; }
    if (d->stdout_fd >= 0) { close(d->stdout_fd); d->stdout_fd = -1; }
    if (d->stderr_fd >= 0) { close(d->stderr_fd); d->stderr_fd = -1; }

    if (d->pid > 0) {
        kill(d->pid, SIGTERM);
        int status;
        waitpid(d->pid, &status, 0);
        d->pid = -1;
    }

    pthread_join(d->audio_thread, NULL);
    pthread_join(d->status_thread, NULL);
    pthread_join(d->stderr_thread, NULL);

    /* Clean up socket */
    if (d->socket_path[0]) {
        unlink(d->socket_path);
        /* Remove temp dir */
        char *slash = strrchr(d->socket_path, '/');
        if (slash) {
            *slash = '\0';
            rmdir(d->socket_path);
            *slash = '/';
        }
        d->socket_path[0] = '\0';
    }

    fprintf(stderr, "DRM: stopped\n");
}

void drm_destroy(drm_decoder_t *d) {
    drm_stop(d);
    pthread_mutex_destroy(&d->lock);
}
