# audio-runtime interface contract

Everything a caller can rely on. Anything not written here is an implementation
detail and may change between minor versions.

## Scope

`audio-runtime` owns five operations on a bench machine: enumerate audio
endpoints, probe a media file, play a file to one or two endpoints, record raw
PCM from a capture endpoint, and convert/normalise a file to the playback
format. It never owns binaries (ffmpeg/ffprobe/OmniPickup are detected, not
shipped) and never owns credentials.

## Timestamps

Every timestamp is Beijing wall-clock in exactly one shape:

```
2026-11-11 06:57:22
```

No `T` separator, no fractional seconds, no `Z`, no `+08:00` offset — the
schemas reject all four, and `tools/time_lint.py` rejects them anywhere in the
repository. The reason is diffability: two nodes' receipts are compared line by
line, and a mixed-format log forces every consumer to normalise before it can
compare, which is exactly the bug this contract removes.

`started_at` is stamped before the device is touched; `finished_at` after the
operation ends (`null` when nothing ran, e.g. a dry run). The one exemption is a
`raw_*` field, which quotes an upstream tool's own string verbatim.

## Device keys

A `device_key` is the stable identity a caller passes to `--device`. It is
derived from the strongest identity the platform offers:

| Source | Key shape | Example |
| --- | --- | --- |
| Windows PnP hardware token | `VID_xxxx&PID_xxxx` | `VID_2886&PID_0018` |
| ALSA card, render | `ALSA:CARD<n>` | `ALSA:CARD0` |
| ALSA card, capture | `ALSA:CARD<n>:IN` | `ALSA:CARD1:IN` |
| Host API + label (no hardware token) | `<API>:<LABEL>` | `WASAPI:SPEAKERS-LISTENAI-AUDIO` |

Rules:

- A hardware token survives a driver reinstall or a renamed label; a label-based
  key does not. When both exist, the token wins.
- A capture endpoint of an ALSA card is a distinct endpoint from the same card's
  render endpoint, so it carries the `:IN` suffix. Passing the bare card key to a
  capture-only command still resolves when it is unambiguous.
- Labels are uppercased ASCII with every non-alphanumeric run folded to `-`.
- `--device` accepts an exact `device_key`, an exact `endpoint_id`, or a unique
  case-insensitive substring of the endpoint name. A substring that matches more
  than one endpoint fails with `device-ambiguous` and lists the candidates —
  silently picking the first match is how a bench measurement ends up on the
  wrong speaker.

## The system default needs an explicit opt-in

