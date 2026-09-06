"""Final RC6 dashboard truth fixes for the 07-Sep PAPER go-live.

Presentation/observability only.  The live introspection producer already writes
`porota_introspection_rc6_*.json`; the legacy dashboard selected only HF-named
files and therefore displayed a stale snapshot even while the RC6 timer was
healthy.  This layer selects the newest supported RC/HF snapshot by mtime.
"""
from __future__ import annotations

import json
from pathlib import Path

import bg_paper_dashboard as bg

_installed = False


def latest_introspection_current():
    directory = Path(bg.DB_PATH).parents[1] / "introspection"
    if not directory.exists():
        return None
    files = []
    for pattern in ("porota_introspection_rc*_*.json", "porota_introspection_hf*_*.json"):
        files.extend(p for p in directory.glob(pattern) if p.is_file() and not p.is_symlink())
    files = sorted(set(files), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("timestamp"):
                return payload
        except (OSError, ValueError, TypeError):
            continue
    return None


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    bg._latest_introspection = latest_introspection_current
