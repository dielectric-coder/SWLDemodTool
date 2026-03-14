# Changelog

## [0.5.3] - 2026-03-14

### Added
- **Station name display on spectrum**: When SWLScheduleTool tunes the radio, the station name is sent via a named FIFO (`$XDG_RUNTIME_DIR/swldemod-station.fifo`) and displayed on the spectrum info line in bold gold. Clears automatically on manual tune.
- **Station FIFO listener**: Daemon thread creates and monitors the FIFO for station name updates from external tools.

### Changed
- **Spectrum display simplified**: Removed frequency display and bandwidth underline from the spectrum info line. Only the center marker (▲), station name (when available), and span indicator remain.

## [0.5.2] - 2026-03-14

### Added
- **Popup selector screens**: New `SelectorScreen` modal for mode (`x`), bandwidth (`b`), VFO (`v`), and tune step (`g`) selection. Uses `OptionList` with keyboard navigation (up/down/Enter/Esc), current-item marker, and solid-border styling.
- **Configurable tune step**: Selectable from 1 Hz to 10 kHz via popup. Left/right arrow keys tune by the selected step. Replaces the fixed 100 Hz fine-tune (Alt+arrow removed).
- **Tune step display**: Current step shown in the radio info line alongside VFO, frequency, mode, and bandwidth.

### Changed
- **Frequency input relocated**: Moved from separate bar above connection status to the right side of the radio info line, with the original powerline-styled label preserved.
- Mode selection changed from cycle (`x` cycled through modes) to popup selector.
- VFO selection changed from toggle to popup selector.

## [0.5.1] - 2026-03-14

### Changed
- **Redesigned audio info panel**: Fixed-width three-column layout with aligned labels and uniform 13-character bars. Renamed labels: "Vol" → "AF Gain" (now in dB), "Audio" → "AF Peak", "Peak" → "RF Peak", "Buf" → "BUF", "Underruns" → "U:". AGC now shown as a bar meter (displays "OFF" in bar when disabled). DNF and APF moved to separate rows. All label colons vertically aligned within each column.
- **CAT connection status**: Renamed "CTL" to "CAT". Now shows backend name and host:port when connected (e.g., `CAT ● Elad FDM-DUO localhost:4532`).
- **IQ connection status**: Now includes host:port in addition to sample rate and bit depth.

## [0.5.0] - 2026-03-14

### Added
- **Pluggable SDR backend architecture**: New `sdr/` package with abstract `SDRSource` base class, allowing multiple SDR hardware backends. IQ streaming and radio control are unified behind a single interface. Backends are selected at startup via `--sdr <name>` CLI argument or `[sdr] backend` config option.
- **Elad FDM-DUO backend** (`--sdr elad-fdmduo`): Default backend wrapping the existing TCP IQ and CAT clients. Fully backward-compatible — existing config and CLI options (`--host`, `--iq-port`, `--cat-port`) continue to work unchanged.
- **Backend registry with lazy imports**: New backends can be added by implementing `SDRSource` and registering in `sdr/registry.py`. Backend-specific dependencies are only imported when that backend is selected, so unused backends don't require their libraries to be installed.
- `[sdr]` config section with `backend` option (default: `elad-fdmduo`)

### Changed
- `DemodApp` constructor takes an `SDRSource` instance instead of separate host/port parameters
- Connection status display shows generic SDR label and control status instead of hardcoded host:port lines
- All IQ/CAT references in `app.py` replaced with `SDRSource` interface calls

## [0.4.8] - 2026-03-12

### Fixed
- **PSK31 carrier frequency**: Changed NCO from 1000 Hz to 1500 Hz to match the standard audio offset convention used by fldigi and other PSK software. Previously required tuning +500 Hz above the published dial frequency.
- **MFSK16 deinterleaver**: Replaced incorrect cascaded 3D table deinterleaver with correct FIFO delay lines matching fldigi's convolutional interleaver (per-column delays of (SIZE-1-i) × DEPTH).
- **MFSK16 Gray decode**: Fixed soft-decision tone mapping — was using Gray encode (`i ^ (i >> 1)`) instead of Gray decode. Tones 4–15 mapped to wrong bit patterns, causing periodic decode failures depending on tone usage.

## [0.4.7] - 2026-03-12

### Added
- **MFSK16 demodulation mode**: 16-tone FSK demodulator with K=7 R=1/2 convolutional FEC (Viterbi soft-decision decoding) and IZ8BLY MFSK Varicode. FFT-based tone detection (3072 samples/symbol at 48 kHz), fldigi-compatible convolutional interleaver (size=4, depth=10), and variable-length Varicode character decoding. Live decoded text in mode info panel. Mode cycle updated: AM → SAM → ... → CW± → RTTY± → PSK31 → MFSK16 → DRM.

## [0.4.6] - 2026-03-12

### Added
- **RTTY mark/space tuning indicator**: Dual bar-graph display showing smoothed mark and space tone levels with active tone label (MARK/SPC), similar to the CW tuning indicator. Helps visualize correct tuning and signal quality.
- **RTTY± polarity modes**: RTTY mode split into RTTY+ (normal, mark low/space high — standard amateur convention) and RTTY- (reversed polarity for commercial/maritime stations). Mode cycle updated: AM → SAM → ... → CW± → RTTY± → PSK31 → DRM.

## [0.4.5] - 2026-03-12

### Added
- **Optional DSP accelerators**: pyfftw (FFTW3-backed FFT with wisdom caching) and numba (JIT compilation) as optional dependencies via `pip install -e ".[accel]"`. All 7 FFT call sites use pyfftw when available (spectrum, SNR, DNR, auto notch, CW tone analysis). Three per-sample inner loops (noise blanker, SAM PLL, CW envelope tracker) use numba JIT. Both fall back transparently to NumPy/SciPy.

### Fixed
- **PLL loss of lock with noise blanker**: SAM/SAM-U/SAM-L PLL now coasts through near-zero input samples (blanked by NB) instead of chasing undefined phase from `atan2(0,0)`. Holds frequency and phase estimate while outputting silence, preventing multi-second recovery transients when enabling the noise blanker.

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
