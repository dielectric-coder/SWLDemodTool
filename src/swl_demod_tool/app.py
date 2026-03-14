#!/usr/bin/env python3

"""SWL Demod Tool - TUI demodulator for Elad FDM-DUO IQ stream."""

import argparse
import csv
import errno
import logging
import os
import stat
import subprocess
import threading
import numpy as np
from collections import deque
from datetime import datetime, timezone

from textual.app import App
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Input, Static, OptionList
from textual.widgets.option_list import Option
from textual.reactive import reactive
from textual import work
from rich.text import Text

from swl_demod_tool import __version__
from swl_demod_tool.config import (load_config, save_config, load_keybindings,
                                    keybindings_to_textual, _to_display_key)
from swl_demod_tool.sdr import create_sdr_source, DEFAULT_BACKEND
from swl_demod_tool.dsp import compute_spectrum_db, spectrum_to_sparkline, Demodulator
from swl_demod_tool.audio import AudioOutput
from swl_demod_tool.drm import DRMDecoder

SPECTRUM_AVG = 3
SPECTRUM_FIFO = "/tmp/swl-spectrum.fifo"
FFT_SIZE = 4096
STATION_FIFO = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
    "swldemod-station.fifo",
)

# SWLScheduleTool schedule CSV search paths
_SKED_CSV_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "..", "SWLScheduleTool", "src",
                 "eibi_swl", "swl-schedules-data", "sked-current.csv"),
    os.path.join(os.environ.get("XDG_DATA_HOME",
                 os.path.expanduser("~/.local/share")),
                 "eibi-swl", "sked-current.csv"),
]


def _find_sked_csv():
    """Locate the SWLScheduleTool schedule CSV file."""
    for path in _SKED_CSV_CANDIDATES:
        resolved = os.path.realpath(path)
        if os.path.isfile(resolved):
            return resolved
    return None


def _lookup_station(freq_hz):
    """Look up active station name from SWLScheduleTool schedule.

    Returns the station name if a broadcast is currently on-air at the
    given frequency, or empty string if none found.
    """
    csv_path = _find_sked_csv()
    if csv_path is None:
        return ""
    freq_khz = str(freq_hz / 1000).rstrip("0").rstrip(".")
    now_utc = datetime.now(timezone.utc)
    current_time = int(now_utc.strftime("%H%M"))
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter=";")
            next(reader, None)  # skip header
            for row in reader:
                if len(row) < 5 or row[0].strip() != freq_khz:
                    continue
                time_range = row[1] if len(row) > 1 else ""
                if "-" not in time_range:
                    continue
                try:
                    start_s, end_s = time_range.split("-")
                    start_t, end_t = int(start_s), int(end_s)
                except (ValueError, IndexError):
                    continue
                duration = end_t - start_t
                if duration < 0:
                    is_active = (current_time >= start_t) or (current_time < end_t)
                else:
                    is_active = start_t <= current_time < end_t
                if is_active:
                    return row[4].strip()
    except OSError:
        pass
    return ""


CSS = """
Screen {
    layout: vertical;
    background: black;
}

#title-bar {
    dock: top;
    height: 1;
    background: black;
    color: #a3aed2;
    text-style: bold;
    padding: 0 1;
}

#conn-status {
    height: 4;
    background: black;
    color: #a3aed2;
    padding: 0 2;
    border-bottom: solid #394260;
}

#radio-info {
    height: 2;
    background: black;
    color: #769ff0;
    padding: 0 2;
    border-bottom: solid #394260;
    layout: horizontal;
}

#radio-text {
    width: 1fr;
    height: 1;
    background: black;
    color: #769ff0;
}

#freq-label {
    width: auto;
    height: 1;
    background: black;
}

#freq-input {
    width: 14;
    height: 1;
    background: black;
    color: #769ff0;
    border: none;
}

#freq-input:focus {
    border: none;
}

#freq-input.-placeholder {
    color: #a3aed2 50%;
}

#spectrum-display {
    height: 12;
    background: black;
    color: #00cccc;
    padding: 0 2;
    border-bottom: solid #394260;
}

#audio-info {
    height: 5;
    background: black;
    color: #a3aed2;
    padding: 0 2;
    border-bottom: solid #394260;
}

#mode-info {
    height: 3;
    background: black;
    color: #c0a36e;
    padding: 0 2;
    border-bottom: solid #394260;
}

#status-bar {
    dock: bottom;
    height: 1;
    background: black;
    color: #a3aed2;
    padding: 0 1;
}
"""

HELP_CSS = """
#help-container {
    align: center middle;
    width: 100%;
    height: 100%;
    background: black 50%;
}

#help-card {
    width: 64;
    height: auto;
    max-height: 90%;
    border: round #769ff0;
    background: black;
    color: #a9b1d6;
    padding: 1 2;
}

#help-title {
    text-style: bold;
    text-align: center;
    width: 100%;
    margin-bottom: 1;
    color: #769ff0;
}

#help-scroll {
    width: 100%;
    height: auto;
    max-height: 100%;
    overflow-y: auto;
}

#help-body {
    width: 100%;
}

#help-hint {
    text-align: center;
    width: 100%;
    margin-top: 1;
    color: $text-muted;
}
"""

# Keybinding metadata: action -> (description, section, status_bar_label or None)
# Paired actions (down/up) share a single help entry via _PAIRED_ACTIONS.
KEYBINDING_META = {
    "quit":              ("Quit",                        "General",         "Quit"),
    "unfocus":           ("Unfocus / Close popup",       "General",         None),
    "show_help":         ("Show keyboard shortcuts",     "General",         "Help"),
    "connect":           ("Connect to server",           "Connection",      "Connect"),
    "disconnect":        ("Disconnect",                  "Connection",      "Disc"),
    "reconnect":         ("Reconnect",                   "Connection",      "Recon"),
    "toggle_mute":       ("Toggle mute",                 "Audio & Mode",    "Mute"),
    "toggle_agc":        ("Toggle AGC",                  "Audio & Mode",    "AGC"),
    "volume_up":         ("AF gain up",                  "Audio & Mode",    None),
    "volume_down":       ("AF gain down",                "Audio & Mode",    None),
    "select_bw":         ("Select bandwidth",            "Audio & Mode",    "BW"),
    "bw_up":             ("Bandwidth up",                "Audio & Mode",    None),
    "bw_down":           ("Bandwidth down",              "Audio & Mode",    None),
    "zoom_in":           ("Zoom in",                     "Display",         None),
    "zoom_out":          ("Zoom out",                    "Display",         None),
    "tune_up":           ("Tune up",                     "Tuning",          None),
    "tune_down":         ("Tune down",                   "Tuning",          None),
    "focus_freq":        ("Direct frequency entry (kHz)","Tuning",          "Freq"),
    "select_mode":       ("Select demod mode",           "Audio & Mode",    "Mode"),
    "select_tune_step":  ("Select tune step",            "Tuning",          "Step"),
    "rit_up":            ("RIT offset up",               "Tuning",          None),
    "rit_down":          ("RIT offset down",             "Tuning",          None),
    "select_rit_step":   ("Cycle RIT step 1/10/100 Hz",  "Tuning",          "RIT"),
    "select_vfo":        ("Select VFO",                  "Tuning",          "VFO"),
    "clear_cw_text":     ("Clear decoded text",           "Audio & Mode",    None),
    "toggle_nb":         ("Cycle NB Off/Low/Med/High",   "Noise Reduction", "NB"),
    "cycle_dnr":         ("Cycle spectral DNR level",    "Noise Reduction", "DNR"),
    "toggle_auto_notch": ("Toggle auto notch filter",    "Noise Reduction", "DNF"),
    "toggle_apf":        ("Toggle CW audio peak filter", "Noise Reduction", "APF"),
    "toggle_spectrum":   ("Toggle spectrum display",     "Display",         "Spec"),
}

