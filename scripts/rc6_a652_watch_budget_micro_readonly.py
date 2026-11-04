#!/usr/bin/env python3
"""A652: one bounded, strictly sanitized RC6 host micro-probe.

The host probe receives its entire program over stdin, requires the A617
private read-only mount, reuses A617's paper workspace/mount guards, reads
only aggregate scanner events, and enumerates systemd unit *names* in memory.
No PPI SDK, HTTP, orders, lease, runtime modification, or PPI Watch mutation.
 Existing A516's `sidecar_kind` is reused via in-memory module, so even a
 missing sidecar is checked with lstat only (never opened or initialized).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote


HOST_SCHEMA = "rc6.a652.watch-budget-scanner-host-ro.v1"
RECEIPT_SCHEMA = "rc6.a652.watch-budget-micro-sanitized.v1"
DATA_ROOT = Path("/opt/porota-trading/data")
DB = DATA_ROOT / "paper_v17/observer_v17.db"
MAX_EVENTS = 512
ALLOWED_EVENT_TYPES = ("DATA_ERROR", "DATA_REJECTED", "HTTP_BLOCKED", "PPI_SESSION_INVALID")
ERROR_BUCKETS = ("PPI_BUDGET_BACKPRESSURE", "PPI_SESSION_INVALID",
                 "PPI_INSTRUMENT_NOT_FOUND", "PPI_EMPTY_OR_NON_JSON_AFTER_RETRY",
                 "PPI_HTTP_4XX", "PPI_HTTP_5XX", "PPI_HTTP_OTHER",
                 "PPI_OTHER_CLASSIFIED", "OTHER")
SCANNER_STATES = {"OBSERVED", "NO_VERIFICADO"}
WATCH_STATES = {"ENUMERATED_NO_MATCH", "ENUMERATED_UNREGISTERED_CANDIDATE",
                "ENUMERATED_AMBIGUOUS", "ENUMERATION_UNAVAILABLE"}
OBSERVER_MODES = {"PRODUCTION_PAPER", "SIMULATION", "UNKNOWN"}
PROCESS_STATES = {"RUNNING", "DEGRADED", "READY_PREOPEN", "WAITING_MARKET", "STOPPED", "UNKNOWN"}
SESSION_STATES = {"MARKET_OPEN", "MARKET_CLOSED", "PREOPEN", "UNKNOWN"}
AUTH_STATES = {"OK", "ERROR", "UNKNOWN"}
_SAFE_UNIT = re.compile(r"^[A-Za-z0-9_.@-]{1,128}\.service$")
_HTTP_CODE = re.compile(r"^PPI_HTTP_([1-5][0-9]{2})$")
_SAFE_CLASS = re.compile(r"^PPI_[A-Z0-9_]{1,64}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def _bounded_int(value, maximum=1_000_000_000):
    return value if type(value) is int and 0 <= value <= maximum else None


def _reject_duplicate_keys(pairs):
    """Never let a raw host JSON duplicate override a safety guard on runner."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _enum(value, allowed):
    return value if type(value) is str and value in allowed else "UNKNOWN"


def _utc_stamp(value):
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            return None
        return stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError):
        return None


def _a617():
    """Use existing A617 helpers: no copy of its RO mount/PAPER identity rules."""
    qualified = sys.modules.get("scripts.rc6_a617_ppi_origin_selector_readonly")
    if qualified is not None:
        return qualified
    if "rc6_a617_ppi_origin_selector_readonly" in sys.modules:
        return sys.modules["rc6_a617_ppi_origin_selector_readonly"]
    from scripts import rc6_a617_ppi_origin_selector_readonly as module
    return module


def _a516():
    qualified = sys.modules.get("scripts.rc6_a516_host_budget_metadata_ro")
    if qualified is not None:
        return qualified
    if "rc6_a516_host_budget_metadata_ro" in sys.modules:
        return sys.modules["rc6_a516_host_budget_metadata_ro"]
    from scripts import rc6_a516_host_budget_metadata_ro as module
    return module


