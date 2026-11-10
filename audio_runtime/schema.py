"""Machine-readable contracts, and a validator small enough to trust.

The schemas are the public API of this repository: every subcommand emits one
of them, and downstream skills pin the ``ark.*.v1`` id instead of parsing prose.
``validate`` implements only the keywords the contracts actually use, which is
why the validator is ~80 lines of explicit checks rather than a dependency.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from . import __version__

_ERROR = "ark.audio-error.v1"
_ENDPOINTS = "ark.audio-endpoints.v1"
_ENDPOINT = "ark.audio-endpoint.v1"
_PROBE = "ark.media-probe.v1"
_PLAY = "ark.audio-play-receipt.v1"
_RECORD = "ark.audio-record-receipt.v1"
_CONVERT = "ark.audio-convert-receipt.v1"
_DOCTOR = "ark.audio-doctor.v1"


class ValidationError(ValueError):
    """Raised when a payload does not satisfy its declared schema."""


def _endpoint_props() -> Dict[str, Any]:
    return {
        "endpoint_id": {"type": "string"},
        "device_key": {"type": "string", "pattern": r"^\S+$"},
        "direction": {"type": "string", "enum": ["render", "capture"]},
        "platform": {"type": "string", "enum": ["windows", "linux", "darwin", "unknown"]},
        "name": {"type": "string"},
        "channels": {"type": "integer", "minimum": 0},
        "default_sample_rate": {"type": "integer", "minimum": 0},
        "host_api": {"type": "string"},
        "backend_target": {"type": "string"},
        "provider": {"type": "string"},
        "interface": {"type": "string"},
        "card_index": {"type": ["integer", "null"]},
        "pcm_device": {"type": ["integer", "null"]},
        "is_default": {"type": "boolean"},
        "source": {"type": "string"},
        "extra": {"type": "object"},
    }


def _receipt_common() -> Dict[str, Any]:
    return {
        "started_at": {"type": "string", "pattern": TIMESTAMP_PATTERN},
        "finished_at": {"type": ["string", "null"], "pattern": TIMESTAMP_PATTERN},
        "exit_code": {"type": "integer"},
        "command": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    }


# Beijing wall-clock, no T separator, no fractional seconds, no Z, no offset.
# Anything else is a contract violation, not a style preference.
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"

SCHEMAS: Dict[str, Dict[str, Any]] = {
    _ERROR: {
        "$id": _ERROR,
        "title": "Audio error",
        "type": "object",
        "required": ["code", "message", "exit_code"],
        "properties": {
            "code": {"type": "string"},
            "message": {"type": "string"},
            "exit_code": {"type": "integer"},
            "hint": {"type": "string"},
        },
    },
    _ENDPOINT: {
        "$id": _ENDPOINT,
        "title": "Audio endpoint",
        "type": "object",
        "required": ["endpoint_id", "device_key", "direction", "platform", "name"],
        "properties": _endpoint_props(),
    },
    _ENDPOINTS: {
        "$id": _ENDPOINTS,
        "title": "Audio endpoint enumeration",
        "type": "object",
        "required": ["schema", "platform", "backend", "generated_at", "endpoints"],
        "properties": {
            "schema": {"type": "string", "enum": [_ENDPOINTS]},
            "platform": {"type": "string"},
            "backend": {"type": "string"},
            "direction": {"type": "string", "enum": ["all", "render", "capture"]},
            "generated_at": {"type": "string", "pattern": TIMESTAMP_PATTERN},
            "endpoints": {"type": "array", "items": {"type": "object"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    },
    _PROBE: {
        "$id": _PROBE,
        "title": "Media probe",
        "type": "object",
        "required": [
            "path",
            "raw_pcm",
            "size_bytes",
            "sha256",
            "sample_rate",
            "channels",
            "bit_depth",
            "duration_seconds",
            "clipping_status",
        ],
        "properties": {
            "path": {"type": "string"},
            "raw_pcm": {"type": "boolean"},
            "size_bytes": {"type": "integer", "minimum": 0},
            "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
            "sample_rate": {"type": "integer", "minimum": 0},
            "channels": {"type": "integer", "minimum": 0},
            "bit_depth": {"type": "integer", "minimum": 0},
            "duration_seconds": {"type": "number", "minimum": 0},
            "container": {"type": "string"},
            "codec": {"type": "string"},
            "byte_order": {"type": "string", "enum": ["little", "big"]},
            "peak_level": {"type": "number"},
            "mean_level": {"type": "number"},
            "peak_volume_db": {"type": ["number", "null"]},
            "mean_volume_db": {"type": ["number", "null"]},
            "non_silent": {"type": ["boolean", "null"]},
            "clipping_status": {"type": "string", "enum": ["none", "clipping", "unknown"]},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    },
    _PLAY: {
        "$id": _PLAY,
        "title": "Playback receipt",
        "type": "object",
        "required": ["device_key", "endpoint_id", "file", "backend", "started_at", "repeats", "degraded"],
        "properties": dict(
            _receipt_common(),
            **{
                "device_key": {"type": "string"},
                "endpoint_id": {"type": "string"},
                "file": {"type": "string"},
                "backend": {"type": "string"},
                "duration_seconds": {"type": "number", "minimum": 0},
                "repeats": {"type": "integer", "minimum": 1},
                "degraded": {"type": "boolean"},
            },
        ),
    },
    _RECORD: {
        "$id": _RECORD,
        "title": "Recording receipt",
        "type": "object",
        "required": [
            "device_key",
            "endpoint_id",
            "output",
            "backend",
            "started_at",
            "sample_rate",
            "channels",
            "bit_depth",
            "expected_bytes",
            "actual_bytes",
        ],
        "properties": dict(
            _receipt_common(),
            **{
                "device_key": {"type": "string"},
                "endpoint_id": {"type": "string"},
                "output": {"type": "string"},
                "backend": {"type": "string"},
                "sample_rate": {"type": "integer", "minimum": 1},
                "channels": {"type": "integer", "minimum": 1},
                "bit_depth": {"type": "integer", "enum": [8, 16, 24, 32]},
                "requested_seconds": {"type": "number", "minimum": 0},
                "actual_seconds": {"type": "number", "minimum": 0},
                "expected_bytes": {"type": "integer", "minimum": 0},
                "actual_bytes": {"type": "integer", "minimum": 0},
            },
        ),
    },
    _CONVERT: {
        "$id": _CONVERT,
        "title": "Conversion receipt",
        "type": "object",
        "required": ["source", "target", "started_at", "sample_rate", "channels", "codec"],
        "properties": dict(
            _receipt_common(),
            **{
                "source": {"type": "string"},
                "target": {"type": "string"},
                "sample_rate": {"type": "integer", "minimum": 1},
                "channels": {"type": "integer", "minimum": 1},
                "codec": {"type": "string"},
                "stderr_tail": {"type": "string"},
            },
        ),
    },
    _DOCTOR: {
        "$id": _DOCTOR,
        "title": "Environment doctor",
        "type": "object",
        "required": ["schema", "version", "platform", "ok", "tools"],
        "properties": {
            "schema": {"type": "string", "enum": [_DOCTOR]},
            "version": {"type": "string"},
            "platform": {"type": "string"},
            "ok": {"type": "boolean"},
            "tools": {"type": "array", "items": {"type": "object"}},
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    },
}


def list_schemas() -> List[str]:
    return sorted(SCHEMAS)


def get_schema(schema_id: str) -> Dict[str, Any]:
    try:
        return SCHEMAS[schema_id]
    except KeyError:
        raise ValidationError("unknown schema id: %s" % schema_id)


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValidationError("unsupported schema type: %s" % expected)


def validate(value: Any, schema_id: str, path: str = "$") -> None:
    """Validate ``value`` against ``schema_id``; raise ValidationError on the
    first violation. Unknown keywords are ignored on purpose."""
    schema = get_schema(schema_id)
    _validate(value, schema, path)


def _validate(value: Any, schema: Dict[str, Any], path: str) -> None:
    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, option) for option in options):
            raise ValidationError("%s: expected %s, got %s" % (path, options, type(value).__name__))
        if value is None:
            return
    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError("%s: %r not in %r" % (path, value, schema["enum"]))
    if isinstance(value, str) and "pattern" in schema:
        if re.search(schema["pattern"], value) is None:
            raise ValidationError("%s: %r does not match %s" % (path, value, schema["pattern"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            raise ValidationError("%s: %r below minimum %r" % (path, value, schema["minimum"]))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                raise ValidationError("%s: missing required key %r" % (path, key))
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _validate(value[key], sub, "%s.%s" % (path, key))
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate(item, schema["items"], "%s[%d]" % (path, index))


def version_banner() -> Dict[str, str]:
    return {"name": "audio-runtime", "version": __version__}
