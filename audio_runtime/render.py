"""Output rendering.

One payload, four surfaces: ``json`` for machines, ``table`` for a terminal,
``md`` for an issue comment, ``plain`` for a log. Rendering never mutates the
payload, so the JSON a caller parses is exactly the JSON the table was drawn from.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence

FORMATS = ("json", "table", "md", "plain")


def _table_rows(payload: Any) -> Optional[List[List[str]]]:
    if isinstance(payload, dict) and isinstance(payload.get("endpoints"), list):
        rows = [["DIR", "DEVICE_KEY", "NAME", "HOST", "CH", "DEFAULT"]]
        for entry in payload["endpoints"]:
            rows.append(
                [
                    str(entry.get("direction", "")),
                    str(entry.get("device_key", "")),
                    str(entry.get("name", ""))[:40],
                    str(entry.get("host_api", "")),
                    str(entry.get("channels", "")),
                    "yes" if entry.get("is_default") else "",
                ]
            )
        return rows
    if isinstance(payload, dict) and isinstance(payload.get("tools"), list):
        rows = [["TOOL", "AVAILABLE", "PATH", "VERSION"]]
        for tool in payload["tools"]:
            rows.append(
                [
                    str(tool.get("name", "")),
                    "yes" if tool.get("available") else "no",
                    str(tool.get("path", "")),
                    str(tool.get("version", ""))[:50],
                ]
            )
        return rows
    return None


def render(payload: Any, fmt: str = "json") -> str:
    if fmt not in FORMATS:
        raise ValueError("unsupported format: %s" % fmt)
    if fmt == "json":
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    rows = _table_rows(payload)
    if fmt == "md" and rows:
        header, body = rows[0], rows[1:]
        lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
        lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in body]
        return "\n".join(lines)
    if fmt in ("table", "plain") and rows:
        widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
        return "\n".join("  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip() for row in rows)
    return _flatten(payload)


def _flatten(payload: Any, prefix: str = "") -> str:
    lines: List[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            label = "%s.%s" % (prefix, key) if prefix else str(key)
            if isinstance(value, dict):
                lines.append(_flatten(value, label))
            elif isinstance(value, list):
                if not value:
                    lines.append("%s: (none)" % label)
                elif all(not isinstance(item, (dict, list)) for item in value):
                    lines.append("%s: %s" % (label, ", ".join(str(item) for item in value)))
                else:
                    for index, item in enumerate(value):
                        lines.append(_flatten(item, "%s[%d]" % (label, index)))
            else:
                lines.append("%s: %s" % (label, value))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            lines.append(_flatten(item, "%s[%d]" % (prefix, index)))
    else:
        lines.append(str(payload))
    return "\n".join(line for line in lines if line)


def summary_line(payload: Any) -> str:
    """One-line result used by the CLI's human default."""
    if not isinstance(payload, dict):
        return str(payload)
    if "endpoints" in payload:
        return "%d endpoint(s) on %s via %s" % (
            len(payload["endpoints"]),
            payload.get("platform", "?"),
            payload.get("backend", "?"),
        )
    for key in ("device_key", "output", "target", "path"):
        if key in payload:
            return "%s: %s" % (key, payload[key])
    if "ok" in payload:
        return "doctor ok=%s" % payload["ok"]
    return "done"
