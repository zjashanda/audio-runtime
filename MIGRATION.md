# Migration

This repository replaces three earlier arrangements. Nothing is deleted
automatically — each project moves when its owner updates the call site.

## From `listenai-play` (embedded copy)

`listenai-play` shipped `scripts/listenai_play.py` (1066 lines) as a skill-local
script, plus a copy of it inside `Trisolaris`, `mars-belt`, `mars-moon`,
`tcl-test-sub` and `power-on-record-check`. Those copies now delegate here.

| Old | New |
| --- | --- |
| `python scripts/listenai_play.py audio-list --json` | `audio-runtime endpoints --format json` |
| `python scripts/listenai_play.py scan --direction Render` | `audio-runtime endpoints --direction render` |
| `python scripts/listenai_play.py ensure-laid` | not needed — no `laid` dependency, `doctor` replaces it |
| `python scripts/listenai_play.py probe --device-key KEY` | `audio-runtime play --dry-run --device KEY --audio-file tone.wav` |
| `python scripts/listenai_play.py play --audio-file F --device-key KEY` | `audio-runtime play --device KEY --audio-file F` |
| `... play --repeat 3 --gap 0.5` | `audio-runtime play ... --repeat 3 --gap 0.5` |
| `... dual-play --left-device-key K --left-file A --right-device-key K --right-file B` | `audio-runtime dual-play --left-device K --left-file A --right-device K --right-file B` |
| `--force-default` / `--force-default-left` / `--force-default-right` | same names |

Differences that will surface at a call site:

- `--device-key` became `--device` (it also accepts an `endpoint_id` or a unique
  name substring).
- `--skip-probe` is gone: playback no longer plays a probe tone first. A caller
  that wants an audible self-test plays a short WAV explicitly.
- A receipt is printed on every run, so the wrapper script no longer needs to
  parse stdout for "played" — read `exit_code` from the receipt instead.
- `internal-play-once` (a process per repeat) no longer exists. Dual playback is
  synchronised in-process on a `threading.Barrier`, so repeat boundaries stay
  aligned instead of drifting by a process-launch time per iteration.

What must not happen: copying `audio_runtime/` into a skill directory. A copy is
how the five divergent copies happened in the first place. Install the package
(`pip install -e .` from a checkout, or a wheel published through the asset
manifest) and call the CLI.

## From `lstest/scripts/playback.py`

`PlaybackBackend` shelled out to `listenai_play.py play` and recorded lifecycle
events itself. Point it at this CLI:

```
audio-runtime play --device <key> --audio-file <path> --format json
```

and keep the receipt as the case artefact (`command[]`, `started_at`,
`finished_at`, `duration_seconds`, `exit_code`, `warnings[]`). The lifecycle
status can be derived from `exit_code` plus `warnings`, so the wrapper no longer
needs to guess whether playback actually happened.

Note the direction the old wrapper relied on: it passed the device key through
unchanged so the player would reject an unavailable or ambiguous device instead
of silently falling back. That behaviour is preserved — and is now the reason
`--device` fails loudly on a two-hit substring.

## From direct OmniPickup invocations

Any project that called `since OmniPickup.py` through its own
`.venv/Scripts/python.exe` should go through `record`:

```
audio-runtime record --device <capture key> --output take.pcm \
  --sample-rate 16000 --channels 8 --bit-depth 16 --duration 5 \
  --omnipickup /path/to/omni-pickup
```

The adapter keeps the tool's own runtime requirement (it still needs its virtual
environment, or its packaged `OmniPickup.exe`), but the caller no longer has to
know that: pass the directory and the adapter resolves
`.venv/Scripts/python.exe` plus `src/OmniPickup.py`. It also keeps the tool's
exit-code meaning (`0` ok, `2` error, `130` interrupted) and validates the byte
count against `rate × channels × bytes × seconds`, which the direct calls did
not.

Do not vendor OmniPickup, its `.venv`, or its output into any repository; the
tool stays at its own location and is referenced by path or `$OMNIPICKUP`.

## Sequencing

1. Run `audio-runtime doctor` on the bench; install the missing required tools.
2. Switch one call site, run its cases, compare the new receipt with the old log.
3. Delete the embedded copy only after that project's cases pass on the new path.
4. Leave `listenai-play` in place (it still owns the install/driver notes) until
   every consumer has moved; then it becomes a thin pointer to this repository.
