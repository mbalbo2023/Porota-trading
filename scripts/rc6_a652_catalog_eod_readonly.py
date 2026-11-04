#!/usr/bin/env python3
"""A652: opt-in host-only EOD catalog receipt, aggregate READ_ONLY.

No PPI client, provider GET, broker, order call, Watch, catalog write or WAL
copy. A private OS read-only bind mount is mandatory against the live WAL DB;
SQLite URI mode=ro + PRAGMA query_only are necessary but not sufficient.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from urllib.parse import quote
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a652.catalog-eod-readonly.v1"
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
ART = ZoneInfo("America/Argentina/Buenos_Aires")
TARGET_DAY = date(2026, 11, 4)
EVENT = "COMPLEMENTARY_CATALOG_RECONCILIATION"
MAX_RAW = 32768
MAX_COUNT = 1000000
FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "ON", "OBLIGACIONES", "OPCIONES",
    "FUTUROS", "CAUCIONES", "FCI", "ETFS", "ETF", "INDICES", "INDICES_USA",
    "ACCIONES_USA",
})
COUNTERS = ("promoted_this_run", "inserted_this_run", "refreshed_this_run",
            "ready_total", "pending_total", "pending_contract_retry")


def _base():
    return {"schema": SCHEMA, "state": "NO_VERIFICADO", "reason": "DB_RO_UNAVAILABLE",
            "read_only": True, "db_opened": False, "private_ro_mount": False,
            "paper_workspace_verified": False, "provider_gets": 0,
            "orders_called": False, "ppi_watch_touched": False,
            "paper_admission_certified": False, "runtime_sha_verified": False,
            "real_orders_sent": None, "caucion_hold_latest": "NO_VERIFICADO",
            "event": {"state": "NO_VERIFICADO"},
            "projection": {"state": "NO_VERIFICADO"},
            "catalog": {"state": "NO_VERIFICADO"},
            "errors": {"catalog_reconciliation_unavailable": 0,
                       "caucion_catalog_reconcile_error": 0}}


def _mount_is_readonly(path):
    chosen = -1
    readonly = False
    try:
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            fields = line.split(" - ", 1)[0].split()
            if len(fields) < 6:
                continue
            point = fields[4].replace("\\040", " ")
            if point != "/" and path != Path(point) and not str(path).startswith(point.rstrip("/") + "/"):
                continue
            if len(point) > chosen:
                chosen = len(point)
                readonly = "ro" in fields[5].split(",")
    except (OSError, UnicodeError, ValueError):
        return False
    return chosen >= 0 and readonly


def _stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return dt.astimezone(ART) if dt.utcoffset() is not None else None


def _after_closed(dt):
    return bool(dt and dt.date() == TARGET_DAY and dt.hour >= 18)


def _columns(conn, name):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({name})")}


def _trusted_workspace(conn):
    needed = {"id", "namespace", "origin", "imported_legacy", "dataset_id"}
    if not needed <= _columns(conn, "paper_workspace"):
        return False
    rows = conn.execute("SELECT id,namespace,origin,imported_legacy,dataset_id "
                        "FROM paper_workspace LIMIT 2").fetchall()
    if len(rows) != 1:
        return False
    try:
        return (rows[0][:4] == (1, "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0)
                and str(uuid.UUID(rows[0][4])) == rows[0][4])
    except (TypeError, ValueError, AttributeError):
        return False


def _bounded(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNT else None


def _event_report(at, detail):
    result = {"state": "NO_VERIFICADO", "reason": "EOD_EVENT_NOT_OBSERVED"}
    stamp = _stamp(at)
    if not _after_closed(stamp) or not isinstance(detail, str) or len(detail) > 8192:
        return result
    counters = {}
    for key in COUNTERS:
        match = re.search(r"(?:^|; )" + re.escape(key) + r"=(\d+)(?:; |$)", detail)
        value = int(match.group(1)) if match else None
        if _bounded(value) is None:
            return {**result, "reason": "EOD_EVENT_CONTRACT_HOLD"}
        counters[key] = value
    family_match = re.search(r"(?:^|; )ready_by_family=(\{[^;]{0,2048}\})(?:; |$)", detail)
    if not family_match:
        return {**result, "reason": "EOD_EVENT_FAMILY_HOLD"}
    try:
        raw_families = json.loads(family_match.group(1))
    except (TypeError, ValueError):
        return {**result, "reason": "EOD_EVENT_FAMILY_HOLD"}
    if (not isinstance(raw_families, dict) or len(raw_families) > len(FAMILIES)
            or any(k not in FAMILIES or _bounded(v) is None
                   for k, v in raw_families.items())):
        return {**result, "reason": "EOD_EVENT_FAMILY_HOLD"}
    if sum(raw_families.values()) != counters["ready_total"]:
        return {**result, "reason": "EOD_READY_COUNTS_MISMATCH"}
    return {"state": "READ_ONLY_OBSERVED", "local_at": stamp.isoformat(),
            **counters, "ready_by_family": dict(sorted(raw_families.items()))}


def _catalog_report(conn):
    columns = _columns(conn, "financial_instrument_catalog")
    needed = {"instrument_type", "status", "capability", "metadata_json"}
    if not needed <= columns:
        return {"state": "NO_VERIFICADO", "reason": "CATALOG_SCHEMA_HOLD"}
    result = {}
    rows = conn.execute("""SELECT instrument_type,COUNT(*),
      SUM(CASE WHEN status='AVAILABLE' AND capability LIKE 'READY_PAPER_%'
               THEN 1 ELSE 0 END),
      SUM(CASE WHEN json_valid(metadata_json) THEN
             CASE WHEN json_type(metadata_json,'$.financial_contract_v17')='object'
                  THEN 1 ELSE 0 END ELSE 0 END)
      FROM financial_instrument_catalog GROUP BY instrument_type""").fetchall()
    totals = {"total": 0, "ready": 0, "v17_declared": 0}
    for family, count, ready, v17 in rows:
        if family not in FAMILIES or any(_bounded(value) is None for value in (count, ready, v17)):
            return {"state": "NO_VERIFICADO", "reason": "CATALOG_FAMILY_HOLD"}
        result[family] = {"total": count, "ready": ready, "v17_declared": v17}
        for key, value in (("total", count), ("ready", ready), ("v17_declared", v17)):
            totals[key] += value
    if any(_bounded(value) is None for value in totals.values()):
        return {"state": "NO_VERIFICADO", "reason": "CATALOG_COUNT_HOLD"}
    return {"state": "READ_ONLY_OBSERVED", "totals": totals, "by_family": dict(sorted(result.items()))}


def diagnose(db=DB, *, enforce_ro_mount=True):
    report = _base()
    connection = None
    try:
        if not db.is_absolute() or db.is_symlink():
            report["reason"] = "DB_PATH_HOLD"
            return report
        if enforce_ro_mount and not _mount_is_readonly(db):
            report["reason"] = "PRIVATE_SQLITE_RO_MOUNT_REQUIRED"
            return report
        report["private_ro_mount"] = bool(enforce_ro_mount)
        connection = sqlite3.connect("file:" + quote(str(db), safe="/") + "?mode=ro",
                                     uri=True, timeout=0.35, isolation_level=None)
        connection.execute("PRAGMA query_only=ON")
        report["db_opened"] = True
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"paper_workspace", "observer_state", "paper_events",
                    "candidate_refresh_state", "financial_instrument_catalog"}
        if not required <= tables or not _trusted_workspace(connection):
            report["reason"] = "PAPER_WORKSPACE_OR_SCHEMA_HOLD"
            return report
        report["paper_workspace_verified"] = True
        state = connection.execute("SELECT real_orders_sent,detail FROM observer_state WHERE id=1").fetchone()
        if not state or _bounded(state[0]) is None:
            report["reason"] = "PAPER_ORDER_COUNTER_HOLD"
            return report
        report["real_orders_sent"] = state[0]
        report["caucion_hold_latest"] = (
            "OBSERVED" if str(state[1] or "").startswith("CAUCION_LEDGER_INVALID:")
            else "NOT_OBSERVED_IN_LATEST_STATE")
        row = connection.execute("SELECT event_at,detail FROM paper_events "
                                 "WHERE event_type=? ORDER BY id DESC LIMIT 1", (EVENT,)).fetchone()
        if row:
            report["event"] = _event_report(*row)
        projection = connection.execute("SELECT state,checked_at FROM candidate_refresh_state WHERE id=1").fetchone()
        if projection and projection[0] in {"COMPLETE", "RUNNING", "FAILED"}:
            marker_at = _stamp(projection[1])
            report["projection"] = {"state": projection[0],
                                     "local_at": marker_at.isoformat() if marker_at else None}
        report["catalog"] = _catalog_report(connection)
        for kind, field in (("COMPLEMENTARY_RECONCILIATION_UNAVAILABLE",
                             "catalog_reconciliation_unavailable"),
                            ("CAUCION_BLOCKED_CATALOG_RECONCILE_ERROR",
                             "caucion_catalog_reconcile_error")):
            # Events are classified only; their arbitrary/private details never leave host.
            count = connection.execute("SELECT COUNT(*) FROM paper_events "
                                       "WHERE event_type=? AND event_at>=?",
                                       (kind, TARGET_DAY.isoformat() + "T18:00:00-03:00")).fetchone()[0]
            report["errors"][field] = _bounded(count)
        if report["real_orders_sent"] != 0:
            report["reason"] = "NONZERO_REAL_ORDERS_HOLD"
        elif report["event"].get("state") != "READ_ONLY_OBSERVED":
            report["reason"] = "EOD_EVENT_NO_VERIFICADO"
        elif report["projection"].get("state") != "COMPLETE":
            report["reason"] = "PROJECTION_NOT_COMPLETE"
        elif (not _after_closed(_stamp(report["projection"].get("local_at")))
              or _stamp(report["projection"]["local_at"]) <
                 _stamp(report["event"]["local_at"])):
            report["reason"] = "PROJECTION_MARKER_NOT_EOD"
        elif report["catalog"].get("state") != "READ_ONLY_OBSERVED":
            report["reason"] = "CATALOG_COUNTS_NO_VERIFICADO"
        else:
            report["state"] = "READ_ONLY_OBSERVED"
            report["reason"] = "EOD_AGGREGATE_ONLY_NOT_PAPER_GO"
    except (sqlite3.Error, OSError, ValueError, OverflowError):
        report["reason"] = "DB_RO_QUERY_UNAVAILABLE"
    finally:
        if connection is not None:
            connection.close()
    return report


def sanitize(untrusted):
    """Runner-side allowlist: never export host detail/symbol/key/path fields."""
    safe = _base()
    if (not isinstance(untrusted, dict) or untrusted.get("schema") != SCHEMA
            or untrusted.get("read_only") is not True
            or untrusted.get("provider_gets") != 0
            or untrusted.get("orders_called") is not False
            or untrusted.get("ppi_watch_touched") is not False
            or untrusted.get("paper_admission_certified") is not False
            or untrusted.get("runtime_sha_verified") is not False):
        safe["reason"] = "UNTRUSTED_HOST_RESPONSE"
        return safe
    for flag in ("db_opened", "private_ro_mount", "paper_workspace_verified"):
        safe[flag] = untrusted.get(flag) is True
    orders = _bounded(untrusted.get("real_orders_sent"))
    safe["real_orders_sent"] = orders
    if untrusted.get("caucion_hold_latest") in {"OBSERVED", "NOT_OBSERVED_IN_LATEST_STATE"}:
        safe["caucion_hold_latest"] = untrusted["caucion_hold_latest"]
    event = untrusted.get("event", {})
    if isinstance(event, dict) and event.get("state") == "READ_ONLY_OBSERVED":
        dt = _stamp(event.get("local_at"))
        counts = {k: _bounded(event.get(k)) for k in COUNTERS}
        family = event.get("ready_by_family")
        if (_after_closed(dt) and all(v is not None for v in counts.values())
                and isinstance(family, dict) and len(family) <= len(FAMILIES)
                and all(k in FAMILIES and _bounded(v) is not None
                        for k, v in family.items())
                and sum(family.values()) == counts["ready_total"]):
            safe["event"] = {"state": "READ_ONLY_OBSERVED", "local_at": dt.isoformat(),
                             **counts, "ready_by_family": dict(sorted(family.items()))}
    projection = untrusted.get("projection", {})
    if isinstance(projection, dict) and projection.get("state") in {"COMPLETE", "RUNNING", "FAILED"}:
        dt = _stamp(projection.get("local_at"))
        safe["projection"] = {"state": projection["state"],
                              "local_at": dt.isoformat() if dt else None}
    catalog = untrusted.get("catalog", {})
    if isinstance(catalog, dict) and catalog.get("state") == "READ_ONLY_OBSERVED":
        totals = catalog.get("totals")
        families = catalog.get("by_family")
        if (isinstance(totals, dict) and isinstance(families, dict)
                and len(families) <= len(FAMILIES)
                and all(k in FAMILIES and isinstance(row, dict) and
                        all(_bounded(row.get(field)) is not None
                            for field in ("total", "ready", "v17_declared"))
                        for k, row in families.items())
                and all(_bounded(totals.get(k)) is not None
                        for k in ("total", "ready", "v17_declared"))
                and all(sum(row[k] for row in families.values()) == totals[k]
                        for k in ("total", "ready", "v17_declared"))):
            safe["catalog"] = {"state": "READ_ONLY_OBSERVED", "totals": {
                k: totals[k] for k in ("total", "ready", "v17_declared")},
                "by_family": {k: {field: families[k][field]
                                  for field in ("total", "ready", "v17_declared")}
                              for k in sorted(families)}}
    errors = untrusted.get("errors", {})
    if isinstance(errors, dict):
        for field in safe["errors"]:
            safe["errors"][field] = _bounded(errors.get(field))
    marker = _stamp(safe["projection"].get("local_at"))
    event_time = _stamp(safe["event"].get("local_at"))
    if (orders == 0 and safe["db_opened"] and safe["private_ro_mount"]
            and safe["paper_workspace_verified"]
            and safe["event"].get("state") == "READ_ONLY_OBSERVED"
            and safe["projection"].get("state") == "COMPLETE"
            and _after_closed(marker) and event_time and marker >= event_time
            and safe["catalog"].get("state") == "READ_ONLY_OBSERVED"):
        safe["state"] = "READ_ONLY_OBSERVED"
        safe["reason"] = "EOD_AGGREGATE_ONLY_NOT_PAPER_GO"
    else:
        safe["reason"] = "EOD_RUNTIME_NOT_CERTIFIED"
    return safe


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["host-probe"]:
        print(json.dumps(diagnose(), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")))
        return 0
    if len(args) == 3 and args[0] == "sanitize":
        raw, target = (Path(args[1]), Path(args[2]))
        report = _base()
        try:
            if raw.stat().st_size <= MAX_RAW:
                report = sanitize(json.loads(raw.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
            pass
        target.write_text(json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n",
                          encoding="utf-8")
        print("A652_EOD_RO_STATE=" + report["state"])
        print("A652_EOD_RO_REASON=" + report["reason"])
        return 0 if report["state"] == "READ_ONLY_OBSERVED" else 2
    raise SystemExit("A652_HOST_PROBE_OR_RUNNER_SANITIZE_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
