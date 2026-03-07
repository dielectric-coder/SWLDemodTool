#!/usr/bin/env python3

"""Elad Demod - TUI demodulator for Elad FDM-DUO IQ stream."""

import argparse
import os
import threading
import numpy as np
from collections import deque
from datetime import datetime, timezone

from textual.app import App
from textual.widgets import Footer, Static
from textual.reactive import reactive
from textual import work
from rich.text import Text

from elad_demod import __version__
from elad_demod.config import load_config
from elad_demod.iq_client import IQClient
from elad_demod.cat_client import CATClient
from elad_demod.dsp import compute_spectrum_db, spectrum_to_sparkline, Demodulator
from elad_demod.audio import AudioOutput

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

#signal-info {
    height: 0;
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
    TITLE = f"Elad Demod v{__version__}"
    CSS = CSS
    theme = "tokyo-night"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
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
    ]

    utc_display = reactive("--:-- UTC")
    frequency_hz = reactive(0)
    mode_str = reactive("---")
    peak_db = reactive(-120.0)
    sample_count = reactive(0)

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
        self._avg_spectrum = np.zeros(FFT_SIZE, dtype=np.float32)
        self._iq_lock = threading.Lock()

        # Demodulation and audio
        self.demod = Demodulator(iq_sample_rate=192000, audio_rate=48000, bandwidth=5000)
        self.audio = AudioOutput(sample_rate=48000, block_size=1024)

        # Audio level tracking
        self._audio_level_db = -120.0
        self._audio_level_lock = threading.Lock()

        # S-meter from CAT
        self._s_unit = "S0"
        self._s_raw = 0
        self._s_lock = threading.Lock()

        # Spectrum zoom: fraction of full bandwidth shown (1.0 = full, 0.0625 = 1/16)
        self._spectrum_zoom = 1.0

    def compose(self):
        yield Static(id="title-bar")
        yield Static(id="conn-status")
        yield Static(id="radio-info")
        yield Static(id="spectrum-display")
        yield Static(id="audio-info")
        yield Static(id="signal-info")
        yield Static(id="status-bar", markup=True)
        yield Footer()

    def on_mount(self):
        try:
            fd = os.open("/dev/tty", os.O_WRONLY)
            os.write(fd, f"\033]0;Elad Demod v{__version__}\007".encode())
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
        self._update_audio_info()
        self._update_signal_info()
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
        self._update_audio_info()
        self._update_signal_info()

    def _update_title(self):
        bar = self.query_one("#title-bar", Static)
        bar.update(f"  Elad Demod v{__version__}     {self.utc_display}")

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
        if self.frequency_hz > 0:
            freq_mhz = self.frequency_hz / 1e6
            text = f"  Frequency: {freq_mhz:.6f} MHz    Mode: {self.mode_str}    BW: {self.demod.bandwidth} Hz"
        else:
            text = f"  Frequency: ---    Mode: ---    BW: {self.demod.bandwidth} Hz"
        w.update(text)

    def _update_spectrum(self):
        w = self.query_one("#spectrum-display", Static)
        if not self.iq_client.connected or len(self._spectrum_buf) == 0:
            w.update("  Spectrum: [no data]")
            return

        with self._iq_lock:
            avg = np.mean(list(self._spectrum_buf), axis=0)
            self._avg_spectrum = avg

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
        marker_line = " " * center + "↑" + " " * (width - center - 1)

        # Show center frequency and visible span
        freq_str = ""
        if self.frequency_hz > 0:
            freq_str = f"{self.frequency_hz / 1e6:.3f}"
        sample_rate = self.iq_client.sample_rate if self.iq_client.sample_rate > 0 else 192000
        span_khz = sample_rate * self._spectrum_zoom / 1000
        span_str = f"Span: {span_khz:.0f} kHz"

        w.update(
            f"{indented}\n"
            f"  {marker_line}\n"
            f"  {'':>{center - len(freq_str)//2}}{freq_str}{'':>{max(1, width - center - len(freq_str)//2 - len(span_str))}}{span_str}"
        )

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
            f"{'Buf: [' + buf_bar + '] ' + f'{fill_pct}%  Underruns: {underruns}'}\n"
            f"{'   Peak: [' + peak_bar + '] ' + f'{self.peak_db:.1f} dBFS':<46s}"
            f"  S: [{s_bar}] {s_unit}"
        )
        w.update(text)

    def _update_signal_info(self):
        pass

    def _update_status(self):
        bar = self.query_one("#status-bar", Static)
        bar.update("  c:Connect  d:Disc  r:Recon  m:Mute  a:AGC  +/-:Vol  \\[/]:BW  S-←/→:Zoom")

    # --- IQ data callback (from network thread) ---

    def _on_iq_data(self, iq_samples):
        """Called from IQ client thread with new IQ data."""
        # Spectrum display
        db = compute_spectrum_db(iq_samples, FFT_SIZE)
        with self._iq_lock:
            self._spectrum_buf.append(db)
        peak = float(np.max(db))

        # Demodulate and output audio
        audio = self.demod.process(iq_samples)
        if len(audio) > 0:
            # Track audio level
            rms = np.sqrt(np.mean(audio ** 2)) if not self.demod.muted else 0.0
            level_db = 20.0 * np.log10(max(rms, 1e-10))
            with self._audio_level_lock:
                self._audio_level_db = level_db

            # Push to audio output
            self.audio.write(audio)

        self.call_from_thread(self._apply_iq_update, peak, len(iq_samples))

    def _apply_iq_update(self, peak, count):
        self.peak_db = peak
        self.sample_count += count

    # --- CAT polling ---

    @work(thread=True)
    def _poll_cat(self):
        if not self.cat_client.connected:
            return
        freq = self.cat_client.get_frequency()
        if freq is not None:
            self.call_from_thread(self._apply_cat_update, freq)
        # Poll S-meter
        sm = self.cat_client.get_s_meter()
        if sm is not None:
            with self._s_lock:
                self._s_unit, self._s_raw = sm

    def _apply_cat_update(self, freq):
        changed = (freq != self.frequency_hz)
        self.frequency_hz = freq
        if changed:
            mode = self.cat_client.get_mode()
            if mode:
                self.mode_str = mode
                self._auto_bandwidth(mode)
            self._update_radio_info()

    def _auto_bandwidth(self, mode):
        """Set demodulation bandwidth based on radio mode."""
        bw_map = {
            "AM": 5000,
            "LSB": 3000,
            "USB": 3000,
            "CW": 500,
            "CW-R": 500,
            "FM": 6000,
        }
        bw = bw_map.get(mode)
        if bw:
            self.demod.set_bandwidth(bw)

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
                # Start audio output
                self.audio.start(device=self.audio_device)
                self.call_from_thread(self._update_conn_status)
                # Start IQ streaming
                self.iq_client.start_streaming(self._on_iq_data)

        # Connect CAT
        if not self.cat_client.connected:
            self.cat_client.connect()
            self.call_from_thread(self._update_conn_status)
            if self.cat_client.connected:
                freq = self.cat_client.get_frequency()
                mode = self.cat_client.get_mode()
                if freq:
                    self.call_from_thread(setattr, self, "frequency_hz", freq)
                if mode:
                    self.call_from_thread(setattr, self, "mode_str", mode)
                    self.call_from_thread(self._auto_bandwidth, mode)
                self.call_from_thread(self._update_radio_info)

    def action_disconnect(self):
        self.iq_client.disconnect()
        self.cat_client.disconnect()
        self.audio.stop()
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

    def action_volume_up(self):
        self.demod.volume = min(1.0, self.demod.volume + 0.05)
        self._update_audio_info()

    def action_volume_down(self):
        self.demod.volume = max(0.0, self.demod.volume - 0.05)
        self._update_audio_info()

    def action_bw_up(self):
        """Increase demodulation bandwidth by 500 Hz."""
        self.demod.set_bandwidth(self.demod.bandwidth + 500)
        self._update_radio_info()

    def action_bw_down(self):
        """Decrease demodulation bandwidth by 500 Hz."""
        self.demod.set_bandwidth(max(100, self.demod.bandwidth - 500))
        self._update_radio_info()

    def action_zoom_in(self):
        """Zoom into the spectrum (halve visible span)."""
        self._spectrum_zoom = max(1 / 64, self._spectrum_zoom / 2)

    def action_zoom_out(self):
        """Zoom out of the spectrum (double visible span)."""
        self._spectrum_zoom = min(1.0, self._spectrum_zoom * 2)

    def on_unmount(self):
        self.audio.stop()
        self.iq_client.disconnect()
        self.cat_client.disconnect()


def main():
    parser = argparse.ArgumentParser(description="Elad Demod - TUI IQ demodulator")
    parser.add_argument("--host", default=None, help="Server host (default: from config)")
    parser.add_argument("--iq-port", type=int, default=None, help="IQ server port")
    parser.add_argument("--cat-port", type=int, default=None, help="CAT server port")
    parser.add_argument("--audio-device", default=None, help="Audio output device")
    parser.add_argument("--version", action="version", version=f"elad-demod {__version__}")
    args = parser.parse_args()

    config = load_config()
    host = args.host or config.get("server", "host")
    iq_port = args.iq_port or config.getint("server", "iq_port")
    cat_port = args.cat_port or config.getint("server", "cat_port")
    audio_device = args.audio_device or config.get("audio", "device")

    app = DemodApp(host=host, iq_port=iq_port, cat_port=cat_port,
                   audio_device=audio_device)
    app.run()


if __name__ == "__main__":
    main()
