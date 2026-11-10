"""Environment report: what this host can actually do.

``doctor`` is the first command any new node should run, because every failure
mode in this repo is an absent external program, not a bug.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, List, Sequence, Tuple

from . import __version__
from .endpoints import detect_platform

# (tool, version argv, role, required)
TOOLS: Sequence[Tuple[str, Sequence[str], str, bool]] = (
    ("ffmpeg", ("-version",), "format conversion and normalisation", True),
    ("ffprobe", ("-version",), "media inspection for non-WAV input", True),
    ("ffplay", ("-version",), "fallback playback backend", False),
    ("aplay", ("--version",), "ALSA playback on Linux", False),
    ("arecord", ("--version",), "ALSA capture on Linux", False),
    ("paplay", ("--version",), "PulseAudio/PipeWire playback", False),
    ("pwsh", ("-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"), "PowerShell 7 enumeration", False),
    ("powershell", ("-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"), "Windows PowerShell enumeration", False),
    ("OmniPickup", ("-h",), "multi host-API capture", False),
)


def _version(argv: Sequence[str], timeout: float = 5.0) -> str:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = (completed.stdout or completed.stderr or "").strip()
    return text.splitlines()[0][:120] if text else ""


def doctor() -> Dict[str, Any]:
    """Return the ``ark.audio-doctor.v1`` payload."""
    platform = detect_platform()
    tools: List[Dict[str, Any]] = []
    warnings: List[str] = []
    ok = True
    for name, version_argv, role, required in TOOLS:
        path = shutil.which(name)
        available = path is not None
        entry: Dict[str, Any] = {
            "name": name,
            "role": role,
            "required": required,
            "available": available,
            "path": path or "",
        }
        if available:
            entry["version"] = _version([path] + list(version_argv))
        elif required:
            ok = False
            warnings.append("%s is missing: %s will not work" % (name, role))
        tools.append(entry)
    if platform == "windows" and not (shutil.which("OmniPickup") or shutil.which("OmniPickup.exe")):
        warnings.append("OmniPickup is absent: recording falls back to unavailable until it is installed")
    if platform == "linux" and not shutil.which("aplay"):
        warnings.append("aplay is absent: ALSA playback is unavailable")
    return {
        "schema": "ark.audio-doctor.v1",
        "version": __version__,
        "platform": platform,
        "ok": ok,
        "tools": tools,
        "warnings": warnings,
    }
