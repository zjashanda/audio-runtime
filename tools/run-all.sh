#!/usr/bin/env bash
# Full gate for audio-runtime: unit tests, the published CLI contract, a fixture
# replay of every backend, and a secret scan. Needs no sound card and no ffmpeg:
# every hardware touch is either a fixture or a --dry-run.
# Usage: tools/run-all.sh
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
FAIL=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

step() { printf '\n== %s\n' "$1"; }
fail() { printf 'FAIL: %s\n' "$1"; FAIL=1; }

step "python unittest"
if python3 -m unittest discover -s tests -v; then
  echo "PASS: unittest"
else
  fail "unittest"
fi

step "package import"
if python3 -c "import sys; sys.path.insert(0,'.'); import audio_runtime; print('audio_runtime', audio_runtime.__version__)"; then
  echo "PASS: import"
else
  fail "import"
fi

step "CLI contract"
if python3 -m audio_runtime.cli schema --list >/dev/null; then
  echo "PASS: schema --list"
else
  fail "schema --list"
fi
python3 -m audio_runtime.cli doctor --format json >"$TMP/doctor.json"
DOCTOR_CODE=$?
if [ "$DOCTOR_CODE" -eq 0 ] || [ "$DOCTOR_CODE" -eq 3 ]; then
  echo "PASS: doctor (exit $DOCTOR_CODE)"
else
  fail "doctor (exit $DOCTOR_CODE)"
fi

step "fixture replay"
shopt -s nullglob
FIXTURES=(fixtures/*.json)
if [ ${#FIXTURES[@]} -eq 0 ]; then
  fail "no fixtures"
else
  for fixture in "${FIXTURES[@]}"; do
    if python3 -m audio_runtime.cli endpoints --backend fixture --fixture "$fixture" --format json >"$TMP/$(basename "$fixture")"; then
      echo "PASS: endpoints $(basename "$fixture") ($(python3 -c "import json,sys;print(len(json.load(open('$TMP/$(basename "$fixture")'))['endpoints']))") endpoints)"
    else
      fail "endpoints $(basename "$fixture")"
    fi
  done
fi

step "wav probe round trip"
python3 - "$TMP/tone.wav" <<'PY'
import struct, sys, wave
path = sys.argv[1]
with wave.open(path, "wb") as handle:
    handle.setnchannels(2)
    handle.setsampwidth(2)
    handle.setframerate(44100)
    frames = b"".join(struct.pack("<hh", 8000, -8000) for _ in range(2205))
    handle.writeframes(frames)
PY
if python3 -m audio_runtime.cli probe "$TMP/tone.wav" --format json >"$TMP/tone.json"; then
  echo "PASS: probe wav"
else
  fail "probe wav"
fi

step "raw pcm probe"
head -c 176400 /dev/zero >"$TMP/silence.pcm"
if python3 -m audio_runtime.cli probe "$TMP/silence.pcm" --sample-rate 44100 --channels 2 --bit-depth 16 --format json >"$TMP/silence.json"; then
  echo "PASS: probe raw pcm"
else
  fail "probe raw pcm"
fi

step "play / dual-play dry run on fixtures"
if python3 -m audio_runtime.cli play --backend fixture --fixture fixtures/windows-omnipickup.json \
    --device "WASAPI:SPEAKERS-LISTENAI-AUDIO" --force-default \
    --audio-file "$TMP/tone.wav" --format json >"$TMP/play.json"; then
  echo "PASS: play fixture"
else
  fail "play fixture"
fi
if python3 -m audio_runtime.cli record --backend fixture --fixture fixtures/windows-omnipickup.json \
    --device "VID_2886&PID_0018" --output "$TMP/take.pcm" --duration 1 --format json >"$TMP/record.json"; then
  echo "PASS: record fixture"
else
  fail "record fixture"
fi
if python3 -m audio_runtime.cli convert "$TMP/tone.wav" "$TMP/tone-out.wav" --dry-run --format json >"$TMP/convert.json"; then
  echo "PASS: convert dry run"
else
  fail "convert dry run"
fi

step "time contract lint"
if python3 tools/time_lint.py --self-test; then
  echo "PASS: time lint self-test"
else
  fail "time lint self-test"
fi
if python3 tools/time_lint.py; then
  echo "PASS: time lint"
else
  fail "time lint"
fi

step "secret scan self-test"
if python3 tools/secret_scan.py --self-test; then
  echo "PASS: secret scan self-test"
else
  fail "secret scan self-test"
fi

step "secret scan"
if python3 tools/secret_scan.py "$ROOT"; then
  echo "PASS: secret scan"
else
  fail "secret scan"
fi

printf '\n'
if [ "$FAIL" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "CHECKS FAILED"
fi
exit "$FAIL"
