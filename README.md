# audio-runtime

One stable interface for the bench audio stack: enumerate endpoints, probe a
file, play it, record raw PCM, and convert to the playback format — on Windows
and Linux, from one Python package with no dependencies outside the standard
library.

It exists because every test project under this workspace grew its own copy of
the same three-hundred lines: a device scan that differs per Windows version, a
`playByOne` helper, a recording wrapper, and a converter invocation. The copies
drifted, so a case that passed on one bench failed on another for reasons that
had nothing to do with the product under test. This repository is the single
canonical implementation; the copies should be deleted rather than patched.

## Install

```
pip install -e .
audio-runtime doctor
```

`doctor` is the first command to run on a new machine. It reports which external
tools exist and which are required, and exits 3 when a required one is missing.

## Commands

| Command | What it does |
| --- | --- |
| `endpoints` | enumerate render/capture endpoints, one `ark.audio-endpoints.v1` payload |
| `probe FILE` | format, duration, digest, peak/mean level, clipping for one file |
| `play --device KEY --audio-file FILE` | play once, or `--repeat N` with `--gap SECONDS` |
| `dual-play --left-device K --left-file F --right-device K --right-file F` | two endpoints, started on a barrier |
| `record --device KEY --output FILE --duration SECONDS` | capture raw PCM via OmniPickup |
| `convert SRC TGT` | transcode with ffmpeg |
| `normalize SRC TGT` | convert to the bench format (44.1 kHz, stereo, `pcm_s16le`) |
| `doctor` | which tools this machine has |
| `schema [ID]` | print one published schema, or `--list` all of them |

Every command accepts `--format json|table|md|plain`. `json` is the contract;
the others exist for a human reading a terminal.

The system default endpoint is never used by accident: playing to it requires
`--force-default`.

## Two design decisions worth knowing

**No binaries in this repository.** ffmpeg, ffprobe, ffplay and OmniPickup are
detected on the machine, never bundled. A repository that carries a 90 MB
`ffmpeg.exe` cannot be reviewed, cannot be diffed across machines, and goes
stale the moment a codec or a driver changes. Binaries belong in the asset
manifest (`asset-tools`) or in the object store, referenced by a hash; this
repository references them by name and reports their absence loudly. The same
rule is why nothing here needs a virtual environment inside the checkout.

**Receipts instead of log lines.** Every operation returns a JSON receipt
carrying the device key, the exact argv that ran, Beijing timestamps, byte
counts and warnings. A test case attaches the receipt and the result is
reviewable later without reproducing the bench. See
[`docs/interface.md`](docs/interface.md) for the full contract, including the
`device_key` derivation rules and the exit-code table.

## Fixtures

`fixtures/*.json` are recorded enumerations — one Windows machine, one Linux
machine. `--backend fixture --fixture fixtures/linux-alsa.json` replays them, so
endpoint parsing, device resolution, playback planning and the whole CLI can be
tested on a machine with no sound card at all. That is how the gate script runs.

## Gate

```
tools/run-all.sh
```

Runs the unit tests, the CLI contract checks, both fixture replays, a WAV
round-trip, a raw-PCM probe, a playback and recording plan, a dry-run
conversion, and the secret scan. It ends with `ALL CHECKS PASSED` or a non-zero
exit code. Nothing is pushed before this passes.

## Related repositories

- `asset-tools` — binary assets and their manifests (the only place a large
  binary may live, by reference)
- `device-io` — ports, boards, roles, driver install; the sibling interface this
  one is shaped after
- `tts-runtime` — speech synthesis; consumes audio files this package produces
  and plays them through these endpoints

## Licence

MIT.
