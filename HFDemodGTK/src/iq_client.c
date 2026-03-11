#include "iq_client.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <netdb.h>

/* Read exactly n bytes. Returns 0 on success, -1 on failure. */
static int read_exact(int fd, void *buf, int n) {
    int total = 0;
    while (total < n) {
        int r = read(fd, (uint8_t *)buf + total, n - total);
        if (r <= 0) return -1;
        total += r;
    }
    return 0;
}

static void *receive_thread(void *arg) {
    iq_client_t *c = (iq_client_t *)arg;

    /* 8192 IQ samples per chunk = 65536 bytes (8 bytes per sample) */
    const int chunk_size = 65536;
    uint8_t *buf = malloc(chunk_size);
    if (!buf) {
        fprintf(stderr, "IQ client: malloc failed\n");
        return NULL;
    }

    while (atomic_load(&c->running)) {
        int n = read(c->fd, buf, chunk_size);
        if (n <= 0) {
            if (n < 0 && (errno == EINTR || errno == EAGAIN))
                continue;
            fprintf(stderr, "IQ client: connection closed\n");
            break;
        }
        if (c->callback)
            c->callback(buf, n, c->callback_user);
    }

    free(buf);
    return NULL;
}

int iq_client_connect(iq_client_t *c, const char *host, int port) {
    memset(c, 0, sizeof(*c));
    c->fd = -1;

    /* Resolve hostname */
    struct addrinfo hints = { .ai_family = AF_INET, .ai_socktype = SOCK_STREAM };
    struct addrinfo *res;
    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%d", port);

    if (getaddrinfo(host, port_str, &hints, &res) != 0) {
        fprintf(stderr, "IQ client: cannot resolve %s\n", host);
        return -1;
    }

    c->fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (c->fd < 0) {
        fprintf(stderr, "IQ client: socket error: %s\n", strerror(errno));
        freeaddrinfo(res);
        return -1;
    }

    if (connect(c->fd, res->ai_addr, res->ai_addrlen) < 0) {
        fprintf(stderr, "IQ client: connect error: %s\n", strerror(errno));
        close(c->fd);
        c->fd = -1;
        freeaddrinfo(res);
        return -1;
    }
    freeaddrinfo(res);

    /* Disable Nagle for low-latency */
    int flag = 1;
    setsockopt(c->fd, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));

    /* Read 16-byte ELAD header */
    iq_header_t hdr;
    if (read_exact(c->fd, &hdr, sizeof(hdr)) < 0) {
        fprintf(stderr, "IQ client: failed to read header\n");
        close(c->fd);
        c->fd = -1;
        return -1;
    }

    if (memcmp(hdr.magic, IQ_CLIENT_MAGIC, 4) != 0) {
        fprintf(stderr, "IQ client: bad magic\n");
        close(c->fd);
        c->fd = -1;
        return -1;
    }

    c->sample_rate = hdr.sample_rate;
    c->format = hdr.format;
    fprintf(stderr, "IQ client: connected, rate=%u format=%u\n",
            c->sample_rate, c->format);

    return 0;
}

int iq_client_start(iq_client_t *c, iq_data_callback_t callback, void *user) {
    c->callback = callback;
    c->callback_user = user;
    atomic_store(&c->running, 1);

    if (pthread_create(&c->thread, NULL, receive_thread, c) != 0) {
        fprintf(stderr, "IQ client: failed to create thread\n");
        atomic_store(&c->running, 0);
        return -1;
    }

    return 0;
}

void iq_client_stop(iq_client_t *c) {
    if (!atomic_load(&c->running)) return;
    atomic_store(&c->running, 0);

    if (c->fd >= 0) {
        shutdown(c->fd, SHUT_RDWR);
        close(c->fd);
        c->fd = -1;
    }

    pthread_join(c->thread, NULL);
    fprintf(stderr, "IQ client: stopped\n");
}
