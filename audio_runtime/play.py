"""Playback orchestration, including the two-endpoint case.

The interesting parts are not calling ``aplay``: they are refusing to silently
fall back to the system default (a bench test that plays out of the laptop
speaker while reporting PASS is worse than a failure) and keeping two endpoints
sample-aligned by starting their worker threads on a barrier.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import List, Optional, Sequence

from .backends import PlaybackBackend, PlayInvocation, resolve_backend
from .endpoints import require_explicit_default
from .errors import BackendUnavailable, UsageError
from .models import AudioEndpoint, PlayReceipt, now_stamp
from .probe import read_wav_format


def _duration_of(path: str) -> float:
    try:
        rate, channels, depth, seconds = _wav_meta(path)
        return seconds
    except Exception:  # noqa: BLE001 - duration is a nicety, never a failure
        return 0.0


def _wav_meta(path: str):
    from .probe import _wav_header  # local import keeps the public surface small

    header = _wav_header(path)
    if header is None:
        raise ValueError("not a wav file")
    rate, channels, depth, seconds = header
    return rate, channels, depth, seconds


def play(
    endpoint: AudioEndpoint,
    path: str,
    *,
    backend: str = "auto",
    repeat: int = 1,
    gap: float = 0.0,
    force_default: bool = False,
    dry_run: bool = False,
    runner=None,
) -> PlayReceipt:
    """Play ``path`` once or ``repeat`` times on ``endpoint``."""
    if repeat < 1:
        raise UsageError("--repeat must be at least 1")
    if gap < 0:
        raise UsageError("--gap must be non-negative")
    if not os.path.isfile(path):
        raise UsageError("no such audio file: %s" % path)
    if endpoint.direction != "render":
        raise UsageError(
            "%s is a %s endpoint; playback needs a render endpoint" % (endpoint.device_key, endpoint.direction)
        )
    require_explicit_default(endpoint, force_default)

    implementation = resolve_backend(backend, endpoint.platform)
    if not implementation.supports(endpoint):
        raise BackendUnavailable(
            "backend %s does not support %s endpoints" % (implementation.name, endpoint.platform)
        )
    if not dry_run and backend != "fixture" and not implementation.available():
        raise BackendUnavailable("backend %s is not available on this host" % implementation.name)

    started = now_stamp()
    warnings: List[str] = []
    degraded = False
    command: Sequence[str] = []
    exit_code = 0
    began = time.monotonic()

    for index in range(repeat):
        invocation: PlayInvocation = implementation.build(endpoint, path)
        command = invocation.argv
        degraded = degraded or invocation.degraded
        for warning in invocation.warnings:
            if warning not in warnings:
                warnings.append(warning)
        if dry_run or backend == "fixture":
            continue
        completed = (runner or _run)(invocation.argv)
        exit_code = completed.returncode
        if exit_code != 0:
            warnings.append(
                "playback attempt %d/%d exited %d: %s"
                % (index + 1, repeat, exit_code, (completed.stderr or "").strip()[:300])
            )
            break
        if gap and index + 1 < repeat:
            time.sleep(gap)

    elapsed = time.monotonic() - began
    if dry_run:
        warnings.append("dry run: nothing was played")
    return PlayReceipt(
        device_key=endpoint.device_key,
        endpoint_id=endpoint.endpoint_id,
        file=path,
        backend=implementation.name,
        started_at=started,
        finished_at=now_stamp(),
        duration_seconds=elapsed,
        repeats=repeat,
        exit_code=exit_code,
        degraded=degraded,
        command=list(command),
        warnings=warnings,
    )


def dual_play(
    left: AudioEndpoint,
    left_file: str,
    right: AudioEndpoint,
    right_file: str,
    *,
    backend: str = "auto",
    repeat: int = 1,
    gap: float = 0.0,
    force_default_left: bool = False,
    force_default_right: bool = False,
    dry_run: bool = False,
) -> List[PlayReceipt]:
    """Play two endpoints at the same time, starting on a shared barrier."""
    if left.direction != "render" or right.direction != "render":
        raise UsageError("dual-play needs two render endpoints")
    if left.device_key == right.device_key:
        raise UsageError("dual-play needs two different endpoints, not %s twice" % left.device_key)

    barrier = threading.Barrier(2)
    receipts: List[Optional[PlayReceipt]] = [None, None]
    failures: List[BaseException] = []

    def worker(slot: int, endpoint: AudioEndpoint, path: str, force: bool) -> None:
        try:
            barrier.wait(timeout=10)
            receipts[slot] = play(
                endpoint,
                path,
                backend=backend,
                repeat=repeat,
                gap=gap,
                force_default=force,
                dry_run=dry_run,
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            failures.append(exc)

    threads = [
        threading.Thread(target=worker, args=(0, left, left_file, force_default_left), daemon=True),
        threading.Thread(target=worker, args=(1, right, right_file, force_default_right), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
    return [receipt for receipt in receipts if receipt is not None]


def _run(argv: Sequence[str]):
    return subprocess.run(argv, capture_output=True, text=True, timeout=600)


def describe_play(receipt: PlayReceipt) -> str:
    return "%s -> %s (%s, %d repeat%s, exit %d)" % (
        receipt.file,
        receipt.device_key,
        receipt.backend,
        receipt.repeats,
        "" if receipt.repeats == 1 else "s",
        receipt.exit_code,
    )


def wav_duration(path: str) -> float:
    rate, channels, depth = read_wav_format(path)
    if rate <= 0 or channels <= 0 or depth <= 0:
        return 0.0
    return _duration_of(path)
