"""Endpoint discovery: parsed, not guessed.

Every source is turned into the same ``AudioEndpoint`` record by an explicit
parser, so a Windows enumeration and a Linux enumeration can be diffed and both
can be replayed from a fixture file in a test. Nothing here opens a device.
"""

from __future__ import annotations

import json
import os
import platform as platform_module
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .errors import BackendUnavailable, DeviceAmbiguous, DeviceNotFound, UsageError
from .models import CAPTURE, DIRECTIONS, RENDER, AudioEndpoint

SUPPORTED_DIRECTIONS = ("all",) + DIRECTIONS

# PnP/udev tokens describe hardware; everything else is a human label. A key
# built from the hardware token survives a driver reinstall, a label-based key
# does not, so the token always wins when present.
_VID_PID = re.compile(r"(VID_[0-9A-Za-z]{4})&(PID_[0-9A-Za-z]{4})", re.IGNORECASE)
_SHORT_TOKEN = re.compile(r"^\[?([0-9A-Za-z]{4,12})\]?$")
_ALSA_CARD = re.compile(
    r"^card\s+(\d+):\s*(\S+)\s*\[([^\]]*)\],\s*device\s+(\d+):\s*([^\[]+?)\s*\[([^\]]*)\]",
    re.MULTILINE,
)
_OMNI_HEADER = re.compile(r"^===\s*(.+?)\s*===\s*$")
_OMNI_INDEX = re.compile(r"^\s*--(\d+)\s*$")
_OMNI_STATUS = re.compile(r"host_api=(.*?)\s+inputs=(\d+)\s+outputs=(\d+)\s+default_rate=(\d+)")


def detect_platform() -> str:
    """The host platform, normalised to the four values the schema allows."""
    name = platform_module.system().lower()
    if name.startswith("win"):
        return "windows"
    if name == "linux":
        return "linux"
    if name == "darwin":
        return "darwin"
    return "unknown"


def resolve_platform(value: str) -> str:
    if value and value != "auto":
        if value not in ("windows", "linux", "darwin", "unknown"):
            raise UsageError("unknown --platform value: %s" % value, hint="Use auto|windows|linux|darwin.")
        return value
    return detect_platform()


def slug(text: str) -> str:
    """Uppercase ASCII token used inside a device_key."""
    cleaned = re.sub(r"[^0-9A-Za-z]+", "-", text.strip()).strip("-").upper()
    return cleaned or "UNKNOWN"


def host_api_token(host_api: str) -> str:
    """Short, stable host-api prefix for a label-based device key.

    Windows reports the full driver name ("Windows WASAPI"), and repeating the
    vendor word in every key triples its length without adding information, so
    the last word wins; anything else is slugged as-is.
    """
    text = (host_api or "").strip()
    if not text:
        return "AUDIO"
    words = text.split()
    if len(words) > 1 and words[0].lower() in ("windows", "microsoft"):
        text = words[-1]
    return slug(text)


def device_key_for(name: str, *, host_api: str = "", card_index: Optional[int] = None, fallback: str = "") -> str:
    """Build the stable key for an endpoint.

    A PnP ``VID_xxxx&PID_xxxx`` token is the strongest identity, an ALSA
    card/device pair is next, and a host-api-qualified label is the last resort.
    """
    match = _VID_PID.search(name) or _VID_PID.search(fallback)
    if match:
        return "%s&%s" % (match.group(1).upper(), match.group(2).upper())
    if card_index is not None:
        return "ALSA:CARD%d" % card_index
    return "%s:%s" % (host_api_token(host_api), slug(name))


def parse_omnipickup_listing(text: str, platform: str = "windows") -> List[AudioEndpoint]:
    """Parse ``OmniPickup --list-devices`` output.

    The tool numbers devices by PortAudio's global index and reports capability
    counts, not names of the host API in a fixed position, so the parser keys on
    the ``host_api=... inputs=... outputs=... default_rate=...`` line.
    """
    entries: List[AudioEndpoint] = []
    host_api = ""
    index: Optional[int] = None
    name = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        header = _OMNI_HEADER.match(line.strip())
        if header:
            host_api = header.group(1)
            index, name = None, ""
            continue
        index_match = _OMNI_INDEX.match(line)
        if index_match:
            index = int(index_match.group(1))
            name = ""
            continue
        status = _OMNI_STATUS.search(line)
        if status and index is not None:
            reported_api = status.group(1).strip() or host_api
            inputs, outputs, rate = int(status.group(2)), int(status.group(3)), int(status.group(4))
            label = name or "%s device %d" % (reported_api, index)
            key = device_key_for(label, host_api=reported_api, fallback="")
            common = dict(
                device_key=key,
                platform=platform,
                name=label,
                default_sample_rate=rate,
                host_api=reported_api,
                backend_target="%d" % index,
                source="omnipickup",
            )
            if inputs > 0:
                entries.append(
                    AudioEndpoint(
                        endpoint_id="omnipickup-%d-capture" % index,
                        direction=CAPTURE,
                        channels=inputs,
                        is_default=(rate != 0 and inputs == 8 and "ListenAI" in label),
                        **common,
                    )
                )
            if outputs > 0:
                entries.append(
                    AudioEndpoint(
                        endpoint_id="omnipickup-%d-render" % index,
                        direction=RENDER,
                        channels=outputs,
                        extra={"render_capability_only": True},
                        **common,
                    )
                )
            index, name = None, ""
            continue
        stripped = line.strip()
        if stripped and index is not None and not stripped.startswith("host_api"):
            name = stripped
    return entries


