# Changelog

## 0.1.1 — 2026-11-11

Unified time contract: every timestamp is Beijing wall-clock
`YYYY-MM-DD HH:MM:SS` with no `T`, no fractional seconds, no `Z` and no
`+08:00` offset.

### Changed (breaking for timestamp consumers)

- `models.now_iso()` / `to_iso()` became `now_stamp()` / `to_stamp()` and render
  wall-clock instead of RFC 3339. Receipts and `generated_at` now carry
  `2026-11-11 06:57:22`.
- `finished_at` is `null` until the operation completes (it used to be an empty
  string), so "not run" and "ran" are distinguishable without a sentinel.
- The schemas reject `T`, `Z`, `+HH:MM` and fractional seconds; a `raw_*` field
  is the only exemption, because it quotes an upstream tool verbatim.

### Added

- `tools/time_lint.py` — repository-wide timestamp-shape lint with a self-test,
  wired into `tools/run-all.sh`; reports `0 violation(s)` or the offending
  `file:line`.
- Three CLI tests that assert on the raw stdout of `play`, `record` and
  `endpoints`, not only on the parsed payload.

## 0.1.0 — 2026-11-10

First public release. Canonical home for the bench audio stack, replacing the
copies embedded in `listenai-play`, `lstest` and the recording wrappers.

### Added

- `endpoints` — Windows (PnP / OmniPickup listing) and Linux (ALSA) enumeration
  into one `ark.audio-endpoints.v1` payload, with a `device_key` derived from the
  strongest available identity (`VID_xxxx&PID_xxxx` → `ALSA:CARD<n>[:IN]` →
  `<API>:<LABEL>`).
- `probe` — container or raw-PCM probe with duration, sha256, peak/mean level,
  clipping and silence verdicts (`ark.media-probe.v1`). Raw PCM is interpreted
  through an explicit format triple; partial frames are reported, not dropped.
- `play` / `dual-play` — one or two render endpoints, repeats and gaps, barrier
  synchronised dual playback, and receipts that carry the executed argv.
- `record` — raw PCM capture through the external OmniPickup tool, with
  expected-vs-actual byte accounting and an empty-capture failure.
- `convert` / `normalize` — ffmpeg transcoding, plus the pinned bench format
  (44.1 kHz, stereo, `pcm_s16le`).
- `doctor` — external tool inventory with required/optional markers.
- `schema` — eight published `ark.*.v1` schemas and a validator that every
  command runs before printing.
- Playback backends: `alsa`, `pulse`, `windows` (WPF MediaPlayer, degraded),
  `ffplay` (degraded), `fixture` (no hardware), resolved per platform.
- `fixtures/linux-alsa.json`, `fixtures/windows-omnipickup.json` — recorded
  enumerations, replayable with `--backend fixture`.
- `tools/run-all.sh` gate and `tools/secret_scan.py` with a self-test.
- 92 unit tests covering schemas, parsers, resolution, receipts, the OmniPickup
  argv contract and the CLI.

### Deliberate non-goals

- No bundled binaries. ffmpeg/ffprobe/ffplay/OmniPickup are detected, and their
  absence is a reported `backend-unavailable` rather than a vendored copy.
- No credentials, no tokens, no `.venv` inside the checkout.
- No speech synthesis — that is `tts-runtime`.
