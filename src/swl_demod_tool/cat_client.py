"""TCP client for sending CAT commands to Elad Spectrum CAT server."""

import bisect
import socket
import threading


class CATClient:
    def __init__(self, host="localhost", port=4532):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        self._lock = threading.Lock()

    def connect(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
            self.sock.settimeout(2)
            self.connected = True
            return True
        except (OSError, TimeoutError):
            self.connected = False
            return False

    def disconnect(self):
        with self._lock:
            if self.sock:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.sock.close()
                self.sock = None
            self.connected = False

    def send_command(self, cmd):
        """Send a CAT command and return the response string."""
        if not cmd.endswith(";"):
            cmd += ";"
        with self._lock:
            if not self.sock:
                return None
            try:
                self.sock.sendall(cmd.encode("ascii"))
                # Read response until ';'
                data = bytearray()
                while True:
                    chunk = self.sock.recv(256)
                    if not chunk:
                        self.connected = False
                        return None
                    data.extend(chunk)
                    if b";" in data:
                        break
                return data.decode("ascii", errors="replace").strip()
            except (OSError, TimeoutError):
                self.connected = False
                return None

    _MODE_MAP = {
        "1": "LSB", "2": "USB", "3": "CW",
        "4": "FM", "5": "AM", "7": "CW-R",
    }

    def get_info(self):
        """Query IF command. Returns (frequency_hz, mode_str) or (None, None)."""
        resp = self.send_command("IF;")
        freq = None
        mode = None
        if resp and resp.startswith("IF") and len(resp) >= 16:
            try:
                freq = int(resp[2:13])
            except ValueError:
                pass
            if len(resp) >= 30:
                try:
                    mode = self._MODE_MAP.get(resp[29], f"?{resp[29]}")
                except IndexError:
                    pass
        return freq, mode

    def set_frequency(self, freq_hz):
        """Set frequency via FA command. freq_hz is an integer in Hz."""
        freq_str = f"FA{int(freq_hz):011d};"
        resp = self.send_command(freq_str)
        return resp is not None

    def get_frequency(self):
        """Query frequency via IF command. Returns Hz or None."""
        freq, _ = self.get_info()
        return freq

    def get_mode(self):
        """Query mode from IF response. Returns mode string or None."""
        _, mode = self.get_info()
        return mode

    # SM command value -> S-unit string mapping (from FDM-DUO manual)
    _SM_KEYS = [0, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22]
    _SM_VALS = ["S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7",
                "S8", "S9", "S9+10", "S9+20", "S9+30", "S9+40", "S9+50", "S9+60"]

    def get_s_meter(self):
        """Query S-meter via SM command. Returns (s_unit_str, raw_value) or None."""
        resp = self.send_command("SM0;")
        if resp and resp.startswith("SM0") and len(resp) >= 7:
            try:
                raw = int(resp[3:7])
                idx = bisect.bisect_right(self._SM_KEYS, raw) - 1
                s_unit = self._SM_VALS[max(0, idx)]
                return s_unit, raw
            except ValueError:
                pass
        return None