def parse_alsa_listing(text: str, direction: str = RENDER) -> List[AudioEndpoint]:
    """Parse ``aplay -l`` / ``arecord -l`` output into ALSA endpoints."""
    entries: List[AudioEndpoint] = []
    for match in _ALSA_CARD.finditer(text):
        card, card_id, card_label, device, device_id, device_label = match.groups()
        card_index, pcm_device = int(card), int(device)
        label = device_label if device_label != card_label else "%s %s" % (card_label, device_label)
        entries.append(
            AudioEndpoint(
                endpoint_id="alsa-card%s-dev%s-%s" % (card, device, direction),
                device_key="ALSA:CARD%s%s" % (card, "" if direction == RENDER else ":IN"),
                direction=direction,
                platform="linux",
                name=label,
                channels=2 if direction == RENDER else 1,
                default_sample_rate=44100,
                host_api="ALSA",
                backend_target="plughw:%s,%s" % (card_id, device),
                provider=card_id,
                interface="plughw:%s,%s" % (card_id, device),
                card_index=card_index,
                pcm_device=pcm_device,
                is_default=(card_index == 0 and pcm_device == 0),
                source="alsa",
                extra={"card_label": card_label},
            )
        )
    return entries


def parse_powershell_endpoints(text: str) -> List[AudioEndpoint]:
    """Parse the JSON produced by ``WINDOWS_ENDPOINT_SCRIPT``.

    MMDevAPI device ids encode the data flow: ``{0.0.0.00000000}`` is render and
    ``{0.0.1.00000000}`` is capture, which is the only reliable direction signal
    in the PnP property set.
    """
    payload = json.loads(text or "[]")
    if isinstance(payload, dict):
        payload = [payload]
    entries: List[AudioEndpoint] = []
    for item in payload:
        device_id = str(item.get("DeviceID") or "")
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        if "{0.0.1." in device_id:
            direction = CAPTURE
        elif "{0.0.0." in device_id:
            direction = RENDER
        else:
            continue
        key = device_key_for(name, host_api="MMDEVAPI", fallback=device_id)
        entries.append(
            AudioEndpoint(
                endpoint_id="mmdevapi-%s-%s" % (slug(device_id)[-12:].lower(), direction),
                device_key=key,
                direction=direction,
                platform="windows",
                name=name,
                channels=2 if direction == RENDER else 1,
                default_sample_rate=44100,
                host_api="MMDEVAPI",
                backend_target=device_id,
                provider=str(item.get("Manufacturer") or ""),
                interface=device_id,
                is_default=bool(item.get("Default")),
                source="powershell",
                extra={"status": str(item.get("Status") or "")},
            )
        )
    return entries


WINDOWS_ENDPOINT_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$endpoints = Get-CimInstance Win32_PnPEntity |
  Where-Object { $_.PNPClass -eq 'AudioEndpoint' } |
  Select-Object Name, DeviceID, Manufacturer, Status
