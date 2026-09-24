#!/usr/bin/env python3
"""Fail-closed secret hygiene scan for Git-tracked Porota source.

Stdlib-only. Findings never include the matched credential value; output is
limited to path, line and detector kind. This protects CI logs while making the
repository-level SECRET_SCAN policy executable.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TOKEN_PATTERNS = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GITHUB_TOKEN", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GOOGLE_API_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("TELEGRAM_BOT_TOKEN", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
)

SENSITIVE_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(
      PPI_API_KEY|PPI_API_SECRET|TELEGRAM_BOT_TOKEN|GEMINI_API_KEY|
      ANTHROPIC_API_KEY|OPENAI_API_KEY|DIGITALOCEAN_ACCESS_TOKEN|
      DO_API_TOKEN|GITHUB_TOKEN|GH_TOKEN
    )\s*[:=]\s*["']?([^"'\s\#]+)
    """
)

PLACEHOLDER_MARKERS = (
    "ci-not-real", "not-real", "placeholder", "example", "dummy",
    "changeme", "replace_me", "replace-me", "your_", "your-",
    "<", "${", "{{", "...",
)

MAX_TEXT_BYTES = 2 * 1024 * 1024


def is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return True
    if normalized in {"0", "none", "null", "false", "true"}:
        return True
    return any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def scan_text(path: str, text: str) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for kind, pattern in TOKEN_PATTERNS:
            if pattern.search(line):
                findings.append({"path": path, "line": line_no, "kind": kind})
        for match in SENSITIVE_ASSIGNMENT.finditer(line):
            variable, value = match.group(1), match.group(2)
            if not is_placeholder(value):
                findings.append({
                    "path": path,
                    "line": line_no,
                    "kind": "SENSITIVE_ASSIGNMENT",
                    "variable": variable.upper(),
                })
    return findings


def tracked_files(repo_root: Path) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        text=False,
    )
    return sorted(x.decode("utf-8") for x in out.split(b"\0") if x)


def scan_repository(repo_root: Path, paths: list[str] | None = None) -> dict:
    findings: list[dict[str, object]] = []
    scanned = 0
    skipped_binary_or_large = 0
    for rel in paths if paths is not None else tracked_files(repo_root):
        path = repo_root / rel
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                skipped_binary_or_large += 1
                continue
            raw = path.read_bytes()
            if b"\0" in raw:
                skipped_binary_or_large += 1
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            skipped_binary_or_large += 1
            continue
        scanned += 1
        findings.extend(scan_text(rel, text))
    return {
        "schema_version": 1,
        "status": "GREEN" if not findings else "FAILED",
        "scanned_text_files": scanned,
        "skipped_binary_or_large": skipped_binary_or_large,
        "findings": findings,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args()
    result = scan_repository(Path(args.repo_root).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] == "GREEN":
        print("POROTA_SECRET_SCAN=GREEN")
        return 0
    print("POROTA_SECRET_SCAN=FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
