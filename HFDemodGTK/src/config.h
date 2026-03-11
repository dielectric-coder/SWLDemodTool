/* config.h — INI config file for HFDemodGTK. */

#ifndef CONFIG_H
#define CONFIG_H

typedef struct {
    char host[128];
    int  iq_port;
    int  cat_port;
    char audio_device[64];
    char dream_path[256];

    /* Noise reduction defaults */
    int  nb_enabled;
    int  nb_threshold;      /* 0=off, 1=low, 2=med, 3=high */
    int  dnr_level;         /* 0-3 */
} hf_config_t;

/* Load config from $XDG_CONFIG_HOME/hfdemod-gtk/config.conf.
 * Missing values get defaults. Returns 0 on success. */
int hf_config_load(hf_config_t *cfg);

/* Save config. Returns 0 on success. */
int hf_config_save(const hf_config_t *cfg);

/* Get config file path. */
const char *hf_config_path(void);

#endif
