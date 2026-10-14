#!/usr/bin/env python3
"""Bounded RC6 market watch. Remote mode is strictly read-only and offline."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

SCHEMA = "rc6.market-watch-readonly.v1"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
ROOT = Path("/opt/porota-trading")
DB_REL = Path("data/paper_v17/observer_v17.db")
SEVERITY = {"GREEN": 0, "P2": 1, "P1": 2, "P0": 3}
CONTAINERS = ("porota_production_observer", "porota_production_dashboard",
              "porota_critical_approval_rc6")
TRACKED = ("paper_decisions", "trade_gate_evaluations", "paper_fills",
           "paper_events", "market_snapshots", "scalping_candidates")
MAX_JSON_BYTES = 1024 * 1024


def now_utc():
    return datetime.now(timezone.utc)


def timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def age_seconds(value, at):
    parsed = timestamp(value)
    return None if parsed is None else round((at - parsed).total_seconds(), 3)


def canonical_digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def load_bounded(path, maximum=MAX_JSON_BYTES):
    """No symlinks, large files, unbounded loads or leaked file contents."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            return None
        with path.open("rb") as handle:
            content = handle.read(maximum + 1)
        if len(content) > maximum:
            return None
        value = json.loads(content)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def read_shadow_status(path):
    value = load_bounded(path, 262144)
    if not value or not isinstance(value.get("payload"), dict):
        return None
    try:
        if value.get("digest") != canonical_digest(value["payload"]):
            return None
    except (ValueError, TypeError):
        return None
    return value["payload"]


def finding(code, severity, *, evidence=None):
    record = {"code": code, "severity": severity}
    if evidence is not None and isinstance(evidence, (int, float, str, bool)):
        record["evidence"] = evidence if not isinstance(evidence, str) else evidence[:160]
    return record


def active_hours(at):
    local = at.astimezone(ART)
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and 10 * 60 + 30 <= minute < 17 * 60 + 15


def _docker_inspect():
    command = ["docker", "inspect", "--format",
               "{{.Name}}|{{.State.Status}}|{{.RestartCount}}|{{.State.OOMKilled}}|{{.Image}}",
               *CONTAINERS]
    try:
        result = subprocess.run(command, text=True, capture_output=True,
                                check=False, timeout=5)
        if result.returncode != 0 or len(result.stdout) > 4096:
            return None
        parsed = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) != 5:
                return None
            name, state, restarts, oom, image = parts
            parsed[name.lstrip("/")] = {
                "state": state, "restarts": int(restarts),
                "oom": oom.lower() == "true", "image": image,
            }
        return parsed if set(parsed) == set(CONTAINERS) else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _resources():
    value = os.statvfs("/")
    data = {"disk_free_bytes": value.f_bavail * value.f_frsize,
            "inode_free_pct": round(100 * value.f_favail / max(1, value.f_files), 2)}
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    data["mem_available_bytes"] = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError):
        pass
    return data


