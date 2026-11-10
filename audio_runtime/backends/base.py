"""Backend contract: build a command, never hide one.

A backend is deliberately dumb. It answers "which argv would play this file on
that endpoint" and nothing else, so the receipt always contains the exact
command that ran and a fixture backend can prove the whole pipeline in CI
without a sound card.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..errors import BackendUnavailable, UsageError
from ..models import AudioEndpoint


@dataclass
class PlayInvocation:
    argv: List[str]
    degraded: bool = False
    warnings: List[str] = field(default_factory=list)


class PlaybackBackend:
    """Base class; subclasses implement :meth:`build`."""

    name = "base"
    platforms: Sequence[str] = ()

    def available(self) -> bool:
        return True

    def supports(self, endpoint: AudioEndpoint) -> bool:
        return not self.platforms or endpoint.platform in self.platforms

    def build(self, endpoint: AudioEndpoint, path: str) -> PlayInvocation:  # pragma: no cover - abstract
        raise NotImplementedError


def _which(*candidates: str) -> Optional[str]:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


class FixturePlayback(PlaybackBackend):
    """Records invocations instead of making noise. The only CI-safe backend."""

    name = "fixture"

    def __init__(self) -> None:
        self.calls: List[PlayInvocation] = []

    def build(self, endpoint: AudioEndpoint, path: str) -> PlayInvocation:
        invocation = PlayInvocation(argv=["fixture-play", "--device", endpoint.device_key, "--file", path])
        self.calls.append(invocation)
        return invocation


class FfplayBackend(PlaybackBackend):
    name = "ffplay"

    def available(self) -> bool:
        return _which("ffplay") is not None

    def build(self, endpoint: AudioEndpoint, path: str) -> PlayInvocation:
        executable = _which("ffplay")
        if not executable:
            raise BackendUnavailable("ffplay is not installed", hint="Install ffmpeg, or use --backend native.")
        argv = [executable, "-nostdin", "-autoexit", "-nodisp", "-loglevel", "error"]
        warnings: List[str] = []
        if endpoint.backend_target:
            # SDL picks the default device; the target is advisory only.
            argv += ["-audio_device", endpoint.backend_target]
            warnings.append("ffplay cannot guarantee endpoint pinning; verify with 'probe' on the recording side")
        argv.append(path)
        return PlayInvocation(argv=argv, degraded=True, warnings=warnings)


class AplayBackend(PlaybackBackend):
    name = "alsa"
    platforms = ("linux",)

    def available(self) -> bool:
        return _which("aplay") is not None

    def build(self, endpoint: AudioEndpoint, path: str) -> PlayInvocation:
        executable = _which("aplay")
        if not executable:
            raise BackendUnavailable("aplay is not installed", hint="apt install alsa-utils.")
        if not endpoint.backend_target:
            raise UsageError("%s has no ALSA backend_target" % endpoint.device_key)
        return PlayInvocation(argv=[executable, "-q", "-D", endpoint.backend_target, path])


class PaplayBackend(PlaybackBackend):
    name = "pulse"
    platforms = ("linux",)

    def available(self) -> bool:
        return _which("paplay") is not None

    def build(self, endpoint: AudioEndpoint, path: str) -> PlayInvocation:
        executable = _which("paplay")
        if not executable:
            raise BackendUnavailable("paplay is not installed", hint="apt install pulseaudio-utils.")
        target = endpoint.backend_target or ""
        argv = [executable]
        if target:
            argv += ["--device", target]
        argv.append(path)
        return PlayInvocation(argv=argv, degraded=not bool(target), warnings=[] if target else ["playing on the PulseAudio default sink"])


def media_player_script(path: str) -> str:
    escaped = path.replace("'", "''")
    return (
        "Add-Type -AssemblyName PresentationCore;"
        "$p = New-Object System.Windows.Media.MediaPlayer;"
        "$p.Open([Uri]'%s');" % escaped
    ) + "$p.Play(); Start-Sleep -Milliseconds 300; while ($p.Position -lt $p.NaturalDuration.TimeSpan) { Start-Sleep -Milliseconds 100 }; $p.Close()"


class WindowsMediaBackend(PlaybackBackend):
    """Windows playback through WPF's MediaPlayer.

    Endpoint pinning is not exposed by MediaPlayer, so this reports itself as a
    degraded backend instead of pretending the device was honoured.
    """

    name = "windows"
    platforms = ("windows",)

    def available(self) -> bool:
        return _which("powershell", "pwsh") is not None

    def build(self, endpoint: AudioEndpoint, path: str) -> PlayInvocation:
        shell = _which("powershell", "pwsh")
        if not shell:
            raise BackendUnavailable("PowerShell is not available on this host")
        return PlayInvocation(
            argv=[shell, "-NoProfile", "-Command", media_player_script(path)],
            degraded=True,
            warnings=["MediaPlayer follows the system default endpoint; use omni-pickup/ListenAI playback for pinning"],
        )


def resolve_backend(name: str, platform: str) -> PlaybackBackend:
    """Pick a backend by name, with ``auto`` preferring native, pinned tools."""
    table = {
        "fixture": FixturePlayback,
        "ffplay": FfplayBackend,
        "alsa": AplayBackend,
        "pulse": PaplayBackend,
        "windows": WindowsMediaBackend,
    }
    if name and name != "auto":
        backend_class = table.get(name)
        if backend_class is None:
            raise UsageError("unknown --backend value: %s" % name, hint="Use auto|" + "|".join(sorted(table)))
        return backend_class()

    order = ["alsa", "pulse"] if platform == "linux" else ["windows", "ffplay"] if platform == "windows" else ["ffplay"]
    for candidate in order:
        backend = table[candidate]()
        if backend.available():
            return backend
    return FixturePlayback()