def _sidecar_probe(db=DB, *, ro_verified=False):
    """Use A516 lstat classification only; no sidecar read or runtime inference."""
    result = {"state": "NO_VERIFICADO", "sidecar_kind": "UNAVAILABLE",
              "diagnosis": "RO_MOUNT_UNVERIFIED", "snapshot_status": "NO_VERIFICADO",
              "effective_policy": "NO_VERIFICADO", "active_leases": "NO_VERIFICADO"}
    if not ro_verified:
        return result
    sidecar = db.parent / "artifacts" / db.name / "ppi-budget/global.sqlite"
    try:
        # A516 guards the final component; refuse intermediate alias paths too.
        parents = [sidecar.parent, sidecar.parent.parent, sidecar.parent.parent.parent]
        kind = "UNSAFE_OR_ALIAS" if any(p.is_symlink() for p in parents) else _a516().sidecar_kind(sidecar)
    except (OSError, TypeError, ValueError):
        kind = "UNAVAILABLE"
    result["state"] = "METADATA_ONLY"
    result["sidecar_kind"] = kind
    result["diagnosis"] = ({"ABSENT": "SIDECAR_ABSENT",
                           "REGULAR_SINGLE_LINK": "METADATA_ONLY_NOT_BUDGET_AUTHORITY",
                           "UNSAFE_OR_ALIAS": "SIDECAR_PATH_UNSAFE",
                           "UNAVAILABLE": "SIDECAR_METADATA_UNAVAILABLE"})[kind]
    return result


def _systemctl_list(args, runner):
    """Return bounded in-memory public metadata; stderr and raw data never emit."""
    try:
        completed = runner(["systemctl", *args], check=False,
                           capture_output=True, text=True, timeout=3,
                           env={"PATH": "/usr/bin:/bin"})
        if completed.returncode or len(completed.stdout) > 262144:
            return None
        return completed.stdout
    except (OSError, subprocess.SubprocessError, TypeError, AttributeError):
        return None


def _watch_probe(runner=subprocess.run):
    """Discovery only: product policy has no registered expected unit names.

    A PPI+Watch name match is *not* identity or quota attribution. No ExecStart,
    fragment, systemd properties, process argv, env, paths, tokens or hostnames.
    """
    result = {"state": "ENUMERATION_UNAVAILABLE", "candidate_units": None,
              "active_candidates": None, "identity": "NO_VERIFICADO",
              "external_provider_quota": "NO_VERIFICADO",
              "expected_unit_names_registered": False}
    files = _systemctl_list(["list-unit-files", "--type=service", "--no-legend", "--no-pager"], runner)
    if files is None:
        return result
    names = set()
    for line in files.splitlines():
        fields = line.split()
        if fields and _SAFE_UNIT.fullmatch(fields[0]) and all(
                token in fields[0].lower() for token in ("ppi", "watch")):
            names.add(fields[0])
    result["candidate_units"] = min(len(names), 64)
    result["state"] = ("ENUMERATED_NO_MATCH" if not names else
                       "ENUMERATED_UNREGISTERED_CANDIDATE" if len(names) == 1 else
                       "ENUMERATED_AMBIGUOUS")
    units = _systemctl_list(["list-units", "--all", "--type=service",
                             "--no-legend", "--plain", "--no-pager"], runner)
    if units is not None:
        active = 0
        for line in units.splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[0] in names and fields[2] == "active":
                active += 1
        result["active_candidates"] = min(active, 64)
    return result


def _error_bucket(detail):
    if not isinstance(detail, str) or len(detail) > 2048:
        return "OTHER"
    suffix = detail.rpartition(": ")[2]
    if suffix in ERROR_BUCKETS[:4]:
        return suffix
    match = _HTTP_CODE.fullmatch(suffix)
    if match:
        return "PPI_HTTP_4XX" if suffix[9] == "4" else (
            "PPI_HTTP_5XX" if suffix[9] == "5" else "PPI_HTTP_OTHER")
    return "PPI_OTHER_CLASSIFIED" if _SAFE_CLASS.fullmatch(suffix) else "OTHER"


