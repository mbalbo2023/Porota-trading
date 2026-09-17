"""Read-only counterfactual learning projection for RC6.

This module only reads an atomically-published evidence snapshot. It never
rewrites paper decisions, learning samples, parameters, or order routes.
A claim is evaluable only when the original decision and contemporaneous,
comparable evidence are both present.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SNAPSHOT_NAME = "counterfactual_learning_rc6.json"
VALID_OUTCOMES = {"WOULD_CONFIRM", "WOULD_WARN", "WOULD_REJECT"}


def snapshot_path(root: Path | str | None = None) -> Path:
    base = Path(root) if root is not None else Path(
        os.getenv("POROTA_VALIDATION_ROOT", "/app/data/validation")
    )
    return base / SNAPSHOT_NAME


def _insufficient(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "candidate_id": str(row.get("candidate_id") or "—"),
        "symbol": str(row.get("symbol") or "—").upper(),
        "original_decision": str(row.get("original_decision") or "UNKNOWN").upper(),
        "outcome": "INSUFFICIENT_EVIDENCE",
        "reason": reason,
        "evidence_at": row.get("evidence_at"),
        "compared_at": row.get("compared_at"),
    }


def _normalise(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return _insufficient({}, "Registro inválido")
    required = ("candidate_id", "original_decision", "evidence_at", "compared_at")
    absent = [name for name in required if not row.get(name)]
    if absent:
        return _insufficient(row, "Faltan " + ", ".join(absent))
    if row.get("comparable") is not True:
        return _insufficient(row, "No hay snapshot contemporáneo comparable")
    outcome = str(row.get("outcome") or "").upper()
    if outcome not in VALID_OUTCOMES:
        return _insufficient(row, "Resultado contrafáctico no verificado")
    return {
        "candidate_id": str(row["candidate_id"]),
        "symbol": str(row.get("symbol") or "—").upper(),
        "original_decision": str(row["original_decision"]).upper(),
        "outcome": outcome,
        "reason": str(row.get("reason") or "Evidencia comparable publicada"),
        "evidence_at": row.get("evidence_at"),
        "compared_at": row.get("compared_at"),
    }


def read(root: Path | str | None = None) -> dict[str, Any]:
    """Load an untrusted snapshot and return a fail-closed view model."""
    try:
        raw = json.loads(snapshot_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raw = {}
    entries = raw.get("entries") if isinstance(raw, dict) else []
    rows = [_normalise(item) for item in entries] if isinstance(entries, list) else []
    counts = {key: 0 for key in (*VALID_OUTCOMES, "INSUFFICIENT_EVIDENCE")}
    for row in rows:
        counts[row["outcome"]] += 1
    return {
        "generated_at": raw.get("generated_at") if isinstance(raw, dict) else None,
        "source": str(raw.get("source") or "SIN_PUBLICACION") if isinstance(raw, dict) else "SIN_PUBLICACION",
        "rows": rows[:100],
        "counts": counts,
        "available": bool(rows),
        "policy": "READ_ONLY_NO_DECISION_OR_PARAMETER_CHANGE",
    }
