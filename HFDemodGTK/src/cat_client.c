#include "cat_client.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <netdb.h>
#include <poll.h>

/* Kenwood mode codes from IF response byte 29 */
static const char *mode_map[] = {
    [1] = "LSB", [2] = "USB", [3] = "CW",
    [4] = "FM",  [5] = "AM",  [7] = "CW-R",
};
#define MODE_MAP_SIZE 8

/* Mode code to RF command parameter (same index as IF byte 29) */
static const char mode_to_rf[] = {
    [1] = '1', [2] = '2', [3] = '3',
    [4] = '4', [5] = '5', [7] = '7',
};

/* Filter bandwidth lookup tables from RF CAT command (per ELAD FDM-DUO manual) */

/* LSB/USB filters (P1=1,2): index 0-21 */
static const char *filter_lsb_usb[] = {
    "1.6k", "1.7k", "1.8k", "1.9k", "2.0k", "2.1k", "2.2k", "2.3k",
    "2.4k", "2.5k", "2.6k", "2.7k", "2.8k", "2.9k", "3.0k", "3.1k",
    "4.0k", "5.0k", "6.0k", "D300", "D600", "D1k"
};
#define FILTER_LSB_USB_COUNT 22

/* CW/CWR filters (P1=3,7): valid indices 07-16 */
static const char *filter_cw[] = {
    NULL, NULL, NULL, NULL, NULL, NULL, NULL,
    "100&4", "100&3", "100&2", "100&1", "100", "300", "500",
    "1.0k", "1.5k", "2.6k"
};
#define FILTER_CW_COUNT 17

/* AM filters (P1=5): index 0-7 */
static const char *filter_am[] = {
    "2.5k", "3.0k", "3.5k", "4.0k", "4.5k", "5.0k", "5.5k", "6.0k"
};
#define FILTER_AM_COUNT 8

/* FM filters (P1=4): index 0-2 */
static const char *filter_fm[] = {
    "Narrow", "Wide", "Data"
};
#define FILTER_FM_COUNT 3

/* Parse filter string to Hz (same logic as EladSpectrum parse_bandwidth_hz) */
static int parse_bandwidth_hz(const char *bw_str) {
    if (!bw_str || bw_str[0] == '\0') return 0;

    if (strcasecmp(bw_str, "Narrow") == 0) return 2500;
    if (strcasecmp(bw_str, "Wide") == 0) return 6000;
    if (strcasecmp(bw_str, "Data") == 0) return 6000;

    /* CW resonator modes: "100&1", "100&2", etc. */
    if (strncmp(bw_str, "100&", 4) == 0) return 100;

    /* Data mode prefix "D" (e.g., "D300", "D600", "D1k") */
    const char *num_start = bw_str;
    if (bw_str[0] == 'D' || bw_str[0] == 'd')
        num_start = bw_str + 1;

    char *endptr;
    double value = strtod(num_start, &endptr);
    if (endptr == num_start) return 0;

    if (*endptr == 'k' || *endptr == 'K')
        return (int)(value * 1000);

    return (int)value;
}

/* Send a command and read response up to ';' terminator.
 * Returns response length, or -1 on error. */
static int cat_send(int fd, const char *cmd, char *resp, int resp_size) {
    int cmd_len = strlen(cmd);
    int total = 0;
    while (total < cmd_len) {
        int n = write(fd, cmd + total, cmd_len - total);
        if (n <= 0) return -1;
        total += n;
    }

    int rlen = 0;
    while (rlen < resp_size - 1) {
        struct pollfd pfd = { .fd = fd, .events = POLLIN };
        int ret = poll(&pfd, 1, 2000);
        if (ret <= 0) return -1;

        int n = read(fd, resp + rlen, 1);
        if (n <= 0) return -1;
        rlen++;
        if (resp[rlen - 1] == ';') break;
    }
    resp[rlen] = '\0';
    return rlen;
}

/* Parse FA response: "FA00007100000;" -> 7100000 Hz */
static double parse_fa(const char *resp) {
    if (resp[0] != 'F' || resp[1] != 'A') return -1;
    if (strlen(resp) < 13) return -1;
    char digits[12];
    memcpy(digits, resp + 2, 11);
    digits[11] = '\0';
    return atof(digits);
}

/* Parse IF response byte 29 for mode code (returns 0-7, or -1) */
static int parse_if_mode_code(const char *resp) {
    if (resp[0] != 'I' || resp[1] != 'F') return -1;
    if (strlen(resp) < 30) return -1;
    return resp[29] - '0';
}

/* Query filter via RF command. mode_code is from IF byte 29.
 * Writes filter string to filter_str. Returns bandwidth in Hz, or 0. */
static int query_filter(int fd, int mode_code, char *filter_str, int filter_str_size) {
    filter_str[0] = '\0';

    if (mode_code < 0 || mode_code >= MODE_MAP_SIZE) return 0;
    char rf_char = mode_to_rf[mode_code];
    if (rf_char == 0) return 0;

    char cmd[8];
    snprintf(cmd, sizeof(cmd), "RF%c;", rf_char);

    char resp[32];
    int n = cat_send(fd, cmd, resp, sizeof(resp));

    /* Response: "RF P1 P2 P2 ;" e.g. "RF10808;" */
    if (n < 6 || strncmp(resp, "RF", 2) != 0) return 0;

    char p2_str[3];
    p2_str[0] = resp[3];
    p2_str[1] = resp[4];
    p2_str[2] = '\0';
    int p2 = atoi(p2_str);

    const char *filter = NULL;
    switch (mode_code) {
    case 1: case 2: /* LSB/USB */
        if (p2 >= 0 && p2 < FILTER_LSB_USB_COUNT)
            filter = filter_lsb_usb[p2];
        break;
    case 3: case 7: /* CW/CW-R */
        if (p2 >= 0 && p2 < FILTER_CW_COUNT)
            filter = filter_cw[p2];
        break;
    case 5: /* AM */
        if (p2 >= 0 && p2 < FILTER_AM_COUNT)
            filter = filter_am[p2];
        break;
    case 4: /* FM */
        if (p2 >= 0 && p2 < FILTER_FM_COUNT)
            filter = filter_fm[p2];
        break;
    }

    if (filter) {
        snprintf(filter_str, filter_str_size, "%s", filter);
        return parse_bandwidth_hz(filter);
    }

    return 0;
}