def _events(conn, start, finish):
    """Only last cycle; never export symbols, detail, raw errors or quotes."""
    report = {"state": "NO_VERIFICADO", "sampled": 0,
              "sample_truncated": False,
              "counts": {name: 0 for name in ALLOWED_EVENT_TYPES},
              "data_error_reasons": {name: 0 for name in ERROR_BUCKETS}}
    rows = conn.execute(
        "SELECT event_type, detail FROM paper_events "
        "WHERE event_at >= ? AND event_at <= ? "
        "AND event_type IN ('DATA_ERROR','DATA_REJECTED','HTTP_BLOCKED','PPI_SESSION_INVALID') "
        "ORDER BY id DESC LIMIT ?", (start, finish, MAX_EVENTS + 1)).fetchall()
    report["sample_truncated"] = len(rows) > MAX_EVENTS
    for event_type, detail in rows[:MAX_EVENTS]:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError("EVENT_UNEXPECTED_TYPE")
        report["counts"][event_type] += 1
        if event_type == "DATA_ERROR":
            report["data_error_reasons"][_error_bucket(detail)] += 1
    report["sampled"] = min(len(rows), MAX_EVENTS)
    report["state"] = "OBSERVED"
    return report


def _db_probe(db=DB, *, enforce_ro_mount=True):
    result = {"state": "NO_VERIFICADO", "reason": "DB_RO_OR_PAPER_IDENTITY_UNAVAILABLE",
              "private_ro_mount_verified": False, "paper_db_identity_verified": False,
              "cycle": {"state": "NO_VERIFICADO"}, "observer": {"state": "NO_VERIFICADO"},
              "events": {"state": "NO_VERIFICADO"}}
    a617 = _a617()
    if (not db.is_absolute() or db.is_symlink() or
            (enforce_ro_mount and not a617._mount_is_readonly(db))):
        return result
    result["private_ro_mount_verified"] = bool(enforce_ro_mount)
    conn = None
    started = time.monotonic()
    try:
        conn = sqlite3.connect("file:" + quote(str(db), safe="/") + "?mode=ro",
                               uri=True, timeout=.3, isolation_level=None)
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(lambda: int(time.monotonic() - started > 3.0), 2000)
        tables = a617._tables(conn)
        if not a617._verified_paper_db(conn, tables):
            return result
        result["paper_db_identity_verified"] = True
        if not {"universe_cycle_metrics", "observer_state", "paper_events"} <= tables:
            result["reason"] = "REQUIRED_TABLES_UNAVAILABLE"
            return result
        columns = {t: {r[1] for r in conn.execute("PRAGMA table_info(" + t + ")")}
                   for t in ("universe_cycle_metrics", "observer_state", "paper_events")}
        if not {"started_at", "finished_at", "eligible_total", "selected_count",
                "successful_count", "failed_count"} <= columns["universe_cycle_metrics"]:
            result["reason"] = "CYCLE_COLUMNS_UNAVAILABLE"
            return result
        if not {"id", "mode", "process_state", "session_state", "ppi_auth",
                "http_allowed", "http_blocked", "real_orders_sent"} <= columns["observer_state"]:
            result["reason"] = "OBSERVER_COLUMNS_UNAVAILABLE"
            return result
        if not {"id", "event_at", "event_type", "detail"} <= columns["paper_events"]:
            result["reason"] = "EVENT_COLUMNS_UNAVAILABLE"
            return result
        cycle = conn.execute(
            "SELECT started_at,finished_at,eligible_total,selected_count,"
            "successful_count,failed_count FROM universe_cycle_metrics "
            "ORDER BY id DESC LIMIT 1").fetchone()
        if not cycle:
            result["reason"] = "CYCLE_ROW_ABSENT"
            return result
        begin, finish = cycle[:2]
        if _utc_stamp(begin) is None or _utc_stamp(finish) is None:
            result["reason"] = "CYCLE_CLOCK_UNVERIFIED"
            return result
        result["cycle"] = {"state": "OBSERVED", "started_at_utc": _utc_stamp(begin),
                           "finished_at_utc": _utc_stamp(finish),
                           "eligible_total": _bounded_int(cycle[2]),
                           "selected_count": _bounded_int(cycle[3]),
                           "successful_count": _bounded_int(cycle[4]),
                           "failed_count": _bounded_int(cycle[5]),
                           "cold_plan": a617._cycle_report(conn, tables)}
        if None in [result["cycle"][x] for x in (
                "eligible_total", "selected_count", "successful_count", "failed_count")]:
            result["reason"] = "CYCLE_COUNTER_UNVERIFIED"
            return result
        row = conn.execute(
            "SELECT mode,process_state,session_state,ppi_auth,http_allowed,"
            "http_blocked,real_orders_sent FROM observer_state ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            result["observer"] = {
                "state": "OBSERVED", "mode": _enum(row[0], OBSERVER_MODES),
                "process_state": _enum(row[1], PROCESS_STATES),
                "session_state": _enum(row[2], SESSION_STATES),
                "ppi_auth": _enum(row[3], AUTH_STATES),
                "http_allowed_counter": _bounded_int(row[4]),
                "http_blocked_counter": _bounded_int(row[5]),
                "runtime_real_orders_sent": _bounded_int(row[6]),
                "http_counter_semantics": "PRE_SEND_COUNTER_NOT_PROVIDER_SUCCESS"}
        result["events"] = _events(conn, begin, finish)
        error_count = result["events"]["counts"]["DATA_ERROR"]
        result["events"]["metric_reconciliation"] = (
            "SAMPLED_TRUNCATED" if result["events"]["sample_truncated"] else
            "MATCH" if error_count == result["cycle"]["failed_count"] else
            "MISMATCH_REQUIRES_RCA")
        result["state"] = "OBSERVED"
        result["reason"] = "AGGREGATE_ONLY_NO_PROVIDER_OR_ADMISSION_AUTHORITY"
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError):
        result["state"] = "NO_VERIFICADO"
        result["reason"] = "DB_READ_OR_SCHEMA_UNAVAILABLE"
    finally:
        if conn is not None:
            conn.close()
    return result


