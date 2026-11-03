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
    snapshot_fresh = (
        snapshot.get("phase") == "postclose"
        and _today(snapshot.get("generated_at")) == now.astimezone(TZ).date().isoformat()
    )
    try:
        generated = datetime.fromisoformat(str(snapshot.get("generated_at")).replace("Z", "+00:00"))
        snapshot_fresh = (snapshot_fresh and generated.tzinfo is not None
                          and generated.utcoffset() is not None and generated <= now)
    except (TypeError, ValueError):
        snapshot_fresh = False
    eod_required = now.astimezone(TZ).date() >= COM19031_EFFECTIVE
    receipt = (snapshot.get("postclose_eod_source")
               if isinstance(snapshot.get("postclose_eod_source"), dict) else {})
    eod_ready = not eod_required or (snapshot_fresh and verified_current_source(receipt, generated))
    snapshot_current = snapshot_fresh and snapshot.get("status") == "VERIFIED" and eod_ready
    findings, next_actions = [], []
    if not snapshot_fresh or snapshot.get("status") == "INSUFFICIENT_EVIDENCE":
        findings.append("POSTCLOSE_SNAPSHOT_MISSING_OR_STALE")
        next_actions.append("REGENERATE_READ_ONLY_POSTCLOSE_SNAPSHOT")
    if snapshot_fresh and eod_required and not eod_ready:
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
    if snapshot.get("urgent_alerts"):
        findings.extend(f"SNAPSHOT_ALERT:{item}" for item in snapshot["urgent_alerts"])
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
        "status": ("VERIFIED" if snapshot_current else EOD_PENDING
                   if snapshot_fresh and snapshot.get("status") == EOD_PENDING
                   and not eod_ready else "INSUFFICIENT_EVIDENCE"),
        "postclose_eod_source": receipt if eod_required else {"state": "NOT_APPLICABLE"},
        "paper_safety_status": snapshot.get("paper_safety_status"),
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
