import os
import configparser

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME",
                          os.path.expanduser("~/.config")), "elad-demod")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.conf")

DEFAULTS = {
    "server": {
        "host": "localhost",
        "iq_port": "4533",
        "cat_port": "4532",
    },
    "audio": {
        "device": "default",
        "sample_rate": "48000",
        "buffer_size": "1024",
    },
}


def load_config():
    config = configparser.ConfigParser()
    for section, values in DEFAULTS.items():
        config[section] = values
    config.read(CONFIG_FILE)
    return config


def save_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        config.write(f)