# Pairs of (down_action, up_action) shown as a single "key1 / key2" help entry
_PAIRED_ACTIONS = [
    ("tune_down", "tune_up"),
    ("volume_down", "volume_up"),
    ("bw_down", "bw_up"),
    ("zoom_out", "zoom_in"),
    ("rit_down", "rit_up"),
]

# Section display order
_SECTION_ORDER = [
    "General", "Connection", "Tuning", "Audio & Mode", "Display",
    "Noise Reduction",
]


def _build_shortcut_table(keys):
    """Build help screen shortcut table from configured keybindings."""
    paired = set()
    pair_map = {}
    for down, up in _PAIRED_ACTIONS:
        paired.add(down)
        paired.add(up)
        pair_map[up] = down  # show pair entry at the "up" action

    sections = {s: [] for s in _SECTION_ORDER}
    for action, key in keys.items():
        meta = KEYBINDING_META.get(action)
        if not meta:
            continue
        desc, section, _ = meta

        if action in paired:
            if action in pair_map:
                down_action = pair_map[action]
                down_key = _to_display_key(keys.get(down_action, ""))
                up_key = _to_display_key(key)
                down_desc = KEYBINDING_META[down_action][0]
                up_desc = desc
                combined_key = f"{down_key} / {up_key}"
                combined_desc = f"{down_desc} / {up_desc.split()[-1]}"
                sections[section].append((combined_key, combined_desc))
            # Skip the "down" action; it's shown via its paired "up"
            continue

        display = _to_display_key(key)
        sections[section].append((display, desc))

    return [(s, entries) for s, entries in sections.items() if entries]


def _build_status_bar(keys):
    """Build status bar string from configured keybindings."""
    parts = []
    for action, key in keys.items():
        meta = KEYBINDING_META.get(action)
        if not meta or not meta[2]:
            continue
        display = _to_display_key(key)
        parts.append(f"{display}:{meta[2]}")
    return "  " + "  ".join(parts)


class HelpScreen(ModalScreen):
    CSS = HELP_CSS
    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("pageup", "scroll_up", "Scroll Up"),
        ("pagedown", "scroll_down", "Scroll Down"),
    ]

    def __init__(self, shortcut_table, help_key="?"):
        super().__init__()
        self._shortcut_table = shortcut_table
        self._help_key = help_key
        self._help_key_display = _to_display_key(help_key)

    def on_mount(self):
        from swl_demod_tool.config import _to_textual_key
        help_tkey = _to_textual_key(self._help_key)
        if help_tkey != "escape":
            self._bindings.bind(help_tkey, "dismiss", description="Close")

    def compose(self):
        lines = []
        for section, shortcuts in self._shortcut_table:
            lines.append(f"[bold #c0a36e]{section}[/]")
            for key, desc in shortcuts:
                lines.append(f"  [#769ff0]{key:<22}[/] {desc}")
            lines.append("")
        body = "\n".join(lines).rstrip()
        hint = f"PgUp/PgDn: scroll \u00b7 Escape or {self._help_key_display}: close"

        with Container(id="help-container"):
            with Container(id="help-card"):
                yield Static("Keyboard Shortcuts", id="help-title")
                with VerticalScroll(id="help-scroll"):
                    yield Static(body, id="help-body")
                yield Static(hint, id="help-hint")

    def action_scroll_up(self):
        self.query_one("#help-scroll").scroll_page_up()

    def action_scroll_down(self):
        self.query_one("#help-scroll").scroll_page_down()


SELECTOR_CSS = """
#selector-container {
    align: center middle;
    width: 100%;
    height: 100%;
    background: black 50%;
}

#selector-card {
    width: 40;
    height: auto;
    max-height: 80%;
    border: solid #a3aed2;
    background: #1a1a2e;
    color: #a9b1d6;
    padding: 0;
}

#selector-title {
    text-style: bold;
    text-align: center;
    width: 100%;
    padding: 1 0;
    color: #a3aed2;
    border-bottom: solid #a3aed2;
}

#selector-list {
    width: 100%;
    height: auto;
    max-height: 20;
    background: #1a1a2e;
    color: #a9b1d6;
    border: none;
    padding: 0 1;
}

#selector-list > .option-list--option-highlighted {
    background: #2a2a4a;
    color: #ffffff;
}

#selector-hint {
    text-align: center;
    width: 100%;
    padding: 0 0;
    color: $text-muted;
    border-top: solid #a3aed2;
}
"""


class SelectorScreen(ModalScreen):
    """Generic popup selector with OptionList."""

    CSS = SELECTOR_CSS
    BINDINGS = [
        ("escape", "dismiss", "Close"),
    ]

    def __init__(self, title, items, current=None):
        """items: list of (value, label) tuples. current: currently selected value."""
        super().__init__()
        self._title = title
        self._items = items
        self._current = current

    def compose(self):
        options = []
        self._highlight_idx = 0
        for i, (value, label) in enumerate(self._items):
            marker = "\u25b6 " if value == self._current else "  "
            options.append(Option(f"{marker}{label}", id=value))
            if value == self._current:
                self._highlight_idx = i

        with Container(id="selector-container"):
            with Container(id="selector-card"):
                yield Static(self._title, id="selector-title")
                yield OptionList(*options, id="selector-list")

                yield Static("\u2191\u2193: navigate \u00b7 Enter: select \u00b7 Esc: cancel", id="selector-hint")

    def on_mount(self):
        ol = self.query_one("#selector-list", OptionList)
        ol.highlighted = self._highlight_idx
        ol.focus()

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option.id)


# S-meter thresholds (dB values corresponding to S-units, approximate)
S_METER_BLOCKS = "▏▎▍▌▋▊▉█"


def s_meter_bar(level_db, width=20, min_db=-120.0, max_db=-20.0):
    """Render an S-meter bar from a dB level."""
    frac = max(0.0, min(1.0, (level_db - min_db) / (max_db - min_db)))
    filled = frac * width
    full = int(filled)
    partial_idx = int((filled - full) * len(S_METER_BLOCKS))

    bar = "█" * full
    if full < width and partial_idx > 0:
        bar += S_METER_BLOCKS[min(partial_idx, len(S_METER_BLOCKS) - 1)]
        full += 1
    bar += " " * (width - full)
    return bar


