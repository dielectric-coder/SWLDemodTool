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