def _db_snapshot(path):
    """Finite SQLite reads only; no full scans, schema changes or writes."""
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=0.5) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=400")
        stop_at = time.monotonic() + 2.0
        db.set_progress_handler(lambda: int(time.monotonic() > stop_at), 1000)
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "observer_state" not in tables:
            raise sqlite3.DatabaseError("OBSERVER_STATE_MISSING")
        state = db.execute("SELECT mode,process_state,session_state,ppi_auth,"
                           "real_orders_sent,heartbeat_at,last_market_data_at "
                           "FROM observer_state WHERE id=1").fetchone()
        observer = dict(state) if state else {}
        workers = {}
        for name in ("intraday_scalping_worker_state", "paper_supervisor_state",
                     "paper_caucion_cash_sweep_state", "paper_notification_worker"):
            if name in tables:
                try:
                    data = db.execute("SELECT * FROM " + name + " WHERE id=1").fetchone()
                    if data:
                        workers[name] = {k: data[k] for k in
                                         ("state", "heartbeat_at", "real_orders_sent",
                                          "selected", "successful", "failed", "candidates")
                                         if k in data.keys()}
                except sqlite3.Error:
                    workers[name] = {"state": "NO_VERIFICADO"}
        cursors = {}
        for name in TRACKED:
            if name not in tables:
                cursors[name] = None
                continue
            try:
                row = db.execute("SELECT id FROM " + name +
                                 " ORDER BY id DESC LIMIT 1").fetchone()
                cursors[name] = int(row[0]) if row else 0
            except sqlite3.Error:
                cursors[name] = None
        readiness = None
        if "candidate_identity_v2" in tables:
            try:
                row = db.execute("SELECT COUNT(*) total,"
                    "SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE' "
                    "THEN 1 ELSE 0 END) ready FROM candidate_identity_v2").fetchone()
                readiness = {"total": int(row["total"]),
                             "ready": int(row["ready"] or 0)}
            except sqlite3.Error:
                pass
        return {"observer": observer, "workers": workers,
                "cursors": cursors, "readiness": readiness}


