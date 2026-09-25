#!/usr/bin/env python3
"""Audit whether the RC6 Python/image dependency inputs are reproducible."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)(.*)$")
EXACT_RE = re.compile(r"^[A-Za-z0-9_.-]+==[^,;\s]+(?:\s*;.*)?$")
FROM_RE = re.compile(r"^\s*FROM\s+([^\s]+)", re.MULTILINE)


def requirement_rows(text: str) -> list[dict[str, str | bool]]:
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("-e ", "--")):
            continue
        m = REQ_RE.match(line)
        if not m:
            continue
        rows.append({"requirement": line, "exact_pin": bool(EXACT_RE.match(line))})
    return rows


def audit(requirements_text: str, lock_text: str, dockerfile_text: str) -> dict:
    source_rows = requirement_rows(requirements_text)
    lock_rows = requirement_rows(lock_text)
    lock_non_exact = [r["requirement"] for r in lock_rows if not r["exact_pin"]]
    m = FROM_RE.search(dockerfile_text)
    base = m.group(1) if m else ""
    digest_pinned = "@sha256:" in base
    docker_uses_lock = "-r requirements.lock.txt" in dockerfile_text
    return {
        "schema_version": 2,
        "status": "GREEN" if lock_rows and not lock_non_exact and digest_pinned and docker_uses_lock else "REPRODUCIBILITY_GAP",
        "source_requirements_total": len(source_rows),
        "lock_requirements_total": len(lock_rows),
        "lock_exact_requirements": sum(bool(r["exact_pin"]) for r in lock_rows),
        "lock_non_exact_requirements": lock_non_exact,
        "base_image": base,
        "base_image_digest_pinned": digest_pinned,
        "docker_uses_lock": docker_uses_lock,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json-out")
    ap.add_argument("--fail-on-gap", action="store_true")
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    result = audit(
        (root / "requirements.txt").read_text(encoding="utf-8"),
        (root / "requirements.lock.txt").read_text(encoding="utf-8"),
        (root / "Dockerfile").read_text(encoding="utf-8"),
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    print("POROTA_DEPENDENCY_REPRO=" + result["status"])
    return 1 if args.fail_on_gap and result["status"] != "GREEN" else 0


if __name__ == "__main__":
    raise SystemExit(main())
