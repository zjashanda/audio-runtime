"""Media inspection without trusting the file extension.

Facts come from three places in this order: the WAV header, an explicit
``--sample-rate/--channels/--bit-depth`` triple for header-less PCM, and
``ffprobe`` for everything else. Level statistics are computed from the samples
themselves, because "the file exists and is 2 MB" has never once proved that a
speaker produced sound.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import struct
import sys
import wave
from array import array
from typing import Optional, Sequence, Tuple

from .errors import ProbeError, UsageError
from .models import MediaProbe, bytes_per_sample

CLIP_THRESHOLD = 0.999
SILENCE_THRESHOLD = 1e-4
LEVEL_SCAN_BYTES = 8 * 1024 * 1024


def sha256_file(path: str, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def ffprobe_path(explicit: Optional[str] = None) -> Optional[str]:
    candidate = explicit or os.environ.get("FFPROBE") or "ffprobe"
    return shutil.which(candidate)


def _wav_header(path: str) -> Optional[Tuple[int, int, int, float]]:
    """Return (rate, channels, bit_depth, duration) for a RIFF/WAVE file."""
    with open(path, "rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            return None
    try:
        with wave.open(path, "rb") as reader:
            rate = reader.getframerate()
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            frames = reader.getnframes()
    except (wave.Error, EOFError, RuntimeError) as exc:
        raise ProbeError("%s looks like WAV but its header is broken: %s" % (path, exc))
    if rate <= 0 or channels <= 0 or width <= 0:
        raise ProbeError("%s declares an unusable WAV format" % path)
    return rate, channels, width * 8, frames / float(rate)


def _pcm_levels(path: str, bit_depth: int) -> Tuple[float, float, int]:
    """Peak and mean absolute level in 0..1 for a header-less PCM file."""
    raw = b""
    remaining = LEVEL_SCAN_BYTES
    with open(path, "rb") as handle:
        while remaining > 0:
            block = handle.read(min(remaining, 1024 * 1024))
            if not block:
                break
            raw += block
            remaining -= len(block)
    return _levels_from_bytes(raw, bit_depth)


def _wav_levels(path: str, bit_depth: int) -> Tuple[float, float, int]:
    """Peak and mean absolute level in 0..1 for the data chunk of a WAV file.

    Reading the file from offset 0 would count the RIFF header as samples, which
    drags the mean up and can even fake clipping on a silent file, so the frame
    data is handed over by the ``wave`` module instead.
    """
    raw = b""
    try:
        with wave.open(path, "rb") as handle:
            remaining = LEVEL_SCAN_BYTES
            while remaining > 0:
                frames = handle.readframes(min(4096, max(1, remaining // max(1, handle.getnchannels() * handle.getsampwidth()))))
                if not frames:
                    break
                raw += frames
                remaining -= len(frames)
    except (wave.Error, EOFError, RuntimeError) as exc:
        raise ProbeError("cannot read the WAV data chunk of %s: %s" % (path, exc)) from exc
    return _levels_from_bytes(raw, bit_depth)


def _levels_from_bytes(raw: bytes, bit_depth: int) -> Tuple[float, float, int]:
    """Peak and mean absolute level in 0..1 for interleaved integer PCM bytes."""
    width = bytes_per_sample(bit_depth)
    usable = len(raw) - (len(raw) % width)
    if usable <= 0:
        return 0.0, 0.0, 0
    samples = 0
    peak = 0.0
    total = 0.0
    if bit_depth == 16:
        values = array("h")
        values.frombytes(raw[:usable])
        if sys.byteorder == "big":
            values.byteswap()
        full_scale = 32768.0
        for value in values:
            magnitude = abs(value) / full_scale
            peak = magnitude if magnitude > peak else peak
            total += magnitude
        samples = len(values)
    elif bit_depth == 8:
        full_scale = 128.0
        for offset in range(0, usable, width):
            value = raw[offset]
            magnitude = abs(value - 128) / full_scale
            peak = magnitude if magnitude > peak else peak
            total += magnitude
        samples = usable
    else:
        full_scale = float(1 << (bit_depth - 1))
        for offset in range(0, usable, width):
            value = int.from_bytes(raw[offset : offset + width], "little", signed=True)
            magnitude = abs(value) / full_scale
            peak = magnitude if magnitude > peak else peak
            total += magnitude
        samples = usable // width
    mean = total / samples if samples else 0.0
    return peak, mean, samples


def _db(level: float) -> Optional[float]:
    if level <= 0:
        return None
    return 20.0 * math.log10(level)


def _ffprobe(path: str, executable: str) -> dict:
    argv = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise ProbeError("ffprobe went missing between detection and use")
    except subprocess.TimeoutExpired:
        raise ProbeError("ffprobe timed out after 60s on %s" % path)
    if completed.returncode != 0:
        raise ProbeError(
            "ffprobe exited %d on %s: %s" % (completed.returncode, path, (completed.stderr or "").strip()[:400]),
            hint="For header-less PCM pass --sample-rate/--channels/--bit-depth.",
        )
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        raise ProbeError("ffprobe produced unparseable JSON for %s" % path)


def probe(
    path: str,
    sample_rate: Optional[int] = None,
    channels: Optional[int] = None,
    bit_depth: Optional[int] = None,
    ffprobe: Optional[str] = None,
) -> MediaProbe:
    """Inspect one file and return the ``ark.media-probe.v1`` record."""
    if not os.path.isfile(path):
        raise UsageError("no such file: %s" % path)
    size = os.path.getsize(path)
    digest = sha256_file(path)
    warnings = []

    header = _wav_header(path)
    if header is not None:
        rate, channel_count, depth, duration = header
        peak, mean, _ = _wav_levels(path, depth) if depth in (8, 16, 24, 32) else (0.0, 0.0, 0)
        return _finish(path, size, digest, rate, channel_count, depth, duration, "wav", "pcm_s%dle" % depth, peak, mean, False, warnings)

    if sample_rate and channels and bit_depth:
        rate = int(sample_rate)
        channel_count = int(channels)
        depth = int(bit_depth)
        frame_bytes = bytes_per_sample(depth) * channel_count
        if frame_bytes <= 0:
            raise UsageError("invalid PCM format triple")
        if size % frame_bytes:
            warnings.append("size %d is not a whole number of %d-byte frames" % (size, frame_bytes))
        duration = size / float(rate * frame_bytes)
        peak, mean, _ = _pcm_levels(path, depth)
        return _finish(path, size, digest, rate, channel_count, depth, duration, "raw", "pcm_s%dle" % depth, peak, mean, True, warnings)

    executable = ffprobe_path(ffprobe)
    if not executable:
        raise ProbeError(
            "%s is neither WAV nor described by a format triple" % path,
            hint="Install ffprobe, or pass --sample-rate/--channels/--bit-depth for raw PCM.",
        )
    payload = _ffprobe(path, executable)
    streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "audio"]
    if not streams:
        raise ProbeError("%s has no audio stream according to ffprobe" % path)
    stream = streams[0]
    rate = int(stream.get("sample_rate") or 0)
    channel_count = int(stream.get("channels") or 0)
    depth = int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0)
    duration = float((payload.get("format") or {}).get("duration") or stream.get("duration") or 0.0)
    container = str((payload.get("format") or {}).get("format_name") or "")
    codec = str(stream.get("codec_name") or "")
    if depth == 0:
        warnings.append("ffprobe did not report a bit depth for %s" % codec)
    return _finish(path, size, digest, rate, channel_count, depth, duration, container, codec, 0.0, 0.0, False, warnings)


def _finish(
    path: str,
    size: int,
    digest: str,
    rate: int,
    channels: int,
    depth: int,
    duration: float,
    container: str,
    codec: str,
    peak: float,
    mean: float,
    raw_pcm: bool,
    warnings: list,
) -> MediaProbe:
    has_levels = peak > 0 or mean > 0
    if peak >= CLIP_THRESHOLD:
        clipping = "clipping"
    elif has_levels:
        clipping = "none"
    else:
        clipping = "unknown"
    if has_levels and mean < SILENCE_THRESHOLD and peak < SILENCE_THRESHOLD:
        warnings.append("noise floor only: the file carries no audible signal")
    return MediaProbe(
        path=path,
        raw_pcm=raw_pcm,
        size_bytes=size,
        sha256=digest,
        sample_rate=rate,
        channels=channels,
        bit_depth=depth,
        duration_seconds=duration,
        container=container,
        codec=codec,
        peak_level=peak,
        mean_level=mean,
        peak_volume_db=_db(peak),
        mean_volume_db=_db(mean),
        non_silent=(peak >= SILENCE_THRESHOLD) if has_levels else None,
        clipping_status=clipping,
        warnings=warnings,
    )


def wav_duration_from_pcm(size_bytes: int, rate: int, channels: int, bit_depth: int) -> float:
    frame = bytes_per_sample(bit_depth) * channels
    if frame <= 0 or rate <= 0:
        return 0.0
    return size_bytes / float(rate * frame)


def read_wav_format(path: str) -> Tuple[int, int, int]:
    header = _wav_header(path)
    if header is None:
        raise ProbeError("%s is not a WAV file" % path)
    rate, channels, depth, _ = header
    return rate, channels, depth


def pcm_header_stub() -> bytes:
    """A zero-length WAV header, used by tests to prove header detection."""
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36, b"WAVE", b"fmt ", 16, 1, 1, 44100, 88200, 2, 16, b"data", 0)