def host_probe(*, db=DB, runner=subprocess.run, enforce_ro_mount=True):
    scanner = _db_probe(db, enforce_ro_mount=enforce_ro_mount)
    return {"schema": HOST_SCHEMA, "read_only": True,
            "provider_gets": 0, "budget_leases": 0, "orders_called": False,
            "ppi_watch_mutations": 0, "host_file_writes": 0,
            "scanner": scanner,
            "budget": _sidecar_probe(db, ro_verified=scanner["private_ro_mount_verified"]),
            "watch": _watch_probe(runner)}


def _sanitized_host(raw):
    """Project exclusively fixed enums, timestamps, booleans and bounded counters."""
    if (not isinstance(raw, dict) or raw.get("schema") != HOST_SCHEMA or
            raw.get("read_only") is not True or
            type(raw.get("provider_gets")) is not int or raw["provider_gets"] != 0 or
            type(raw.get("budget_leases")) is not int or raw["budget_leases"] != 0 or
            raw.get("orders_called") is not False or
            type(raw.get("ppi_watch_mutations")) is not int or raw["ppi_watch_mutations"] != 0 or
            type(raw.get("host_file_writes")) is not int or raw["host_file_writes"] != 0):
        raise ValueError("HOST_GUARD_FAILED")
    scan = raw.get("scanner", {})
    budget = raw.get("budget", {})
    watch = raw.get("watch", {})
    if not isinstance(scan, dict) or not isinstance(watch, dict) or not isinstance(budget, dict):
        raise ValueError("HOST_SCHEMA_FAILED")
    if scan.get("state") not in {"OBSERVED", "NO_VERIFICADO"}:
        raise ValueError("HOST_SCANNER_STATE_FAILED")
    out = {"state": scan["state"],
           "private_ro_mount_verified": scan.get("private_ro_mount_verified") is True,
           "paper_db_identity_verified": scan.get("paper_db_identity_verified") is True,
           "reason": _enum(scan.get("reason"), {
               "DB_RO_OR_PAPER_IDENTITY_UNAVAILABLE", "REQUIRED_TABLES_UNAVAILABLE",
               "CYCLE_COLUMNS_UNAVAILABLE", "OBSERVER_COLUMNS_UNAVAILABLE",
               "EVENT_COLUMNS_UNAVAILABLE", "CYCLE_ROW_ABSENT",
               "CYCLE_CLOCK_UNVERIFIED", "CYCLE_COUNTER_UNVERIFIED",
               "DB_READ_OR_SCHEMA_UNAVAILABLE",
               "AGGREGATE_ONLY_NO_PROVIDER_OR_ADMISSION_AUTHORITY"})}
    cycle = scan.get("cycle", {})
    if not isinstance(cycle, dict):
        raise ValueError("CYCLE_NOT_OBJECT")
    out["cycle"] = {"state": _enum(cycle.get("state"), SCANNER_STATES)}
    if out["cycle"]["state"] == "OBSERVED":
        for field in ("started_at_utc", "finished_at_utc"):
            out["cycle"][field] = _utc_stamp(cycle.get(field))
        for field in ("eligible_total", "selected_count", "successful_count", "failed_count"):
            out["cycle"][field] = _bounded_int(cycle.get(field))
        if any(x is None for x in out["cycle"].values()):
            raise ValueError("CYCLE_MISSING_VALID_FIELDS")
        plan = cycle.get("cold_plan", {})
        if isinstance(plan, dict):
            out["cycle"]["cold_bootstrap_state"] = _enum(
                plan.get("reported_cold_bootstrap_state"), {
                    "NO_COLD_BOOTSTRAP_AUTHORITY", "NO_BASELINE_COLD_SLOT",
                    "BASELINE_COLD_SLOT_AVAILABLE", "ELIGIBLE_SELECTOR_READ_HOLD"})
            out["cycle"]["active_budget_state"] = _enum(
                plan.get("reported_active_budget_state"), {"FEASIBLE", "INFEASIBLE"})
            out["cycle"]["cold_slots"] = _bounded_int(plan.get("reported_cold_slots"), 100000)
    observer = scan.get("observer", {})
    if not isinstance(observer, dict):
        raise ValueError("OBSERVER_NOT_OBJECT")
    out["observer"] = {"state": _enum(observer.get("state"), SCANNER_STATES)}
    if out["observer"]["state"] == "OBSERVED":
        for field, allowed in (("mode", OBSERVER_MODES), ("process_state", PROCESS_STATES),
                               ("session_state", SESSION_STATES), ("ppi_auth", AUTH_STATES)):
            out["observer"][field] = _enum(observer.get(field), allowed)
        for field in ("http_allowed_counter", "http_blocked_counter", "runtime_real_orders_sent"):
            out["observer"][field] = _bounded_int(observer.get(field))
        out["observer"]["counter_semantics"] = "PRE_SEND_NOT_SUCCESSFUL_GET"
    events = scan.get("events", {})
    if not isinstance(events, dict):
        raise ValueError("EVENTS_NOT_OBJECT")
    out["events"] = {"state": _enum(events.get("state"), SCANNER_STATES)}
    if out["events"]["state"] == "OBSERVED":
        out["events"]["sampled"] = _bounded_int(events.get("sampled"), MAX_EVENTS)
        out["events"]["sample_truncated"] = events.get("sample_truncated") is True
        out["events"]["metric_reconciliation"] = _enum(
            events.get("metric_reconciliation"), {
                "SAMPLED_TRUNCATED", "MATCH", "MISMATCH_REQUIRES_RCA"})
        for field, allowed in (("counts", ALLOWED_EVENT_TYPES),
                               ("data_error_reasons", ERROR_BUCKETS)):
            values = events.get(field, {})
            if not isinstance(values, dict):
                raise ValueError("EVENT_BUCKETS_NOT_OBJECT")
            out["events"][field] = {key: _bounded_int(values.get(key), MAX_EVENTS)
                                    for key in allowed}
        if (out["events"]["sampled"] is None or
                out["events"]["metric_reconciliation"] == "UNKNOWN" or
                any(value is None for section in ("counts", "data_error_reasons")
                    for value in out["events"][section].values())):
            raise ValueError("EVENT_AGGREGATES_UNVERIFIED")
    out["watch"] = {"state": _enum(watch.get("state"), WATCH_STATES),
                    "candidate_units": _bounded_int(watch.get("candidate_units"), 64),
                    "active_candidates": _bounded_int(watch.get("active_candidates"), 64),
                    "identity": "NO_VERIFICADO", "quota_usage": "NO_VERIFICADO",
                    "expected_unit_names_registered": False, "mutation_attempted": False}
    if (out["watch"]["state"] != "ENUMERATION_UNAVAILABLE" and
            (out["watch"]["candidate_units"] is None or
             out["watch"]["active_candidates"] is None)):
        raise ValueError("WATCH_COUNT_UNVERIFIED")
    out["budget"] = {"state": _enum(budget.get("state"), {"METADATA_ONLY", "NO_VERIFICADO"}),
                     "sidecar_kind": _enum(budget.get("sidecar_kind"), {
                         "ABSENT", "REGULAR_SINGLE_LINK", "UNSAFE_OR_ALIAS", "UNAVAILABLE"}),
                     "diagnosis": _enum(budget.get("diagnosis"), {
                         "SIDECAR_ABSENT", "METADATA_ONLY_NOT_BUDGET_AUTHORITY",
                         "SIDECAR_PATH_UNSAFE", "SIDECAR_METADATA_UNAVAILABLE",
                         "RO_MOUNT_UNVERIFIED"}),
                     "snapshot_status": "NO_VERIFICADO", "effective_policy": "NO_VERIFICADO",
                     "active_leases": "NO_VERIFICADO"}
    if (out["state"] == "OBSERVED" and not
            (out["private_ro_mount_verified"] and out["paper_db_identity_verified"])):
        raise ValueError("RO_MOUNT_OR_DB_IDENTITY_MISSING")
    return out


