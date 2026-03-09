#!/usr/bin/env python3

"""SWL Demod Tool - TUI demodulator for Elad FDM-DUO IQ stream."""

import argparse
import logging
import os
import threading
import numpy as np
from collections import deque
from datetime import datetime, timezone

from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Static
from textual.reactive import reactive
from textual import work
from rich.text import Text

from swl_demod_tool import __version__
from swl_demod_tool.config import load_config
from swl_demod_tool.iq_client import IQClient
from swl_demod_tool.cat_client import CATClient
from swl_demod_tool.dsp import compute_spectrum_db, spectrum_to_sparkline, Demodulator
from swl_demod_tool.audio import AudioOutput
from swl_demod_tool.drm import DRMDecoder

SPECTRUM_AVG = 3
FFT_SIZE = 4096


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
}

#freq-bar {
    height: 2;
    background: black;
    padding: 0 1;
    align-horizontal: center;
}

#freq-prompt {
    width: 28;
    height: 2;
}

.prompt-char {
    width: 4;
    height: 1;
}

#freq-bar Input {
    width: 1fr;
    height: 1;
    background: black;
    color: #769ff0;
    border: none;
}

#freq-bar Input:focus {
    border: none;
}

#freq-bar Input.-placeholder {
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
    height: 4;
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
    FREQ_LABEL = (
        "[#769ff0 on #394260]╭─[/]"
        "[#a3aed2]░▒▓[/]"
        "[#090c0c on #a3aed2]  Freq [/]"
        "[#a3aed2 on black]\ue0b0[/]"
    )
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "unfocus", "Unfocus"),
        ("c", "connect", "Connect"),
        ("d", "disconnect", "Disconnect"),
        ("r", "reconnect", "Reconnect"),
        ("m", "toggle_mute", "Mute"),
        ("a", "toggle_agc", "AGC"),
        ("plus", "volume_up", "Vol+"),
        ("minus", "volume_down", "Vol-"),
        ("right_square_bracket", "bw_up", "BW+"),
        ("left_square_bracket", "bw_down", "BW-"),
        ("shift+right", "zoom_in", "Zoom+"),
        ("shift+left", "zoom_out", "Zoom-"),
        ("right", "tune_up", "Tune+"),
        ("left", "tune_down", "Tune-"),
        ("slash", "focus_freq", "Freq"),
        ("x", "cycle_mode", "Mode"),
        ("alt+right", "fine_tune_up", "Fine+"),
        ("alt+left", "fine_tune_down", "Fine-"),
        ("pageup", "rit_up", "RIT+"),
        ("pagedown", "rit_down", "RIT-"),
        ("v", "toggle_vfo", "VFO"),
        ("t", "clear_cw_text", "ClrTxt"),
    ]

    utc_display = reactive("--:-- UTC")
    frequency_hz = reactive(0)
    rit_offset = reactive(0)  # Cumulative RIT offset in Hz

    active_vfo = reactive("--")
    peak_db = reactive(-120.0)
    tune_step = reactive(1000)  # Fine tune step in Hz

    def __init__(self, host="localhost", iq_port=4533, cat_port=4532,
                 audio_device="default"):
        super().__init__()
        self.host = host
        self.iq_port = iq_port
        self.cat_port = cat_port
        self.audio_device = audio_device
        self.iq_client = IQClient(host, iq_port)
        self.cat_client = CATClient(host, cat_port)
        self._spectrum_buf = deque(maxlen=SPECTRUM_AVG)
        self._iq_lock = threading.Lock()

        # Demodulation and audio
        self.demod = Demodulator(iq_sample_rate=192000, audio_rate=48000, bandwidth=5000)
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

    def compose(self):
        yield Static(id="title-bar")
        with Horizontal(id="freq-bar"):
            with Vertical(id="freq-prompt"):
                yield Static(self.FREQ_LABEL)
                with Horizontal():
                    yield Static("[#769ff0 on #394260]╰─\uf10c[/]", classes="prompt-char")
                    yield Input(placeholder="kHz", id="freq-input")
        yield Static(id="conn-status")
        yield Static(id="radio-info")
        yield Static(id="spectrum-display")
        yield Static(id="audio-info")
        yield Static(id="mode-info")
        yield Static(id="status-bar", markup=True)
        yield Footer()

    def on_mount(self):
        try:
            fd = os.open("/dev/tty", os.O_WRONLY)
            try:
                os.write(fd, f"\033]0;SWL Demod Tool v{__version__}\007".encode())
            finally:
                os.close(fd)
        except OSError:
            pass
        self._update_all()
        self.set_interval(1, self._tick)
        self.set_interval(0.1, self._update_displays)
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
        if self.cat_client.connected:
            self._poll_cat()

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
        iq_icon = "[green]●[/]" if self.iq_client.connected else "[#888888]○[/]"
        cat_icon = "[green]●[/]" if self.cat_client.connected else "[#888888]○[/]"
        audio_icon = "[green]●[/]" if self.audio.is_running else "[#888888]○[/]"

        rate_str = ""
        if self.iq_client.connected:
            rate_str = f"  {self.iq_client.sample_rate} Hz  {self.iq_client.format_bits}-bit IQ"

        text = (
            f"    IQ {iq_icon} {self.host}:{self.iq_port}{rate_str}\n"
            f"   CAT {cat_icon} {self.host}:{self.cat_port}\n"
            f" Audio {audio_icon} {self.audio.sample_rate} Hz"
        )
        w.update(Text.from_markup(text))

    def _update_radio_info(self):
        w = self.query_one("#radio-info", Static)
        vfo = self.active_vfo
        mode = self.demod.mode
        bw_str = f"BW: {self.demod.bandwidth} Hz"
        if self.frequency_hz > 0:
            freq_mhz = self.frequency_hz / 1e6
            text = f"  VFO: {vfo}    Frequency: {freq_mhz:.6f} MHz    Mode: {mode}    {bw_str}"
        else:
            text = f"  VFO: {vfo}    Frequency: ---    Mode: {mode}    {bw_str}"
        w.update(Text.from_markup(text))

    def _update_spectrum(self):
        w = self.query_one("#spectrum-display", Static)
        if not self.iq_client.connected or len(self._spectrum_buf) == 0:
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
        except Exception:
            width = 60
        width = max(20, min(width, 200))

        graph = spectrum_to_sparkline(zoomed, width=width, height=9, min_db=-120.0, max_db=-20.0)

        # Indent each row
        indented = "\n".join("  " + row for row in graph.split("\n"))

        center = width // 2

        # Bandwidth underline centered on the marker
        sample_rate = self.iq_client.sample_rate if self.iq_client.sample_rate > 0 else 192000
        span_hz = sample_rate * self._spectrum_zoom
        bw = self.demod.bandwidth if self.demod.mode != "DRM" else 10000
        bw_half = max(1, int(bw / span_hz * width) // 2)
        bw_start = max(0, center - bw_half)
        bw_end = min(width, center + bw_half)
        # Build bandwidth bar with center marker
        bw_bar = [" "] * width
        for i in range(bw_start, center):
            bw_bar[i] = "▁"
        if 0 <= center < width:
            bw_bar[center] = "▲"
        for i in range(center + 1, bw_end + 1):
            if i < width:
                bw_bar[i] = "▁"
        bw_line = "".join(bw_bar)

        # Show center frequency and visible span
        freq_str = ""
        if self.frequency_hz > 0:
            freq_str = f"{self.frequency_hz / 1e6:.3f}"
        span_khz = span_hz / 1000
        span_str = f"Span: {span_khz:.0f} kHz"

        w.update(
            f"{indented}\n"
            f"  {bw_line}\n"
            f"  {'':>{center - len(freq_str)//2}}{freq_str}{'':>{max(1, width - center - len(freq_str)//2 - len(span_str))}}{span_str}"
        )

    def _cw_tuning_bar(self, width=20):
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
            self.demod._cw_wpm = 0.0
            self.demod._cw_element_ms = []
            return f"   Tune: [{''.join(bar)}] - ---.- Hz    SNR: -- dB    -- WPM"
        peak = self.demod.get_cw_peak_hz()
        target = self.demod._bfo_offset  # 700 Hz
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
        vol_pct = int(self.demod.volume * 100)
        mute_str = " [MUTE]" if self.demod.muted else ""
        vol_bar = "█" * (vol_pct // 5) + "░" * (20 - vol_pct // 5)

        # AGC info
        agc_str = "ON " if self.demod._agc_enabled else "OFF"
        agc_gain_db = self.demod.get_agc_gain_db()

        # Audio level meter
        with self._audio_level_lock:
            level_db = self._audio_level_db
        level_bar = s_meter_bar(level_db, width=20, min_db=-80.0, max_db=0.0)

        # Buffer fill
        fill_pct = int(self.audio.buffer_fill * 100)
        buf_bar = "█" * (fill_pct // 5) + "░" * (20 - fill_pct // 5)
        underruns = self.audio.underruns

        peak_bar = s_meter_bar(self.peak_db, width=20, min_db=-120.0, max_db=-20.0)
        with self._s_lock:
            s_unit = self._s_unit
            s_raw = self._s_raw
        # SM raw value 0-22 maps to S0-S9+60
        s_frac = max(0.0, min(1.0, s_raw / 22.0))
        s_filled = int(s_frac * 20)
        s_bar = "█" * s_filled + "░" * (20 - s_filled)

        text = (
            f"{'    Vol: [' + vol_bar + '] ' + str(vol_pct) + '%' + mute_str:<46s}"
            f"AGC:  {agc_str} ({agc_gain_db:+.0f} dB)\n"
            f"{'  Audio: [' + level_bar + '] ' + f'{level_db:.0f} dB':<46s}"
            f"{'Buf: [' + buf_bar + '] ' + f'{fill_pct:2d}%  Underruns: {underruns}'}\n"
            f"{'   Peak: [' + peak_bar + '] ' + f'{self.peak_db:.1f} dBFS':<46s}"
            f"  S: [{s_bar}] {s_unit}"
        )
        w.update(text)

    def _rit_str(self):
        """Format RIT offset for display."""
        if self.rit_offset == 0:
            return "RIT:    0 Hz"
        sign = "+" if self.rit_offset > 0 else "-"
        return f"RIT: {sign}{abs(self.rit_offset):3d} Hz"

    def _update_mode_info(self):
        w = self.query_one("#mode-info", Static)
        mode = self.demod.mode
        if mode in ("CW+", "CW-"):
            cw_text = self.demod.get_cw_text()
            line1 = f"{self._cw_tuning_bar()}    {self._rit_str()}"
            if cw_text:
                w.update(f"{line1}\n   {cw_text}")
            else:
                w.update(line1)
        elif mode == "DRM":
            t = self._drm_status_text()
            if t is not None:
                w.update(t)
        elif mode in ("SAM", "SAM-U", "SAM-L"):
            offset = self.demod.get_pll_offset_hz()
            sign = "+" if offset >= 0 else "-"
            w.update(f"   PLL Offset: {sign}{abs(offset):6.1f} Hz")
        elif mode in ("USB", "LSB"):
            w.update(f"   {self._rit_str()}")
        else:
            w.update("")

    _SYNC_STYLES = {"O": "green", "X": "red", "*": "yellow", "-": "#888888"}

    def _drm_status_text(self):
        """Build DRM status as a Text object (avoids markup parsing)."""
        st = self.drm.get_status()
        parts = [f"   Sync: {st['sync']}"]
        if st["signal"]:
            parts.append(f"  SNR: {st['snr']:.1f} dB    Mode: {st['mode']}")
            if st["label"]:
                parts.append(f"    Station: {st['label']}")
            if st["bitrate"] > 0:
                parts.append(f"    {st['bitrate']:.1f} kbps")
            if st.get("audio_mode"):
                parts.append(f"  {st['audio_mode']}")
            if st.get("country"):
                parts.append(f"    {st['country']}")
            if st.get("language"):
                parts.append(f"  ({st['language']})")
            if st.get("text"):
                parts.append(f"\n   {st['text']}")
        else:
            parts.append("  Acquiring...")
        plain = "".join(parts)

        # Skip widget update if nothing changed
        if plain == self._last_drm_plain:
            return None
        self._last_drm_plain = plain

        t = Text("   Sync: ")
        for c in st["sync"]:
            t.append(c, style=self._SYNC_STYLES.get(c, "#888888"))
        if st["signal"]:
            t.append(f"  SNR: {st['snr']:.1f} dB    Mode: {st['mode']}")
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
        return t

    def _update_status(self):
        bar = self.query_one("#status-bar", Static)
        bar.update("  c:Connect  d:Disc  r:Recon  m:Mute  a:AGC  x:Mode  v:VFO  +/-:Vol  \\[/]:BW  ←/→:Tune  PgU/D:RIT  S-←/→:Zoom  /:Freq")

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

    # --- CAT polling ---

    @work(thread=True)
    def _poll_cat(self):
        if not self.cat_client.connected:
            return
        # Poll active VFO first so we query the right frequency
        vfo = self.cat_client.get_active_vfo()
        if vfo is not None:
            self.call_from_thread(setattr, self, "active_vfo", vfo)
        # Query frequency for the active VFO
        active = vfo or self.active_vfo
        if active == "B":
            freq = self.cat_client.get_vfo_b_freq()
        else:
            freq = self.cat_client.get_vfo_a_freq()
        if freq is not None:
            self.call_from_thread(self._apply_cat_update, freq)
        # Poll S-meter
        sm = self.cat_client.get_s_meter()
        if sm is not None:
            with self._s_lock:
                self._s_unit, self._s_raw = sm
        # Update connection indicator if CAT dropped
        if not self.cat_client.connected:
            self.call_from_thread(self._update_conn_status)

    def _apply_cat_update(self, freq):
        self.frequency_hz = freq
        self._update_radio_info()

    # --- Actions ---

    def action_connect(self):
        self._do_connect()

    @work(thread=True)
    def _do_connect(self):
        # Connect IQ
        if not self.iq_client.connected:
            ok = self.iq_client.connect()
            self.call_from_thread(self._update_conn_status)
            if ok:
                # Update demod sample rate from server
                if self.iq_client.sample_rate > 0:
                    sr = self.iq_client.sample_rate
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
                self.iq_client.start_streaming(self._on_iq_data)

        # Connect CAT
        if not self.cat_client.connected:
            self.cat_client.connect()
            self.call_from_thread(self._update_conn_status)
            if self.cat_client.connected:
                vfo = self.cat_client.get_active_vfo()
                if vfo:
                    self.call_from_thread(setattr, self, "active_vfo", vfo)
                if vfo == "B":
                    freq = self.cat_client.get_vfo_b_freq()
                else:
                    freq = self.cat_client.get_vfo_a_freq()
                if freq:
                    self.call_from_thread(setattr, self, "frequency_hz", freq)
                self.call_from_thread(self._update_radio_info)

    def action_disconnect(self):
        self.iq_client.disconnect()
        self.cat_client.disconnect()
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
        self.demod._agc_enabled = not self.demod._agc_enabled
        self._update_audio_info()

    def action_cycle_mode(self):
        """Cycle demodulation mode: AM → SAM → SAM-U → SAM-L → USB → LSB → CW+ → CW- → DRM → AM."""
        modes = ["AM", "SAM", "SAM-U", "SAM-L", "USB", "LSB", "CW+", "CW-", "DRM"]
        old_mode = self.demod.mode
        idx = modes.index(old_mode) if old_mode in modes else 0
        new_mode = modes[(idx + 1) % len(modes)]

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
        # Set default bandwidth for the new mode
        defaults = {"AM": 5000, "SAM": 5000, "SAM-U": 5000, "SAM-L": 5000, "USB": 2400, "LSB": 2400, "CW+": 500, "CW-": 500}
        if new_mode in defaults:
            self.demod.set_bandwidth(defaults[new_mode])
        self._update_radio_info()

    def action_clear_cw_text(self):
        """Clear the decoded CW text buffer."""
        self.demod._cw_decoded_text = ""

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
        return 100, 24000, 500  # fallback

    def action_bw_up(self):
        """Increase demodulation bandwidth."""
        bw_min, bw_max, step = self._bw_limits()
        self.demod.set_bandwidth(min(bw_max, self.demod.bandwidth + step))
        self._update_radio_info()

    def action_bw_down(self):
        """Decrease demodulation bandwidth."""
        bw_min, bw_max, step = self._bw_limits()
        self.demod.set_bandwidth(max(bw_min, self.demod.bandwidth - step))
        self._update_radio_info()

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

    def action_fine_tune_up(self):
        """Fine tune up by 100 Hz."""
        self._tune_offset(100)
        self.rit_offset = 0

    def action_fine_tune_down(self):
        """Fine tune down by 100 Hz."""
        self._tune_offset(-100)
        self.rit_offset = 0

    def action_rit_up(self):
        """RIT tune up by 10 Hz (SSB/CW modes)."""
        if self.demod.mode in ("USB", "LSB", "CW+", "CW-"):
            self._tune_offset(10)
            self.rit_offset += 10

    def action_rit_down(self):
        """RIT tune down by 10 Hz (SSB/CW modes)."""
        if self.demod.mode in ("USB", "LSB", "CW+", "CW-"):
            self._tune_offset(-10)
            self.rit_offset -= 10

    def action_toggle_vfo(self):
        """Switch between VFO-A and VFO-B."""
        if not self.cat_client.connected:
            return
        new_vfo = "B" if self.active_vfo == "A" else "A"
        self._do_set_vfo(new_vfo)

    @work(thread=True)
    def _do_set_vfo(self, vfo):
        """Send VFO switch command to radio in a worker thread."""
        if self.cat_client.set_active_vfo(vfo):
            self.call_from_thread(setattr, self, "active_vfo", vfo)
            # Refresh frequency for the new VFO
            if vfo == "B":
                freq = self.cat_client.get_vfo_b_freq()
            else:
                freq = self.cat_client.get_vfo_a_freq()
            if freq is not None:
                self.call_from_thread(self._apply_cat_update, freq)
            else:
                self.call_from_thread(self._update_radio_info)

    def _tune_offset(self, offset_hz):
        """Tune the radio by an offset from current frequency."""
        if not self.cat_client.connected or self.frequency_hz <= 0:
            return
        new_freq = self.frequency_hz + offset_hz
        if new_freq < 0:
            return
        self._do_tune(new_freq)

    @work(thread=True)
    def _do_tune(self, freq_hz):
        """Send tune command to radio in a worker thread."""
        if self.active_vfo == "B":
            ok = self.cat_client.set_frequency_b(freq_hz)
        else:
            ok = self.cat_client.set_frequency(freq_hz)
        if ok:
            self.call_from_thread(self._apply_tune, freq_hz)

    def _apply_tune(self, freq_hz):
        self.frequency_hz = freq_hz
        self._update_radio_info()

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
        if freq_hz > 0 and self.cat_client.connected:
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

    def on_unmount(self):
        self.audio.stop()
        self.drm.stop()
        self.iq_client.disconnect()
        self.cat_client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="SWL Demod Tool - TUI IQ demodulator")
    parser.add_argument("--host", default=None, help="Server host (default: from config)")
    parser.add_argument("--iq-port", type=int, default=None, help="IQ server port")
    parser.add_argument("--cat-port", type=int, default=None, help="CAT server port")
    parser.add_argument("--audio-device", default=None, help="Audio output device")
    parser.add_argument("--version", action="version", version=f"swl-demod {__version__}")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging to swl-demod.log")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            filename="swl-demod.log", level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_config()
    host = args.host or config.get("server", "host")
    iq_port = args.iq_port or config.getint("server", "iq_port")
    cat_port = args.cat_port or config.getint("server", "cat_port")
    audio_device = args.audio_device or config.get("audio", "device")
    dream_path = config.get("drm", "dream_path", fallback="") or None

    app = DemodApp(host=host, iq_port=iq_port, cat_port=cat_port,
                   audio_device=audio_device)
    app.drm.dream_path = app.drm.dream_path or dream_path
    app.run()


if __name__ == "__main__":
    main()
