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


def audit(requirements_text: str, dockerfile_text: str) -> dict:
    rows = requirement_rows(requirements_text)
    non_exact = [r["requirement"] for r in rows if not r["exact_pin"]]
    m = FROM_RE.search(dockerfile_text)
    base = m.group(1) if m else ""
    digest_pinned = "@sha256:" in base
    return {
        "schema_version": 1,
        "status": "GREEN" if not non_exact and digest_pinned else "REPRODUCIBILITY_GAP",
        "requirements_total": len(rows),
        "exact_requirements": sum(bool(r["exact_pin"]) for r in rows),
        "non_exact_requirements": non_exact,
        "base_image": base,
        "base_image_digest_pinned": digest_pinned,
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
