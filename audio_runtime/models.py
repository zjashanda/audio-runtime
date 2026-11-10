"""Plain data models shared by every subcommand.

All of them are dataclasses with an explicit ``to_dict`` so the JSON written to
stdout is stable, ordered and free of tuples/objects a consumer cannot parse.
Timestamps are always Beijing time with an explicit ``+08:00`` offset: a bench
log without an offset is ambiguous the moment two nodes are compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BEIJING = timezone(timedelta(hours=8), name="CST")


def now() -> datetime:
    """Current time in Beijing."""
    return datetime.now(BEIJING)


def now_iso() -> str:
    """ISO-8601 with milliseconds and an explicit +08:00 offset."""
    return now().isoformat(timespec="milliseconds")


def to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=BEIJING)
    return value.isoformat(timespec="milliseconds")


RENDER = "render"
CAPTURE = "capture"
DIRECTIONS = (RENDER, CAPTURE)


@dataclass
class AudioEndpoint:
    """One addressable audio endpoint.

    ``device_key`` is the long-lived identity a caller stores; ``endpoint_id``
    is the short id that is only valid for one enumeration (PortAudio indices
    and ALSA card numbers move when a device is replugged).
    """

    endpoint_id: str
    device_key: str
    direction: str
    platform: str
    name: str = ""
    channels: int = 0
    default_sample_rate: int = 0
    host_api: str = ""
    backend_target: str = ""
    provider: str = ""
    interface: str = ""
    card_index: Optional[int] = None
    pcm_device: Optional[int] = None
    is_default: bool = False
    source: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "endpoint_id": self.endpoint_id,
            "device_key": self.device_key,
            "direction": self.direction,
            "platform": self.platform,
            "name": self.name,
            "channels": self.channels,
            "default_sample_rate": self.default_sample_rate,
            "host_api": self.host_api,
            "backend_target": self.backend_target,
            "provider": self.provider,
            "interface": self.interface,
            "is_default": self.is_default,
            "source": self.source,
        }
        if self.card_index is not None:
            payload["card_index"] = self.card_index
        if self.pcm_device is not None:
            payload["pcm_device"] = self.pcm_device
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "AudioEndpoint":
        known = {k: raw.get(k) for k in cls.__dataclass_fields__ if k in raw}  # type: ignore[attr-defined]
        known.pop("extra", None)
        endpoint = cls(**known)
        endpoint.extra = dict(raw.get("extra") or {})
        return endpoint


@dataclass
class MediaProbe:
    """What a single media file actually is.

    ``raw_pcm`` marks header-less files: they are only interpretable together
    with the format triple below, which is why that triple is always reported.
    """

    path: str
    raw_pcm: bool
    size_bytes: int
    sha256: str
    sample_rate: int = 0
    channels: int = 0
    bit_depth: int = 0
    duration_seconds: float = 0.0
    container: str = ""
    codec: str = ""
    byte_order: str = "little"
    peak_level: float = 0.0
    mean_level: float = 0.0
    peak_volume_db: Optional[float] = None
    mean_volume_db: Optional[float] = None
    non_silent: Optional[bool] = None
    clipping_status: str = "unknown"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "raw_pcm": self.raw_pcm,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bit_depth": self.bit_depth,
            "duration_seconds": round(self.duration_seconds, 6),
            "container": self.container,
            "codec": self.codec,
            "byte_order": self.byte_order,
            "peak_level": round(self.peak_level, 6),
            "mean_level": round(self.mean_level, 6),
            "peak_volume_db": None if self.peak_volume_db is None else round(self.peak_volume_db, 2),
            "mean_volume_db": None if self.mean_volume_db is None else round(self.mean_volume_db, 2),
            "non_silent": self.non_silent,
            "clipping_status": self.clipping_status,
            "warnings": list(self.warnings),
        }


@dataclass
class PlayReceipt:
    """Evidence that a playback attempt happened, and on which endpoint."""

    device_key: str
    endpoint_id: str
    file: str
    backend: str
    started_at: str
    finished_at: str = ""
    duration_seconds: float = 0.0
    repeats: int = 1
    exit_code: int = 0
    degraded: bool = False
    command: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_key": self.device_key,
            "endpoint_id": self.endpoint_id,
            "file": self.file,
            "backend": self.backend,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 6),
            "repeats": self.repeats,
            "exit_code": self.exit_code,
            "degraded": self.degraded,
            "command": list(self.command),
            "warnings": list(self.warnings),
        }


@dataclass
class RecordReceipt:
    """Evidence for one recording, including the format triple it implies."""

    device_key: str
    endpoint_id: str
    output: str
    backend: str
    started_at: str
    finished_at: str = ""
    sample_rate: int = 0
    channels: int = 0
    bit_depth: int = 0
    requested_seconds: float = 0.0
    actual_seconds: float = 0.0
    expected_bytes: int = 0
    actual_bytes: int = 0
    exit_code: int = 0
    command: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_key": self.device_key,
            "endpoint_id": self.endpoint_id,
            "output": self.output,
            "backend": self.backend,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bit_depth": self.bit_depth,
            "requested_seconds": round(self.requested_seconds, 6),
            "actual_seconds": round(self.actual_seconds, 6),
            "expected_bytes": self.expected_bytes,
            "actual_bytes": self.actual_bytes,
            "exit_code": self.exit_code,
            "command": list(self.command),
            "warnings": list(self.warnings),
        }


@dataclass
class ConvertReceipt:
    """Evidence for one ffmpeg format conversion."""

    source: str
    target: str
    started_at: str
    finished_at: str = ""
    sample_rate: int = 0
    channels: int = 0
    codec: str = ""
    exit_code: int = 0
    command: List[str] = field(default_factory=list)
    stderr_tail: str = ""
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "codec": self.codec,
            "exit_code": self.exit_code,
            "command": list(self.command),
            "stderr_tail": self.stderr_tail,
            "warnings": list(self.warnings),
        }


def bytes_per_sample(bit_depth: int) -> int:
    """Bytes per sample for an integer PCM depth."""
    if bit_depth <= 0:
        raise ValueError("bit_depth must be positive")
    return (bit_depth + 7) // 8
