"""TCP client for sending CAT commands to Elad Spectrum CAT server."""

import bisect
import socket
import threading

# Maximum bytes to buffer when waiting for a ';' terminator
_MAX_RESPONSE_LEN = 4096


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
                    if len(data) > _MAX_RESPONSE_LEN:
                        self.connected = False
                        return None
                # Return only up to the first ';' terminator
                end = data.index(b";") + 1
                return data[:end].decode("ascii", errors="replace").strip()
            except (OSError, TimeoutError):
                self.connected = False
                return None

    _MODE_MAP = {
        "1": "LSB", "2": "USB", "3": "CW",
        "4": "FM", "5": "AM", "7": "CW-R",
    }

    def get_info(self, vfo="A"):
        """Query frequency and mode for given VFO. Returns (frequency_hz, mode_str) or (None, None)."""
        if vfo == "B":
            freq = self.get_vfo_b_freq()
        else:
            freq = self.get_vfo_a_freq()
        mode = self._get_mode_from_if()
        return freq, mode

    def get_vfo_a_freq(self):
        """Query VFO-A frequency via FA command. Returns Hz or None."""
        resp = self.send_command("FA;")
        if resp and resp.startswith("FA") and len(resp) >= 13:
            try:
                return int(resp[2:13])
            except ValueError:
                pass
        return None

    def get_vfo_b_freq(self):
        """Query VFO-B frequency via FB command. Returns Hz or None."""
        resp = self.send_command("FB;")
        if resp and resp.startswith("FB") and len(resp) >= 13:
            try:
                return int(resp[2:13])
            except ValueError:
                pass
        return None

    def _get_mode_from_if(self):
        """Query mode from IF response. Returns mode string or None."""
        resp = self.send_command("IF;")
        if resp and resp.startswith("IF") and len(resp) >= 30:
            return self._MODE_MAP.get(resp[29], f"?{resp[29]}")
        return None

    def get_active_vfo(self):
        """Query active receive VFO via FR command. Returns 'A' or 'B', or None."""
        resp = self.send_command("FR;")
        if resp and resp.startswith("FR") and len(resp) >= 4:
            if resp[2] == "0":
                return "A"
            elif resp[2] == "1":
                return "B"
        return None

    def set_active_vfo(self, vfo):
        """Set active receive VFO. vfo must be 'A' or 'B'."""
        if vfo not in ("A", "B"):
            return False
        digit = "0" if vfo == "A" else "1"
        resp = self.send_command(f"FR{digit};")
        return resp is not None

    # Maximum frequency the radio will accept (2 GHz, generous upper bound)
    _MAX_FREQ_HZ = 2_000_000_000

    def set_frequency(self, freq_hz):
        """Set VFO-A frequency via FA command. freq_hz is an integer in Hz."""
        freq_hz = int(freq_hz)
        if freq_hz <= 0 or freq_hz > self._MAX_FREQ_HZ:
            return False
        freq_str = f"FA{freq_hz:011d};"
        resp = self.send_command(freq_str)
        return resp is not None

    def set_frequency_b(self, freq_hz):
        """Set VFO-B frequency via FB command. freq_hz is an integer in Hz."""
        freq_hz = int(freq_hz)
        if freq_hz <= 0 or freq_hz > self._MAX_FREQ_HZ:
            return False
        freq_str = f"FB{freq_hz:011d};"
        resp = self.send_command(freq_str)
        return resp is not None

    def get_frequency(self):
        """Query VFO-A frequency. Returns Hz or None."""
        return self.get_vfo_a_freq()

    def get_mode(self):
        """Query mode from IF response. Returns mode string or None."""
        return self._get_mode_from_if()

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