def build_receipt(host_raw, *, run_sha="", run_id=""):
    """A malicious/oversized remote payload cannot escape this allowlist."""
    receipt = {"schema": RECEIPT_SCHEMA,
               "code_sha": run_sha if _SHA40.fullmatch(run_sha) else "NO_VERIFICADO",
               "run_id": str(run_id) if str(run_id).isdigit() and len(str(run_id)) < 24 else "NO_VERIFICADO",
               "state": "NO_VERIFICADO", "read_only": True,
               "probe_provider_gets": 0, "probe_budget_leases": 0,
               "probe_host_file_writes": 0, "probe_real_orders": 0,
               "ppi_watch_mutation_attempted": False, "paper_ready": False,
               "runtime_paper_ready": "NO_VERIFICADO",
               "ppi_watch_identity": "NO_VERIFICADO",
               "ppi_watch_external_quota_usage": "NO_VERIFICADO",
               "live_provider_quota_attribution": "NO_VERIFICADO"}
    try:
        receipt["host"] = _sanitized_host(host_raw)
    except (ValueError, TypeError, AttributeError):
        receipt["host"] = {"state": "NO_VERIFICADO", "reason": "INVALID_OR_UNAVAILABLE_HOST_OUTPUT"}
    receipt["budget"] = receipt["host"].get("budget", {
        "state": "NO_VERIFICADO", "diagnosis": "RO_MOUNT_UNVERIFIED"})
    observed = receipt["host"]
    if (observed.get("state") == "OBSERVED" and
            observed.get("observer", {}).get("state") == "OBSERVED" and
            observed.get("events", {}).get("state") == "OBSERVED" and
            observed.get("watch", {}).get("state") in {
                "ENUMERATED_NO_MATCH", "ENUMERATED_UNREGISTERED_CANDIDATE",
                "ENUMERATED_AMBIGUOUS"} and
            receipt["budget"].get("state") == "METADATA_ONLY" and
            receipt["budget"].get("sidecar_kind") in {"ABSENT", "REGULAR_SINGLE_LINK"}):
        receipt["state"] = "DIAGNOSTIC_OBSERVED_NO_PAPER_GO"
    else:
        receipt["state"] = "INCOMPLETE_NO_GO"
    payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) >= 16384:
        raise ValueError("SANITIZED_OUTPUT_EXCEEDS_16_KB")
    return receipt


