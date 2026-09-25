#!/usr/bin/env python3
"""Fail closed when an installed systemd unit references a missing Porota host asset.

This validator is intentionally read-only.  It does not import application code,
change systemd state, or create files under the inspected root.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEFAULT_ROOT = Path("/opt/porota-trading")
PATH_RE = re.compile(r"(/opt/porota-trading/[A-Za-z0-9_.\-/]+\.(?:py|sh))")


def referenced_paths(unit_text: str, root: Path = DEFAULT_ROOT) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    root_text = str(root).rstrip("/")
    for raw in PATH_RE.findall(unit_text):
        rel = raw[len("/opt/porota-trading/") :]
        normalized = str(Path(root_text) / rel)
        if normalized not in seen:
            seen.add(normalized)
            out.append(Path(normalized))
    return out


def inspect_units(unit_paths: list[Path], root: Path = DEFAULT_ROOT) -> dict:
    rows = []
    missing = []
    for unit in unit_paths:
        text = unit.read_text(encoding="utf-8", errors="replace")
        refs = referenced_paths(text, root)
        unit_missing = [str(p) for p in refs if not p.is_file()]
        rows.append({
            "unit": str(unit),
            "references": [str(p) for p in refs],
            "missing": unit_missing,
        })
        missing.extend(unit_missing)
    return {
        "schema": "POROTA_HOST_UNIT_DEPENDENCY_GUARD_V1",
        "root": str(root),
        "units": rows,
        "missing": sorted(set(missing)),
        "status": "GREEN" if not missing else "RED",
        "mutation": "NONE",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--unit", action="append", required=True)
    args = parser.parse_args()
    payload = inspect_units([Path(x) for x in args.unit], Path(args.root))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "GREEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