def probe(*, root=ROOT, expected_sha, at=None, inspect=None):
    at = at or now_utc()
    root = Path(root)
    findings = []
    result = {"schema": SCHEMA, "generated_at": at.isoformat(), "scope": "READ_ONLY",
              "expected_deploy_sha": expected_sha, "market_clock_window": active_hours(at),
              "broker_requests": 0, "real_order_routes_called": 0,
              "ppi_watch_accessed": False, "findings": findings}
    def add(code, level, evidence=None):
        findings.append(finding(code, level, evidence=evidence))
    path = root / DB_REL
    try:
        state = _db_snapshot(path)
        result.update(state)
        observer = state["observer"]
        if observer.get("mode") != "PRODUCTION_PAPER":
            add("RUNTIME_MODE_INVALID", "P0")
        counter = observer.get("real_orders_sent")
        if type(counter) is not int or counter != 0:
            add("REAL_ORDER_COUNTER_INVALID", "P0")
        heartbeat_age = age_seconds(observer.get("heartbeat_at"), at)
        result["observer_heartbeat_age_seconds"] = heartbeat_age
        if heartbeat_age is None or not 0 <= heartbeat_age <= 180:
            add("OBSERVER_HEARTBEAT_INVALID", "P0")
        if str(observer.get("ppi_auth") or "").upper() not in {"OK", "AUTHENTICATED"}:
            add("PPI_AUTH_NOT_OK", "P1" if result["market_clock_window"] else "P2")
        market_open_observed = str(observer.get("session_state") or "").upper() in {
            "OPEN", "MARKET_OPEN", "TRADING"}
        result["market_open_observed"] = market_open_observed
        market_age = age_seconds(observer.get("last_market_data_at"), at)
        result["ppi_market_age_seconds"] = market_age
        if market_open_observed and (market_age is None or market_age > 900 or market_age < -30):
            add("PPI_MARKETDATA_NOT_FRESH", "P1")
        for name, worker in state["workers"].items():
            lag = age_seconds(worker.get("heartbeat_at"), at)
            if lag is None or not 0 <= lag <= 300:
                add("WORKER_HEARTBEAT_INVALID:" + name, "P1")
            if "real_orders_sent" in worker and (
                    type(worker["real_orders_sent"]) is not int or worker["real_orders_sent"] != 0):
                add("WORKER_REAL_ORDER_COUNTER_INVALID:" + name, "P0")
    except (sqlite3.Error, OSError, ValueError, TypeError):
        add("SQLITE_READ_OR_SCHEMA_UNAVAILABLE", "P0")
        result.update(observer={}, workers={}, cursors={}, readiness=None)
        market_open_observed = False

    provenance = load_bounded(root / "data/deploy/CURRENT_STATE_V2.json", 32768)
    result["provenance"] = {k: provenance.get(k) for k in (
        "deploy_sha", "candidate_sha", "image_id", "validation_status", "mode",
        "real_orders_sent", "real_order_routes")} if provenance else {}
    if (not provenance or provenance.get("deploy_sha") != expected_sha or
            provenance.get("mode") != "PRODUCTION_PAPER" or
            provenance.get("validation_status") != "VALIDATED_RUNTIME" or
            type(provenance.get("real_orders_sent")) is not int or
            provenance["real_orders_sent"] != 0 or
            provenance.get("real_order_routes") != "NOT_CALLED"):
        add("DEPLOY_PROVENANCE_INVALID", "P0")

    containers = _docker_inspect() if inspect is None else inspect
    result["containers"] = containers or {}
    if not containers or not provenance:
        add("CONTAINER_OR_IMAGE_UNVERIFIED", "P0")
    else:
        for name in CONTAINERS:
            state = containers.get(name, {})
            if state.get("state") != "running" or state.get("oom") is True:
                add("CONTAINER_NOT_HEALTHY:" + name, "P0")
            if state.get("image") != provenance.get("image_id"):
                add("CONTAINER_IMAGE_MISMATCH:" + name, "P0")
            if state.get("restarts", 0) > 0:
                add("CONTAINER_RESTART_COUNT:" + name, "P2", state["restarts"])

    shadow_path = root / "data/paper_v17/artifacts/observer_v17.db/dynamic-shadow/status.json"
    shadow = read_shadow_status(shadow_path)
    result["shadow"] = {k: shadow.get(k) for k in (
        "as_of", "status", "phase", "entry_signal_lab_status",
        "operational_funnel_status", "real_orders_sent", "real_routes",
        "provider_requests", "error_class")} if shadow else {}
    if shadow is None:
        add("DYNAMIC_SHADOW_STATUS_MISSING_OR_INVALID",
            "P1" if market_open_observed else "P2")
    else:
        if (type(shadow.get("real_orders_sent")) is not int or
                shadow["real_orders_sent"] != 0 or
                shadow.get("real_routes") != "NOT_CALLED"):
            add("DYNAMIC_SHADOW_SAFETY_INVALID", "P0")
        lag = age_seconds(shadow.get("as_of"), at)
        result["shadow_age_seconds"] = lag
        if shadow.get("status") == "FAIL_CLOSED":
            add("DYNAMIC_SHADOW_FAIL_CLOSED", "P1",
                result["shadow"].get("error_class"))
        elif lag is None or lag < -30 or lag > 180:
            add("DYNAMIC_SHADOW_NOT_PROGRESSING",
                "P1" if market_open_observed else "P2")

    perf_path = root / ("data/paper_v17/observer_v17.db.performance.sqlite.latest.json")
    perf = load_bounded(perf_path, 524288)
    result["performance"] = {"present": bool(perf), "generated_at": perf.get("generated_at"),
                             "edge_validated": perf.get("economic_edge_validated"),
                             "forward_status": (perf.get("forward_signal_evaluation") or {}).get("status")
                             } if perf else {"present": False}
    if not perf:
        add("PERFORMANCE_REPORT_UNAVAILABLE",
            "P1" if market_open_observed else "P2")
    else:
        lag = age_seconds(perf.get("generated_at"), at)
        result["performance_age_seconds"] = lag
        if lag is None or lag < -30 or lag > 900:
            add("PERFORMANCE_REPORT_STALE",
                "P1" if market_open_observed else "P2")

    try:
        resources = _resources()
        result["resources"] = resources
        if resources["disk_free_bytes"] < 2 * 1024**3 or resources["inode_free_pct"] < 10:
            add("DISK_CRITICAL", "P0")
        elif resources["disk_free_bytes"] < 3 * 1024**3:
            add("DISK_LOW", "P1")
        elif resources["disk_free_bytes"] < 8 * 1024**3:
            add("DISK_BELOW_TARGET", "P2")
        if resources.get("mem_available_bytes", 2**63) < 64 * 1024**2:
            add("MEMORY_PRESSURE", "P1")
    except (OSError, ValueError, ZeroDivisionError):
        add("RESOURCE_METRICS_UNAVAILABLE", "P2")

    result["findings"] = sorted(findings, key=lambda x: (-SEVERITY[x["severity"]], x["code"]))
    result["severity"] = max((x["severity"] for x in findings),
                             key=lambda value: SEVERITY[value], default="GREEN")
    return result


