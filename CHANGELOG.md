# Changelog

## [0.2.0] - 2026-03-07

### Added
- Multi-row spectrum display (9-row Unicode block chart, up from single sparkline)
- Peak-hold downsampling for spectrum so narrow signals (carriers, spurs) are visible
- Spectrum zoom via Shift+Left/Right (halve/double visible span, down to ~3 kHz)
- Span indicator on spectrum display
- S-meter readout from radio via CAT `SM0;` command (S0 through S9+60)
- S-meter bar display with live updates
- Peak signal bar with dBFS readout
- Audio buffer fill bar
- Connection status for IQ, CAT, and Audio on separate lines

### Changed
- Spectrum height increased to 12 rows
- Audio info panel reorganized: Vol, Audio, Peak bars with aligned columns
- AGC/Buf/S-meter displayed as a second column with aligned colons
- Connection status panel shows IQ, CAT, Audio each on its own line

## [0.1.0] - 2026-03-07

### Added
- Initial release
- TUI demodulator for Elad FDM-DUO IQ stream
- TCP IQ client with 32-bit signed int to complex64 conversion
- TCP CAT client for frequency/mode polling (Kenwood TS-480 protocol)
- AM envelope demodulation with FIR lowpass, decimation, DC removal, AGC
- Audio output via sounddevice with ring buffer
- Unicode sparkline spectrum display
- Keybindings: connect/disconnect, mute, AGC, volume, bandwidth
- Configuration via `$XDG_CONFIG_HOME/elad-demod/config.conf`