class DemodApp(App):
    TITLE = f"SWL Demod Tool v{__version__}"
    CSS = CSS
    AUTO_FOCUS = None
    theme = "tokyo-night"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "unfocus", "Unfocus"),
        ("question_mark", "show_help", "Help"),
        ("c", "connect", "Connect"),
        ("x", "disconnect", "Disc"),
        ("r", "reconnect", "Recon"),
        ("0", "toggle_mute", "Mute"),
        ("a", "toggle_agc", "AGC"),
        ("plus", "volume_up", "Gain+"),
        ("minus", "volume_down", "Gain\u2212"),
        ("right_square_bracket", "bw_up", "BW+"),
        ("left_square_bracket", "bw_down", "BW\u2212"),
        ("shift+right", "zoom_in", "Zoom+"),
        ("shift+left", "zoom_out", "Zoom\u2212"),
        ("right", "tune_up", "Tune+"),
        ("left", "tune_down", "Tune\u2212"),
        ("slash", "focus_freq", "Freq"),
        ("m", "select_mode", "Mode"),
        ("b", "select_bw", "BW"),
        ("s", "select_tune_step", "Step"),
        ("up", "rit_up", "RIT+"),
        ("down", "rit_down", "RIT\u2212"),
        ("f", "select_rit_step", "RIT"),
        ("v", "select_vfo", "VFO"),
        ("t", "clear_cw_text", "ClrTxt"),
        ("p", "toggle_apf", "APF"),
        ("n", "toggle_nb", "NB"),
        ("N", "cycle_dnr", "DNR"),
        ("alt+n", "toggle_auto_notch", "DNF"),
        ("d", "toggle_spectrum", "Spec"),
    ]

    utc_display = reactive("--:-- UTC")
    frequency_hz = reactive(0)
    rit_offset = reactive(0)  # Cumulative RIT offset in Hz
    rit_step = reactive(10)  # RIT step size in Hz (1 or 10)

    active_vfo = reactive("--")
    peak_db = reactive(-120.0)
    tune_step = reactive(1000)  # Fine tune step in Hz
    station_name = reactive("")  # Station name from external scheduler

    def __init__(self, sdr_source, audio_device="default", keybindings=None, config=None):
        self._keybindings = keybindings or {}
        self._config = config
        self._shortcut_table = _build_shortcut_table(self._keybindings)
        self._status_bar_text = _build_status_bar(self._keybindings)
        super().__init__()
        self.audio_device = audio_device
        self.sdr = sdr_source
        self._spectrum_buf = deque(maxlen=SPECTRUM_AVG)
        self._iq_lock = threading.Lock()

        # Demodulation and audio — restore saved mode/bandwidth
        saved_mode = config.get("state", "mode", fallback="AM") if config else "AM"
        saved_bw = config.getint("state", "bandwidth", fallback=5000) if config else 5000
        self.demod = Demodulator(iq_sample_rate=192000, audio_rate=48000, bandwidth=saved_bw)
        self.demod.mode = saved_mode
        self.demod.reset()
        self.audio = AudioOutput(sample_rate=48000, block_size=1024)
        self.drm = DRMDecoder(iq_sample_rate=192000, audio_rate=48000)

        # Audio level tracking
        self._audio_level_db = -120.0
        self._audio_level_lock = threading.Lock()

        # S-meter from CAT
        self._s_unit = "S0"
        self._s_raw = 0
        self._s_lock = threading.Lock()

        # Spectrum zoom: fraction of full bandwidth shown (1.0 = full, 0.0625 = 1/16)
        self._spectrum_zoom = 1.0

        # CW tone hold: keep last reading visible for a short time after tone drops
        self._cw_tone_hold = 0

        # DRM status change tracking
        self._last_drm_plain = ""

        # Guard against concurrent CAT polls
        self._cat_polling = False

        # Spectrum display subprocess
        self._spectrum_proc = None
        self._spectrum_fifo_fd = -1

        # Station name FIFO listener
        self._station_fifo_stop = threading.Event()
        self._station_fifo_thread = None

        # Schedule lookup: track last queried frequency to avoid redundant lookups
        self._sked_last_freq = 0

    def compose(self):
        yield Static(id="title-bar")
        yield Static(id="conn-status")
        with Horizontal(id="radio-info"):
            yield Static(id="radio-text")
            yield Static(
                "[#a3aed2]░▒▓[/]"
                "[#090c0c on #a3aed2] Freq. [/]"
                "[#a3aed2 on black]\ue0b0 [/]",
                id="freq-label",
            )
            yield Input(placeholder="kHz", id="freq-input")
        yield Static(id="spectrum-display")
        yield Static(id="audio-info")
        yield Static(id="mode-info")
        yield Static(id="status-bar", markup=True)
        yield Footer()

    def _apply_keybinding_overrides(self):
        """Rebind any keys that differ from class-level defaults."""
        from swl_demod_tool.config import DEFAULT_KEYS, _to_textual_key
        for action, configured_key in self._keybindings.items():
            default_key = DEFAULT_KEYS.get(action)
            if default_key is None or configured_key == default_key:
                continue
            default_tkey = _to_textual_key(default_key)
            new_tkey = _to_textual_key(configured_key)
            meta = KEYBINDING_META.get(action)
            desc = (meta[2] or meta[0]) if meta else action
            # Remove old binding and add new one
            self._bindings.key_to_bindings.pop(default_tkey, None)
            self._bindings.bind(new_tkey, action, description=desc)

    def on_mount(self):
        try:
            fd = os.open("/dev/tty", os.O_WRONLY)
            try:
                os.write(fd, f"\033]0;SWL Demod Tool v{__version__}\007".encode())
            finally:
                os.close(fd)
        except OSError:
            pass
        # Apply keybinding overrides from config
        self._apply_keybinding_overrides()
        self._update_all()
        self.set_interval(1, self._tick)
        self.set_interval(0.1, self._update_displays)
        # Start station name FIFO listener
        self._station_fifo_thread = threading.Thread(
            target=self._station_fifo_reader, daemon=True)
        self._station_fifo_thread.start()
        # Auto-connect on start
        self.action_connect()

    def _update_all(self):
        self._update_title()
        self._update_conn_status()
        self._update_radio_info()
        self._update_spectrum()
        self._update_mode_info()
        self._update_audio_info()
        self._update_status()

    def _tick(self):
        now = datetime.now(timezone.utc)
        self.utc_display = now.strftime("%H:%M:%S UTC")
        self._update_title()
        if self.sdr.has_control:
            self._poll_cat()

    def _station_fifo_reader(self):
        """Daemon thread: read station names from FIFO."""
        path = STATION_FIFO
        # Create FIFO if it doesn't exist
        if not os.path.exists(path):
            try:
                os.mkfifo(path)
            except OSError:
                return
        elif not stat.S_ISFIFO(os.stat(path).st_mode):
            return
        while not self._station_fifo_stop.is_set():
            try:
                # Blocking open — waits for a writer
                with open(path, "r") as f:
                    for line in f:
                        if self._station_fifo_stop.is_set():
                            break
                        name = line.strip()
                        if name:
                            self.call_from_thread(setattr, self, "station_name", name)
                # Writer closed — reopen to wait for next writer
            except OSError:
                if self._station_fifo_stop.is_set():
                    break
                self._station_fifo_stop.wait(1)

    def _update_displays(self):
        """Periodic UI refresh for fast-changing displays."""
        self._update_spectrum()
        self._update_mode_info()
        self._update_audio_info()
        self._update_radio_info()

    def _update_title(self):
        bar = self.query_one("#title-bar", Static)
        bar.update(f"  SWL Demod Tool v{__version__}     {self.utc_display}")

    def _update_conn_status(self):
        w = self.query_one("#conn-status", Static)
        iq_icon = "[green]●[/]" if self.sdr.connected else "[#888888]○[/]"
        ctl_icon = "[green]●[/]" if self.sdr.has_control else "[#888888]○[/]"
        audio_icon = "[green]●[/]" if self.audio.is_running else "[#888888]○[/]"

        sdr_label = ""
        iq_detail = ""
        cat_detail = ""
        if self.sdr.info:
            sdr_label = self.sdr.info.label
            host = getattr(self.sdr, 'host', '')
            iq_detail = f"  {host}:{getattr(self.sdr, 'iq_port', '')}  {self.sdr.info.sample_rate} Hz  {self.sdr.info.sample_bits}-bit IQ"
        if self.sdr.has_control:
            host = getattr(self.sdr, 'host', '')
            cat_detail = f" {sdr_label}  {host}:{getattr(self.sdr, 'cat_port', '')}"

        text = (
            f"    IQ {iq_icon} {sdr_label}{iq_detail}\n"
            f"   CAT {ctl_icon}{cat_detail}\n"
            f" Audio {audio_icon} {self.audio.sample_rate} Hz"
        )
        w.update(Text.from_markup(text))

    def _update_radio_info(self):
        w = self.query_one("#radio-text", Static)
        vfo = self.active_vfo
        mode = self.demod.mode
        bw = 10000 if mode == "DRM" else self.demod.bandwidth
        bw_str = f"BW: {bw} Hz"
        step_str = f"Step: {self.tune_step} Hz"
        if self.frequency_hz > 0:
            freq_mhz = self.frequency_hz / 1e6
            text = f"  VFO: {vfo}    Frequency: {freq_mhz:.6f} MHz    Mode: {mode}    {bw_str}    {step_str}"
        else:
            text = f"  VFO: {vfo}    Frequency: ---    Mode: {mode}    {bw_str}    {step_str}"
        w.update(Text.from_markup(text))

    def _update_spectrum(self):
        w = self.query_one("#spectrum-display", Static)
        if not self.sdr.connected or len(self._spectrum_buf) == 0:
            w.update("  Spectrum: [no data]")
            return

        with self._iq_lock:
            avg = np.mean(list(self._spectrum_buf), axis=0)

        # Zoom: slice the center portion of the FFT
        n = len(avg)
        visible = max(4, int(n * self._spectrum_zoom))
        start = (n - visible) // 2
        zoomed = avg[start:start + visible]

        try:
            width = self.size.width - 6
        except AttributeError:
            width = 60
        width = max(20, min(width, 200))

        graph = spectrum_to_sparkline(zoomed, width=width, height=9, min_db=-120.0, max_db=-20.0)

        # Indent each row
        indented = "\n".join("  " + row for row in graph.split("\n"))

        center = width // 2

        # Bandwidth underline centered on the marker
        sample_rate = self.sdr.info.sample_rate if self.sdr.info and self.sdr.info.sample_rate > 0 else 192000
        span_hz = sample_rate * self._spectrum_zoom
        bw = self.demod.bandwidth if self.demod.mode != "DRM" else 10000
        bw_half = max(1, int(bw / span_hz * width) // 2)
        bw_start = max(0, center - bw_half)
        bw_end = min(width, center + bw_half)
        # Build center marker line
        bw_bar = [" "] * width
        if 0 <= center < width:
            bw_bar[center] = "▲"
        bw_line = "".join(bw_bar)

        # Show station name and visible span
        span_khz = span_hz / 1000
        span_str = f"Span: {span_khz:.0f} kHz"

        stn = self.station_name
        if stn:
            avail = width - len(span_str) - 2
            if len(stn) > avail:
                stn = stn[:max(0, avail - 1)] + "…"
            info_line = f"  [bold #f0c674]{stn}[/]{' ' * max(1, width - len(stn) - len(span_str))}{span_str}"
        else:
            info_line = f"  {' ' * max(1, width - len(span_str))}{span_str}"

        w.update(
            f"{indented}\n"
            f"  {bw_line}\n"
            f"{info_line}"
        )

    def _cw_tuning_bar(self, width=21):
        """Build a center-zero tuning indicator for CW mode."""
        center = width // 2
        bar = list("░" * width)
        bar[center] = "│"  # center mark (on-tune target)
        wpm = self.demod.get_cw_wpm()
        wpm_str = f"{wpm:2.0f} WPM" if wpm > 0 else "-- WPM"
        if self.demod.get_cw_tone_present():
            self._cw_tone_hold = 10  # ~1 second at 100ms refresh
        elif self._cw_tone_hold > 0:
            self._cw_tone_hold -= 1
        if self._cw_tone_hold <= 0:
            self.demod.clear_cw_timing()
            return f"   Tune: [{''.join(bar)}] - ---.- Hz    SNR: -- dB    -- WPM"
        peak = self.demod.get_cw_peak_hz()
        target = self.demod.bfo_offset
        deviation = peak - target  # Hz off from ideal
        # Map deviation to bar position: ±150 Hz range, center = on-tune
        max_dev = 150.0
        norm = max(-1.0, min(1.0, deviation / max_dev))
        pos = center + int(norm * center)
        pos = max(0, min(width - 1, pos))
        bar[pos] = "█"     # actual peak position
        snr = self.demod.get_cw_snr_db()
        sign = "+" if deviation >= 0 else "-"
        abs_dev = abs(deviation)
        tune_str = f"{sign}{abs_dev:6.1f}"
        snr_str = f"{snr:2.0f}"
        return f"   Tune: [{''.join(bar)}] {tune_str} Hz    SNR: {snr_str} dB    {wpm_str}"

    def _update_audio_info(self):
        w = self.query_one("#audio-info", Static)

        # Volume display
        vol_db = 20.0 * np.log10(max(self.demod.volume, 1e-10))
        mute_str = " [MUTE]" if self.demod.muted else ""
        vol_pct = int(self.demod.volume * 100)
        vol_filled = int(vol_pct * 13 / 100)
        vol_bar = "█" * vol_filled + "░" * (13 - vol_filled)

        # AGC info
        agc_gain_db = self.demod.get_agc_gain_db()
        if self.demod.agc_enabled:
            agc_bar = s_meter_bar(agc_gain_db, width=13, min_db=-60.0, max_db=100.0)
            agc_str = f"AGC: [{agc_bar}] {agc_gain_db:+.0f} dB"
        else:
            agc_str = f"AGC: [{'OFF':░<13s}] {agc_gain_db:+.0f} dB"

        # Audio level meter
        with self._audio_level_lock:
            level_db = self._audio_level_db
        level_bar = s_meter_bar(level_db, width=13, min_db=-80.0, max_db=0.0)

        # Buffer fill
        fill_pct = int(self.audio.buffer_fill * 100)
        buf_filled = int(fill_pct * 13 / 100)
        buf_bar = "█" * buf_filled + "░" * (13 - buf_filled)
        underruns = self.audio.underruns

        peak_bar = s_meter_bar(self.peak_db, width=13, min_db=-120.0, max_db=-20.0)
        with self._s_lock:
            s_unit = self._s_unit
            s_raw = self._s_raw
        # SM raw value 0-22 maps to S0-S9+60
        s_frac = max(0.0, min(1.0, s_raw / 22.0))
        s_filled = int(s_frac * 13)
        s_bar = "█" * s_filled + "░" * (13 - s_filled)

        # Noise reduction status
        nb_str = f" NB: ON ({self.demod.nb_threshold_name})" if self.demod.nb_enabled else " NB: OFF"
        dnr_lvl = self.demod.dnr_level
        dnr_str = f"DNR: {dnr_lvl}" if dnr_lvl > 0 else "DNR: OFF"
        an_str = "DNF: ON" if self.demod.auto_notch else "DNF: OFF"
        apf_str = "APF: ON" if self.demod.apf_enabled else "APF: OFF"
        buf_str = f"BUF: [{buf_bar}] {fill_pct:2d}% U:{underruns}"
        s_str = f"  S: [{s_bar}] {s_unit}"

        CW = 45  # fixed column width

        text = (
            f"{'AF Gain: [' + vol_bar + '] ' + f'{vol_db:.1f} dB' + mute_str:<{CW}s}"
            f"{agc_str:<{CW}s}"
            f"{nb_str}\n"
            f"{'AF Peak: [' + level_bar + '] ' + f'{level_db:.0f} dB':<{CW}s}"
            f"{buf_str:<{CW}s}"
            f"{dnr_str}\n"
            f"{'RF Peak: [' + peak_bar + '] ' + f'{self.peak_db:.1f} dBFS':<{CW}s}"
            f"{s_str:<{CW}s}"
            f"{an_str}\n"
            f"{'':>{CW}s}"
            f"{'':>{CW}s}"
            f"{apf_str}"
        )
        w.update(text)

    def _rit_str(self):
        """Format RIT offset for display."""
        step = f"Step:{self.rit_step:3d}Hz"
        if self.rit_offset == 0:
            return f"RIT:    0 Hz  {step}"
        sign = "+" if self.rit_offset > 0 else "-"
        return f"RIT: {sign}{abs(self.rit_offset):3d} Hz  {step}"

    def _snr_str(self):
        """Format SNR measurement for display."""
        snr = self.demod.get_snr_db()
        if snr > 0.5:
            return f"SNR: {snr:2.0f} dB"
        return "SNR: -- dB"

    def _rtty_tuning_bar(self, width=10):
        """Build a mark/space level indicator for RTTY mode."""
        mark, space = self.demod.get_rtty_levels()
        peak = max(mark, space, 1e-12)
        m_norm = mark / peak
        s_norm = space / peak
        m_filled = int(m_norm * width)
        s_filled = int(s_norm * width)
        m_bar = "█" * m_filled + "░" * (width - m_filled)
        s_bar = "█" * s_filled + "░" * (width - s_filled)
        # Show which tone is active
        active = "MARK" if mark > space else "SPC " if space > mark else "----"
        return f"M [{m_bar}]  S [{s_bar}]  {active}"

    def _update_mode_info(self):
        w = self.query_one("#mode-info", Static)
        mode = self.demod.mode
        if mode in ("CW+", "CW-"):
            cw_text = self.demod.get_cw_text()
            t = Text(f"{self._cw_tuning_bar()}    {self._rit_str()}")
            if cw_text:
                t.append(f"\n   {cw_text}")
            w.update(t)
        elif mode in ("RTTY+", "RTTY-"):
            rtty_text = self.demod.get_rtty_text()
            polarity = "normal" if mode == "RTTY+" else "reverse"
            t = Text(f"   {mode} 45.45 Bd / 170 Hz shift ({polarity})    {self._rtty_tuning_bar()}    {self._snr_str()}")
            if rtty_text:
                t.append(f"\n   {rtty_text}")
            w.update(t)
        elif mode == "PSK31":
            psk_text = self.demod.get_psk_text()
            t = Text(f"   BPSK31 31.25 Bd    {self._snr_str()}")
            if psk_text:
                t.append(f"\n   {psk_text}")
            w.update(t)
        elif mode == "MFSK16":
            mfsk_text = self.demod.get_mfsk_text()
            tone, conf = self.demod.get_mfsk_tone()
            tone_str = f"T:{tone:2d}" if tone >= 0 else "T:--"
            conf_str = f"{conf * 100:2.0f}%" if tone >= 0 else "--%"
            t = Text(f"   MFSK16 15.625 Bd / 16 tones / 250 Hz    {tone_str} {conf_str}    {self._snr_str()}")
            if mfsk_text:
                t.append(f"\n   {mfsk_text}")
            w.update(t)
        elif mode == "DRM":
            t = self._drm_status_text()
            if t is not None:
                w.update(t)
        elif mode in ("SAM", "SAM-U", "SAM-L"):
            offset = self.demod.get_pll_offset_hz()
            sign = "+" if offset >= 0 else "-"
            w.update(f"   PLL Offset: {sign}{abs(offset):6.1f} Hz    {self._snr_str()}")
        elif mode in ("USB", "LSB"):
            w.update(f"   {self._rit_str()}    {self._snr_str()}")
        elif mode == "AM":
            w.update(f"   {self._snr_str()}")
        else:
            w.update("")

    _SYNC_STYLES = {"O": "green", "X": "red", "*": "yellow", "-": "#888888"}

    def _drm_status_text(self):
        """Build DRM status as a Rich Text object (avoids markup parsing).

        Returns None if nothing changed since the last call.
        """
        st = self.drm.get_status()

        # Build styled Text, then derive plain string for change detection
        sd = st.get("sync_detail", {})
        t = Text("   Sync: ")
        for key in ("io", "time", "frame", "fac", "sdc", "msc"):
            c = sd.get(key, "-")
            t.append(f"{key}:", style="#888888")
            t.append(c, style=self._SYNC_STYLES.get(c, "#888888"))
            t.append(" ")
        if st["signal"]:
            t.append(f"  SNR: {st['snr']:.1f} dB    Mode: {st['mode']}")
            if st.get("sdc_qam") or st.get("msc_qam"):
                t.append("    Coding: ")
                if st.get("sdc_qam"):
                    t.append(f"SDC {st['sdc_qam']}")
                if st.get("sdc_qam") and st.get("msc_qam"):
                    t.append(", ")
                if st.get("msc_qam"):
                    t.append(f"MSC {st['msc_qam']}")
            if st["label"]:
                t.append("    Station: ")
                t.append(st["label"], style="bold yellow")
            if st["bitrate"] > 0:
                t.append(f"    {st['bitrate']:.1f} kbps")
            if st.get("audio_mode"):
                t.append(f"  {st['audio_mode']}")
            if st.get("country"):
                t.append("    ")
                t.append(st["country"], style="bright_white")
            if st.get("language"):
                t.append(f"  ({st['language']})")
            if st.get("text"):
                t.append("\n   ")
                t.append(st["text"], style="cyan")
        else:
            t.append("  Acquiring...")

        plain = t.plain
        if plain == self._last_drm_plain:
            return None
        self._last_drm_plain = plain
        return t

    def _update_status(self):
        bar = self.query_one("#status-bar", Static)
        bar.update(self._status_bar_text)

    # --- IQ data callback (from network thread) ---

    def _on_iq_data(self, iq_samples):
        """Called from IQ client thread with new IQ data."""
        # Spectrum display (always, regardless of mode)
        db = compute_spectrum_db(iq_samples, FFT_SIZE)
        with self._iq_lock:
            self._spectrum_buf.append(db)
        peak = float(np.max(db))

        if self.demod.mode == "DRM":
            # Feed IQ to Dream subprocess
            self.drm.write_iq(iq_samples)
        else:
            # Demodulate and output audio locally
            audio = self.demod.process(iq_samples)
            if len(audio) > 0:
                # Track audio level
                rms = np.sqrt(np.mean(audio ** 2)) if not self.demod.muted else 0.0
                level_db = 20.0 * np.log10(max(rms, 1e-10))
                with self._audio_level_lock:
                    self._audio_level_db = level_db

                # Push to audio output
                self.audio.write(audio)

        self.call_from_thread(self._apply_iq_update, peak)

    def _on_drm_audio(self, audio):
        """Called from DRM reader thread with decoded float32 audio."""
        self.audio.write(audio)

    def _apply_iq_update(self, peak):
        self.peak_db = peak

    # --- CAT helpers ---

    def _get_active_freq(self, vfo=None):
        """Query frequency for the given or current VFO."""
        vfo = vfo or self.active_vfo
        return self.sdr.get_frequency(vfo)

    # --- CAT polling ---

    @work(thread=True)
    def _poll_cat(self):
        if self._cat_polling or not self.sdr.has_control:
            return
        self._cat_polling = True
        try:
            vfo = self.sdr.get_active_vfo()
            if vfo is not None:
                self.call_from_thread(setattr, self, "active_vfo", vfo)
            freq = self._get_active_freq(vfo)
            if freq is not None:
                self.call_from_thread(self._apply_cat_update, freq)
                # Schedule lookup when frequency changes
                if freq != self._sked_last_freq:
                    self._sked_last_freq = freq
                    name = _lookup_station(freq)
                    if name:
                        self.call_from_thread(
                            setattr, self, "station_name", name)
            sm = self.sdr.get_s_meter()
            if sm is not None:
                with self._s_lock:
                    self._s_unit, self._s_raw = sm
            if not self.sdr.has_control:
                self.call_from_thread(self._update_conn_status)
        finally:
            self._cat_polling = False

    def _apply_cat_update(self, freq):
        self.frequency_hz = freq
        self._update_radio_info()
        self._spectrum_update()

    # --- Actions ---

    def action_connect(self):
        self._do_connect()

    @work(thread=True)
    def _do_connect(self):
        if not self.sdr.connected:
            ok = self.sdr.connect()
            self.call_from_thread(self._update_conn_status)
            if ok:
                # Update demod sample rate from SDR
                if self.sdr.info and self.sdr.info.sample_rate > 0:
                    sr = self.sdr.info.sample_rate
                    self.demod.iq_sample_rate = sr
                    self.demod.decimation = sr // self.demod.audio_rate
                    self.demod.set_bandwidth(self.demod.bandwidth)
                    self.drm.iq_sample_rate = sr
                # Start audio output and DRM decoder if active
                self.audio.start(device=self.audio_device)
                if self.demod.mode == "DRM":
                    self.drm.start(audio_callback=self._on_drm_audio)
                self.call_from_thread(self._update_conn_status)
                # Start IQ streaming
                self.sdr.start_streaming(self._on_iq_data)

        # Query radio control if available
        if self.sdr.has_control:
            vfo = self.sdr.get_active_vfo()
            if vfo:
                self.call_from_thread(setattr, self, "active_vfo", vfo)
            freq = self._get_active_freq(vfo)
            if freq:
                self.call_from_thread(setattr, self, "frequency_hz", freq)
            self.call_from_thread(self._update_radio_info)

    def action_disconnect(self):
        self.sdr.disconnect()
        self.audio.stop()
        self.drm.stop()
        self.demod.reset()
        self._spectrum_buf.clear()
        self._update_conn_status()
        self._update_radio_info()

    def action_reconnect(self):
        self.action_disconnect()
        self.action_connect()

    def action_toggle_mute(self):
        self.demod.muted = not self.demod.muted
        self._update_audio_info()

    def action_toggle_agc(self):
        self.demod.agc_enabled = not self.demod.agc_enabled
        self._update_audio_info()

    _MODE_LIST = ["AM", "SAM", "SAM-U", "SAM-L", "USB", "LSB", "CW+", "CW-",
                   "RTTY+", "RTTY-", "PSK31", "MFSK16", "DRM"]
    _MODE_HINTS = {
        "AM": "Amplitude modulation", "SAM": "Synchronous AM",
        "SAM-U": "Sync AM upper", "SAM-L": "Sync AM lower",
        "USB": "Upper sideband", "LSB": "Lower sideband",
        "CW+": "CW upper beat", "CW-": "CW lower beat",
        "RTTY+": "RTTY normal", "RTTY-": "RTTY inverted",
        "PSK31": "31.25 baud BPSK", "MFSK16": "16-tone FSK + FEC",
        "DRM": "Digital Radio Mondiale",
    }
    _BW_PRESETS = [100, 250, 500, 1200, 2400, 3200, 5000, 10000]
    _MODE_DEFAULT_BW = {"AM": 5000, "SAM": 5000, "SAM-U": 5000, "SAM-L": 5000,
                        "USB": 2400, "LSB": 2400, "CW+": 500, "CW-": 500,
                        "RTTY+": 2400, "RTTY-": 2400, "PSK31": 500, "MFSK16": 500}

    def action_select_mode(self):
        """Show mode selector popup."""
        items = [(m, f"{m:8s} {self._MODE_HINTS.get(m, '')}") for m in self._MODE_LIST]
        self.push_screen(
            SelectorScreen("Select Mode", items, current=self.demod.mode),
            callback=self._on_mode_selected,
        )

    def _on_mode_selected(self, value):
        if value is None:
            return
        new_mode = value
        old_mode = self.demod.mode
        if new_mode == old_mode:
            return

        # Transition away from DRM: stop Dream
        if old_mode == "DRM" and new_mode != "DRM":
            self.drm.stop()

        # Transition to DRM: start Dream with audio callback
        if new_mode == "DRM" and old_mode != "DRM":
            if not self.drm.start(audio_callback=self._on_drm_audio):
                self.notify("Dream binary not found", severity="error")
                return

        self.demod.mode = new_mode
        self.demod.reset()
        self.rit_offset = 0
        if new_mode in self._MODE_DEFAULT_BW:
            self.demod.set_bandwidth(self._MODE_DEFAULT_BW[new_mode])
        self._update_radio_info()
        self._spectrum_update()

    def action_select_bw(self):
        """Show bandwidth selector popup."""
        items = [(str(bw), f"{bw:>5d} Hz") for bw in self._BW_PRESETS]
        current_bw = str(self.demod.bandwidth)
        self.push_screen(
            SelectorScreen("Select Bandwidth", items, current=current_bw),
            callback=self._on_bw_selected,
        )

    def _on_bw_selected(self, value):
        if value is None:
            return
        self.demod.set_bandwidth(int(value))
        self._update_radio_info()
        self._spectrum_update()

    def action_clear_cw_text(self):
        """Clear the decoded CW/RTTY/PSK text buffer."""
        self.demod.clear_cw_text()
        self.demod.clear_rtty_text()
        self.demod.clear_psk_text()
        self.demod.clear_mfsk_text()

    def action_toggle_nb(self):
        """Cycle NB: Off → Low → Med → High → Off."""
        if not self.demod.nb_enabled:
            self.demod.nb_enabled = True
            self.demod.set_nb_threshold("Low")
        elif self.demod.nb_threshold_name == "High":
            self.demod.nb_enabled = False
        else:
            self.demod.cycle_nb_threshold()
        self._update_audio_info()

    def action_cycle_nb_threshold(self):
        self.action_toggle_nb()

    def action_cycle_dnr(self):
        self.demod.cycle_dnr_level()
        self._update_audio_info()

    def action_toggle_auto_notch(self):
        self.demod.toggle_auto_notch()
        self._update_audio_info()

    def action_toggle_apf(self):
        self.demod.toggle_apf()
        self._update_audio_info()

    # --- Spectrum display (swl-spectrum) ---

    def _spectrum_fifo_send(self, msg):
        """Send a message to the spectrum FIFO. Returns True on success."""
        try:
            if self._spectrum_fifo_fd < 0:
                self._spectrum_fifo_fd = os.open(
                    SPECTRUM_FIFO, os.O_WRONLY | os.O_NONBLOCK)
            os.write(self._spectrum_fifo_fd, msg.encode())
            return True
        except OSError:
            # FIFO not open or broken — close and reset
            if self._spectrum_fifo_fd >= 0:
                try:
                    os.close(self._spectrum_fifo_fd)
                except OSError:
                    pass
                self._spectrum_fifo_fd = -1
            return False

    def _spectrum_update(self):
        """Push current freq/mode/bw to swl-spectrum via FIFO."""
        if self._spectrum_proc is None or self._spectrum_proc.poll() is not None:
            return
        if self.frequency_hz > 0:
            self._spectrum_fifo_send(f"FREQ:{self.frequency_hz}\n")
        mode = self.demod.mode
        if mode:
            self._spectrum_fifo_send(f"MODE:{mode}\n")
        bw = self.demod.bandwidth
        if bw > 0:
            self._spectrum_fifo_send(f"BW:{bw}\n")

    def action_toggle_spectrum(self):
        """Launch or kill the swl-spectrum display."""
        # If running, kill it
        if self._spectrum_proc is not None and self._spectrum_proc.poll() is None:
            self._spectrum_proc.terminate()
            self._spectrum_proc = None
            if self._spectrum_fifo_fd >= 0:
                try:
                    os.close(self._spectrum_fifo_fd)
                except OSError:
                    pass
                self._spectrum_fifo_fd = -1
            return

        # Create FIFO if needed
        try:
            os.mkfifo(SPECTRUM_FIFO)
        except FileExistsError:
            # Ensure it's actually a FIFO
            if not stat.S_ISFIFO(os.stat(SPECTRUM_FIFO).st_mode):
                os.unlink(SPECTRUM_FIFO)
                os.mkfifo(SPECTRUM_FIFO)

        # Build command: swl-spectrum <host> <iq_port> [-f freq] [-m mode] [-b bw]
        host = getattr(self.sdr, 'host', 'localhost')
        iq_port = getattr(self.sdr, 'iq_port', 4533)
        cmd = ["swl-spectrum", host, str(iq_port)]
        if self.frequency_hz > 0:
            cmd += ["-f", str(self.frequency_hz)]
        mode = self.demod.mode
        if mode:
            cmd += ["-m", mode]
        bw = self.demod.bandwidth
        if bw > 0:
            cmd += ["-b", str(bw)]

        try:
            self._spectrum_proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            self.notify("swl-spectrum not found in PATH", severity="error")

    def action_volume_up(self):
        self.demod.volume = min(1.0, self.demod.volume + 0.05)
        self._update_audio_info()

    def action_volume_down(self):
        self.demod.volume = max(0.0, self.demod.volume - 0.05)
        self._update_audio_info()

    def _bw_limits(self):
        """Return (min, max, step) for the current demod mode."""
        if self.demod.mode in ("AM", "SAM", "SAM-U", "SAM-L"):
            return 4000, 10000, 1000
        elif self.demod.mode in ("USB", "LSB"):
            return 1200, 3200, 100
        elif self.demod.mode in ("CW+", "CW-"):
            return 100, 1000, 50
        elif self.demod.mode in ("RTTY+", "RTTY-"):
            return 1200, 3200, 100
        elif self.demod.mode == "PSK31":
            return 200, 1000, 50
        elif self.demod.mode == "MFSK16":
            return 200, 1000, 50
        return 100, 24000, 500  # fallback

    def action_bw_up(self):
        """Increase demodulation bandwidth."""
        bw_min, bw_max, step = self._bw_limits()
        self.demod.set_bandwidth(min(bw_max, self.demod.bandwidth + step))
        self._update_radio_info()
        self._spectrum_update()

    def action_bw_down(self):
        """Decrease demodulation bandwidth."""
        bw_min, bw_max, step = self._bw_limits()
        self.demod.set_bandwidth(max(bw_min, self.demod.bandwidth - step))
        self._update_radio_info()
        self._spectrum_update()

    def action_zoom_in(self):
        """Zoom into the spectrum (halve visible span)."""
        self._spectrum_zoom = max(1 / 64, self._spectrum_zoom / 2)

    def action_zoom_out(self):
        """Zoom out of the spectrum (double visible span)."""
        self._spectrum_zoom = min(1.0, self._spectrum_zoom * 2)

    def action_focus_freq(self):
        """Focus the frequency input field."""
        self.query_one("#freq-input", Input).focus()

    def action_tune_up(self):
        """Fine tune up by tune_step Hz."""
        self._tune_offset(self.tune_step)
        self.rit_offset = 0

    def action_tune_down(self):
        """Fine tune down by tune_step Hz."""
        self._tune_offset(-self.tune_step)
        self.rit_offset = 0

    _TUNE_STEPS = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]

    def action_select_tune_step(self):
        """Show tune step selector popup."""
        items = [(str(s), f"{s:>5d} Hz") for s in self._TUNE_STEPS]
        self.push_screen(
            SelectorScreen("Select Tune Step", items, current=str(self.tune_step)),
            callback=self._on_tune_step_selected,
        )

    def _on_tune_step_selected(self, value):
        if value is None:
            return
        self.tune_step = int(value)

    def action_rit_up(self):
        """RIT tune up (SSB/CW modes)."""
        if self.demod.mode in ("USB", "LSB", "CW+", "CW-"):
            self._tune_offset(self.rit_step)
            self.rit_offset += self.rit_step

    def action_rit_down(self):
        """RIT tune down (SSB/CW modes)."""
        if self.demod.mode in ("USB", "LSB", "CW+", "CW-"):
            self._tune_offset(-self.rit_step)
            self.rit_offset -= self.rit_step

    def action_select_rit_step(self):
        """Cycle RIT step: 1 → 10 → 100 → 1 Hz."""
        steps = [1, 10, 100]
        idx = steps.index(self.rit_step) if self.rit_step in steps else 0
        self.rit_step = steps[(idx + 1) % len(steps)]
        self._update_mode_info()

    def action_select_vfo(self):
        """Show VFO selector popup."""
        if not self.sdr.has_control:
            return
        items = [("A", "VFO-A"), ("B", "VFO-B")]
        self.push_screen(
            SelectorScreen("Select VFO", items, current=self.active_vfo),
            callback=self._on_vfo_selected,
        )

    def _on_vfo_selected(self, value):
        if value is None or value == self.active_vfo:
            return
        self._do_set_vfo(value)

    @work(thread=True)
    def _do_set_vfo(self, vfo):
        """Send VFO switch command to radio in a worker thread."""
        if self.sdr.set_active_vfo(vfo):
            self.call_from_thread(setattr, self, "active_vfo", vfo)
            freq = self._get_active_freq(vfo)
            if freq is not None:
                self.call_from_thread(self._apply_cat_update, freq)
            else:
                self.call_from_thread(self._update_radio_info)

    def _tune_offset(self, offset_hz):
        """Tune the radio by an offset from current frequency."""
        if not self.sdr.has_control or self.frequency_hz <= 0:
            return
        new_freq = self.frequency_hz + offset_hz
        if new_freq < 0:
            return
        self._do_tune(new_freq)

    @work(thread=True)
    def _do_tune(self, freq_hz):
        """Send tune command to radio in a worker thread."""
        ok = self.sdr.set_frequency(freq_hz, vfo=self.active_vfo)
        if ok:
            self._sked_last_freq = freq_hz
            name = _lookup_station(freq_hz)
            self.call_from_thread(self._apply_tune, freq_hz, name)

    def _apply_tune(self, freq_hz, station=""):
        self.frequency_hz = freq_hz
        self.station_name = station
        self._update_radio_info()
        self._spectrum_update()

    def on_input_submitted(self, event):
        """Handle frequency input submission — tunes the active VFO."""
        if event.input.id != "freq-input":
            return
        value = event.input.value.strip()
        if not value:
            return
        try:
            freq_khz = float(value)
            freq_hz = int(freq_khz * 1000)
        except ValueError:
            return
        if 0 < freq_hz <= 2_000_000_000 and self.sdr.has_control:
            self._do_tune(freq_hz)
        event.input.value = ""
        self.set_focus(None)

    def check_action(self, action, parameters):
        """Suppress most keybindings while typing in the frequency input."""
        if isinstance(self.focused, Input) and action not in ("quit", "unfocus"):
            return None
        return True

    def action_unfocus(self):
        """Remove focus from any focused widget."""
        self.set_focus(None)

    def action_show_help(self):
        """Show keyboard shortcuts popup."""
        help_key = self._keybindings.get("show_help", "?")
        self.push_screen(HelpScreen(self._shortcut_table, help_key))

    def on_unmount(self):
        # Stop station FIFO listener
        self._station_fifo_stop.set()
        # Save mode and bandwidth to config
        if self._config:
            if not self._config.has_section("state"):
                self._config.add_section("state")
            self._config.set("state", "mode", self.demod.mode)
            self._config.set("state", "bandwidth", str(self.demod.bandwidth))
            save_config(self._config)
        self.audio.stop()
        self.drm.stop()
        self.sdr.disconnect()
        # Clean up spectrum display
        if self._spectrum_fifo_fd >= 0:
            try:
                os.close(self._spectrum_fifo_fd)
            except OSError:
                pass
        if self._spectrum_proc is not None and self._spectrum_proc.poll() is None:
            self._spectrum_proc.terminate()


