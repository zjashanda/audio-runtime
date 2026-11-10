"""Format conversion: one ffmpeg invocation, fully reported.

The default target is the bench standard the legacy players assume — 44.1 kHz,
stereo, signed 16-bit little endian — because normalising to it is what makes a
recording from one node comparable with a recording from another.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional, Sequence

from .errors import ConversionError, UsageError
from .models import ConvertReceipt, now_stamp

TARGET_RATE = 44100
TARGET_CHANNELS = 2
TARGET_CODEC = "pcm_s16le"


def ffmpeg_path(explicit: Optional[str] = None) -> Optional[str]:
    candidate = explicit or os.environ.get("FFMPEG") or "ffmpeg"
    return shutil.which(candidate)


def build_convert_argv(
    source: str,
    target: str,
    *,
    sample_rate: int = TARGET_RATE,
    channels: int = TARGET_CHANNELS,
    codec: str = TARGET_CODEC,
    ffmpeg: Optional[str] = None,
    extra_args: Sequence[str] = (),
) -> List[str]:
    """Build ``ffmpeg`` argv that drops video and pins the output format."""
    executable = ffmpeg or ffmpeg_path()
    if not executable:
        raise ConversionError(
            "ffmpeg is not installed",
            hint="Install ffmpeg; this repo never bundles a binary.",
        )
    if sample_rate <= 0:
        raise UsageError("--sample-rate must be positive")
    if channels <= 0:
        raise UsageError("--channels must be positive")
    argv = [executable, "-y", "-v", "error", "-i", source, "-vn", "-acodec", codec, "-ac", str(channels), "-ar", str(sample_rate)]
    argv += list(extra_args)
    argv.append(target)
    return argv


def convert(
    source: str,
    target: str,
    *,
    sample_rate: int = TARGET_RATE,
    channels: int = TARGET_CHANNELS,
    codec: str = TARGET_CODEC,
    extra_args: Sequence[str] = (),
    ffmpeg: Optional[str] = None,
    dry_run: bool = False,
    timeout: float = 600.0,
) -> ConvertReceipt:
    """Convert ``source`` to ``target`` and return the receipt."""
    if not os.path.isfile(source):
        raise UsageError("no such audio file: %s" % source)
    directory = os.path.dirname(os.path.abspath(target))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    argv = build_convert_argv(
        source,
        target,
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
        ffmpeg=ffmpeg,
        extra_args=extra_args,
    )
    receipt = ConvertReceipt(
        source=source,
        target=target,
        started_at=now_stamp(),
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
        command=list(argv),
    )
    if dry_run:
        receipt.warnings.append("dry run: ffmpeg was not executed")
        return receipt
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise ConversionError("ffmpeg disappeared between detection and use")
    except subprocess.TimeoutExpired:
        raise ConversionError("ffmpeg exceeded %.0fs" % timeout)
    receipt.exit_code = completed.returncode
    receipt.stderr_tail = (completed.stderr or "").strip()[-800:]
    receipt.finished_at = now_stamp()
    if completed.returncode != 0:
        raise ConversionError(
            "ffmpeg exited %d converting %s" % (completed.returncode, source),
            hint=receipt.stderr_tail or "Re-run with --dry-run and inspect the command.",
        )
    if not os.path.exists(target) or os.path.getsize(target) == 0:
        raise ConversionError("ffmpeg exited 0 but %s is missing or empty" % target)
    return receipt


def normalize(source: str, target: str, **kwargs) -> ConvertReceipt:
    """Shortcut for the bench standard: 44.1 kHz stereo s16le."""
    kwargs.setdefault("sample_rate", TARGET_RATE)
    kwargs.setdefault("channels", TARGET_CHANNELS)
    kwargs.setdefault("codec", TARGET_CODEC)
    return convert(source, target, **kwargs)
