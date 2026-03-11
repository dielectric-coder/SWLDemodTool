/* config.c — INI config file handling. */

#include "config.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static char config_path_buf[512];

const char *hf_config_path(void) {
    const char *xdg = getenv("XDG_CONFIG_HOME");
    if (xdg && xdg[0])
        snprintf(config_path_buf, sizeof(config_path_buf),
                 "%s/hfdemod-gtk/config.conf", xdg);
    else
        snprintf(config_path_buf, sizeof(config_path_buf),
                 "%s/.config/hfdemod-gtk/config.conf", getenv("HOME"));
    return config_path_buf;
}

int hf_config_load(hf_config_t *cfg) {
    /* Defaults */
    strncpy(cfg->host, "localhost", sizeof(cfg->host));
    cfg->iq_port = 4533;
    cfg->cat_port = 4532;
    cfg->audio_device[0] = '\0';
    cfg->dream_path[0] = '\0';
    cfg->nb_enabled = 0;
    cfg->nb_threshold = 0;
    cfg->dnr_level = 0;

    const char *path = hf_config_path();
    FILE *f = fopen(path, "r");
    if (!f) return -1;

    char line[512];
    char section[64] = "";

    while (fgets(line, sizeof(line), f)) {
        /* Strip newline */
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';

        /* Skip empty/comment lines */
        if (line[0] == '#' || line[0] == ';' || line[0] == '\0')
            continue;

        /* Section header */
        if (line[0] == '[') {
            char *end = strchr(line, ']');
            if (end) {
                *end = '\0';
                strncpy(section, line + 1, sizeof(section) - 1);
            }
            continue;
        }

        /* Key = value */
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *key = line;
        char *val = eq + 1;
        /* Trim leading spaces */
        while (*key == ' ') key++;
        while (*val == ' ') val++;

        if (strcmp(section, "server") == 0) {
            if (strcmp(key, "host") == 0)
                strncpy(cfg->host, val, sizeof(cfg->host) - 1);
            else if (strcmp(key, "iq_port") == 0)
                cfg->iq_port = atoi(val);
            else if (strcmp(key, "cat_port") == 0)
                cfg->cat_port = atoi(val);
        } else if (strcmp(section, "audio") == 0) {
            if (strcmp(key, "device") == 0)
                strncpy(cfg->audio_device, val, sizeof(cfg->audio_device) - 1);
        } else if (strcmp(section, "drm") == 0) {
            if (strcmp(key, "dream_path") == 0)
                strncpy(cfg->dream_path, val, sizeof(cfg->dream_path) - 1);
        } else if (strcmp(section, "noise_reduction") == 0) {
            if (strcmp(key, "nb_enabled") == 0)
                cfg->nb_enabled = atoi(val);
            else if (strcmp(key, "nb_threshold") == 0)
                cfg->nb_threshold = atoi(val);
            else if (strcmp(key, "dnr_level") == 0)
                cfg->dnr_level = atoi(val);
        }
    }

    fclose(f);
    return 0;
}

int hf_config_save(const hf_config_t *cfg) {
    const char *path = hf_config_path();

    /* Ensure directory exists */
    char dir[512];
    strncpy(dir, path, sizeof(dir));
    char *slash = strrchr(dir, '/');
    if (slash) {
        *slash = '\0';
        mkdir(dir, 0755);
    }

    FILE *f = fopen(path, "w");
    if (!f) return -1;

    fprintf(f, "[server]\n");
    fprintf(f, "host = %s\n", cfg->host);
    fprintf(f, "iq_port = %d\n", cfg->iq_port);
    fprintf(f, "cat_port = %d\n", cfg->cat_port);
    fprintf(f, "\n[audio]\n");
    fprintf(f, "device = %s\n", cfg->audio_device);
    fprintf(f, "\n[drm]\n");
    fprintf(f, "dream_path = %s\n", cfg->dream_path);
    fprintf(f, "\n[noise_reduction]\n");
    fprintf(f, "nb_enabled = %d\n", cfg->nb_enabled);
    fprintf(f, "nb_threshold = %d\n", cfg->nb_threshold);
    fprintf(f, "dnr_level = %d\n", cfg->dnr_level);

    fclose(f);
    return 0;
}
