"""TCP client for sending CAT commands to Elad Spectrum CAT server."""

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

    def get_frequency(self):
        """Query frequency via IF command. Returns Hz or None."""
        resp = self.send_command("IF;")
        if resp and resp.startswith("IF") and len(resp) >= 16:
            try:
                return int(resp[2:13])
            except ValueError:
                pass
        return None

    def get_mode(self):
        """Query mode from IF response. Returns mode string or None."""
        resp = self.send_command("IF;")
        if resp and resp.startswith("IF") and len(resp) >= 30:
            mode_map = {
                "1": "LSB", "2": "USB", "3": "CW",
                "4": "FM", "5": "AM", "7": "CW-R",
            }
            try:
                mode_code = resp[29]
                return mode_map.get(mode_code, f"?{mode_code}")
            except IndexError:
                pass
        return None

    # SM command value -> S-unit string mapping (from FDM-DUO manual)
    _SM_TO_S = {
        0: "S0", 2: "S1", 3: "S2", 4: "S3", 5: "S4", 6: "S5",
        8: "S6", 9: "S7", 10: "S8", 11: "S9", 12: "S9+10",
        14: "S9+20", 16: "S9+30", 18: "S9+40", 20: "S9+50", 22: "S9+60",
    }

    def get_s_meter(self):
        """Query S-meter via SM command. Returns (s_unit_str, raw_value) or None."""
        resp = self.send_command("SM0;")
        if resp and resp.startswith("SM0") and len(resp) >= 7:
            try:
                raw = int(resp[3:7])
                # Find closest matching S-unit
                best_key = 0
                for key in self._SM_TO_S:
                    if key <= raw:
                        best_key = key
                s_unit = self._SM_TO_S.get(best_key, "S0")
                return s_unit, raw
            except ValueError:
                pass
        return None
