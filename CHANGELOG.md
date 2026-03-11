# Changelog

## HFDemodGTK [0.1.0] - 2026-03-11

### Added
- **Native C/GTK4 port** of SWL Demod Tool with OpenGL spectrum and waterfall display
- Full demodulation pipeline in C: AM, SAM, SAM-U, SAM-L, USB, LSB, CW+, CW-, RTTY, PSK31, DRM
- OpenGL spectrum display with Blackman-windowed FFT, peak hold, and zoom
- OpenGL waterfall (scrolling spectrogram) with turbo colormap
- **DRM decoder integration** via Dream 2.2 subprocess with FIR decimation (192→48 kHz), stdin/stdout pipes, JSON status via Unix domain socket
- Dream binary auto-detection relative to executable via `/proc/self/exe` (searches up to 3 directory levels)
- **Noise Blanker (NB)**: Impulse noise suppression at full IQ rate with 3 threshold presets (Low/Med/High), cycle with Shift+N
- **Spectral DNR**: STFT-based spectral gate (512-point, 50% overlap) with percentile noise floor estimation, 3 levels
- **Auto Notch**: STFT-based tone detection and removal
- **SNR estimator**: In-band signal-to-noise ratio from decimated IQ
- **CW Audio Peak Filter (APF)**: Biquad bandpass (Q=15) centered on 700 Hz BFO
- **CW Morse decoder** with visual tuning bar, tone SNR, WPM estimation
- **RTTY decoder**: FSK mark/space (2125/2295 Hz), 45.45 baud Baudot with FIGS/LTRS
- **PSK31 decoder**: BPSK 31.25 baud with Varicode
- Per-mode info display: PLL offset (SAM), tuning bar (CW), baud/shift (RTTY/PSK31), SNR (all modes)
- DRM status display: sync detail per field, SNR, robustness, SDC/MSC QAM, audio codec, service label, text, bitrate, country, language
- PulseAudio audio output with ring buffer
- GTK4 CSS-styled UI with MesloLGS NF font support (Unicode block character bars with ASCII fallback)
- Keyboard shortcuts: q (quit), Esc (unfocus), Shift+N (cycle NB), mode/ctrl buttons
- `--host` CLI flag for server address
- INI config file support
- CMake build system (C11, GTK4, libepoxy, FFTW3f, PulseAudio)

## [0.4.4] - 2026-03-10

### Added
- **RTTY demodulation mode**: FSK demodulator for Radio Teletype with ITA2/Baudot decoder. Mark 2125 Hz, space 2295 Hz (170 Hz shift), 45.45 baud. Dual bandpass filters for mark/space tone separation, envelope-based discriminator, start-bit synchronized clock recovery, 5-bit Baudot framing with LTRS/FIGS shift support. Live decoded text in mode info panel.
- **BPSK31 demodulation mode**: Phase Shift Keying 31.25 baud demodulator with Varicode decoder. NCO downconversion to baseband, 127-tap lowpass I/Q filtering, symbol-period accumulation, differential phase detection, and variable-length Varicode character decoding (full 128-entry ASCII table). Live decoded text in mode info panel.
- **Audio Peak Filter (APF)**: Narrow IIR bandpass (biquad, Q=15, ~50 Hz bandwidth) centered on the CW BFO tone (700 Hz). Toggle with `p`. Helps isolate a single CW signal in a crowded band.
- Mode cycle expanded: AM → SAM → SAM-U → SAM-L → USB → LSB → CW+ → CW- → RTTY → PSK31 → DRM → AM
- `t` key now clears decoded text for CW, RTTY, and PSK31 modes

### Fixed
- **Rich markup crash**: Decoded text containing `:`, `[`, `]` or other Rich markup characters caused `MarkupError` in the mode info display. CW, RTTY, and PSK31 text now uses `Text` objects (bypasses markup parsing), matching the approach already used for DRM status.

## [0.4.3] - 2026-03-10

### Added
- **DRM detailed sync display**: Sync line now shows individually labeled fields (`io:O time:O frame:O fac:O sdc:O msc:O`) with per-field color coding
- **DRM constellation info**: SDC and MSC QAM modulation (4-QAM, 16-QAM, 64-QAM) displayed in the info panel when available from Dream
- **DRM debug logging**: Raw JSON from Dream's status socket logged at debug level for diagnostics

## [0.4.2] - 2026-03-09

