#ifndef IQ_CLIENT_H
#define IQ_CLIENT_H

#include <stdbool.h>
#include <stdint.h>
#include <pthread.h>
#include <stdatomic.h>

#define IQ_CLIENT_MAGIC "ELAD"

/* Header received from IQ server on connection (16 bytes) */
typedef struct __attribute__((packed)) {
    char magic[4];          /* "ELAD" */
    uint32_t sample_rate;   /* e.g., 192000 */
    uint32_t format;        /* 32 = 32-bit signed int IQ pairs */
    uint32_t reserved;
} iq_header_t;

/* Callback invoked from receive thread when raw IQ data arrives.
 * data: raw bytes (32-bit int IQ pairs), length: byte count. */
typedef void (*iq_data_callback_t)(const uint8_t *data, int length, void *user);

typedef struct {
    int fd;
    atomic_int running;
    pthread_t thread;

    uint32_t sample_rate;
    uint32_t format;

    iq_data_callback_t callback;
    void *callback_user;
} iq_client_t;

/* Connect to IQ server. Returns 0 on success. */
int iq_client_connect(iq_client_t *c, const char *host, int port);

/* Start receive thread. callback is invoked with raw IQ chunks. */
int iq_client_start(iq_client_t *c, iq_data_callback_t callback, void *user);

/* Stop receive thread and close connection. */
void iq_client_stop(iq_client_t *c);

#endif
