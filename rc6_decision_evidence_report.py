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
from tempfile import NamedTemporaryFile
import time
from typing import Any

from cg_paper_workspace import artifact_root, database_path, identity_from_connection
from rc6_audit_evidence.sqlite_snapshot import readonly_copy
from rc6_audit_evidence.sqlite_scratch import SnapshotError
from rc6_shadow_profiles import evaluate_profiles
from rc6_postclose_source_contract import (ART, PENDING as EOD_PENDING,
                                          postclose_eod_receipt,
                                          verified_current_source)
from rc6_postclose_review import paper_safety_findings, verified_snapshot_paper_safety

DEFAULT_ROOT = Path(os.getenv("RC6_DECISION_EVIDENCE_ROOT", "data/paper_v17/reports"))
OUTPUT_NAME = "decision_evidence_latest.json"
COUNTERFACTUAL_NAME = "decision_evidence_counterfactual_latest.json"
MAX_ROWS = 500
COUNTERFACTUAL_MAX_BYTES = 1024 * 1024
POSTCLOSE_SNAPSHOT = Path(os.getenv(
    "RC6_SNAPSHOT_OUTPUT", "data/paper_v17/snapshots/latest.json"))


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


def build(*, db_path: Path | str | None = None, limit: int = MAX_ROWS) -> dict[str, Any]:
    limit = max(1, min(int(limit), MAX_ROWS))
    rows = []
    database = database_path()
    if db_path is not None and Path(db_path).absolute() != database:
        raise SnapshotError("PAPER_SOURCE_DATABASE_UNVERIFIED")
    # Use the same private, verified SQLite capture as other RC6 audits. A
    # direct SQLite mode=ro on the SOURCE could create a missing WAL -shm.
    with readonly_copy(database,
                       deadline=time.monotonic() + 10.0,
                       scratch_root=artifact_root(database) / "sqlite-read-scratch",
                       consumer="rc6_decision_projection") as conn:
        identity_from_connection(conn)
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
        "projection_status": "VERIFIED_PRIVATE_SOURCE",
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


