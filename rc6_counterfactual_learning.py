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


def _evidence_fallback(root: Path | str | None = None) -> dict[str, Any]:
    """Project verified SHADOW profile evaluations when no standalone feed exists.

    The source is the signed decision-evidence publication. This remains a
    read-only comparison of frozen entry criteria; it is not a fill or a
    realised PnL outcome.
    """
    candidates = []
    if root is not None:
        base = Path(root)
        candidates.extend((base / "decision_evidence_latest.json",
                           base.parent / "reports" / "decision_evidence_latest.json"))
    evidence_root = os.getenv("RC6_DECISION_EVIDENCE_ROOT", "").strip()
    if evidence_root:
        candidates.append(Path(evidence_root) / "decision_evidence_latest.json")
    candidates.append(Path("/app/data/paper_v17/reports/decision_evidence_latest.json"))
    payload: dict[str, Any] = {}
    for path in candidates:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
                break
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
    decisions = payload.get("decisions") if isinstance(payload.get("decisions"), list) else []
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, dict) or decision.get("state") != "VERIFIED":
            continue
        decision_key = str(decision.get("decision_key") or "").strip()
        if not decision_key:
            continue
        factual = str(decision.get("factual_decision") or "UNKNOWN").upper()
        evidence_at = decision.get("evidence_at")
        profiles = decision.get("profiles") if isinstance(decision.get("profiles"), list) else []
        for profile in profiles:
            if not isinstance(profile, dict) or profile.get("name") == "BASELINE_CONSERVATIVE_V1":
                continue
            state = str(profile.get("state") or "").upper()
            action = str(profile.get("action") or "").upper()
            if state == "INSUFFICIENT_EVIDENCE":
                outcome = "INSUFFICIENT_EVIDENCE"
                reason = "SHADOW: " + str(profile.get("reason") or "Falta evidencia contemporánea")
            elif state == "HARD_SAFETY_BLOCKED" or action == "HOLD":
                outcome = "WOULD_WARN" if factual in {"BUY", "OPEN", "OPENED_SIMULATED", "ENTER"} else "WOULD_REJECT"
                reason = "SHADOW: " + str(profile.get("reason") or "Perfil habría mantenido HOLD")
            elif state == "EVALUATED" and action in {"CANDIDATE_OPEN", "BUY", "OPEN"}:
                outcome = "WOULD_CONFIRM"
                reason = "SHADOW: perfil habría habilitado el criterio de entrada"
            else:
                outcome = "INSUFFICIENT_EVIDENCE"
                reason = "SHADOW: resultado de perfil no verificable"
            rows.append({
                "candidate_id": f"{decision_key}:{profile.get('name') or 'PROFILE'}",
                "symbol": decision.get("symbol"),
                "original_decision": factual,
                "outcome": outcome,
                "comparable": True,
                "reason": reason,
                "evidence_at": evidence_at,
                "compared_at": payload.get("generated_at") or evidence_at,
            })
    if not rows:
        return {}
    return {
        "generated_at": payload.get("generated_at"),
        "source": str(payload.get("source") or "decision_evidence_snapshots/read-only") + " · SHADOW_DUAL_EVALUATION",
        "entries": rows[:100],
    }


def read(root: Path | str | None = None) -> dict[str, Any]:
    """Load the standalone feed, falling back to the active evidence publication."""
    raw: Any = {}
    try:
        raw = json.loads(snapshot_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list) or not raw.get("entries"):
        raw = _evidence_fallback(root)
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
