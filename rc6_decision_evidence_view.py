"""Read-only dashboard projection for RC6 decision-evidence learning.

The decision writer owns the evidence artifact. This module only validates and
normalises that atomically-published JSON for the standard dashboard. It does
not query brokers, mutate SQLite, change the PAPER decision, or touch private
sites/cockpits.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SNAPSHOT_NAME = "decision_evidence_latest.json"
EVIDENCE_STATES = frozenset({
    "VERIFIED", "PENDING", "NOT_APPLICABLE", "INSUFFICIENT_EVIDENCE",
})
MAX_DASHBOARD_DECISIONS = 10
PROFILE_NAMES = (
    "BASELINE_CONSERVATIVE_V1",
    "SHADOW_BALANCED_V1",
    "SHADOW_AGGRESSIVE_V1",
)


def snapshot_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else Path(
        os.getenv("RC6_DECISION_EVIDENCE_ROOT", "data/paper_v17/reports")
    )
    return base / SNAPSHOT_NAME


def _state(value: Any) -> str:
    value = str(value or "").upper()
    return value if value in EVIDENCE_STATES else "INSUFFICIENT_EVIDENCE"


def _text(value: Any, fallback: str = "—") -> str:
    value = str(value or "").strip()
    return value or fallback


def _profile(raw: Any, expected_name: str | None = None) -> dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "name": _text(raw.get("name") or raw.get("profile") or expected_name, "UNKNOWN_PROFILE"),
        "decision": _text(raw.get("decision") or raw.get("action"), "UNKNOWN"),
        "state": _state(raw.get("state") or raw.get("evidence_status")),
        "reason": _text(raw.get("reason"), "Sin detalle publicado"),
        "outcome": _text(raw.get("outcome"), "PENDING"),
    }


def _profiles(raw: Any) -> list[dict[str, str]]:
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        items = [entry for entry in raw if isinstance(entry, dict)]
    elif isinstance(raw, dict):
        for name, entry in raw.items():
            if isinstance(entry, dict):
                clone = dict(entry)
                clone.setdefault("name", name)
                items.append(clone)
    result = [_profile(entry) for entry in items]
    names = {entry["name"] for entry in result}
    for name in PROFILE_NAMES:
        if name not in names:
            result.append(_profile({}, name))
    return result


def _sources(raw: Any) -> str:
    if isinstance(raw, list):
        return ", ".join(_text(item) for item in raw[:6]) or "No publicado"
    if isinstance(raw, dict):
        rendered = []
        for name, detail in list(raw.items())[:6]:
            if isinstance(detail, dict):
                rendered.append(f"{name}: {_text(detail.get('state') or detail.get('freshness'), 'UNKNOWN')}")
            else:
                rendered.append(f"{name}: {_text(detail, 'UNKNOWN')}")
        return ", ".join(rendered) or "No publicado"
    return _text(raw, "No publicado")


def _decision(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "decision_key": _text(raw.get("decision_key") or raw.get("decision_id") or raw.get("id")),
        "symbol": _text(raw.get("symbol")).upper(),
        "at": raw.get("decided_at") or raw.get("evidence_at") or raw.get("timestamp"),
        "state": _state(raw.get("state") or raw.get("evidence_status")),
        "factual_decision": _text(raw.get("factual_decision") or raw.get("decision") or raw.get("action"), "UNKNOWN"),
        "reason": _text(raw.get("reason") or raw.get("factual_reason"), "Sin detalle publicado"),
        "sources": _sources(raw.get("sources") or raw.get("inputs")),
        "profiles": _profiles(raw.get("profiles")),
    }


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Derive counts from normalised decisions, never trust publisher totals."""
    result = {state: 0 for state in EVIDENCE_STATES}
    for row in rows:
        result[row["state"]] += 1
    return result


def read(root: Path | str | None = None) -> dict[str, Any]:
    """Return a fail-closed, bounded view model from one published artifact."""
    try:
        raw = json.loads(snapshot_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    original = raw.get("decisions")
    rows = [_decision(entry) for entry in original] if isinstance(original, list) else []
    return {
        "available": bool(rows),
        "generated_at": raw.get("generated_at"),
        "source": _text(raw.get("source"), "SIN_PUBLICACION"),
        "rows": rows[:MAX_DASHBOARD_DECISIONS],
        "total_decisions": len(rows),
        "counts": _counts(rows),
        "aggregate": raw.get("aggregate") if isinstance(raw.get("aggregate"), dict) else {},
        "policy": "READ_ONLY_DASHBOARD_NO_DECISION_OR_ORDER_CHANGE",
    }
