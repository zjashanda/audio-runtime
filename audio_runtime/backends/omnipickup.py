"""Adapter for the omni-pickup recording tool.

omni-pickup ships a 2170-file ``.venv`` next to its source, which is exactly the
kind of payload a canonical repo must not copy. So this module owns the
*contract* instead: it builds the documented argv, enforces the format triple the
tool supports, and interprets the documented exit codes. The executable itself
is an external prerequisite, discovered on PATH or passed explicitly.
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional, Sequence

from ..errors import BackendUnavailable, RecordingError, UsageError

HOST_APIS = ("asio", "directsound", "wasapi", "wdm-ks")
BIT_DEPTHS = (8, 16, 24, 32)
ALIASES = {
    "windows wasapi": "wasapi",
    "windows directsound": "directsound",
    "windows wdm-ks": "wdm-ks",
    "wdmks": "wdm-ks",
    "mm e": "mme",
}
EXIT_OK = 0
EXIT_ERROR = 2
EXIT_INTERRUPTED = 130

# Relative layout of the skill as published by omni-pickup.
DEFAULT_RELATIVE_SOURCE = os.path.join("src", "OmniPickup.py")
DEFAULT_RELATIVE_VENV = os.path.join(".venv", "Scripts", "python.exe")


def normalize_host_api(value: str) -> str:
    text = (value or "").strip().lower()
    text = ALIASES.get(text, text)
    if text not in HOST_APIS:
        raise UsageError(
            "unsupported --host-api value: %s" % value,
            hint="Use one of " + ", ".join(HOST_APIS) + ".",
        )
    return text


def find_executable(explicit: Optional[str] = None) -> str:
    """Locate a runnable OmniPickup entry point, in the documented order.

    An explicit ``--omnipickup`` may be the standalone EXE, the source script, or
    the skill root; a bare skill root is resolved to its ``.venv`` python.
    """
    if explicit:
        if os.path.isdir(explicit):
            source = os.path.join(explicit, DEFAULT_RELATIVE_SOURCE)
            venv_python = os.path.join(explicit, DEFAULT_RELATIVE_VENV)
            if os.path.isfile(venv_python) and os.path.isfile(source):
                return "%s|%s" % (venv_python, source)
            raise BackendUnavailable(
                "%s does not look like an omni-pickup skill root" % explicit,
                hint="Expected %s and %s." % (DEFAULT_RELATIVE_SOURCE, DEFAULT_RELATIVE_VENV),
            )
        if os.path.isfile(explicit):
            return explicit
        raise BackendUnavailable("omnipickup path does not exist: %s" % explicit)
    for name in ("OmniPickup", "OmniPickup.exe", "OmniPickup.cmd"):
        found = shutil.which(name)
        if found:
            return found
    env_value = os.environ.get("OMNIPICKUP")
    if env_value:
        return find_executable(env_value)
    raise BackendUnavailable(
        "OmniPickup was not found",
        hint="Pass --omnipickup <exe|skill-root>, set OMNIPICKUP, or use --backend fixture for a dry run.",
    )


def executable_argv(resolved: str) -> List[str]:
    """Split a resolved entry point into an argv prefix."""
    if "|" in resolved:
        python, script = resolved.split("|", 1)
        return [python, script]
    if resolved.lower().endswith(".py"):
        return ["python", resolved]
    return [resolved]


def validate_format(sample_rate: int, channels: int, bit_depth: int) -> None:
    if sample_rate <= 0:
        raise UsageError("--sample-rate must be positive")
    if channels <= 0:
        raise UsageError("--channels must be positive")
    if bit_depth not in BIT_DEPTHS:
        raise UsageError(
            "unsupported --bit-depth: %s" % bit_depth,
            hint="omni-pickup accepts " + ", ".join(str(depth) for depth in BIT_DEPTHS) + ".",
        )


def build_record_argv(
    resolved_executable: str,
    output: str,
    *,
    sample_rate: int,
    channels: int,
    bit_depth: int,
    duration: float = 0.0,
    host_api: str = "wasapi",
    device_index: Optional[int] = None,
    device_name: Optional[str] = None,
    wait_device_name: Optional[str] = None,
    device_appear_delay: Optional[float] = None,
    frames_per_buffer: Optional[int] = None,
    validate_only: bool = False,
) -> List[str]:
    """Build the exact argv documented by omni-pickup.

    ``duration == 0`` means continuous recording, which is why it is emitted
    explicitly rather than omitted: a receipt should show the intent.
    """
    validate_format(sample_rate, channels, bit_depth)
    api = normalize_host_api(host_api)
    if duration < 0:
        raise UsageError("--duration cannot be negative (0 means continuous)")
    argv = executable_argv(resolved_executable)
    argv += [
        "--host-api",
        api,
        "--sample-rate",
        str(sample_rate),
        "--channels",
        str(channels),
        "--bit-depth",
        str(bit_depth),
        "--duration",
        ("%g" % duration),
    ]
    if device_index is not None:
        if device_index < 0:
            raise UsageError("--device-index must be non-negative")
        argv += ["--device-index", str(device_index)]
    if device_name:
        argv += ["--device-name", device_name]
    if wait_device_name:
        argv += ["--wait-device-name", wait_device_name]
    if device_appear_delay is not None:
        if device_appear_delay < 0:
            raise UsageError("--device-appear-delay must be non-negative")
        argv += ["--device-appear-delay", ("%g" % device_appear_delay)]
    if frames_per_buffer is not None:
        if frames_per_buffer <= 0:
            raise UsageError("--frames-per-buffer must be positive")
        argv += ["--frames-per-buffer", str(frames_per_buffer)]
    if validate_only:
        argv.append("--validate-only")
    else:
        argv += ["--output", output]
    return argv


def expected_bytes(sample_rate: int, channels: int, bit_depth: int, seconds: float) -> int:
    if seconds <= 0:
        return 0
    return int(round(sample_rate * channels * (bit_depth / 8.0) * seconds))


def interpret_exit(code: int, stderr: str = "") -> None:
    """Map omni-pickup's documented exit codes onto this package's errors."""
    if code == EXIT_OK:
        return
    tail = (stderr or "").strip()[:400]
    if code == EXIT_INTERRUPTED:
        from ..errors import Interrupted

        raise Interrupted("recording interrupted by the operator")
    raise RecordingError(
        "OmniPickup exited %d%s" % (code, (": " + tail) if tail else ""),
        hint="Run with --validate-only to separate device/format problems from recording problems.",
    )


def list_devices_argv(resolved_executable: str) -> List[str]:
    return executable_argv(resolved_executable) + ["--list-devices"]


def record_argv_preview(argv: Sequence[str]) -> str:
    """Quoted, copy-pasteable rendering used in receipts and docs."""
    parts = []
    for token in argv:
        parts.append('"%s"' % token if (" " in token or "\t" in token) else token)
    return " ".join(parts)
