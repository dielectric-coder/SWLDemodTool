"""Abstract base class for SDR backends."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass
class SDRInfo:
    """Metadata returned after a successful connection."""
    sample_rate: int    # IQ sample rate in Hz
    sample_bits: int    # Bit depth per I or Q sample
    label: str          # Human-readable description


class SDRSource(ABC):
    """Abstract SDR backend providing IQ streaming and optional radio control.

    Subclasses must implement connect/disconnect/start_streaming.
    Radio control methods have default no-op implementations so that
    IQ-only backends (HackRF, RTL-SDR, file playback) need not override them.
    """

    @property
    @abstractmethod
    def connected(self) -> bool: ...

    @property
    @abstractmethod
    def info(self) -> Optional[SDRInfo]:
        """SDRInfo after connect(), None before."""
        ...

    @abstractmethod
    def connect(self) -> bool:
        """Connect to the SDR. Returns True on success."""
        ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def start_streaming(self, callback: Callable) -> None:
        """Start IQ streaming. callback receives numpy complex64 arrays."""
        ...

    # --- Radio control (optional) ---

    @property
    def has_control(self) -> bool:
        """Whether this backend supports frequency/VFO control."""
        return False

    def get_frequency(self, vfo: str = "A") -> Optional[int]:
        """Query frequency in Hz for the given VFO."""
        return None

    def set_frequency(self, freq_hz: int, vfo: str = "A") -> bool:
        """Set frequency in Hz for the given VFO."""
        return False

    def get_active_vfo(self) -> Optional[str]:
        """Query active VFO. Returns 'A', 'B', or None."""
        return None

    def set_active_vfo(self, vfo: str) -> bool:
        """Set active VFO ('A' or 'B')."""
        return False

    def get_s_meter(self) -> Optional[Tuple[str, int]]:
        """Query S-meter. Returns (s_unit_str, raw_value) or None."""
        return None

    def get_mode(self) -> Optional[str]:
        """Query current mode string."""
        return None

    def send_demod_status(self, mode: str, bandwidth_hz: int) -> None:
        """Report demod bandwidth to spectrum display (if supported)."""
        pass

    def clear_demod_status(self) -> None:
        """Clear demod bandwidth display (if supported)."""
        pass
