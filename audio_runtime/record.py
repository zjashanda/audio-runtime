"""Recording: one place that decides what "a valid take" means.

The output is header-less interleaved PCM, so the only honest validation is
arithmetic: expected bytes from the format triple, actual bytes from the disk,
and a warning when they disagree by more than a few percent. A take that is
short is reported as short, never rounded up.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional, Sequence

from .backends import omnipickup
from .errors import RecordingError, UsageError
from .models import AudioEndpoint, RecordReceipt, bytes_per_sample, now_stamp

TOLERANCE = 0.05


def record(
    endpoint: AudioEndpoint,
    output: str,
    *,
    sample_rate: int = 16000,
    channels: int = 8,
    bit_depth: int = 16,
    duration: float = 0.0,
    host_api: str = "wasapi",
    backend: str = "omnipickup",
    omnipickup_path: Optional[str] = None,
    device_index: Optional[int] = None,
    device_name: Optional[str] = None,
    wait_device_name: Optional[str] = None,
    device_appear_delay: Optional[float] = None,
    frames_per_buffer: Optional[int] = None,
    validate_only: bool = False,
    dry_run: bool = False,
    timeout: Optional[float] = None,
) -> RecordReceipt:
    """Record from ``endpoint`` and return the ``ark.audio-record-receipt.v1``."""
    omnipickup.validate_format(sample_rate, channels, bit_depth)
    if endpoint.direction != "capture":
        raise UsageError(
            "%s is a %s endpoint; recording needs a capture endpoint" % (endpoint.device_key, endpoint.direction)
        )
    started = now_stamp()
    warnings: List[str] = []
    expected = omnipickup.expected_bytes(sample_rate, channels, bit_depth, duration)

    if backend == "fixture":
        argv = ["fixture-record", "--output", output, "--seconds", "%g" % duration]
        receipt = _receipt(
            endpoint, output, backend, started, argv, sample_rate, channels, bit_depth, duration, expected, warnings
        )
        if dry_run:
            warnings.append("dry run: no audio was captured")
            return receipt
        if duration <= 0:
            raise UsageError("--backend fixture needs a finite --duration")
        _write_silence(output, sample_rate, channels, bit_depth, duration)
        receipt.actual_bytes = os.path.getsize(output)
        frame_bytes = max(1, channels * bytes_per_sample(bit_depth))
        receipt.actual_seconds = receipt.actual_bytes / float(sample_rate * frame_bytes)
        receipt.finished_at = now_stamp()
        return receipt

    if backend != "omnipickup":
        raise UsageError("unknown recording backend: %s" % backend, hint="Use omnipickup|fixture.")

    resolved = omnipickup.find_executable(omnipickup_path)
    argv = omnipickup.build_record_argv(
        resolved,
        output,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=bit_depth,
        duration=duration,
        host_api=host_api,
        device_index=device_index,
        device_name=device_name,
        wait_device_name=wait_device_name,
        device_appear_delay=device_appear_delay,
        frames_per_buffer=frames_per_buffer,
        validate_only=validate_only,
    )
    if dry_run:
        warnings.append("dry run: no audio was captured")
        return _receipt(endpoint, output, backend, started, argv, sample_rate, channels, bit_depth, duration, expected, warnings)

    code, stdout, stderr = _execute(argv, timeout)
    omnipickup.interpret_exit(code, stderr)
    if validate_only:
        warnings.append("validate-only: device and format accepted, nothing recorded")
        return _receipt(endpoint, output, backend, started, argv, sample_rate, channels, bit_depth, duration, expected, warnings)
    actual = os.path.getsize(output) if os.path.exists(output) else 0
    if actual == 0:
        raise RecordingError(
            "OmniPickup reported success but %s is missing or empty" % output,
            hint="Check that the output directory is writable and the device stayed connected.",
        )
    frame_bytes = max(1, channels * ((bit_depth + 7) // 8))
    actual_seconds = actual / float(sample_rate * frame_bytes) if sample_rate else 0.0
    if expected:
        drift = abs(actual - expected) / float(expected)
        if drift > TOLERANCE:
            warnings.append(
                "captured %d bytes, expected about %d (%.1f%% off)" % (actual, expected, drift * 100.0)
            )
    receipt = _receipt(
        endpoint, output, backend, started, argv, sample_rate, channels, bit_depth, duration, expected, warnings
    )
    receipt.actual_bytes = actual
    receipt.actual_seconds = actual_seconds
    receipt.finished_at = now_stamp()
    return receipt


def _receipt(
    endpoint: AudioEndpoint,
    output: str,
    backend: str,
    started: str,
    argv: Sequence[str],
    sample_rate: int,
    channels: int,
    bit_depth: int,
    duration: float,
    expected: int,
    warnings: List[str],
) -> RecordReceipt:
    return RecordReceipt(
        device_key=endpoint.device_key,
        endpoint_id=endpoint.endpoint_id,
        output=output,
        backend=backend,
        started_at=started,
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=bit_depth,
        requested_seconds=duration,
        expected_bytes=expected,
        command=list(argv),
        warnings=warnings,
    )


def _execute(argv: Sequence[str], timeout: Optional[float]) -> tuple:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RecordingError("cannot execute %s" % argv[0], hint="Check --omnipickup.")
    except subprocess.TimeoutExpired:
        raise RecordingError(
            "recording exceeded the %.0fs timeout" % (timeout or 0),
            hint="A continuous recording never finishes on its own; give a finite --duration or a longer --timeout.",
        )
    except KeyboardInterrupt:
        raise RecordingError("recording interrupted")
    return completed.returncode, completed.stdout, completed.stderr


def _write_silence(path: str, sample_rate: int, channels: int, bit_depth: int, seconds: float) -> None:
    """Fixture recordings are silence, but their *length* is real."""
    frame_bytes = channels * ((bit_depth + 7) // 8)
    total = int(round(sample_rate * frame_bytes * seconds))
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"\x00" * total)


def default_output(prefix: str = "record") -> str:
    from .models import now

    return "%s-%s.pcm" % (prefix, now().strftime("%Y%m%d-%H%M%S"))


def python_hint() -> str:
    return sys.executable