def main():
    parser = argparse.ArgumentParser(description="SWL Demod Tool - TUI IQ demodulator")
    parser.add_argument("--sdr", default=None,
                        help=f"SDR backend (default: from config or {DEFAULT_BACKEND})")
    parser.add_argument("--host", default=None, help="Server host (default: from config)")
    parser.add_argument("--iq-port", type=int, default=None, help="IQ server port")
    parser.add_argument("--cat-port", type=int, default=None, help="CAT server port")
    parser.add_argument("--audio-device", default=None, help="Audio output device")
    parser.add_argument("--version", action="version", version=f"swl-demod {__version__}")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to swl-demod.log")
    args = parser.parse_args()

    if args.debug:
        log_dir = os.path.join(
            os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
            "swl-demod-tool")
        os.makedirs(log_dir, mode=0o700, exist_ok=True)
        logging.basicConfig(
            filename=os.path.join(log_dir, "swl-demod.log"),
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_config()
    backend = args.sdr or config.get("sdr", "backend", fallback=DEFAULT_BACKEND)
    sdr_source = create_sdr_source(backend, config, args)

    audio_device = args.audio_device or config.get("audio", "device")
    dream_path = config.get("drm", "dream_path", fallback="") or None
    keybindings = load_keybindings(config)

    app = DemodApp(sdr_source=sdr_source, audio_device=audio_device,
                   keybindings=keybindings, config=config)
    app.drm.dream_path = app.drm.dream_path or dream_path
    app.run()


if __name__ == "__main__":
    main()
