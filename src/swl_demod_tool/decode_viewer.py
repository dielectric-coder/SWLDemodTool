"""Generic GTK4 decode viewer — external window for visualizing decoded data.

Currently supports WEFAX image viewing.  Designed to be extensible for
future decode modes (WSPR, SSTV, etc.) by adding new view classes.

Usage:
  python -m swl_demod_tool.decode_viewer --mode wefax --data-dir /tmp/swl_wefax_XXXXX/

The viewer polls the data directory for updates and displays the decoded
content in real-time.  It can be launched/killed independently of the
main TUI — decoding continues regardless of whether the viewer is running.
"""

import argparse
import json
import logging
import os
import sys

log = logging.getLogger(__name__)


def _check_gtk():
    """Check if GTK4/PyGObject is available."""
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Gdk', '4.0')
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import Gtk, Gdk, GLib, GdkPixbuf  # noqa: F401
        return True
    except (ImportError, ValueError) as e:
        log.error("GTK4/PyGObject not available: %s", e)
        return False


class WefaxView:
    """WEFAX image view — displays a growing fax image with auto-scroll."""

    def __init__(self, data_dir):
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import Gtk, GdkPixbuf, GLib

        self.data_dir = data_dir
        self._last_height = 0
        self._auto_scroll = True

        # Main container
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Scrolled window for the image
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_hexpand(True)
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Track scroll position for auto-scroll behavior
        vadj = self.scrolled.get_vadjustment()
        vadj.connect("value-changed", self._on_scroll)

        # Image display
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(False)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.scrolled.set_child(self.picture)

        self.box.append(self.scrolled)

        # Status bar
        self.status_label = Gtk.Label(label="WEFAX  [IDLE]")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_margin_start(8)
        self.status_label.set_margin_end(8)
        self.status_label.set_margin_top(4)
        self.status_label.set_margin_bottom(4)
        self.box.append(self.status_label)

    def _on_scroll(self, adj):
        """Track whether user has scrolled away from bottom."""
        upper = adj.get_upper()
        page = adj.get_page_size()
        value = adj.get_value()
        # Consider "at bottom" if within 50px of the bottom
        self._auto_scroll = (upper - page - value) < 50

    def get_widget(self):
        return self.box

    def poll(self):
        """Poll data directory for updates. Returns True to keep polling."""
        import gi
        gi.require_version('GdkPixbuf', '2.0')
        from gi.repository import GdkPixbuf, GLib

        meta_path = os.path.join(self.data_dir, "meta.json")
        raw_path = os.path.join(self.data_dir, "image.raw")

        try:
            with open(meta_path, "r") as f:
                meta = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return True

        width = meta.get("width", 0)
        height = meta.get("height", 0)
        state = meta.get("state", "IDLE")
        line_count = meta.get("line_count", 0)
        ioc = meta.get("ioc", 576)
        rpm = meta.get("rpm", 120)

        # Update status bar
        self.status_label.set_text(
            f"IOC:{ioc}  {rpm}RPM  [{state}]  Line: {line_count}")

        if height < 1 or width < 1:
            return True

        # Read raw image data
        try:
            with open(raw_path, "rb") as f:
                raw = f.read()
        except (FileNotFoundError, OSError):
            return True

        expected = width * height
        if len(raw) < expected:
            # Partial write, use what we have
            height = len(raw) // width
            if height < 1:
                return True
            raw = raw[:width * height]

        if height == self._last_height:
            return True  # No new lines

        self._last_height = height

        # Convert grayscale to RGB for GdkPixbuf
        import numpy as np
        gray = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
        rgb = np.repeat(gray, 3).reshape(height, width, 3)
        rgb_bytes = rgb.tobytes()

        gbytes = GLib.Bytes.new(rgb_bytes)
        pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
            gbytes, GdkPixbuf.Colorspace.RGB, False, 8,
            width, height, width * 3)

        from gi.repository import Gdk
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        self.picture.set_paintable(texture)

        # Auto-scroll to bottom
        if self._auto_scroll:
            GLib.idle_add(self._scroll_to_bottom)

        return True

    def _scroll_to_bottom(self):
        vadj = self.scrolled.get_vadjustment()
        vadj.set_value(vadj.get_upper() - vadj.get_page_size())
        return False  # GLib.idle_add one-shot


class DecodeViewerApp:
    """Generic decode viewer application.

    Accepts a --mode argument to select the view type and a --data-dir
    argument pointing to the shared temp directory.
    """

    # Registry of mode -> view class
    _VIEW_CLASSES = {
        "wefax": WefaxView,
    }

    def __init__(self, mode, data_dir):
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import Gtk, GLib

        self.mode = mode
        self.data_dir = data_dir

        self.app = Gtk.Application(application_id="com.swldemodtool.decodeviewer")
        self.app.connect("activate", self._on_activate)
        self._poll_id = None

    def _on_activate(self, app):
        from gi.repository import Gtk, GLib

        # Create window
        self.window = Gtk.ApplicationWindow(application=app)
        mode_label = self.mode.upper()
        self.window.set_title(f"SWL Decode Viewer \u2014 {mode_label}")
        self.window.set_default_size(900, 700)

        # Create the appropriate view
        view_class = self._VIEW_CLASSES.get(self.mode)
        if view_class is None:
            label = Gtk.Label(label=f"Unknown mode: {self.mode}")
            self.window.set_child(label)
            self.window.present()
            return

        self.view = view_class(self.data_dir)
        self.window.set_child(self.view.get_widget())

        # Start polling for data updates (200ms interval)
        self._poll_id = GLib.timeout_add(200, self.view.poll)

        self.window.present()

    def run(self):
        self.app.run(None)


def main():
    parser = argparse.ArgumentParser(description="SWL Decode Viewer")
    parser.add_argument("--mode", required=True,
                        help="Decode mode (e.g., wefax)")
    parser.add_argument("--data-dir", required=True,
                        help="Path to shared data directory")
    args = parser.parse_args()

    if not _check_gtk():
        print("Error: GTK4/PyGObject is required for the decode viewer.",
              file=sys.stderr)
        print("Install with: sudo pacman -S python-gobject gtk4", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(args.data_dir):
        print(f"Error: data directory does not exist: {args.data_dir}",
              file=sys.stderr)
        sys.exit(1)

    viewer = DecodeViewerApp(mode=args.mode, data_dir=args.data_dir)
    viewer.run()


if __name__ == "__main__":
    main()
