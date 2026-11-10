#!/usr/bin/env python3
"""Secret scan: fail if a credential-looking string reached the repo.

This is a guard, not a proof. It looks for the shapes that actually leak in
practice — personal access tokens, cloud keys, private keys, inline passwords —
and skips binary files and build/vcs directories.

Exit codes: 0 clean, 1 findings, 2 scan error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple

SKIP_DIRS = {".git", "__pycache__", "build", "dist", ".venv", "venv", ".mypy_cache", "node_modules"}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".zip",
    ".gz",
    ".dll",
    ".exe",
    ".pyc",
    ".pdf",
    ".pcm",
    ".wav",
    ".mp3",
    ".flac",
}

PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("github-pat", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github-fine-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("aws-akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("slack-token", re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}")),
    ("gitlab-pat", re.compile(r"glpat-[A-Za-z0-9_-]{20,}")),
    ("inline-password", re.compile(r"(?i)\b(password|passwd|pwd)\s*[=:]\s*['\"][^'\"]{6,}['\"]")),
    ("sshpass", re.compile(r"sshpass\s+-p\s+\S+")),
    ("bearer-literal", re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._-]{20,}")),
]

# Lines that only mention the words are fine; these keep the report useful.
ALLOW = re.compile(r"(?i)(placeholder|example|dummy|redacted|xxxx|<token>|\$\{?[A-Z_]+\}?|%[A-Z_]+%)")


def iter_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            yield path


def scan_text(text: str) -> List[Tuple[str, int, str]]:
    """Findings for one blob of text, as (rule, line number, snippet)."""
    findings: List[Tuple[str, int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW.search(line):
            continue
        for name, pattern in PATTERNS:
            if pattern.search(line):
                findings.append((name, lineno, line.strip()[:160]))
                break
    return findings


def scan(root: Path) -> List[Tuple[str, Path, int, str]]:
    findings: List[Tuple[str, Path, int, str]] = []
    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, lineno, snippet in scan_text(text):
            findings.append((name, path, lineno, snippet))
    return findings


SELF_TEST = {
    "github-pat": "token = ghp_" + "a" * 24,
    "github-fine-pat": "token = github_pat_" + "b" * 24,
    "aws-akid": "key = AKIA" + "B" * 16,
    "private-key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "slack-token": "hook = xoxb-" + "1" * 12,
    "gitlab-pat": "token = glpat-" + "c" * 22,
    "inline-password": "pass" + 'word = "hunter2hunter2"',
    "sshpass": "ssh" + "pass -p s3cret ssh host",
    "bearer-literal": "Authorization: Bearer " + "d" * 32,
}


def self_test() -> List[str]:
    failures: List[str] = []
    for rule, sample in SELF_TEST.items():
        hits = [name for name, _, _ in scan_text(sample)]
        if rule not in hits:
            failures.append("rule %s did not fire on its own sample" % rule)
    allowed = 'password = "<placeholder>"'
    if scan_text(allowed):
        failures.append("ALLOW list did not suppress a placeholder")
    return failures


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="scan a tree for credential-shaped strings")
    parser.add_argument("root", nargs="?", default=".", help="directory to scan (default: .)")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="prove every rule fires on a sample")
    args = parser.parse_args(argv)
    if args.self_test:
        failures = self_test()
        for failure in failures:
            print("self-test FAIL: %s" % failure)
        print("secret scan self-test: %d rule(s), %d failure(s)" % (len(SELF_TEST), len(failures)))
        return 0 if not failures else 1
    root = Path(args.root).resolve()
    if not root.exists():
        print("scan error: %s does not exist" % root, file=sys.stderr)
        return 2
    findings = scan(root)
    if not findings:
        if not args.quiet:
            print("secret scan: 0 findings in %s" % root.name)
        return 0
    for name, path, lineno, snippet in findings:
        print("%s:%d: %s: %s" % (path, lineno, name, snippet))
    print("secret scan: %d finding(s)" % len(findings), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
