"""SDR backend for Elad FDM-DUO via TCP IQ server + CAT server."""

from swl_demod_tool.iq_client import IQClient
from swl_demod_tool.cat_client import CATClient
from swl_demod_tool.sdr.base import SDRSource, SDRInfo


class EladFDMDuoSource(SDRSource):
    """Wraps the existing IQClient and CATClient as a single SDRSource."""

    def __init__(self, host="localhost", iq_port=4533, cat_port=4532):
        self._iq = IQClient(host, iq_port)
        self._cat = CATClient(host, cat_port)
        self._info = None
        self.host = host
        self.iq_port = iq_port
        self.cat_port = cat_port

    @property
    def connected(self):
        return self._iq.connected

    @property
    def info(self):
        return self._info

    @property
    def has_control(self):
        return self._cat.connected

    def connect(self):
        ok = self._iq.connect()
        if ok:
            self._info = SDRInfo(
                sample_rate=self._iq.sample_rate,
                sample_bits=self._iq.format_bits,
                label=f"Elad FDM-DUO  {self.host}:{self.iq_port}"
            )
        # CAT is independent — non-fatal if it fails
        self._cat.connect()
        return ok

    def disconnect(self):
        self._iq.disconnect()
        self._cat.disconnect()
        self._info = None

    def start_streaming(self, callback):
        self._iq.start_streaming(callback)

    def get_frequency(self, vfo="A"):
        if vfo == "B":
            return self._cat.get_vfo_b_freq()
        return self._cat.get_vfo_a_freq()

    def set_frequency(self, freq_hz, vfo="A"):
        if vfo == "B":
            return self._cat.set_frequency_b(freq_hz)
        return self._cat.set_frequency(freq_hz)

    def get_active_vfo(self):
        return self._cat.get_active_vfo()

    def set_active_vfo(self, vfo):
        return self._cat.set_active_vfo(vfo)

    def get_s_meter(self):
        return self._cat.get_s_meter()

    def get_mode(self):
        return self._cat.get_mode()

    def send_demod_status(self, mode, bandwidth_hz):
        """Report demod bandwidth to spectrum display via DM command."""
        self._cat.send_demod_status(mode, bandwidth_hz)

    def clear_demod_status(self):
        """Clear demod bandwidth display."""
        self._cat.clear_demod_status()
