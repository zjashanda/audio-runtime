#!/usr/bin/env python3
"""Reject any timestamp that is not Beijing wall-clock ``YYYY-MM-DD HH:MM:SS``.

The workspace time contract is one shape only:

    2026-11-11 06:57:22

Forbidden anywhere in code, schemas, docs, fixtures and captured output:

- a ``T`` separator between date and time (RFC 3339 / ISO 8601)
- a ``Z`` suffix, or any ``+HH:MM`` / ``-HH:MM`` / ``+HHMM`` offset
- fractional seconds (the contract is second precision)

The single exemption is a ``raw_*`` field: those quote an upstream tool's own
string verbatim, and rewriting them would falsify evidence. A line that mentions
a ``raw_`` name is reported separately as an exemption instead of being silently
dropped.

Usage::

    python3 tools/time_lint.py            # scan the repository
    python3 tools/time_lint.py --self-test # prove every rule still fires

Exit codes: 0 clean, 1 violations found, 2 scan error.
"""

from __future__ import annotations

import os
import re
import sys
from typing import List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {".git", "__pycache__", "build", "dist", ".venv", "venv", ".mypy_cache", "node_modules"}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".exe", ".dll",
    ".pcm", ".wav", ".mp3", ".flac", ".m4a", ".ogg", ".woff", ".woff2",
}

# Rule id -> pattern. Every rule describes a shape the contract forbids.
RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("rfc3339-t-separator", re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")),
    ("zulu-suffix", re.compile(r"\b\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?Z\b")),
    ("utc-offset", re.compile(r"\b\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?[+-]\d{2}:?\d{2}\b")),
    ("fractional-seconds", re.compile(r"\b\d{2}:\d{2}:\d{2}\.\d+\b")),
]

RAW_FIELD = re.compile(r"\braw_[A-Za-z0-9_]*")

# This file necessarily contains the forbidden shapes in its own rules and
# self-test corpus, so it is exempt by path and the exemption is printed.
SELF = os.path.abspath(__file__)


def iter_files(root: str) -> List[str]:
    found: List[str] = []
    for base, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in SKIP_SUFFIXES:
                continue
            found.append(os.path.join(base, name))
    return found


def scan_text(text: str) -> List[Tuple[str, int, str]]:
    """Return (rule, lineno, line) for every violation in ``text``."""
    hits: List[Tuple[str, int, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in RULES:
            if pattern.search(line):
                hits.append((rule, number, line.strip()))
    return hits


def scan(root: str = ROOT) -> Tuple[List[Tuple[str, str, int, str]], List[Tuple[str, int, str]]]:
    violations: List[Tuple[str, str, int, str]] = []
    exempt: List[Tuple[str, int, str]] = []
    for path in iter_files(root):
        if os.path.abspath(path) == SELF:
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except (UnicodeDecodeError, OSError):
            continue
        relative = os.path.relpath(path, root)
        for rule, lineno, line in scan_text(text):
            if RAW_FIELD.search(line):
                exempt.append((relative, lineno, line))
            else:
                violations.append((rule, relative, lineno, line))
    return violations, exempt


SELF_TEST = [
    ("rfc3339-t-separator", "stamp = \"2026-11-10" + "T21:55:03\""),
    ("zulu-suffix", "stamp = \"2026-11-10 21:55:0" + "3Z\""),
    ("utc-offset", "stamp = \"2026-11-10 21:55:0" + "3+08:00\""),
    ("fractional-seconds", "stamp = \"2026-11-10 21:55:0" + "3.123\""),
]


def self_test() -> List[str]:
    failures: List[str] = []
    for rule, sample in SELF_TEST:
        hits = [name for name, _, _ in scan_text(sample)]
        if rule not in hits:
            failures.append("rule %s did not fire on its own sample" % rule)
    clean = 'stamp = "2026-11-11 06:57:22"'
    if scan_text(clean):
        failures.append("compliant timestamp was reported as a violation")
    exempt = 'raw_started_at = "2026-11-10' + 'T21:55:03Z"'
    if not RAW_FIELD.search(exempt):
        failures.append("raw_* exemption did not recognise its own marker")
    return failures


def main(argv: List[str]) -> int:
    if "--self-test" in argv:
        failures = self_test()
        for failure in failures:
            print("FAIL: %s" % failure)
        print("time lint self-test: %d rule(s), %d failure(s)" % (len(RULES), len(failures)))
        return 0 if not failures else 1
    try:
        violations, exempt = scan()
    except OSError as exc:
        print("time lint error: %s" % exc, file=sys.stderr)
        return 2
    for rule, path, lineno, line in violations:
        print("%s:%d: %s: %s" % (path, lineno, rule, line))
    for path, lineno, line in exempt:
        print("exempt: %s:%d: raw_* field: %s" % (path, lineno, line))
    print(
        "time lint: %d violation(s), %d raw_* exemption(s) in %s"
        % (len(violations), len(exempt), os.path.basename(ROOT))
    )
    return 0 if not violations else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
