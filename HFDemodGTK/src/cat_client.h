#ifndef CAT_CLIENT_H
#define CAT_CLIENT_H

#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>

typedef struct {
    int fd;
    atomic_int running;
    pthread_t thread;
    pthread_mutex_t mutex;

    char host[128];
    int port;

    /* Polled values (read by main thread under mutex) */
    double frequency_hz;    /* VFO-A frequency */
    char mode[8];           /* "USB", "LSB", "AM", "CW", etc. */
    int bandwidth_hz;       /* Filter bandwidth in Hz */
    char filter_str[16];    /* Raw filter string (e.g., "2.4k", "500") */
    bool updated;           /* set when new data polled */
} cat_client_t;

/* Connect to CAT server. Returns 0 on success. */
int cat_client_connect(cat_client_t *c, const char *host, int port);

/* Start polling thread (polls FA; and IF; every ~200ms). */
int cat_client_start(cat_client_t *c);

/* Read latest frequency/mode/bandwidth. Returns true if data was updated since last read. */
bool cat_client_read(cat_client_t *c, double *freq_hz, char *mode, int mode_len,
                     int *bandwidth_hz);

/* Set VFO-A frequency (sends FA command). Thread-safe. Returns 0 on success. */
int cat_client_set_frequency(cat_client_t *c, double freq_hz);

/* Stop polling and close connection. */
void cat_client_stop(cat_client_t *c);

#endif
