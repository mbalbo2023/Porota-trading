#!/usr/bin/env python3
"""Read-only RC6 post-close review derived from the verified PAPER snapshot."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from rc6_postclose_source_contract import (PENDING as EOD_PENDING,
                                          verified_current_source)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
COM19031_EFFECTIVE = datetime(2026, 11, 2).date()
SNAPSHOT = Path(os.getenv("RC6_SNAPSHOT_OUTPUT", "data/paper_v17/snapshots/latest.json"))
OUTPUT = Path(os.getenv("RC6_POSTCLOSE_REVIEW_OUTPUT", "data/paper_v17/reports/postclose_review_latest.json"))


def paper_safety_findings(snapshot: dict) -> list[str]:
    """Recheck raw PAPER invariants; textual safety flags are not authority."""
    if not isinstance(snapshot, dict):
        return ["PAPER_SAFETY_ATTESTATION_UNVERIFIED"]
    findings = []
    if snapshot.get("mode") != "PRODUCTION_PAPER":
        findings.append("MODE_NOT_PRODUCTION_PAPER")
    real_orders = snapshot.get("real_orders_sent")
    if type(real_orders) is not int or real_orders != 0:
        findings.append("REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED")
    alerts = snapshot.get("urgent_alerts")
    if not isinstance(alerts, list):
        findings.append("SNAPSHOT_ALERTS_UNVERIFIED")
    if alerts:
        findings.append("SNAPSHOT_HAS_RUNTIME_ALERTS")
    if snapshot.get("paper_safety_status") != "VERIFIED":
        findings.append("PAPER_SAFETY_ATTESTATION_UNVERIFIED")
    return findings


def verified_snapshot_paper_safety(snapshot: dict) -> bool:
    """Pure cross-report safety check, not physical runtime validation."""
    return isinstance(snapshot, dict) and not paper_safety_findings(snapshot)


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _today(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(TZ).date().isoformat()
    except (TypeError, ValueError):
        return None


def build_review(snapshot: dict, now: datetime) -> dict:
    operations = [row for row in (snapshot.get("operations") or []) if isinstance(row, dict)]
    metrics = snapshot.get("metrics") if isinstance(snapshot.get("metrics"), dict) else {}
    closed = int(metrics.get("closed_operations") or len(operations))
    net_pnl = _number(metrics.get("net_pnl_ars"))
    win_rate = _number(metrics.get("win_rate_pct"))
    snapshot_fresh = (snapshot.get("phase") == "postclose" and
                      _today(snapshot.get("generated_at")) ==
                      now.astimezone(TZ).date().isoformat())
    try:
        generated = datetime.fromisoformat(str(snapshot.get("generated_at")).replace("Z", "+00:00"))
        snapshot_fresh = (snapshot_fresh and generated.tzinfo is not None
                          and generated.utcoffset() is not None and generated <= now)
    except (TypeError, ValueError):
        snapshot_fresh = False
    eod_required = now.astimezone(TZ).date() >= COM19031_EFFECTIVE
    receipt = (snapshot.get("postclose_eod_source") if
               isinstance(snapshot.get("postclose_eod_source"), dict) else {})
    eod_ready = (not eod_required or
                 (snapshot_fresh and verified_current_source(receipt, generated)))
    # An EOD source shortage is AMBER; a LIVE runtime or an ambiguous PAPER
    # order count is RED. Re-check raw snapshot fields here: a forged/old
    # paper_safety_status string is never operational safety evidence.
    safety_findings = paper_safety_findings(snapshot)
    alerts = snapshot.get("urgent_alerts")
    paper_safe = (
        not safety_findings
        and snapshot.get("status") in ({"VERIFIED", EOD_PENDING}
                                        if eod_required else {"VERIFIED"})
        and not alerts)
    snapshot_current = snapshot_fresh and paper_safe and snapshot.get("status") == "VERIFIED" and eod_ready
    review_status = ("INSUFFICIENT_EVIDENCE" if not snapshot_fresh or not paper_safe else
                     EOD_PENDING if eod_required and not snapshot_current else
                     "VERIFIED" if snapshot_current else "INSUFFICIENT_EVIDENCE")
    findings, next_actions = [], []
    if not snapshot_fresh:
        findings.append("POSTCLOSE_SNAPSHOT_MISSING_OR_STALE")
        next_actions.append("REGENERATE_READ_ONLY_POSTCLOSE_SNAPSHOT")
    if not paper_safe:
        findings.extend(safety_findings or ["PAPER_SAFETY_ATTESTATION_UNVERIFIED"])
        next_actions.append("RESOLVE_PAPER_SAFETY_WITH_RUNTIME_EVIDENCE")
    if snapshot_fresh and paper_safe and eod_required and not snapshot_current:
        findings.append("PPI_EOD_CURRENT_SESSION_SOURCE_PENDING")
        next_actions.append("RETRY_READ_ONLY_POSTCLOSE_AFTER_SOURCE_RECEIPT")
    if closed == 0:
        findings.append("NO_CLOSED_PAPER_OPERATIONS")
        next_actions.append("REVIEW_DECISION_LOG_AND_CANDIDATE_COVERAGE")
    if net_pnl is not None and net_pnl < 0:
        findings.append("NET_PNL_NEGATIVE")
        next_actions.append("REVIEW_LOSING_OPERATIONS_BEFORE_ANY_STRATEGY_CHANGE")
    if win_rate is not None and win_rate < 50:
        findings.append("WIN_RATE_BELOW_50_PCT")
        next_actions.append("REVIEW_ENTRY_EXIT_REASON_COHORTS")
    if isinstance(alerts, list) and alerts:
        findings.extend(f"SNAPSHOT_ALERT:{item}" for item in alerts)
        next_actions.append("RESOLVE_SNAPSHOT_ALERTS_WITH_EVIDENCE")
    if not findings:
        findings.append("NO_AUTOMATIC_STRATEGY_CHANGE_RECOMMENDED")
        next_actions.append("REVIEW_RESULTS_WITH_OPERATOR_BEFORE_CHANGING_RULES")
    return {
        "schema_version": 1,
        "generated_at": now.astimezone(TZ).isoformat(timespec="seconds"),
        "source_snapshot_generated_at": snapshot.get("generated_at"),
        "read_only": True,
        "decision_authority": "HUMAN_REVIEW_REQUIRED",
        "status": review_status,
        "postclose_eod_source": receipt if eod_required else {"state": "NOT_APPLICABLE"},
        "metrics": {
            "closed_operations": closed, "net_pnl_ars": net_pnl,
            "win_rate_pct": win_rate, "decisions_observed": int(metrics.get("decisions_observed") or snapshot.get("decision_count") or 0),
        },
        "operations": operations,
        "findings": findings,
        "next_actions": next_actions,
        "automatic_strategy_change": False,
        "real_orders_authorized": False,
    }


def write_review(review: dict, output: Path = OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".postclose-review.", suffix=".json", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(review, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    try:
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise ValueError("SNAPSHOT_INVALID")
        review = build_review(snapshot, datetime.now(TZ))
        write_review(review)
        print(json.dumps({"status": review["status"], "read_only": True, "automatic_strategy_change": False}, sort_keys=True))
        return 0 if review["status"] in {"VERIFIED", EOD_PENDING} else 2
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "read_only": True, "error": type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