def transport_error():
    return {"schema": SCHEMA, "generated_at": now_utc().isoformat(), "scope": "READ_ONLY",
            "findings": [finding("REMOTE_PROBE_UNAVAILABLE", "P0")],
            "severity": "P0", "data_verified": False, "ppi_watch_accessed": False,
            "real_order_routes_called": 0}


def shadow_diagnostic():
    """Bounded, read-only probes in the existing observer image.

    The result contains only stage names, durations, and SQLite error codes.
    Exception messages, SQL parameters, paths, and market data never leave
    the host. This does not invoke ShadowRuntime.tick or any writer.
    """
    at = now_utc()
    database = Path(os.getenv(
        "PAPER_V17_DB_PATH", "/app/data/paper_v17/observer_v17.db"))
    result = {"schema": "rc6.shadow-readonly-diagnostic.v1",
              "generated_at": at.isoformat(), "read_only": True,
              "stages": []}
    try:
        from rc6_dynamic_universe.runtime import read_runtime
        from rc6_shadow_runtime.worker import ShadowRuntime
        stages = (
            ("RUNTIME_INPUT", lambda: read_runtime(
                database, as_of=at, row_limit=20000, query_budget_seconds=.35)),
            ("METADATA", lambda: ShadowRuntime._metadata(
                type("ReadOnlyShadow", (), {"database": database.resolve(strict=True)})(),
                at, at.isoformat())),
        )
        for name, fn in stages:
            started = time.monotonic()
            try:
                fn()
                stage = {"stage": name, "status": "OK"}
            except Exception as exc:
                stage = {"stage": name, "status": "FAIL_CLOSED",
                         "error_class": type(exc).__name__}
                if isinstance(exc, sqlite3.Error):
                    code = getattr(exc, "sqlite_errorcode", None)
                    label = getattr(exc, "sqlite_errorname", None)
                    if type(code) is int:
                        stage["sqlite_errorcode"] = code
                    if isinstance(label, str) and label.startswith("SQLITE_"):
                        stage["sqlite_errorname"] = label[:48]
            stage["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
            result["stages"].append(stage)
    except Exception as exc:
        result["setup_error_class"] = type(exc).__name__
    return result


def shadow_diagnostic_host():
    """Use the already authorized root Python entry to execute an isolated probe.

    The host never emits Docker stderr, exception messages, paths or SQL. A
    subprocess timeout bounds the entire container probe independently of SSH.
    """
    child = '''import json, os, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
at=datetime.now(timezone.utc)
db=Path(os.getenv("PAPER_V17_DB_PATH", "/app/data/paper_v17/observer_v17.db"))
result={"schema":"rc6.shadow-readonly-diagnostic.v1","generated_at":at.isoformat(),"read_only":True,"stages":[]}
try:
 from rc6_dynamic_universe.runtime import read_runtime
 from rc6_shadow_runtime.worker import ShadowRuntime
 stages=(("RUNTIME_INPUT",lambda:read_runtime(db,as_of=at,row_limit=20000,query_budget_seconds=.35)),("METADATA",lambda:ShadowRuntime._metadata(type("ReadOnlyShadow",(),{"database":db.resolve(strict=True)})(),at,at.isoformat())))
 for name,fn in stages:
  start=time.monotonic()
  try:
   fn(); stage={"stage":name,"status":"OK"}
  except Exception as exc:
   stage={"stage":name,"status":"FAIL_CLOSED","error_class":type(exc).__name__}
   if isinstance(exc,sqlite3.Error):
    code=getattr(exc,"sqlite_errorcode",None); label=getattr(exc,"sqlite_errorname",None)
    if type(code) is int: stage["sqlite_errorcode"]=code
    if isinstance(label,str) and label.startswith("SQLITE_"): stage["sqlite_errorname"]=label[:48]
  stage["elapsed_ms"]=round((time.monotonic()-start)*1000,3)
  result["stages"].append(stage)
 plans={}
 queries=(
  ("CATALOG_READY", "SELECT ticker FROM financial_instrument_catalog WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER%' ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?", (20001,)),
  ("CATALOG_FULL", "SELECT ticker FROM financial_instrument_catalog ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?", (20001,)),
  ("INTRADAY", "SELECT event_at FROM ppi_intraday_points WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?) AND julianday(first_received_at)<=julianday(?) AND julianday(last_verified_at)<=julianday(?) ORDER BY julianday(event_at) DESC LIMIT ?", (at.isoformat(),)*4+(20001,)),
  ("SNAPSHOTS", "SELECT observed_at FROM market_snapshots WHERE julianday(observed_at)<=julianday(?) AND julianday(observed_at)>=julianday(?) ORDER BY id DESC LIMIT ?", (at.isoformat(),at.isoformat(),20001)),
  ("EVENTS", "SELECT event_at FROM paper_events WHERE event_type IN ('INTRADAY_SCALPING_UNSUPPORTED','INTRADAY_SCALPING_ERROR') AND julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?) ORDER BY id DESC LIMIT 200", (at.isoformat(),at.isoformat())))
 with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True,timeout=.005) as c:
  c.execute("PRAGMA query_only=ON")
  for name,query,params in queries:
   try: plans[name]=[str(row[3])[:120] for row in c.execute("EXPLAIN QUERY PLAN "+query,params)]
   except sqlite3.Error as exc: plans[name]=[getattr(exc,"sqlite_errorname","SQLITE_ERROR")]
  try:
   result["observer_cycles"]=[{"started_at":r[0],"finished_at":r[1],"selected_count":r[2],"duration_seconds":r[3]} for r in c.execute("SELECT started_at,finished_at,selected_count,duration_seconds FROM universe_cycle_metrics ORDER BY id DESC LIMIT 16")]
  except sqlite3.Error: result["observer_cycles_status"]="UNAVAILABLE"
 result["query_plans"]=plans
except Exception as exc: result["setup_error_class"]=type(exc).__name__
print(json.dumps(result,separators=(",",":")))'''
    try:
        completed = subprocess.run(
            ["docker", "exec", "porota_production_observer", "python", "-B", "-c", child],
            capture_output=True, text=True, timeout=9, check=False)
        if completed.returncode == 0:
            data = json.loads(completed.stdout)
            if (data.get("schema") == "rc6.shadow-readonly-diagnostic.v1"
                    and data.get("read_only") is True):
                for label, unit in (("legacy_health_timer", "porota-functional-health-rc6.timer"),
                                    ("fast_health_timer", "porota-fast-functional-health-rc6.timer")):
                    try:
                        state = subprocess.run(["systemctl", "is-active", unit],
                            capture_output=True, text=True, timeout=.8, check=False)
                        data[label] = (state.stdout.strip() if state.stdout.strip() in
                            {"active", "inactive", "failed", "activating"} else "UNKNOWN")
                    except (OSError, subprocess.TimeoutExpired):
                        data[label] = "UNKNOWN"
                try:
                    state = subprocess.run(["docker", "inspect", "--format", "{{json .State}}",
                        "porota_production_observer"], capture_output=True, text=True,
                        timeout=.8, check=False)
                    info = json.loads(state.stdout) if state.returncode == 0 else {}
                    data["observer_container"] = {name: info.get(name) for name in
                        ("Status", "StartedAt", "FinishedAt", "ExitCode", "OOMKilled")}
                except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
                    data["observer_container"] = {"status": "UNKNOWN"}
                return data
        status = "CONTAINER_PROBE_FAILED"
    except subprocess.TimeoutExpired:
        status = "CONTAINER_PROBE_TIMEOUT"
    except (OSError, ValueError, TypeError):
        status = "CONTAINER_PROBE_UNAVAILABLE"
    return {"schema": "rc6.shadow-readonly-diagnostic.v1",
            "status": status, "read_only": True}


def classify(current, previous=None, previous_decision=None):
    """Escalate P0 now, P1 after consecutive comparable observations."""
    if current.get("schema") != SCHEMA:
        raise ValueError("CURRENT_SNAPSHOT_SCHEMA_INVALID")
    at = timestamp(current.get("generated_at"))
    if at is None:
        raise ValueError("CURRENT_SNAPSHOT_CLOCK_INVALID")
    prev_time = timestamp((previous or {}).get("generated_at"))
    comparable = (prev_time is not None and
                  0 < (at - prev_time).total_seconds() <= 30 * 60 and
                  current.get("expected_deploy_sha") ==
                  (previous or {}).get("expected_deploy_sha"))
    p_now = {x["code"] for x in current.get("findings", []) if x["severity"] == "P0"}
    p1_now = {x["code"] for x in current.get("findings", []) if x["severity"] == "P1"}
    p1_prev = ({x["code"] for x in previous.get("findings", [])
                if x["severity"] == "P1"} if comparable else set())
    confirmed = p_now | (p1_now & p1_prev)
    old_alerted = set(previous_decision.get("alerted", [])) if comparable and previous_decision else set()
    new_alerts = sorted(confirmed - old_alerted)
    recovered = sorted(old_alerted - confirmed - (p1_now - p1_prev))
    alerted = sorted(confirmed | (old_alerted & p1_now))
    status = "P0" if p_now else "P1" if p1_now else current.get("severity", "GREEN")
    return {"schema": SCHEMA, "generated_at": current["generated_at"],
            "status": status, "confirmed": sorted(confirmed),
            "alerted": alerted, "new_alerts": new_alerts,
            "recovered": recovered, "pending_confirmation": sorted(p1_now - p1_prev),
            "notification": "ALERT" if new_alerts else "RECOVERY" if recovered else "NONE",
            "previous_comparable": comparable,
            "read_only": True, "real_orders_sent_verified": bool(
                current.get("observer", {}).get("real_orders_sent") == 0
                and type(current.get("observer", {}).get("real_orders_sent")) is int)}


def _read_optional(path):
    if not path:
        return None
    return load_bounded(Path(path), MAX_JSON_BYTES)


def main(argv=None):
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    remote = actions.add_parser("probe")
    remote.add_argument("--expected-sha", required=True)
    remote.add_argument("--root", type=Path, default=ROOT)
    actions.add_parser("transport-error")
    actions.add_parser("shadow-diagnostic")
    actions.add_parser("shadow-diagnostic-host")
    evaluation = actions.add_parser("classify")
    evaluation.add_argument("--current", required=True)
    evaluation.add_argument("--previous")
    evaluation.add_argument("--previous-decision")
    args = parser.parse_args(argv)
    if args.action == "probe":
        if not (len(args.expected_sha) == 40 and
                all(x in "0123456789abcdef" for x in args.expected_sha)):
            raise SystemExit("INVALID_EXPECTED_SHA")
        data = probe(root=args.root, expected_sha=args.expected_sha)
    elif args.action == "transport-error":
        data = transport_error()
    elif args.action == "shadow-diagnostic":
        data = shadow_diagnostic()
    elif args.action == "shadow-diagnostic-host":
        data = shadow_diagnostic_host()
    else:
        current = _read_optional(args.current)
        if not current:
            current = transport_error()
        data = classify(current, _read_optional(args.previous),
                        _read_optional(args.previous_decision))
    print(json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