def _runner_transport():
    """In-memory module loading, no remote file creation/import-time pyc."""
    parent = Path(__file__).resolve().parent
    a617_source = (parent / "rc6_a617_ppi_origin_selector_readonly.py").read_text(encoding="utf-8")
    a516_source = (parent / "rc6_a516_host_budget_metadata_ro.py").read_text(encoding="utf-8")
    a652_source = Path(__file__).read_text(encoding="utf-8")
    print("import sys,types")
    print("_source_a617=" + repr(a617_source))
    print("_module_a617=types.ModuleType('rc6_a617_ppi_origin_selector_readonly')")
    print("exec(compile(_source_a617,'a617-in-memory','exec'),_module_a617.__dict__)")
    print("sys.modules['rc6_a617_ppi_origin_selector_readonly']=_module_a617")
    print("_source_a516=" + repr(a516_source))
    print("_module_a516=types.ModuleType('rc6_a516_host_budget_metadata_ro')")
    print("exec(compile(_source_a516,'a516-in-memory','exec'),_module_a516.__dict__)")
    print("sys.modules['rc6_a516_host_budget_metadata_ro']=_module_a516")
    print("_source_a652=" + repr(a652_source))
    print("exec(compile(_source_a652,'a652-in-memory','exec'),{'__name__':'__main__'})")


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--prepare-transport"]:
        _runner_transport()
        return 0
    if args == ["host-probe"]:
        print(json.dumps(host_probe(), sort_keys=True, separators=(",", ":")))
        return 0
    if len(args) == 3 and args[0] == "--sanitize":
        _, host_path, output_path = args
        try:
            host_file = Path(host_path)
            if host_file.stat().st_size > 16384:
                raise ValueError("RAW_RESPONSE_UNBOUNDED")
            host_raw = json.loads(host_file.read_text(encoding="utf-8"),
                                  object_pairs_hook=_reject_duplicate_keys)
        except (OSError, ValueError, UnicodeError):
            host_raw = {}
        receipt = build_receipt(host_raw,
                                run_sha=os.getenv("GITHUB_SHA", ""),
                                run_id=os.getenv("GITHUB_RUN_ID", ""))
        Path(output_path).write_text(json.dumps(receipt, sort_keys=True,
                                                separators=(",", ":")) + "\n", encoding="utf-8")
        print("A652_MICRO_READ_ONLY=" + receipt["state"] +
              "|host=" + receipt["host"]["state"] +
              "|budget=" + receipt["budget"]["state"] +
              "|provider_gets=0|orders=0|watch_mutations=0|paper_ready=false")
        return 0 if receipt["state"] == "DIAGNOSTIC_OBSERVED_NO_PAPER_GO" else 2
    raise SystemExit("A652_INVALID_MODE")


if __name__ == "__main__":
    raise SystemExit(main())