Playback to the endpoint flagged `is_default` (usually the machine's speakers)
requires `--force-default`. Without it the command exits 2 with a hint. This is
a safety rule, not a limitation: an unattended agent must not make a tool room
start talking.

## Schemas

Every command prints one payload, and that payload is validated against its
published schema before it is written to stdout. `audio-runtime schema --list`
names them; `audio-runtime schema ark.media-probe.v1` prints one definition.

| Schema | Emitted by |
| --- | --- |
| `ark.audio-endpoint.v1` | one endpoint record inside `ark.audio-endpoints.v1` |
| `ark.audio-endpoints.v1` | `endpoints` |
| `ark.media-probe.v1` | `probe` |
| `ark.audio-play-receipt.v1` | `play`, `dual-play` |
| `ark.audio-record-receipt.v1` | `record` |
| `ark.audio-convert-receipt.v1` | `convert`, `normalize` |
| `ark.audio-doctor.v1` | `doctor` |
| `ark.audio-error.v1` | every failure |

`dual-play` prints one payload whose `receipts` array holds two
`ark.audio-play-receipt.v1` objects. Each receipt is validated individually; the
wrapper itself carries `schema: "ark.audio-play-receipt.v1"` plus the array.

### `ark.media-probe.v1`

```
schema, version, path, size_bytes, sha256, container, codec,
sample_rate, channels, bit_depth, duration_seconds,
raw_pcm, clipping_status, non_silent,
peak_volume_db, mean_volume_db, warnings[]
```

- `raw_pcm` is true only when the file was interpreted through an explicit
  `--sample-rate --channels --bit-depth` triple, i.e. a header-less capture.
- `clipping_status` is `clipping`, `none`, or `unknown`. It is `unknown` when the
  level scan could not decode the samples (compressed containers), and computed
  from the sample peak otherwise (`peak >= 0.999` is clipping).
- `peak_volume_db` / `mean_volume_db` are `null` rather than `-inf` for silence;
  `non_silent` is `null` when the level scan did not run.
- The probe reads at most 8 MiB of samples for the level scan. Duration, format
  and digest always describe the whole file.
- `sha256` always covers the whole file, so a receipt can be matched against the
  artefact even when the file is renamed.

### `ark.audio-record-receipt.v1`

```
schema, version, device_key, endpoint_id, output, backend,
started_at, finished_at, sample_rate, channels, bit_depth,
requested_seconds, actual_seconds, expected_bytes, actual_bytes,
exit_code, command[], warnings[]
```

- `expected_bytes = sample_rate * channels * (bit_depth / 8) * requested_seconds`.
- After a real recording the file size is compared with `expected_bytes`; a
  drift above 5 % adds a warning instead of failing, because a short read is
  often a real device drop-out that the operator needs to see, not a hard error.
- Exit code 0 with a zero-byte output is an error (`record-failed`, exit 8): a
  capture that produced nothing must never look successful.

### Receipts are evidence, not logs

A receipt carries `command[]` (the exact argv that was executed, or would be in
a dry run) and `warnings[]`. `degraded: true` on a playback receipt means the
chosen backend cannot do something the caller might expect — for example
`ffplay` has no per-device routing, and the Windows MediaPlayer fallback blocks.

## Raw PCM contract

Recording output and `probe` input for capture artefacts are **header-less
interleaved little-endian signed PCM**. Consequences:

- The file is only interpretable together with its format triple. That is why
  `probe` demands `--sample-rate --channels --bit-depth` for such a file and why
  the record receipt always records the triple it used.
- The byte length must be a whole number of frames. A trailing partial frame
  produces a warning (`probe` and `record` both report it) rather than being
  silently dropped.
- Playback expects a container the player can open; `convert` / `normalize` turn
  a raw capture into a 44.1 kHz / stereo / `pcm_s16le` WAV.

## Conversion

`normalize` is `convert` pinned to the bench playback format:

```
TARGET_RATE = 44100
TARGET_CHANNELS = 2
TARGET_CODEC = pcm_s16le
```

`convert` builds `ffmpeg -y -v error -i SRC -vn -acodec <codec> -ac <ch> -ar <rate> TGT`
and reports the last lines of ffmpeg's stderr in the receipt on failure. A
missing `ffmpeg` is `backend-unavailable` (exit 3) with an install hint; it is
never silently skipped.

## Backends are detected, never bundled

This repository contains no executables. `doctor` reports which tools exist on
the machine and which are required:

| Tool | Role | Required |
| --- | --- | --- |
| `ffmpeg` | conversion | yes |
| `ffprobe` | probing containers | yes |
| `ffplay` | playback fallback | no |
| `aplay`, `arecord` | ALSA playback / capture | no |
| `paplay` | PulseAudio playback | no |
| `pwsh`, `powershell` | Windows playback + endpoint enumeration | no |
| `OmniPickup` | Windows multi-host-API capture | no |

Playback backend resolution (`--backend auto`):

- Linux: `alsa` → `pulse` → `ffplay` → `fixture`
- Windows: `windows` (WPF MediaPlayer, degraded) → `ffplay` → `fixture`
- Anything else: `ffplay` → `fixture`

`fixture` never touches hardware: it records the invocation it would have made.
It is the backend used by the gate script and by tests, and it is the reason a
CI run can assert on a playback plan without a sound card.

## OmniPickup adapter

`record --backend omnipickup` drives the external OmniPickup script. The adapter
locates it in this order: `--omnipickup <path>` → `$OMNIPICKUP` → `OmniPickup`
on `PATH`. A directory is resolved to `<dir>/.venv/Scripts/python.exe|<dir>/src/OmniPickup.py`
(that tool must run inside its own virtual environment); a file is used as-is.

The contract the adapter relies on, verified against OmniPickup v1.0.1:

- `--host-api {asio,directsound,wasapi,wdm-ks}` — `"Windows WASAPI"`, `WDMKS`
  and similar spellings are normalised; unknown names are usage errors.
- `--device-index N` (PortAudio global index) or `--device-name S` (exact, then
  substring; empty selects the lowest-index input of that host API), plus
  `--wait-device-name S` for hot-plug.
- `--sample-rate`, `--channels`, `--bit-depth {8,16,24,32}`, `--frames-per-buffer`,
  `--duration` (0 or omitted = continuous). `--validate-only` enumerates and
  validates without writing.
- Output is header-less interleaved raw PCM of exactly
  `rate * channels * bytes * seconds` bytes.
- Exit codes: `0` ok, `2` argument/dependency/device/stream/output error,
  `130` interrupted. `130` maps to our own `interrupted` (exit 130); everything
  else non-zero maps to `record-failed` (exit 8) with the tool's stderr quoted.

We do not re-implement this capture stack, and we do not copy the tool into this
repository. A Python virtual environment inside a skill directory is a packaging
decision of that tool, not an interface we adopt.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | unclassified runtime error |
| 2 | usage error (bad argument, missing file, default endpoint without `--force-default`) |
| 3 | backend unavailable (ffmpeg/ffprobe/OmniPickup/player not found) |
| 4 | device not found (the key is not in the enumeration) |
| 5 | device ambiguous (the query matched several endpoints) |
| 6 | probe failed (unreadable file, dead WAV header, no way to learn the format) |
| 7 | conversion failed (ffmpeg non-zero or no output written) |
| 8 | recording failed (OmniPickup error, or an empty capture reported as success) |
| 130 | interrupted by the operator |

Failures print `ark.audio-error.v1` to stdout **and** a one-line hint to stderr,
so a script can parse the payload while a human still sees what to do next.

## Stability

`v0.1.1` publishes the schemas above. Adding a field is a minor change; removing
or retyping a field, changing a timestamp shape, or changing a `device_key`
derivation rule, is a major one. The timestamp shape changed in `0.1.1`
(RFC 3339 → Beijing wall-clock), which is why that release is marked breaking
for timestamp consumers.