### Added
- **Noise Blanker (NB)**: Impulse noise suppression on raw IQ at full sample rate (192 kHz). EMA-based impulse detection with lookahead delay buffer and holdoff. Three threshold presets (Low 10×, Med 20×, High 40×). Toggle with `n`, cycle threshold with `Shift+N`.
- **Dynamic Noise Reduction (DNR)**: Spectral gate on detected audio using 512-point STFT with 50% overlap. Percentile-based noise floor estimation from passband bins. Three levels controlling gate threshold and attenuation depth. Cycle with `f`.
- **SNR estimator**: In-band signal-to-noise ratio from decimated IQ using 1024-point FFT. Median-based noise floor (robust to carriers/tones), asymmetric smoothing. Displayed for AM, SAM, USB, LSB modes.
- NB/DNR status line in audio info panel
- `[noise_reduction]` config section for NB/DNR defaults

## [0.4.1] - 2026-03-09

### Security
- DRM status socket now uses a private temp directory (`tempfile.mkdtemp`) instead of a predictable path in `/tmp`, preventing symlink attacks
- Debug log writes to `$XDG_STATE_HOME/swl-demod-tool/` instead of CWD, preventing symlink attacks on shared systems
- Config file created with `0o600` permissions, config directory with `0o700`
- CAT `set_frequency`/`set_frequency_b` reject values ≤0 or >2 GHz
- CAT `set_active_vfo` validates input is "A" or "B"
- Frequency input field enforces upper bound (2 GHz)

### Fixed
- **Audio ring buffer rewritten as lock-free**: removed `threading.Lock` from real-time audio callback (was causing audio glitches under contention). Single-writer/single-reader design with one-slot reservation to distinguish full from empty.
- **Ring buffer full/empty ambiguity**: reserved one slot so `write_pos == read_pos` unambiguously means empty (was a latent bug where a completely full buffer appeared empty)
- Ring buffer overflow now advances read pointer (drops oldest samples) instead of resetting the entire buffer, reducing audible discontinuities
- **Thread safety in `Demodulator`**: added `threading.Lock` protecting `agc_enabled`, `volume`, `muted`, CW text/timing state shared between UI and IQ threads. `clear_cw_text`, `clear_cw_timing`, `get_cw_text`, `get_cw_wpm` are now properly synchronized.
- **DRM process race condition**: `running` property and `write_iq` now hold lock when accessing `self._process`, preventing race with `stop()`
- CAT poll guard prevents concurrent `_poll_cat` worker threads from accumulating when the server is slow
- CAT response buffer capped at 4 KB; response truncated at first `;` to prevent misalignment
- DRM status socket buffer capped at 64 KB to prevent unbounded growth
- Removed dead `except IndexError` in `_get_mode_from_if` (unreachable due to prior length check)

### Changed
- `Demodulator.agc_enabled` exposed as a thread-safe property (replaces direct `_agc_enabled` access)
- `Demodulator.bfo_offset` exposed as a read-only property (replaces direct `_bfo_offset` access)
- PLL uses `math.cos`/`math.sin`/`math.atan2` instead of `np.*` for scalar values (reduces per-call overhead)
- Replaced all `lfilter_zi(...) * 0` with `np.zeros()` via `_make_filter()` helper (avoids unnecessary matrix solve)
- Blackman window cached across calls (no reallocation per FFT)
- CW buffer uses ring buffer index instead of `np.roll` (avoids full-array copy each chunk)
- CW detection extracted into `_detect_cw()` and `_cw_analyze_tone()` methods (was 55 lines inline in `process()`)
- All DSP magic numbers extracted to named module-level constants (`_DC_ALPHA`, `_AGC_*`, `_PLL_*`, `_CW_*`)
- Decimation ratio validated with assertion (`iq_sample_rate % audio_rate == 0`)
- DRM service info extraction deduplicated into `_extract_service_info()` helper
- DRM status `Text` object built once; plain string derived via `t.plain` (was built twice in parallel)
- VFO frequency query deduplicated into `_get_active_freq()` helper (was 3 identical if/else blocks)
- `spectrum_to_sparkline` guards against empty bins from floating-point edge effects
- Narrowed `except Exception` to `except AttributeError` for `self.size.width` access
- Removed unused `sample_rate` and `buffer_size` config defaults (were never read; hardcoded in app)

## [0.4.0] - 2026-03-09

### Added
- `--debug` CLI flag enables debug logging to `swl-demod.log`
- DRM text message display (station text/programme info) in mode info panel
- CW Morse code decoder with live text output in mode info panel
- `t` key to clear decoded CW text
- DRM status: country, language, and audio mode (Mono/Stereo/P-Stereo) display

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
- Ring buffer overflow handling: clean reset to 50% fill instead of constant micro-drops that caused sustained distortion

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