$endpoints | ConvertTo-Json -Compress
""".strip()


def fixture_endpoints(fixture_path: str) -> List[AudioEndpoint]:
    """Load a replayed enumeration. Fixtures are the only backend CI can run."""
    try:
        with open(fixture_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise UsageError("cannot read fixture %s: %s" % (fixture_path, exc))
    except json.JSONDecodeError as exc:
        raise UsageError("fixture %s is not valid JSON: %s" % (fixture_path, exc))
    if isinstance(payload, list):
        raw_endpoints = payload
    elif isinstance(payload, dict):
        raw_endpoints = payload.get("endpoints") or []
    else:
        raise UsageError("fixture %s must be an object or a list" % fixture_path)
    return [AudioEndpoint.from_dict(item) for item in raw_endpoints]


def _run(argv: Sequence[str], timeout: float = 20.0) -> str:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise BackendUnavailable("program not found: %s" % argv[0])
    except subprocess.TimeoutExpired:
        raise BackendUnavailable("timed out after %.0fs: %s" % (timeout, " ".join(argv)))
    if completed.returncode != 0:
        raise BackendUnavailable(
            "%s exited %d: %s" % (argv[0], completed.returncode, (completed.stderr or completed.stdout).strip()[:400])
        )
    return completed.stdout


def discover(
    platform: str = "auto",
    backend: str = "auto",
    direction: str = "all",
    fixture: Optional[str] = None,
) -> Dict[str, Any]:
    """Enumerate endpoints and return the ``ark.audio-endpoints.v1`` payload."""
    if direction not in SUPPORTED_DIRECTIONS:
        raise UsageError("unknown --direction value: %s" % direction, hint="Use all|render|capture.")
    resolved_platform = resolve_platform(platform)
    chosen = backend
    warnings: List[str] = []

    if chosen == "auto":
        if fixture:
            chosen = "fixture"
        elif resolved_platform == "windows":
            chosen = "omnipickup" if shutil.which("OmniPickup") else "powershell"
        elif resolved_platform == "linux":
            chosen = "alsa"
        else:
            chosen = "fixture"

    if chosen == "fixture":
        if not fixture:
            raise UsageError(
                "--backend fixture requires --fixture <path>",
                hint="Pass --fixture fixtures/linux-alsa.json (or your own recorded endpoints).",
            )
        entries = fixture_endpoints(fixture)
    elif chosen == "omnipickup":
        executable = shutil.which("OmniPickup") or shutil.which("OmniPickup.exe")
        if not executable:
            raise BackendUnavailable(
                "OmniPickup is not on PATH",
                hint="Install the omni-pickup skill or pass --fixture; this repo never bundles the EXE.",
            )
        entries = parse_omnipickup_listing(_run([executable, "--list-devices"]), platform="windows")
    elif chosen == "powershell":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            raise BackendUnavailable("PowerShell is not available", hint="Turn on Windows PowerShell or pass --fixture.")
        entries = parse_powershell_endpoints(_run([shell, "-NoProfile", "-Command", WINDOWS_ENDPOINT_SCRIPT]))
    elif chosen == "alsa":
        if not shutil.which("aplay"):
            raise BackendUnavailable("aplay is not installed", hint="apt install alsa-utils, or pass --fixture.")
        entries = parse_alsa_listing(_run(["aplay", "-l"]), direction=RENDER)
        if shutil.which("arecord"):
            try:
                entries += parse_alsa_listing(_run(["arecord", "-l"]), direction=CAPTURE)
            except BackendUnavailable as exc:
                warnings.append("capture enumeration skipped: %s" % exc.message)
    else:
        raise UsageError("unknown --backend value: %s" % chosen, hint="Use auto|fixture|omnipickup|powershell|alsa.")

    if direction != "all":
        entries = [entry for entry in entries if entry.direction == direction]

    return {
        "schema": "ark.audio-endpoints.v1",
        "platform": resolved_platform,
        "backend": chosen,
        "direction": direction,
        "generated_at": _now_stamp(),
        "endpoints": [entry.to_dict() for entry in entries],
        "warnings": warnings,
    }


def _now_stamp() -> str:
    from .models import now_stamp

    return now_stamp()


def find(entries: Iterable[AudioEndpoint], query: str, direction: Optional[str] = None) -> AudioEndpoint:
    """Resolve a caller-supplied key/id/name to exactly one endpoint.

    Exact key beats exact id beats name substring, and a substring that matches
    several endpoints fails loudly: silently picking the first one is how a
    bench test ends up measuring the wrong speaker.
    """
    if not query:
        raise UsageError("a device query is required")
    candidates = [entry for entry in entries if direction is None or entry.direction == direction]
    # A capture endpoint of an ALSA card carries the ":IN" suffix that its render
    # sibling does not; callers think in card keys, so accept the bare key too.
    aliases = [query]
    if direction == CAPTURE and not query.upper().endswith(":IN"):
        aliases.append(query + ":IN")
    for matcher in (
        lambda entry: entry.device_key.lower() in [alias.lower() for alias in aliases],
        lambda entry: entry.endpoint_id.lower() == query.lower(),
        lambda entry: query.lower() in entry.name.lower(),
    ):
        hits = [entry for entry in candidates if matcher(entry)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise DeviceAmbiguous(
                "%r matched %d endpoints: %s"
                % (query, len(hits), ", ".join(sorted(entry.device_key for entry in hits)))
            )
    available = ", ".join(sorted(entry.device_key for entry in candidates)) or "none"
    raise DeviceNotFound("%r is not among the enumerated endpoints (%s)" % (query, available))


def require_explicit_default(entry: AudioEndpoint, force_default: bool) -> None:
    """Using the machine default is a deliberate act, never an accident."""
    if entry.is_default and not force_default:
        raise UsageError(
            "%s is the system default %s endpoint" % (entry.device_key, entry.direction),
            hint="Pass --force-default when the system default really is the target.",
        )


def env_has(name: str) -> bool:
    return bool(os.environ.get(name))


def describe(entries: Sequence[AudioEndpoint]) -> str:
    """Human table used by ``--format table`` and by error hints."""
    if not entries:
        return "(no endpoints)"
    rows = [("DIR", "DEVICE_KEY", "NAME", "HOST", "CH")]
    for entry in entries:
        rows.append((entry.direction, entry.device_key, entry.name[:38], entry.host_api, str(entry.channels)))
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows)


def python_executable() -> str:
    return sys.executable
