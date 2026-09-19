"""Post-close read-only publication of immutable RC6 decision evidence.

This module is a projection only: it reads the PAPER database, verifies the
stored snapshot hash and writes one atomic dashboard artifact.  It never calls
IOL/PPI, writes decisions, changes profiles or sends orders.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from typing import Any

from rc6_shadow_profiles import evaluate_profiles

DEFAULT_DB = os.getenv("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")
DEFAULT_ROOT = Path(os.getenv("RC6_DECISION_EVIDENCE_ROOT", "data/paper_v17/reports"))
OUTPUT_NAME = "decision_evidence_latest.json"
MAX_ROWS = 500


def output_path(root: Path | str | None = None) -> Path:
    return (Path(root) if root is not None else DEFAULT_ROOT) / OUTPUT_NAME


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _safe_json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _sources(payload: dict[str, Any]) -> dict[str, Any]:
    detail = payload.get("inputs_used") if isinstance(payload.get("inputs_used"), dict) else {}
    sources = detail.get("sources") if isinstance(detail.get("sources"), dict) else {}
    iol = detail.get("iol") if isinstance(detail.get("iol"), dict) else {}
    if iol:
        sources = {**sources, "IOL": iol}
    return sources


def _profile_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    detail = payload.get("inputs_used") if isinstance(payload.get("inputs_used"), dict) else {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    candidate = detail.get("candidate") if isinstance(detail.get("candidate"), dict) else {}
    hard_safety = detail.get("hard_safety") if isinstance(detail.get("hard_safety"), dict) else {}
    return {
        "decision_key": payload.get("decision_key"),
        "factual": {
            "decision_key": payload.get("decision_key"),
            "action": decision.get("final_result"),
            "score": detail.get("score"),
            "reason": decision.get("reason"),
        },
        "candidate": candidate,
        "hard_safety": hard_safety,
    }


def _profile_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = evaluate_profiles(_profile_snapshot(payload))
    rows = []
    for name, profile in result.get("profiles", {}).items():
        state = "VERIFIED" if name == "BASELINE_CONSERVATIVE_V1" and profile.get("state") == "FACTUAL_REFERENCE" else (
            "VERIFIED" if profile.get("state") == "EVALUATED" else "INSUFFICIENT_EVIDENCE")
        rows.append({
            "name": name, "action": profile.get("action"),
            "state": state, "reason": ", ".join(profile.get("reason_codes") or []),
            "outcome": "PENDING",
        })
    return rows


def build(*, db_path: Path | str = DEFAULT_DB, limit: int = MAX_ROWS) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_ROWS))
    rows = []
    with sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        sql = """SELECT e.decision_key,e.captured_at,e.payload_sha256,e.payload_json,
                         p.status AS paper_status,p.net_pnl,p.closed_at
                  FROM decision_evidence_snapshots e
                  LEFT JOIN paper_positions p
                    ON p.paper_id=json_extract(e.payload_json,'$.decision.paper_id')
                  ORDER BY e.captured_at DESC LIMIT ?"""
        for row in conn.execute(sql, (limit,)):
            payload = _safe_json(row["payload_json"])
            valid = bool(payload) and _canonical_hash(payload) == str(row["payload_sha256"] or "")
            decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
            outcome = ("PENDING" if not row["paper_status"] else
                       ("CLOSED:" + str(row["net_pnl"]) if row["paper_status"] == "CLOSED" else str(row["paper_status"])))
            rows.append({
                "decision_key": row["decision_key"], "symbol": decision.get("symbol"),
                "evidence_at": row["captured_at"],
                "state": "VERIFIED" if valid else "INSUFFICIENT_EVIDENCE",
                "factual_decision": decision.get("final_result"),
                "reason": decision.get("reason"),
                "sources": _sources(payload),
                "profiles": _profile_rows(payload) if valid else [],
                "outcome": outcome,
            })
    counts = {key: sum(item["state"] == key for item in rows)
              for key in ("VERIFIED", "PENDING", "NOT_APPLICABLE", "INSUFFICIENT_EVIDENCE")}
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "decision_evidence_snapshots/read-only",
        "decisions": rows, "counts": counts,
        "aggregate": {"decisions_total": len(rows), "profiles_mode": "SHADOW_DUAL_EVALUATION"},
        "read_only": True, "real_orders_authorized": False,
    }


def write(payload: dict[str, Any], *, root: Path | str | None = None) -> Path:
    target = output_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, prefix=".decision-evidence-", suffix=".tmp", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def publish(**kwargs) -> dict[str, Any]:
    root = kwargs.pop("root", None)
    payload = build(**kwargs)
    write(payload, root=root)
    return payload


if __name__ == "__main__":
    try:
        result = publish()
        print(json.dumps({"status": "OK", "decisions": len(result["decisions"]), "read_only": True}, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": type(exc).__name__, "read_only": True}, sort_keys=True))
        raise
