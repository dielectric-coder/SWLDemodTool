# Changelog

## [0.4.0] - 2026-03-09

### Added
- `--debug` CLI flag enables debug logging to `swl-demod.log`
- DRM text message display (station text/programme info) in mode info panel

### Changed
- Upgraded Dream integration from 2.1.1 (patched stderr) to Dream 2.2 (JSON status via Unix domain socket)
- DRM status uses `--status-socket` instead of parsing stderr `DRM|...` lines
- DRM sync characters rendered with Rich `Text` objects (avoids markup parsing issues)
- DRM status display skips redundant widget updates when content is unchanged
- DRM station label styled bold yellow, text messages styled cyan
- Removed `audio_ok`/`audio_total` fields from DRM status (not available in Dream 2.2 JSON)
- Clean thread shutdown via `threading.Event` instead of polling `process.poll()`

### Fixed
- DRM audio distortion over time caused by unaligned stereo frame reads from Dream's stdout (partial reads losing remainder bytes, compounding into permanent frame misalignment)

## [0.3.0] - 2026-03-08

### Added
- Synchronous AM demodulation (SAM) with PLL carrier tracking
- Selectable sideband SAM modes: SAM-U (upper) and SAM-L (lower) for interference rejection
- DRM (Digital Radio Mondiale) decoding via Dream subprocess integration
- DRM status display: sync indicators (colour-coded), SNR, robustness mode, service label, bitrate
- Dream binary auto-detection from `../DRM/`, `PATH`, or config file
- VFO-A/B toggle via `v` key with CAT `FR0;`/`FR1;` commands
- VFO-B frequency query via `FB;` command
- `[drm]` config section with `dream_path` option

### Changed
- Frequency input consolidated to a single field, moved to top of UI (tunes active VFO)
- AM default bandwidth changed to 5000 Hz, minimum lowered to 4000 Hz
- Mode cycling: AM → SAM → SAM-U → SAM-L → USB → LSB → CW+ → CW- → DRM → AM
- CW (Morse code) demodulation with 700 Hz BFO tone offset: CW+ (upper sideband) and CW- (lower sideband), 500 Hz default bandwidth (100-1000 Hz adjustable), two-stage filtering (pre-decimation anti-alias + post-decimation audio-rate narrow filter)
- CW tuning indicator with center-zero bar (±150 Hz range), tone SNR, and keying speed estimation (WPM)
- RIT tuning via PgUp/PgDn (10 Hz steps) for SSB and CW modes, with offset display
- Mode info panel: dedicated area below audio info for mode-specific indicators (CW tuning/SNR/WPM/RIT, DRM sync/status, SAM PLL offset, SSB RIT)
- DRM status display moved from radio info line to mode info panel
- SAM PLL offset display moved from radio info line to mode info panel
- Demod mode and bandwidth are fully local — no longer polled from the radio
- CAT polling is VFO-aware: queries active VFO's frequency via `FA;` or `FB;`
- Tuning controls tune the active VFO (VFO-A or VFO-B)
- Radio info display shows `demod.mode` instead of radio-reported mode
- DRM audio flows through the existing ring buffer (no separate PulseAudio output)

### Removed
- `mode_str` reactive (mode display now reads directly from `demod.mode`)
- `_auto_bandwidth()` — mode/bandwidth no longer auto-set from radio

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

### Fixed
- Removed dead code: unused `#signal-info` CSS, `sample_count` reactive, `_avg_spectrum`, `_update_signal_info()`
- Combined duplicate CAT `IF;` calls into single `get_info()` per poll cycle
- CAT S-meter lookup uses bisect for O(log n) instead of linear scan
- Vectorized sparkline inner loop (numpy `clip` replaces Python list comprehension)
- Fixed file descriptor leak in TTY title escape sequence (added try/finally)
- Added CAT disconnect detection when server drops connection

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
- Configuration via `$XDG_CONFIG_HOME/swl-demod-tool/config.conf`
