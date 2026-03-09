import os
import configparser

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME",
                          os.path.expanduser("~/.config")), "swl-demod-tool")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.conf")

DEFAULTS = {
    "server": {
        "host": "localhost",
        "iq_port": "4533",
        "cat_port": "4532",
    },
    "audio": {
        "device": "default",
    },
    "drm": {
        "dream_path": "",
    },
    "noise_reduction": {
        "nb_enabled": "false",
        "nb_threshold": "Med",
        "dnr_level": "0",
    },
}


def load_config():
    config = configparser.ConfigParser()
    for section, values in DEFAULTS.items():
        config[section] = values
    config.read(CONFIG_FILE)
    return config


def save_config(config):
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd = os.open(CONFIG_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        config.write(f)