def write_counterfactual(payload: dict[str, Any], *, root: Path | str | None = None) -> Path:
    """Publish a bounded, observational subset of already verified decisions."""
    decisions = []
    for row in payload.get("decisions", [])[:100]:
        if not isinstance(row, dict) or row.get("state") != "VERIFIED":
            continue
        decisions.append({
            "decision_key": row.get("decision_key"), "symbol": row.get("symbol"),
            "state": "VERIFIED", "factual_decision": row.get("factual_decision"),
            "evidence_at": row.get("evidence_at"),
            "profiles": [{key: profile.get(key) for key in ("name", "state", "action", "reason")}
                         for profile in row.get("profiles", [])[:3] if isinstance(profile, dict)],
        })
    compact = {"schema_version": 1, "compact_projection": True,
               "generated_at": payload.get("generated_at"),
               "source": "decision_evidence_snapshots/read-only",
               "postclose_report_status": payload.get("postclose_report_status", "NOT_APPLICABLE"),
               "postclose_report_reason": payload.get("postclose_report_reason", "NOT_APPLICABLE"),
               "postclose_paper_safety_findings": payload.get("postclose_paper_safety_findings", []),
               "postclose_eod_source": payload.get("postclose_eod_source", {"state": "NOT_APPLICABLE"}),
               "decisions": decisions, "read_only": True, "real_orders_authorized": False}
    raw = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    while len(raw) > COUNTERFACTUAL_MAX_BYTES and decisions:
        decisions.pop()
        raw = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(raw) > COUNTERFACTUAL_MAX_BYTES:
        raise ValueError("COUNTERFACTUAL_PUBLICATION_BYTE_BUDGET")
    target = output_path(root).with_name(COUNTERFACTUAL_NAME)
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("wb", dir=target.parent, prefix=".decision-counterfactual-", suffix=".tmp", delete=False) as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def publish(**kwargs) -> dict[str, Any]:
    root = kwargs.pop("root", None)
    postclose = bool(kwargs.pop("postclose", False))
    source_at = kwargs.pop("source_at", None)
    snapshot_path = Path(kwargs.pop("snapshot_path", POSTCLOSE_SNAPSHOT))
    try:
        payload = build(**kwargs)
    except (SnapshotError, OSError, ValueError):
        if not postclose:
            raise
        # External/source unavailable is a reporting AMBER, never an excuse
        # to call the report VERIFIED or stop healthy PAPER runtime.
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "decision_evidence_snapshots/read-only",
            "decisions": [], "counts": {},
            "aggregate": {"decisions_total": 0,
                          "profiles_mode": "SHADOW_DUAL_EVALUATION"},
            "projection_status": "NO_VERIFICADO",
            "projection_reason": "PAPER_PRIVATE_SOURCE_UNAVAILABLE",
            "read_only": True, "real_orders_authorized": False,
        }
    if postclose:
        at = source_at or datetime.now(timezone.utc)
        source = postclose_eod_receipt(at, db_path=kwargs.get("db_path"))
        # A newly arrived PPI receipt cannot retroactively certify an earlier
        # provisional snapshot: all three reports must agree on the session.
        snapshot_ready = False
        snapshot_paper_red = False
        unsafe_findings = []
        try:
            if snapshot_path.is_symlink() or not snapshot_path.is_file():
                raise ValueError("POSTCLOSE_SNAPSHOT_UNVERIFIED")
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if isinstance(snapshot, dict) and snapshot.get("phase") == "postclose":
                unsafe_findings = paper_safety_findings(snapshot)
                snapshot_paper_red = not verified_snapshot_paper_safety(snapshot)
            seen = datetime.fromisoformat(str(snapshot.get("generated_at")).replace("Z", "+00:00"))
            snapshot_ready = (
                snapshot.get("phase") == "postclose" and
                snapshot.get("status") == "VERIFIED" and
                verified_snapshot_paper_safety(snapshot) and
                isinstance(snapshot.get("postclose_eod_source"), dict) and
                snapshot["postclose_eod_source"].get("paper_dataset_id") == source.get("paper_dataset_id") and
                snapshot["postclose_eod_source"].get("source_detail_sha256") == source.get("source_detail_sha256") and
                snapshot["postclose_eod_source"].get("source_success_at") == source.get("source_success_at") and
                seen.tzinfo is not None and seen.utcoffset() is not None and
                seen.astimezone(ART).date() == at.astimezone(ART).date() and
                seen <= at and
                verified_current_source(snapshot["postclose_eod_source"], seen))
        except (OSError, ValueError, TypeError, AttributeError):
            snapshot_ready = False
        payload["postclose_eod_source"] = source
        if snapshot_paper_red:
            payload["postclose_report_status"] = "INSUFFICIENT_EVIDENCE"
            payload["postclose_report_reason"] = "PAPER_SAFETY_CONTRADICTION"
            payload["postclose_paper_safety_findings"] = unsafe_findings
        elif (snapshot_ready and payload["projection_status"] == "VERIFIED_PRIVATE_SOURCE"
              and verified_current_source(source, at)):
            payload["postclose_report_status"] = "VERIFIED"
            payload["postclose_report_reason"] = "POSTCLOSE_PRIVATE_SNAPSHOT_AND_EOD_VERIFIED"
        else:
            payload["postclose_report_status"] = EOD_PENDING
            payload["postclose_report_reason"] = "POSTCLOSE_EOD_OR_SNAPSHOT_PENDING"
    write(payload, root=root)
    write_counterfactual(payload, root=root)
    return payload


def main() -> int:
    result = publish(postclose=True)
    status = result.get("postclose_report_status", "OK")
    print(json.dumps({"status": status,
                      "decisions": len(result["decisions"]), "read_only": True}, sort_keys=True))
    return 2 if status == "INSUFFICIENT_EVIDENCE" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error": type(exc).__name__, "read_only": True}, sort_keys=True))
        raise
