"""Publish Action 4 and validation progress from the existing rootless postclose job."""
from argparse import ArgumentParser
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path

from cg_paper_workspace import artifact_root, database_path
from em_validation_campaign_rc6 import MILESTONES, append_records, load_records
import rc6_action4_audit as action4
import rc6_validation_dynamic as dynamic
import rc6_validation_projection as projection
import rc6_validation_runtime as runtime
from rc6_validation_operational_daily import OperationalDailyEvidenceError, collect
from scripts.rc6_source_cycle_dispatch import phase_status


_LEDGER_SOURCE = "ROOTLESS_POSTCLOSE_VALIDATION"


def _campaign_days(ledger):
    days = {}
    for row in reversed(ledger):
        if row.get("source") != _LEDGER_SOURCE:
            continue
        key = (row["date_ar"], row["deployment_sha"])
        day = days.get(key)
        if day is None:
            if len(days) == 10:
                continue
            day = days[key] = {
                "session_date": row["date_ar"], "deployment_sha": row["deployment_sha"],
                "candidate_sha": row["commit_release"], "recorded_at": row["recorded_at"],
                "records": 0, "milestones_current": 0,
                "evidence_ref": row.get("session_evidence_ref") or "",
            }
        day["records"] += 1
        day["milestones_current"] += int(row.get("claim_status") == "VERIFIED_CURRENT"
                                          and row.get("state") == "GREEN")
    return list(days.values())


def _action4_report(path, db, now, release, day):
    previous = runtime.read_object(path)
    origin = previous.get("origin")
    daily = previous.get("dashboard_daily_report")
    if (action4.published_audit_is_safe(previous) and isinstance(origin, dict)
            and origin.get("deployment_sha") == release["deployment_sha"]
            and origin.get("commit") == release["candidate_sha"]
            and isinstance(daily, dict) and daily.get("periodo") == day
            and daily.get("closure_state") == "POSTCLOSE_VERIFIED"):
        return previous
    if path.exists():
        raise ValueError("CAMPAIGN_ACTION4_ARCHIVE_UNVERIFIED")
    report = action4.build(db_path=db, now=now)
    if report["dashboard_daily_report"]["closure_state"] != "POSTCLOSE_VERIFIED":
        raise ValueError("CAMPAIGN_PAPER_POSTCLOSE_UNVERIFIED")
    report["origin"] = {"job": "porota-snapshot-postclose-rc6.service",
                        "run_id": "SYSTEMD_POSTCLOSE", "commit": release["candidate_sha"],
                        "deployment_sha": release["deployment_sha"]}
    projection._write_atomic(path, report)
    return report


def publish(proof, *, now=None, db_path=None, root=None):
    now = now or datetime.now(timezone.utc)
    due, reason = phase_status(now, "postclose_snapshot")
    if not due:
        return {"state": "NOT_DUE", "reason": reason}
    db = Path(db_path or database_path())
    observer = dynamic._observer_evidence(db)
    if (observer.get("mode") != "PRODUCTION_PAPER"
            or type(observer.get("real_orders_sent")) is not int or observer["real_orders_sent"] != 0
            or observer.get("session_state") != "MARKET_CLOSED"):
        raise ValueError("CAMPAIGN_PAPER_POSTCLOSE_UNVERIFIED")
    evidence = runtime.observations(db.parent.parent, observer, proof,
                                    build=os.getenv("POROTA_BUILD_SHA"),
                                    tree=os.getenv("POROTA_CANDIDATE_TREE_SHA"), now=now)
    release = evidence["release"]
    if not release:
        raise ValueError("CAMPAIGN_DEPLOY_RECEIPT_UNVERIFIED")

    root = projection.snapshot_path(root).parent
    root.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(projection.TZ_AR).date().isoformat()
    report_root = artifact_root(db) / "reports"
    archive = report_root / "validation-action4" / day / f"{release['deployment_sha']}.json"
    report_ref = str(archive.relative_to(report_root.parent))
    with (root / ".daily-publication.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        report = _action4_report(archive, db, now, release, day)
        ledger = load_records(root, verify=True)
        completed = [row for row in ledger if row.get("source") == _LEDGER_SOURCE
                     and row.get("date_ar") == day and row.get("deployment_sha") == release["deployment_sha"]
                     and row.get("commit_release") == release["candidate_sha"]]
        previous = projection._read_raw(root) or {}
        if (len(completed) == len(MILESTONES) and previous.get("release") == release
                and previous.get("session_date") == day
                and previous.get("ledger_head_sha256") == ledger[-1]["record_hash"]
                and runtime.read_object(report_root / action4.FILENAME) == report):
            return {"state": "UNCHANGED", "session_date": day, "records": len(completed)}

        operational = collect(db_path=db, limit_days=10)
        activity = [row for row in operational["days"] if row["date_ar"] <= day]
        if not completed:
            rows = dynamic.evaluate(ledger, runtime=evidence, observer=observer)
            rows = projection._apply_daily_operational_audit(rows, now=now,
                                                             operational={"days": activity},
                                                             published_only=True)
            rows["M8"].update(
                state="YELLOW", implementation_status="IMPLEMENTED", work_status="IN_PROGRESS",
                observed_evidence="Action 4 de la rueda publicada. Firmas humanas pendientes.",
                blocker="Registrar revisión y decisión por jornada.",
                next_action="Revisar resultados y lecciones sobre el recibo de cierre.",
                evidence_ref=report_ref,
            )
            payloads = [{**rows[m.code], "milestone": m.code, "date_ar": day,
                         "commit_release": release["candidate_sha"], "deployment_sha": release["deployment_sha"],
                         "source": _LEDGER_SOURCE, "decision": rows[m.code]["work_status"],
                         "lesson": " · ".join(report["dashboard_daily_report"]["lessons"]) if m.code == "M8" else "",
                         "session_evidence_ref": report_ref, "evaluated_at": now.isoformat(),
                         "evidence_at": rows[m.code].get("evidence_at") or now.isoformat(),
                         "evidence_ref": rows[m.code]["evidence_ref"] or report_ref}
                        for m in MILESTONES]
            completed, ledger = append_records(payloads, root, idempotent=True, now=now)
        if {row["milestone"] for row in completed} != {m.code for m in MILESTONES}:
            raise ValueError("CAMPAIGN_DAILY_RECORDS_INCOMPLETE")
        rows = {row["milestone"]: row for row in completed}
        latest = {
            "schema_version": projection.SCHEMA_VERSION, "generated_at": now.isoformat(),
            "source": "rc6-validation-projection-worker", "audit_kind": "POSTCLOSE_READ_ONLY",
            "ledger_status": "FULLY_VERIFIED", "ledger_head_sha256": ledger[-1]["record_hash"],
            "session_date": day, "release": release, "action4_ref": report_ref,
            "milestones": rows, "summary": dynamic.summary(rows),
            "activity": activity, "campaign_days": _campaign_days(ledger), "real_money_state": "BLOCKED",
        }
        projection._write_atomic(report_root / action4.FILENAME, report)
        projection._write_atomic(projection.snapshot_path(root), latest)
    return {"state": "PUBLISHED", "session_date": day, "records": len(completed)}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-proof", required=True)
    args = parser.parse_args()
    try:
        result = publish(json.loads(args.runtime_proof))
    except (OSError, ValueError, TypeError, OperationalDailyEvidenceError) as exc:
        # Campaign errors contain no raw rows or provider values.
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("CAMPAIGN_") else type(exc).__name__
        print(json.dumps({"state": "BLOCKED", "reason": reason}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
