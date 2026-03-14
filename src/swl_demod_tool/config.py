import os
import configparser

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME",
                          os.path.expanduser("~/.config")), "swl-demod-tool")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.conf")

DEFAULTS = {
    "sdr": {
        "backend": "elad-fdmduo",
    },
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
    "state": {
        "mode": "AM",
        "bandwidth": "5000",
    },
}

# Default keybindings: action -> human-readable key
DEFAULT_KEYS = {
    "quit": "q",
    "unfocus": "escape",
    "show_help": "?",
    "connect": "c",
    "disconnect": "d",
    "reconnect": "r",
    "toggle_mute": "m",
    "toggle_agc": "a",
    "volume_up": "+",
    "volume_down": "-",
    "bw_up": "]",
    "bw_down": "[",
    "zoom_in": "shift+right",
    "zoom_out": "shift+left",
    "tune_up": "right",
    "tune_down": "left",
    "focus_freq": "/",
    "cycle_mode": "x",
    "fine_tune_up": "alt+right",
    "fine_tune_down": "alt+left",
    "rit_up": "pageup",
    "rit_down": "pagedown",
    "toggle_vfo": "v",
    "clear_cw_text": "t",
    "toggle_apf": "p",
    "toggle_nb": "n",
    "cycle_nb_threshold": "N",
    "cycle_dnr": "f",
    "toggle_auto_notch": "alt+n",
    "toggle_spectrum": "s",
}

# Human-readable key <-> Textual internal key name
_KEY_TO_TEXTUAL = {
    "?": "question_mark",
    "]": "right_square_bracket",
    "[": "left_square_bracket",
    "+": "plus",
    "-": "minus",
    "/": "slash",
}
_TEXTUAL_TO_KEY = {v: k for k, v in _KEY_TO_TEXTUAL.items()}


def _split_key(key):
    """Split a key combo like 'shift+right' into parts, handling bare '+' and '-'."""
    if key in _KEY_TO_TEXTUAL:
        return [key]
    parts = key.split("+")
    # Rejoin empty strings caused by splitting a bare "+" or trailing "+"
    result = []
    for p in parts:
        if p == "" and (not result or result[-1] == ""):
            result.append("+")
        else:
            result.append(p)
    return [p for p in result if p]


def _to_textual_key(key):
    """Convert a human-readable key name to Textual's internal name."""
    parts = _split_key(key)
    parts[-1] = _KEY_TO_TEXTUAL.get(parts[-1], parts[-1])
    return "+".join(parts)


def _to_display_key(key):
    """Convert a human-readable key name to a nice display string."""
    display_map = {
        "right": "\u2192", "left": "\u2190", "up": "\u2191", "down": "\u2193",
        "escape": "Escape", "pageup": "PgUp", "pagedown": "PgDn",
        "shift": "Shift", "alt": "Alt", "ctrl": "Ctrl",
        "+": "+", "-": "\u2212",
    }
    parts = _split_key(key)
    return "+".join(display_map.get(p, p) for p in parts)


def load_keybindings(config):
    """Load keybindings from config, merged with defaults.

    Returns dict of action -> human-readable key string.
    """
    keys = dict(DEFAULT_KEYS)
    if config.has_section("keys"):
        for action in config.options("keys"):
            if action in keys:
                keys[action] = config.get("keys", action)
    return keys


def keybindings_to_textual(keys, meta=None):
    """Convert keybindings dict to list of Textual Binding 3-tuples.

    If meta dict is provided (action -> (desc, section, label)), the label
    is used as the binding description for the Footer widget.
    """
    result = []
    for action, key in keys.items():
        tkey = _to_textual_key(key)
        if meta and action in meta:
            desc = meta[action][2] or meta[action][0]
            result.append((tkey, action, desc))
        else:
            result.append((tkey, action))
    return result


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