static void *poll_thread(void *arg) {
    cat_client_t *c = (cat_client_t *)arg;
    char resp[256];

    while (atomic_load(&c->running)) {
        /* Poll frequency (FA;) */
        int n = cat_send(c->fd, "FA;", resp, sizeof(resp));
        if (n < 0) {
            fprintf(stderr, "CAT: connection lost\n");
            break;
        }
        double freq = parse_fa(resp);

        /* Poll mode (IF;) */
        const char *mode = NULL;
        int mode_code = -1;
        n = cat_send(c->fd, "IF;", resp, sizeof(resp));
        if (n > 0) {
            mode_code = parse_if_mode_code(resp);
            if (mode_code >= 0 && mode_code < MODE_MAP_SIZE)
                mode = mode_map[mode_code];
        }

        /* Poll filter bandwidth (RF<mode>;) */
        char filter_str[16] = "";
        int bw_hz = 0;
        if (mode_code >= 0)
            bw_hz = query_filter(c->fd, mode_code, filter_str, sizeof(filter_str));

        /* Update shared state */
        pthread_mutex_lock(&c->mutex);
        if (freq > 0) c->frequency_hz = freq;
        if (mode) {
            strncpy(c->mode, mode, sizeof(c->mode) - 1);
            c->mode[sizeof(c->mode) - 1] = '\0';
        }
        if (bw_hz > 0) c->bandwidth_hz = bw_hz;
        if (filter_str[0]) {
            strncpy(c->filter_str, filter_str, sizeof(c->filter_str) - 1);
            c->filter_str[sizeof(c->filter_str) - 1] = '\0';
        }
        c->updated = true;
        pthread_mutex_unlock(&c->mutex);

        /* Poll interval ~200ms */
        usleep(200000);
    }

    return NULL;
}

int cat_client_connect(cat_client_t *c, const char *host, int port) {
    memset(c, 0, sizeof(*c));
    c->fd = -1;
    strncpy(c->host, host, sizeof(c->host) - 1);
    c->port = port;
    pthread_mutex_init(&c->mutex, NULL);

    struct addrinfo hints = { .ai_family = AF_INET, .ai_socktype = SOCK_STREAM };
    struct addrinfo *res;
    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%d", port);

    if (getaddrinfo(host, port_str, &hints, &res) != 0) {
        fprintf(stderr, "CAT: cannot resolve %s\n", host);
        return -1;
    }

    c->fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (c->fd < 0) {
        freeaddrinfo(res);
        return -1;
    }

    if (connect(c->fd, res->ai_addr, res->ai_addrlen) < 0) {
        fprintf(stderr, "CAT: connect error %s:%d: %s\n", host, port, strerror(errno));
        close(c->fd);
        c->fd = -1;
        freeaddrinfo(res);
        return -1;
    }
    freeaddrinfo(res);

    int flag = 1;
    setsockopt(c->fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));

    fprintf(stderr, "CAT: connected to %s:%d\n", host, port);
    return 0;
}

int cat_client_start(cat_client_t *c) {
    atomic_store(&c->running, 1);
    if (pthread_create(&c->thread, NULL, poll_thread, c) != 0) {
        atomic_store(&c->running, 0);
        return -1;
    }
    return 0;
}

bool cat_client_read(cat_client_t *c, double *freq_hz, char *mode, int mode_len,
                     int *bw_hz) {
    pthread_mutex_lock(&c->mutex);
    bool upd = c->updated;
    if (upd) {
        *freq_hz = c->frequency_hz;
        if (mode && mode_len > 0) {
            strncpy(mode, c->mode, mode_len - 1);
            mode[mode_len - 1] = '\0';
        }
        if (bw_hz) *bw_hz = c->bandwidth_hz;
        c->updated = false;
    }
    pthread_mutex_unlock(&c->mutex);
    return upd;
}

int cat_client_set_frequency(cat_client_t *c, double freq_hz) {
    if (c->fd < 0) return -1;

    char cmd[32], resp[32];
    snprintf(cmd, sizeof(cmd), "FA%011.0f;", freq_hz);

    pthread_mutex_lock(&c->mutex);
    int n = cat_send(c->fd, cmd, resp, sizeof(resp));
    if (n > 0)
        c->frequency_hz = freq_hz;
    pthread_mutex_unlock(&c->mutex);

    return (n > 0) ? 0 : -1;
}

void cat_client_stop(cat_client_t *c) {
    if (!atomic_load(&c->running)) return;
    atomic_store(&c->running, 0);

    if (c->fd >= 0) {
        shutdown(c->fd, SHUT_RDWR);
        close(c->fd);
        c->fd = -1;
    }

    pthread_join(c->thread, NULL);
    pthread_mutex_destroy(&c->mutex);
    fprintf(stderr, "CAT: stopped\n");
}
