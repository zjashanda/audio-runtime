"""Typed errors with stable exit codes.

Every failure that a caller can reasonably branch on gets its own class and
its own exit code, so a shell caller never has to parse English prose.
"""

from __future__ import annotations

from typing import Optional


class AudioRuntimeError(Exception):
    """Base class for every error this package raises on purpose."""

    code = "audio-error"
    exit_code = 1
    hint = ""

    def __init__(self, message: str, hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint

    def as_dict(self) -> dict:
        payload = {"code": self.code, "message": self.message, "exit_code": self.exit_code}
        if self.hint:
            payload["hint"] = self.hint
        return payload


class UsageError(AudioRuntimeError):
    code = "usage"
    exit_code = 2
    hint = "Check --help for the accepted arguments."


class BackendUnavailable(AudioRuntimeError):
    code = "backend-unavailable"
    exit_code = 3
    hint = "Install the missing program or pick another --backend."


class DeviceNotFound(AudioRuntimeError):
    code = "device-not-found"
    exit_code = 4
    hint = "Run 'audio-runtime endpoints' and use a listed device_key."


class DeviceAmbiguous(AudioRuntimeError):
    code = "device-ambiguous"
    exit_code = 5
    hint = "Pass the exact device_key; a name substring matched several endpoints."


class ProbeError(AudioRuntimeError):
    code = "probe-failed"
    exit_code = 6
    hint = "Pass --sample-rate/--channels/--bit-depth for raw PCM, or install ffprobe."


class ConversionError(AudioRuntimeError):
    code = "convert-failed"
    exit_code = 7
    hint = "Inspect the ffmpeg stderr captured in the receipt."


class RecordingError(AudioRuntimeError):
    code = "record-failed"
    exit_code = 8
    hint = "Verify the input endpoint with 'record --validate-only' first."


class Interrupted(AudioRuntimeError):
    code = "interrupted"
    exit_code = 130
    hint = "Recording stopped by the user."
