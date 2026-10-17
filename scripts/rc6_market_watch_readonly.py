#!/usr/bin/env python3
"""Bounded RC6 market watch. Remote mode is strictly read-only and offline."""
from __future__ import annotations

import argparse
import base64
import io
import zipfile
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
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


def previous_artifact_candidates(current_run, own_artifacts, runs, artifacts_by_run):
    """Retry artifacts precede other runs; never accept an in-flight artifact."""
    result = []
    for run_id, artifacts in [(current_run, own_artifacts)] + [
            (run.get("id"), artifacts_by_run.get(run.get("id"), []))
            for run in runs if run.get("id") != current_run
            and run.get("status") == "completed"]:
        if type(run_id) is not int:
            continue
        for artifact in sorted(artifacts, key=lambda a: (a.get("created_at", ""),
                                                          a.get("id", 0)), reverse=True):
            if (artifact.get("name") == f"rc6-market-watch-{run_id}"
                    and artifact.get("expired") is False
                    and type(artifact.get("id")) is int
                    and artifact.get("workflow_run", {}).get("id") == run_id):
                result.append((run_id, artifact))
    return result


def verified_previous_zip(raw, artifact, current):
    """Accept only a digest-matched, complete, earlier comparable observation."""
    digest = artifact.get("digest", "")
    if (not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or len(raw) > 128 * 1024 or hashlib.sha256(raw).hexdigest() != digest[7:]):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = {part.filename: part for part in archive.infolist()}
            if not {"snapshot.json", "decision.json"} <= members.keys():
                return None
            if any(m.file_size > MAX_JSON_BYTES or m.is_dir() or
                   (m.external_attr >> 16) & 0o170000 == 0o120000
                   for m in members.values()):
                return None
            previous = json.loads(archive.read("snapshot.json"))
            decision = json.loads(archive.read("decision.json"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError):
        return None
    if (not isinstance(previous, dict) or not isinstance(decision, dict)
            or previous.get("schema") != SCHEMA or decision.get("schema") != SCHEMA
            or previous.get("generated_at") != decision.get("generated_at")
            or previous.get("expected_deploy_sha") != current.get("expected_deploy_sha")):
        return None
    at, before = timestamp(current.get("generated_at")), timestamp(previous.get("generated_at"))
    if at is None or before is None or not 0 < (at - before).total_seconds() <= 1800:
        return None
    return previous, decision


def read_previous_artifact(current, repo, workflow_id, run_id, destination, *, get=None):
    """READ_ONLY GitHub API retrieval with bounded transfers and no secret output."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("INVALID_REPOSITORY")
    if type(workflow_id) is not int or type(run_id) is not int:
        raise ValueError("INVALID_RUN_ID")
    if get is None:
        def get(url, limit):
            prefix = "https://api.github.com/"
            if not url.startswith(prefix + "repos/" + repo + "/actions/"):
                raise ValueError("INVALID_GITHUB_ENDPOINT")
            # The runner's gh transport is already used by the canonical
            # monitor. Python urlopen timed out on the same Actions API even
            # when gh completed promptly. Never put GH_TOKEN on argv.
            with tempfile.TemporaryFile() as output:
                completed = subprocess.run(["gh", "api", url[len(prefix):]],
                    stdout=output, stderr=subprocess.DEVNULL, timeout=25,
                    check=False)
                if completed.returncode:
                    raise OSError("GITHUB_API_UNAVAILABLE")
                output.seek(0)
                raw = output.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("GITHUB_RESPONSE_TOO_LARGE")
            return raw
    base = f"https://api.github.com/repos/{repo}"
    try:
        own = json.loads(get(f"{base}/actions/runs/{run_id}/artifacts?per_page=100", 256 * 1024))
        def candidates():
            # A re-run can confirm against its own previous attempt without
            # listing any unrelated workflow runs.
            yield from previous_artifact_candidates(
                run_id, own.get("artifacts", []), [], {})
            listed = json.loads(get(
                f"{base}/actions/workflows/{workflow_id}/runs?branch=main&per_page=8",
                160 * 1024))
            for candidate in listed.get("workflow_runs", []):
                other_id = candidate.get("id")
                if candidate.get("status") != "completed" or other_id == run_id:
                    continue
                try:
                    artifacts = json.loads(get(
                        f"{base}/actions/runs/{other_id}/artifacts?per_page=20",
                        64 * 1024)).get("artifacts", [])
                except (OSError, ValueError, TypeError, json.JSONDecodeError,
                        subprocess.TimeoutExpired):
                    continue
                yield from previous_artifact_candidates(
                    run_id, [], [candidate], {other_id: artifacts})
        for source_run, artifact in candidates():
            if artifact.get("size_in_bytes", 0) > 128 * 1024:
                continue
            try:
                raw = get(f"{base}/actions/artifacts/{artifact['id']}/zip", 128 * 1024)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                continue
            previous = verified_previous_zip(raw, artifact, current)
            if previous is None:
                continue
            destination.mkdir(parents=True, exist_ok=True)
            for name, data in zip(("snapshot.json", "decision.json"), previous):
                (destination / name).write_text(json.dumps(data, sort_keys=True) + "\n")
            receipt = {"run_id": source_run, "artifact_id": artifact["id"],
                       "artifact_sha256": artifact["digest"],
                       "product_sha": previous[0]["expected_deploy_sha"],
                       "generated_at": previous[0]["generated_at"]}
            (destination / "source.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
            return receipt
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.TimeoutExpired):
        pass
    return None


def now_utc():
    return datetime.now(timezone.utc)


def transport_diagnostic(stderr, exit_code):
    """Never publish SSH text/host/user/key; return only reviewed classes."""
    classes=[('CONNECT_TIMEOUT',r'connection timed out|connect.*timed out'),
             ('CONNECT_REFUSED',r'connection refused'),
             ('NETWORK_UNREACHABLE',r'network is unreachable|no route to host'),
             ('AUTH_DENIED',r'permission denied|authentication failed'),
             ('HOST_KEY_REJECTED',r'host key verification failed|remote host identification has changed'),
             ('DNS_FAILED',r'could not resolve hostname|name or service not known'),
             ('REMOTE_PERMISSION',r'sudo:.*password|not allowed to execute'),
             ('CONNECTION_CLOSED',r'connection (?:closed|reset)|kex_exchange_identification')]
    kind=next((name for name,pattern in classes if re.search(pattern,stderr,re.I)),
              'OK' if exit_code==0 else 'REMOTE_COMMAND_OR_TRANSPORT_UNVERIFIED')
    return {'schema':'rc6.transport-diagnostic.v1','exit_code':exit_code,
            'class':kind,'raw_output_exported':False,'read_only':True}


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


def independent_session(at, root=ROOT):
    """Read the deployed audited calendar in a bounded, separate interpreter.

    This never asks observer_state whether the market ought to be open. The
    checked-in spot schedule is scoped to BYMA PAPER spot, not every family.
    """
    code = '''import json,sys
from datetime import datetime
sys.path.insert(0,sys.argv[1])
import ak_byma_calendar as c
from co_market_sessions_hf6 import TZ,BYMA_PAPER_SPOT_OPEN,BYMA_PAPER_SPOT_CLOSE
at=datetime.fromisoformat(sys.argv[2]).astimezone(TZ)
audited=c.CALENDARIO_ACTUALIZADO_EL
known=at.year in c.ANIOS_AUDITADOS and 0<=(at.date()-audited).days<=60
opened=known and c.es_dia_habil_operativo(at.date()) and BYMA_PAPER_SPOT_OPEN<=at.time().replace(tzinfo=None)<BYMA_PAPER_SPOT_CLOSE
print(json.dumps({'state':'OPEN' if opened else 'CLOSED' if known else 'UNKNOWN','scope':'BYMA_PAPER_SPOT','source':c.FUENTE_OFICIAL,'audited_at':audited.isoformat()}))'''
    try:
        out = subprocess.run([sys.executable, '-B', '-c', code, str(root), at.isoformat()],
                             capture_output=True, text=True, timeout=2, check=True)
        result = json.loads(out.stdout)
        if result.get('state') in {'OPEN','CLOSED','UNKNOWN'}:
            return result
    except (OSError,subprocess.SubprocessError,ValueError):
        pass
    return {'state':'UNKNOWN','scope':'BYMA_PAPER_SPOT','reason':'AUDITED_CALENDAR_UNAVAILABLE'}


def schedule_health(runs, at, *, expected_open):
    """Manual, retry and push runs never certify scheduler cadence."""
    scheduled = sorted({timestamp(r.get('run_started_at')) for r in runs
        if isinstance(r,dict) and r.get('event')=='schedule' and r.get('run_attempt')==1 and r.get('status')=='completed' and
        timestamp(r.get('run_started_at')) is not None and timestamp(r['run_started_at'])<=at}, reverse=True)
    lag = (at-scheduled[0]).total_seconds() if scheduled else None
    recent = [r for r in scheduled if 0 <= (at-r).total_seconds() <= 1800]
    gaps = [(a-b).total_seconds() for a,b in zip(recent,recent[1:])]
    silence = expected_open and (lag is None or lag > 1200)
    return {'state':'SCHEDULE_SILENT' if silence else 'TWO_INTERVALS_OBSERVED' if
            len(gaps)>=2 and all(0<g<=1200 for g in gaps[:2]) else 'NO_VERIFICADO',
            'last_schedule_age_seconds':lag,'genuine_recent_runs':len(recent),
            'consecutive_gaps_seconds':gaps[:3],'silence':bool(silence),
            'independent_watchdog_ack':'NO_VERIFICADO'}


def add_schedule_health(current, repo, *, get=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo):
        raise ValueError('INVALID_REPOSITORY')
    if get is None:
        from urllib.request import Request, urlopen
        def get(url):
            token=os.getenv('GH_TOKEN','')
            headers={'Accept':'application/vnd.github+json'}
            if token: headers['Authorization']='Bearer '+token
            with urlopen(Request(url,headers=headers),timeout=15) as response:
                raw=response.read(2*1024**2+1)
            if len(raw)>2*1024**2: raise ValueError('SCHEDULE_RESPONSE_LIMIT')
            return json.loads(raw)
    expected=(current.get('expected_session') or {}).get('state')=='OPEN'
    try:
        result=get(f'https://api.github.com/repos/{repo}/actions/workflows/384354765/runs?event=schedule&per_page=100')
        health=schedule_health(result['workflow_runs'],timestamp(current['generated_at']),expected_open=expected)
    except (OSError,ValueError,KeyError,TypeError):
        health={'state':'NO_VERIFICADO','reason':'SCHEDULE_API_UNAVAILABLE','silence':None}
    receipt=current.get('schedule_watchdog_receipt') or {}
    ack=str(receipt.get('message_id',''))
    ack_age=receipt.get('ack_age_seconds')
    if (receipt.get('status') in {'OK','SILENT','UNKNOWN'} and ack.isdecimal() and int(ack)>0 and
            type(ack_age) in (int,float) and 0<=ack_age):
        health['independent_watchdog_ack']='HISTORICAL_VERIFIED'
        health['independent_watchdog_ack_age_seconds']=ack_age
    current['schedule_health']=health
    if health.get('silence') is True:
        current['findings'].append(finding('SCHEDULE_SILENCE_TWO_INTERVALS','P1'))
    elif expected and health['state']=='NO_VERIFICADO':
        current['findings'].append(finding('SCHEDULE_CADENCE_UNVERIFIED','P2'))
    current['severity']=max((r['severity'] for r in current['findings']),key=lambda s:SEVERITY[s],default='GREEN')
    return current


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


def _host_control_state():
    """Small host-only facts even when the optional container diagnostic times out."""
    data = {}
    for label, unit in (("legacy_health_timer", "porota-functional-health-rc6.timer"),
                        ("fast_health_timer", "porota-fast-functional-health-rc6.timer"),
                        ("schedule_silence_timer", "porota-market-watch-silence-rc6.timer")):
        try:
            state = subprocess.run(["systemctl", "is-active", unit],
                                   capture_output=True, text=True, timeout=.8, check=False)
            data[label] = (state.stdout.strip() if state.stdout.strip() in
                           {"active", "inactive", "failed", "activating"} else "UNKNOWN")
        except (OSError, subprocess.TimeoutExpired):
            data[label] = "UNKNOWN"
    try:
        state = subprocess.run(["systemctl", "show", "porota-market-watch-silence-rc6.service",
            "--property=Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp",
            "--no-pager"], capture_output=True, text=True, timeout=.8, check=False)
        allowed = {"Result", "ExecMainCode", "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainExitTimestamp"}
        data['schedule_silence_service'] = {k:v[:100] for line in state.stdout[:2048].splitlines()
            for k,sep,v in [line.partition('=')] if sep and k in allowed}
    except (OSError, subprocess.TimeoutExpired):
        data['schedule_silence_service'] = {'Result':'UNKNOWN'}
    try:
        state = subprocess.run(["docker", "inspect", "--format", "{{json .State}}",
                                "porota_production_observer"], capture_output=True, text=True,
                               timeout=.8, check=False)
        info = json.loads(state.stdout) if state.returncode == 0 else {}
        data["observer_container"] = {name: info.get(name) for name in
                                      ("Status", "StartedAt", "FinishedAt", "ExitCode", "OOMKilled")}
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        data["observer_container"] = {"Status": "UNKNOWN"}
    return data


def historical_observer_oom(state, current, at):
    """Prior lifecycle only: unknown clocks/stale pulse never downgrade OOM."""
    detail=(current.get('host_control_state') or {}).get('observer_container') or {}
    started,finished=timestamp(detail.get('StartedAt')),timestamp(detail.get('FinishedAt'))
    heartbeat=timestamp((current.get('observer') or {}).get('heartbeat_at'))
    return bool(state.get('state')=='running' and detail.get('Status')=='running' and
        type(detail.get('ExitCode')) is int and detail['ExitCode']==0 and
        started and finished and finished.year>=2020 and finished<started<=at and
        heartbeat and started<heartbeat<=at and (at-heartbeat).total_seconds()<=180)


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
        sqlite_ops = {}
        if "operational_jobs" in tables:
            try:
                for row in db.execute("SELECT job_key,last_run_at,state FROM operational_jobs "
                        "WHERE job_key IN ('DAILY_BACKUP','HISTORY_BACKUP','SRE_SNAPSHOT')"):
                    sqlite_ops[row["job_key"]] = {"last_run_at": row["last_run_at"], "state": row["state"]}
            except sqlite3.Error:
                sqlite_ops["query"] = "UNAVAILABLE"
        if "backup_runs" in tables:
            try:
                row = db.execute("SELECT created_at,kind,state,size_bytes FROM backup_runs "
                                 "ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    sqlite_ops["latest_backup"] = dict(row)
            except sqlite3.Error:
                sqlite_ops["backup_query"] = "UNAVAILABLE"
        if "candle_worker_state" in tables:
            try:
                row = db.execute("SELECT cursor,heartbeat_at,state FROM candle_worker_state WHERE id=1").fetchone()
                if row:
                    sqlite_ops["candle_worker"] = dict(row)
            except sqlite3.Error:
                sqlite_ops["candle_query"] = "UNAVAILABLE"
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
        # Open ledger contracts are separate from today's trade counts and
        # from an in-flight evaluation/fill, which this reader cannot attest.
        activity = {'inflight_evaluation_or_fill':'NO_VERIFICADO'}
        for table,status in (('paper_positions','OPEN'),('paper_future_positions','ACTIVE'),
                             ('paper_cauciones','OPEN')):
            activity[table] = None
            if table in tables:
                try:
                    activity[table] = db.execute('SELECT COUNT(*) FROM '+table+' WHERE status=?',(status,)).fetchone()[0]
                except sqlite3.Error:
                    pass
        return {"observer": observer, "workers": workers,
                "cursors": cursors, "readiness": readiness, "sqlite_ops": sqlite_ops,
                'paper_open_contracts':activity}


def probe(*, root=ROOT, expected_sha, at=None, inspect=None):
    root = Path(root)
    live_clock = at is None
    started_at = at or now_utc()
    try:
        state = _db_snapshot(root / DB_REL)
    except (sqlite3.Error, OSError, ValueError, TypeError):
        state = None
    # A writer can advance its heartbeat while these bounded SELECTs run.
    # The completed read is the clock reference, not the earlier probe start.
    # Explicit fixture/as-of calls remain strict; no future/stale tolerance is
    # added and no source timestamp is rewritten.
    at = now_utc() if live_clock else started_at
    findings = []
    result = {"schema": SCHEMA, "generated_at": at.isoformat(), "scope": "READ_ONLY",
              "collection_started_at": started_at.isoformat(),
              "expected_deploy_sha": expected_sha, "market_clock_window": active_hours(at),
              "broker_requests": 0, "real_order_routes_called": 0,
              "ppi_watch_accessed": False, "findings": findings}
    def add(code, level, evidence=None):
        findings.append(finding(code, level, evidence=evidence))
    expected = independent_session(at, root)
    result['expected_session'] = expected
    if expected['state']=='UNKNOWN':
        add('MARKET_SESSION_UNKNOWN','P1' if active_hours(at) else 'P2')
    market_should_be_open = expected['state']=='OPEN'
    try:
        if state is None:
            raise ValueError('DATABASE_READ_UNVERIFIED')
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
        if expected['state']!='UNKNOWN' and market_open_observed != market_should_be_open:
            add('OBSERVER_SESSION_DISAGREEMENT','P1')
        market_freshness_required = market_should_be_open or (expected['state']=='UNKNOWN' and market_open_observed)
        market_age = age_seconds(observer.get("last_market_data_at"), at)
        result["ppi_market_age_seconds"] = market_age
        if market_freshness_required and (market_age is None or market_age > 900 or market_age < -30):
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
        market_freshness_required = market_should_be_open

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
    result["host_control_state"] = _host_control_state()
    receipt = load_bounded(root / 'data/paper_v17/market_watch_silence.json',4096)
    ack = str((receipt or {}).get('message_id',''))
    if (receipt and receipt.get('schema')=='rc6.market-watch-silence.v1' and
            receipt.get('status') in {'OK','SILENT','UNKNOWN'} and ack.isdecimal() and int(ack)>0 and
            timestamp(receipt.get('acknowledged_at')) is not None and
            age_seconds(receipt['acknowledged_at'],at)>=0):
        result['schedule_watchdog_receipt'] = {k:receipt[k] for k in
            ('status','acknowledged_at','message_id')}
        result['schedule_watchdog_receipt']['ack_age_seconds'] = age_seconds(receipt['acknowledged_at'],at)
    else:
        result['schedule_watchdog_receipt'] = {'status':'NO_VERIFICADO'}
    if not containers or not provenance:
        add("CONTAINER_OR_IMAGE_UNVERIFIED", "P0")
    else:
        for name in CONTAINERS:
            state = containers.get(name, {})
            if state.get("state") != "running":
                add("CONTAINER_NOT_HEALTHY:" + name, "P0")
            elif state.get('oom') is True:
                if name=='porota_production_observer' and historical_observer_oom(state,result,at):
                    add('OOM_HISTORICAL:'+name,'P2')
                else:
                    add('CONTAINER_NOT_HEALTHY:'+name,'P0')
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
    if shadow:
        if shadow.get('runtime_read_stage') in {'CATALOG_FULL','OPEN_POSITIONS','SCHEMA_TABLES','INTRADAY_CONTRACTS','INTRADAY_POINTS','MARKET_SNAPSHOTS'}:
            result['shadow']['runtime_read_stage']=shadow['runtime_read_stage']
        if type(shadow.get('runtime_deadline_expired')) is bool:
            result['shadow']['runtime_deadline_expired']=shadow['runtime_deadline_expired']
        diagnostics=shadow.get('runtime_sql_diagnostics')
        if isinstance(diagnostics,dict):
            allowed={'CATALOG_FULL','OPEN_POSITIONS','SCHEMA_TABLES','INTRADAY_CONTRACTS','INTRADAY_POINTS','MARKET_SNAPSHOTS'}
            safe=[]
            for stage in diagnostics.get('stages',[])[:6]:
                if not isinstance(stage,dict) or stage.get('stage') not in allowed:
                    continue
                item={'stage':stage['stage']}
                for key in ('elapsed_ms','remaining_ms','rows'):
                    value=stage.get(key)
                    if type(value) in (int,float) and 0<=value<=1000000: item[key]=value
                for key in ('deadline_elapsed','interrupted_by_deadline'):
                    if type(stage.get(key)) is bool: item[key]=stage[key]
                safe.append(item)
            result['shadow']['runtime_sql_stages']=safe
        for key in ("sqlite_errorcode", "error_line"):
            value = shadow.get(key)
            if type(value) is int and 0 <= value <= 1000000:
                result["shadow"][key] = value
        label = shadow.get("sqlite_errorname")
        if isinstance(label, str) and re.fullmatch(r"SQLITE_[A-Z0-9_]{1,40}", label):
            result["shadow"]["sqlite_errorname"] = label
        module = shadow.get("error_module")
        if isinstance(module, str) and module in {"runtime", "worker", "preopen", "promotion",
                "stages", "families", "lab", "entry_signals", "funnel", "source_reads",
                "read_contract", "persistence", "snapshot", "common", "live"}:
            result["shadow"]["error_module"] = module
        if (shadow.get("status") == "FAIL_CLOSED" and
                shadow.get("error_class") == "EvidencePayloadLimit"):
            role = shadow.get("evidence_file")
            measured = shadow.get("evidence_measured_bytes")
            limit = shadow.get("evidence_limit_bytes")
            if (type(role) is str and role in {"latest.json.gz", "checkpoint.json.gz", "status.json", "preopen", "other"}
                    and type(measured) is int and type(limit) is int
                    and limit == 64 * 1024**2 and limit < measured <= 2 * 1024**3):
                result["shadow"].update(evidence_file=role,
                    evidence_measured_bytes=measured, evidence_limit_bytes=limit)
    if shadow is None:
        add("DYNAMIC_SHADOW_STATUS_MISSING_OR_INVALID",
            "P1" if market_freshness_required else "P2")
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
                "P1" if market_freshness_required else "P2")

    perf_path = root / ("data/paper_v17/observer_v17.db.performance.sqlite.latest.json")
    perf = load_bounded(perf_path, 524288)
    result["performance"] = {"present": bool(perf), "generated_at": perf.get("generated_at"),
                             "edge_validated": perf.get("economic_edge_validated"),
                             "forward_status": (perf.get("forward_signal_evaluation") or {}).get("status")
                             } if perf else {"present": False}
    if not perf:
        add("PERFORMANCE_REPORT_UNAVAILABLE",
            "P1" if market_freshness_required else "P2")
    else:
        lag = age_seconds(perf.get("generated_at"), at)
        result["performance_age_seconds"] = lag
        if lag is None or lag < -30 or lag > 900:
            add("PERFORMANCE_REPORT_STALE",
                "P1" if market_freshness_required else "P2")

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


GENERATION_RECEIPT_PROBE = '''
def _committed_generation_receipt():
 import json, math, os, re
 from datetime import datetime, timezone
 from pathlib import Path
 receipt={"schema":"rc6.committed-generation-receipt.v1","integrity":"UNVERIFIED","read_only":True}
 try:
  from rc6_shadow_runtime.persistence import read_committed_generation
  from rc6_shadow_runtime.timing import generation_budget
  db=Path(os.getenv("PAPER_V17_DB_PATH","/app/data/paper_v17/observer_v17.db"))
  root=db.parent/"artifacts"/db.name
  cut=read_committed_generation(root/"dynamic-shadow",payload_limit=4*1024**2,require_current_status=True)
  status=cut["status"]; pointer=cut["pointer"]
  captured=datetime.now(timezone.utc)
  stamp=datetime.fromisoformat(status["as_of"].replace("Z","+00:00"))
  budget,authority=generation_budget(status)
  if stamp.tzinfo is None or authority!="TWO_MEASURED_CYCLES_SOURCE_CAPPED":raise ValueError("GENERATION_CLOCK_UNVERIFIED")
  age=(captured-stamp).total_seconds()
  if not 0<=age<=budget:raise ValueError("GENERATION_FRESHNESS_UNVERIFIED")
  for value in (pointer["generation_id"],pointer["manifest_sha256"],status["configuration_fingerprint"]):
   if not isinstance(value,str) or not re.fullmatch("[0-9a-f]{64}",value):raise ValueError("GENERATION_HASH_UNVERIFIED")
  if (type(pointer["sequence"]) is not int or pointer["sequence"]<=0 or
      status.get("mode")!="SHADOW" or type(status.get("provider_requests")) is not int or status["provider_requests"]!=0):
   raise ValueError("GENERATION_AUTHORITY_UNVERIFIED")
  if status.get("status") not in {"SHADOW_OBSERVING","PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION"}:
   raise ValueError("GENERATION_WORKER_STATE_UNVERIFIED")
  if type(status.get("real_orders_sent")) is not int or status["real_orders_sent"]!=0 or status.get("real_routes")!="NOT_CALLED":
   raise ValueError("GENERATION_SAFETY_UNVERIFIED")
  def number(value):
   return type(value) in (int,float) and math.isfinite(value) and 0<=value<=120000
  raw=status.get("runtime_sql_diagnostics") or {}
  stages=[]
  allowed={"CATALOG_FULL","OPEN_POSITIONS","SCHEMA_TABLES","INTRADAY_CONTRACTS","INTRADAY_POINTS","MARKET_SNAPSHOTS"}
  for row in raw.get("stages",[])[:6]:
   if not isinstance(row,dict) or row.get("stage") not in allowed:continue
   clean={"stage":row["stage"]}
   for key in ("elapsed_ms","remaining_ms","rows"):
    if number(row.get(key)):clean[key]=row[key]
   for key in ("deadline_elapsed","interrupted_by_deadline"):
    if type(row.get(key)) is bool:clean[key]=row[key]
   stages.append(clean)
  elapsed=raw.get("sql_elapsed_ms")
  within=(raw.get("budget_ms")==2000 and number(elapsed) and elapsed<=2000 and bool(stages)
          and all(row.get("deadline_elapsed") is False and row.get("interrupted_by_deadline") is False for row in stages))
  receipt.update(integrity="VERIFIED",captured_at=captured.isoformat(),as_of=status["as_of"],
   generation_id=pointer["generation_id"],sequence=pointer["sequence"],manifest_sha256=pointer["manifest_sha256"],
   configuration_fingerprint=status["configuration_fingerprint"],worker_status=status["status"],
   live_status_required=True,age_seconds=round(age,3),liveness_budget_seconds=budget,
   native_sql_within_budget=within,native_sql_elapsed_ms=elapsed if number(elapsed) else None,
   native_sql_stages=stages,real_orders_sent=0,real_routes="NOT_CALLED",provider_requests=0)
  # Source identity is reported exactly as observed; unknown is never filled
  # from GitHub's expected SHA or from a different container.
  health=root/"runtime-health.json"
  if health.is_file() and not health.is_symlink() and health.stat().st_size<=131072:
   source=json.loads(health.read_text())
   for key in ("source_sha","candidate_tree_sha"):
    value=source.get(key)
    receipt[key]=value if isinstance(value,str) and re.fullmatch("[0-9a-f]{40}",value) else None
 except Exception as exc:
  receipt={"schema":"rc6.committed-generation-receipt.v1","integrity":"UNVERIFIED","read_only":True,"error_class":type(exc).__name__}
 return receipt
generation_receipt=_committed_generation_receipt()
'''


def shadow_diagnostic_host():
    """Use the already authorized root Python entry to execute an isolated probe.

    The host never emits Docker stderr, exception messages, paths or SQL. A
    subprocess timeout bounds the entire container probe independently of SSH.
    """
    child = GENERATION_RECEIPT_PROBE + '''
import json, os, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
at=datetime.now(timezone.utc)
db=Path(os.getenv("PAPER_V17_DB_PATH", "/app/data/paper_v17/observer_v17.db"))
result={"schema":"rc6.shadow-readonly-diagnostic.v1","generated_at":at.isoformat(),"read_only":True,"stages":[]}
result["committed_generation"]=generation_receipt
result["query_shape_authority"]="LEGACY_ROW_CONTROL_NOT_CURRENT_NATIVE_RUNTIME"
try:
 # The prior probe first executed the entire 1.5-second runtime read and
 # then a second 2-second timing pass. Under live load it exceeded the
 # independent 9-second container guard before producing stage evidence.
 # This isolated mode measures the exact SQL stages only once.
 result["stages"]=[{"stage":"RUNTIME_INPUT","status":"NOT_RUN_STAGE_ONLY"}]
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
   def cadence(detail):
    try:
     value=json.loads(detail[detail.index("{"):]).get("active_plan",{}).get("cadence_seconds")
     return value if type(value) in (int,float) and 0 <= value <= 900 else None
    except (ValueError,TypeError,AttributeError): return None
   result["observer_cycles"]=[{"started_at":r[0],"finished_at":r[1],"selected_count":r[2],"duration_seconds":r[3],"planned_cadence_seconds":cadence(r[4])} for r in c.execute("SELECT started_at,finished_at,selected_count,duration_seconds,detail FROM universe_cycle_metrics ORDER BY id DESC LIMIT 16")]
  except sqlite3.Error: result["observer_cycles_status"]="UNAVAILABLE"
 result["query_plans"]=plans
 # One bounded live timing pass over the production query shapes. No values,
 # SQL parameters, result rows, account data or exception messages are exported.
 floor=at.replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
 # Historical row-per-row control, not the current packed native query model.
 # Current native SQL timings come only from the committed generation above.
 observation_limit=3001
 detailed=(
  ("CATALOG_FULL", "SELECT ticker,instrument_type,market,currency,settlement,status,capability FROM financial_instrument_catalog ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?", (20001,)),
  ("OPEN_POSITIONS", "SELECT symbol,asset_class,market,currency,settlement FROM paper_positions WHERE status='OPEN' ORDER BY paper_id LIMIT ?", (20001,)),
  ("CONTRACTS", "SELECT * FROM ppi_intraday_contract_state LIMIT ?", (20001,)),
  ("INTRADAY", "SELECT symbol,asset_class,market,currency,settlement,event_at,first_received_at,last_verified_at,price,volume,source FROM ppi_intraday_points WHERE julianday(event_at)>=julianday(?) AND julianday(event_at)<=julianday(?) AND julianday(first_received_at)<=julianday(?) AND julianday(last_verified_at)<=julianday(?) ORDER BY julianday(event_at) DESC LIMIT ?", (floor,at.isoformat(),at.isoformat(),at.isoformat(),observation_limit)),
  ("SNAPSHOTS", "SELECT symbol,asset_class,market,currency,settlement,last,trade_at,book_at,observed_at,bid,ask,bid_size,ask_size,last_kind FROM market_snapshots NOT INDEXED WHERE id >= (SELECT MIN(id) FROM market_snapshots WHERE julianday(observed_at)<=julianday(?) AND julianday(observed_at)>=julianday(?)) AND julianday(observed_at)<=julianday(?) AND julianday(observed_at)>=julianday(?) ORDER BY id DESC LIMIT ?", (at.isoformat(),floor,at.isoformat(),floor,observation_limit)))
 budget_start=time.monotonic(); deadline=budget_start+2.0
 measurements=[]
 def expired(): return int(time.monotonic()>deadline)
 c.set_progress_handler(expired,1000)
 for name,sql,params in detailed:
  if time.monotonic()>deadline:
   measurements.append({"stage":name,"status":"SKIP_BUDGET"}); continue
  entry={"stage":name,"status":"OK","rows":0}
  started=time.monotonic()
  try:
   entry["plan"]=[str(p[3])[:120] for p in c.execute("EXPLAIN QUERY PLAN "+sql,params)]
   for _ in c.execute(sql,params): entry["rows"]+=1
  except sqlite3.Error as exc:
   entry["status"]="FAIL_CLOSED"; entry["sqlite_errorname"]=getattr(exc,"sqlite_errorname","SQLITE_UNKNOWN")
  entry["elapsed_ms"]=round((time.monotonic()-started)*1000,3)
  entry["deadline_expired"]=time.monotonic()>deadline
  entry["remaining_ms"]=round(max(0,deadline-time.monotonic())*1000,3)
  measurements.append(entry)
  if entry["status"]!="OK": break
 c.set_progress_handler(None,0)
 result["query_stages"]=measurements
 result["query_budget_ms"]=2000
 result["observation_source_limit_with_sentinel"]=observation_limit
 # Probe the later prospective lab's bounded bootstrap read at its deployed
 # deadline and a proposed 2s deadline; never export source rows or values.
 prospective=[]
 try:
  import rc6_shadow_runtime.entry_signals as entry
  for seconds in (.25,2.0):
   entry.QUERY_SECONDS=seconds
   started=time.monotonic()
   sample={"budget_ms":int(seconds*1000),"status":"OK"}
   try:
    source=entry._read_rows(db,as_of=at,
      tables=("decision_evidence_snapshots","market_snapshots"),
      cursors=None,source_key=None)
    sample["bootstrap"]=source["bootstrap"]
   except Exception as exc:
    sample["status"]="FAIL_CLOSED"
    sample["error_class"]=type(exc).__name__
    if isinstance(exc,sqlite3.Error):
     sample["sqlite_errorname"]=getattr(exc,"sqlite_errorname","SQLITE_UNKNOWN")
   sample["elapsed_ms"]=round((time.monotonic()-started)*1000,3)
   prospective.append(sample)
 except Exception as exc:
  prospective.append({"status":"PROBE_UNAVAILABLE","error_class":type(exc).__name__})
 result["prospective_bootstrap_probe"]=prospective
 # A committed failure can be stale even while the observer heartbeat moves.
 # Inspect only the isolated child process state, never argv, env or stacks.
 workers=[]
 for process in Path("/proc").iterdir():
  if not process.name.isdecimal(): continue
  try:
   argv=(process/"cmdline").read_bytes()[:4096].split(b"\\0")
   if b"--dynamic-shadow-worker" not in argv: continue
   state=(process/"stat").read_text().split(") ",1)[1].split()[0]
   wchan=(process/"wchan").read_text().strip()
   workers.append({"state":state if state in "RSDTZI" else "UNKNOWN",
                   "wchan":wchan if re.fullmatch(r"[A-Za-z0-9_]{1,64}",wchan) else "UNKNOWN"})
  except (OSError,IndexError): continue
 result["shadow_worker_process_count"]=min(10,len(workers))
 result["shadow_worker_processes"]=workers[:3]
 try: result["wal_bytes"]=db.with_name(db.name+"-wal").stat().st_size
 except OSError: result["wal_bytes"]=None
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
                return data
        status = "CONTAINER_PROBE_FAILED"
    except subprocess.TimeoutExpired:
        status = "CONTAINER_PROBE_TIMEOUT"
    except (OSError, ValueError, TypeError):
        status = "CONTAINER_PROBE_UNAVAILABLE"
    return {"schema": "rc6.shadow-readonly-diagnostic.v1",
            "status": status, "read_only": True}


def schedule_api_host():
    """Manual-only connectivity proof from the PAPER container, no credentials."""
    child = '''import json,urllib.request
url="https://api.github.com/repos/mbalbo2023/Porota-trading/actions/workflows/rc6-market-watch-readonly.yml/runs?event=schedule&per_page=1"
request=urllib.request.Request(url,headers={"Accept":"application/vnd.github+json","User-Agent":"porota-rc6-paper-silence/1"})
try:
 with urllib.request.urlopen(request,timeout=5) as response: raw=response.read(131073)
 if len(raw)>131072: raise ValueError("OVERSIZE")
 data=json.loads(raw); runs=data.get("workflow_runs")
 if not isinstance(runs,list): raise ValueError("INVALID_RUNS")
 latest=runs[0] if runs else {}
 print(json.dumps({"status":"API_OK","total_count":data.get("total_count"),
  "latest_event":latest.get("event"),"latest_path":latest.get("path"),
  "latest_head_branch":latest.get("head_branch"),"latest_created_at":latest.get("created_at")},separators=(",",":")))
except Exception as exc: print(json.dumps({"status":"API_UNAVAILABLE","error_class":type(exc).__name__}))'''
    try:
        completed = subprocess.run(["docker", "exec", "porota_production_observer",
                                    "python", "-B", "-c", child], capture_output=True,
                                   text=True, timeout=7, check=False)
        data = json.loads(completed.stdout) if completed.returncode == 0 else {}
        if data.get("status") in {"API_OK", "API_UNAVAILABLE"}:
            return {"schema": "rc6.market-schedule-api.v1", "read_only": True,
                    **{key: data.get(key) for key in (
                        "status", "total_count", "latest_event", "latest_path",
                        "latest_head_branch", "latest_created_at", "error_class") if key in data}}
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        pass
    return {"schema": "rc6.market-schedule-api.v1", "read_only": True,
            "status": "CONTAINER_PROBE_UNAVAILABLE"}


def observer_lifecycle_host():
    """Manual-only Docker metadata and proximate exception classes, no raw logs."""
    until = now_utc().isoformat()
    command = ["docker", "events", "--since", "90m", "--until", until,
               "--filter", "type=container",
               "--filter", "container=porota_production_observer",
               "--format", "{{json .}}"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=6, check=False)
        if completed.returncode or len(completed.stdout) > 65536:
            raise ValueError("EVENTS_UNAVAILABLE")
        events = []
        allowed = {"start", "die", "kill", "stop", "restart", "oom"}
        for line in completed.stdout.splitlines():
            row = json.loads(line)
            action = row.get("Action") or row.get("status")
            moment = row.get("time")
            if action not in allowed or type(moment) is not int:
                continue
            item = {"action": action, "epoch_seconds": moment}
            attributes = (row.get("Actor") or {}).get("Attributes") or {}
            if action == "die":
                code = attributes.get("exitCode")
                if str(code).isdigit() and 0 <= int(code) <= 255:
                    item["exit_code"] = int(code)
            if action == "kill":
                signal_number = attributes.get("signal")
                if str(signal_number).isdigit() and 0 < int(signal_number) <= 64:
                    item["signal"] = int(signal_number)
            events.append(item)
        proximate = {"status": "NO_NONZERO_EXIT"}
        exits = [e for e in events if e["action"] == "die"
                 and e.get("exit_code", 0) != 0]
        if exits:
            exit_at = exits[-1]["epoch_seconds"]
            since = datetime.fromtimestamp(exit_at - 90, timezone.utc).isoformat()
            through = datetime.fromtimestamp(exit_at + 10, timezone.utc).isoformat()
            try:
                logs = subprocess.run(
                    ["docker", "logs", "--since", since, "--until", through,
                     "--tail", "300", "porota_production_observer"],
                    capture_output=True, text=True, timeout=5, check=False)
                if logs.returncode or len(logs.stdout) + len(logs.stderr) > 262144:
                    raise ValueError("LOG_PROBE_UNAVAILABLE")
                classes = []
                sqlite_hints = []
                tracebacks = []
                last_app_frame = None
                content = logs.stdout + "\n" + logs.stderr
                for line in content.splitlines():
                    if line.strip() == "Traceback (most recent call last):":
                        last_app_frame = None
                    frame = re.match(
                        r'^\s*File "/app/([A-Za-z0-9_./-]+\.py)", line ([0-9]{1,5}), '
                        r'in ([A-Za-z_][A-Za-z0-9_<>]{0,79})$', line)
                    if frame:
                        last_app_frame = {
                            "module": frame.group(1).rsplit("/", 1)[-1],
                            "line": int(frame.group(2)),
                            "function": frame.group(3)}
                    match = re.fullmatch(
                        r"([A-Za-z_][A-Za-z0-9_.]{0,79}(?:Error|Exception)|"
                        r"SystemExit|KeyboardInterrupt)(?::.*)?", line.strip())
                    if match:
                        classes.append(match.group(1))
                        tracebacks.append({"exception_class": match.group(1),
                                           "last_app_frame": last_app_frame})
                        if match.group(1) == "sqlite3.OperationalError":
                            detail = line.lower()
                            hint = ("SQLITE_BUSY_LOCKED" if "locked" in detail or
                                    "busy" in detail else
                                    "SQLITE_INTERRUPT" if "interrupted" in detail else
                                    "SQLITE_FULL" if "database or disk is full" in detail else
                                    "SQLITE_IOERR" if "disk i/o error" in detail else
                                    "SQLITE_CANTOPEN" if "unable to open database" in detail else
                                    "SQLITE_UNKNOWN")
                            sqlite_hints.append(hint)
                proximate = {"status": "OK", "exit_epoch_seconds": exit_at,
                             "exception_classes": classes[-12:],
                             "sqlite_hints": sqlite_hints[-12:],
                             "tracebacks": tracebacks[-12:],
                             "traceback_count": content.count(
                                 "Traceback (most recent call last):")}
            except (OSError, ValueError, subprocess.TimeoutExpired):
                proximate = {"status": "UNAVAILABLE",
                             "exit_epoch_seconds": exit_at}
        return {"schema": "rc6.observer-lifecycle.v1", "read_only": True,
                "status": "OK", "events": events[-32:],
                "event_count": len(events), "proximate_log": proximate}
    except (OSError, ValueError, TypeError, json.JSONDecodeError,
            subprocess.TimeoutExpired):
        return {"schema": "rc6.observer-lifecycle.v1", "read_only": True,
                "status": "UNAVAILABLE", "events": []}


def recent_critical_host():
    """Classify bounded current-container signatures; never export raw logs."""
    result = {"schema": "rc6.recent-critical-readonly.v1", "read_only": True,
              "window": "30m", "containers": {}}
    collected_at = now_utc()
    for name in CONTAINERS[:2]:
        try:
            logs = subprocess.run(
                ["docker", "logs", "--timestamps", "--since", "30m",
                 "--tail", "1000", name],
                capture_output=True, text=True, timeout=6, check=False)
            if logs.returncode or len(logs.stdout) + len(logs.stderr) > 262144:
                raise ValueError("LOG_PROBE_UNAVAILABLE")
            content = logs.stdout + "\n" + logs.stderr
            tracebacks = []
            last_frame = None
            traceback_count = 0
            capability_count = 0
            order_count = 0
            last_five_minutes = 0
            for line in content.splitlines():
                stamp = re.match(
                    r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})'
                    r'(?:\.\d{1,9})?Z\s', line)
                observed = (datetime.fromisoformat(stamp.group(1) + "+00:00")
                            if stamp else None)
                if "Traceback (most recent call last):" in line:
                    traceback_count += 1
                    last_frame = None
                frame = re.search(
                    r'File "/app/([A-Za-z0-9_./-]+\.py)", line ([0-9]{1,5}), '
                    r'in ([A-Za-z_][A-Za-z0-9_<>]{0,79})', line)
                if frame:
                    last_frame = {"module": frame.group(1).rsplit("/", 1)[-1],
                                  "line": int(frame.group(2)),
                                  "function": frame.group(3)}
                match = re.search(
                    r'(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_.]{0,79}'
                    r'(?:Error|Exception)|SystemExit|KeyboardInterrupt):', line)
                if match:
                    if observed and timedelta(0) <= collected_at - observed <= timedelta(minutes=5):
                        last_five_minutes += 1
                    hint = None
                    if match.group(1) == "sqlite3.OperationalError":
                        detail = line.lower()
                        hint = ("SQLITE_BUSY_LOCKED" if "locked" in detail or
                                "busy" in detail else
                                "SQLITE_INTERRUPT" if "interrupted" in detail else
                                "SQLITE_FULL" if "database or disk is full" in detail else
                                "SQLITE_IOERR" if "disk i/o error" in detail else
                                "SQLITE_CANTOPEN" if "unable to open database" in detail else
                                "SQLITE_UNKNOWN")
                    tracebacks.append({"exception_class": match.group(1),
                                       "last_app_frame": last_frame,
                                       "sqlite_hint": hint,
                                       "observed_utc": stamp.group(1) + "Z" if stamp else None})
                if re.search(r'REAL_ORDER_CAPABILITY=(?:ENABLED|REAL)\b', line):
                    capability_count += 1
                if re.search(r'real_orders_sent=[1-9][0-9]*\b', line):
                    order_count += 1
            result["containers"][name] = {
                "status": "OK", "traceback_count": traceback_count,
                "tracebacks": tracebacks[-12:],
                "exception_lines_last_5m": last_five_minutes,
                "capability_signature_count": capability_count,
                "nonzero_order_signature_count": order_count}
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            result["containers"][name] = {"status": "UNAVAILABLE"}
    return result


def deliver_transition_once(path, event_id, body, send):
    """Durable bounded reservation; uncertain remote ACKs are never resent.

    This is a separate monitor receipt, never the PAPER ledger. A committed
    SENT receipt may acknowledge the same transition again without sending.
    Crash after send and before receipt remains UNVERIFIED for reconciliation.
    """
    if (type(event_id) is not str or type(body) is not str or
            not re.fullmatch('[0-9a-f]{64}', event_id) or len(body)>3500):
        return {'status':'INVALID_REQUEST'}
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):
        return {'status':'UNVERIFIED'}
    try:
        if path.exists() and (path.stat().st_nlink!=1 or path.stat().st_size>4*1024**2):
            return {'status':'UNVERIFIED'}
        with closing(sqlite3.connect(path, timeout=.2)) as c, c:
            c.execute('PRAGMA busy_timeout=200')
            c.execute('PRAGMA synchronous=FULL')
            c.execute('PRAGMA max_page_count=1024')
            c.execute('CREATE TABLE IF NOT EXISTS monitor_delivery_v1(event_id TEXT PRIMARY KEY,body_hash TEXT NOT NULL,state TEXT NOT NULL,message_id TEXT,created_at TEXT NOT NULL)')
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT state,message_id FROM monitor_delivery_v1 WHERE event_id=?',(event_id,)).fetchone()
            if row:
                if row[0]=='SENT' and str(row[1]).isdigit() and int(row[1])>0:
                    return {'status':'SENT','message_id':str(row[1]),'deduplicated':True,'transition_id':event_id}
                return {'status':'UNVERIFIED','transition_id':event_id,'reason':'PRIOR_DELIVERY_UNCERTAIN'}
            c.execute("DELETE FROM monitor_delivery_v1 WHERE state='SENT' AND created_at<?",
                      ((now_utc()-timedelta(days=2)).isoformat(),))
            if c.execute('SELECT COUNT(*) FROM monitor_delivery_v1').fetchone()[0]>=256:
                return {'status':'UNVERIFIED','reason':'DELIVERY_RECEIPT_CAPACITY'}
            c.execute('INSERT INTO monitor_delivery_v1 VALUES(?,?,?,?,?)',
                      (event_id,hashlib.sha256(body.encode()).hexdigest(),'IN_FLIGHT',None,now_utc().isoformat()))
        try:
            receipt=send(body)
        except Exception:
            receipt={'status':'UNVERIFIED'}
        acknowledged=(receipt.get('status')=='SENT' and
                      str(receipt.get('message_id','')).isdigit() and int(receipt['message_id'])>0)
        if acknowledged:
            with closing(sqlite3.connect(path,timeout=.2)) as c, c:
                c.execute('PRAGMA synchronous=FULL')
                c.execute("UPDATE monitor_delivery_v1 SET state='SENT',message_id=? WHERE event_id=? AND state='IN_FLIGHT'",
                          (str(receipt['message_id']),event_id))
            return {'status':'SENT','message_id':str(receipt['message_id']),
                    'deduplicated':False,'transition_id':event_id}
        return {'status':'UNVERIFIED','transition_id':event_id,'reason':'DELIVERY_ACK_UNCERTAIN'}
    except (OSError,sqlite3.Error,ValueError,TypeError):
        return {'status':'UNVERIFIED'}


def alert_host(encoded):
    """Use the existing container Telegram transport without exporting secrets."""
    try:
        if not 1 <= len(encoded) <= 6000 or any(c not in
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=" for c in encoded):
            raise ValueError("INVALID_ALERT_REQUEST")
        decoded = base64.urlsafe_b64decode(encoded).decode("utf-8")
        if decoded.startswith('{'):
            request=json.loads(decoded)
            event_id=request['transition_id']; body=request['body']
        else:
            # Legacy unkeyed requests cannot guarantee dedupe; fail closed.
            return {'status':'INVALID_REQUEST'}
        if type(body) is not str or not body.startswith("POROTA RC6 | ") or len(body) > 3500:
            raise ValueError("INVALID_ALERT_BODY")
    except (ValueError, UnicodeError, KeyError, TypeError):
        return {"status": "INVALID_REQUEST"}
    child = '''import json,os,sys
from bn_telegram_bus import TelegramTransport,DeliveryError
token=os.getenv("TELEGRAM_BOT_TOKEN",""); chat=os.getenv("TELEGRAM_CHAT_ID","")
if not token or not chat: print(json.dumps({"status":"NOT_CONFIGURED"})); sys.exit(0)
try:
 mid=TelegramTransport(token,chat)(sys.argv[1])
 print(json.dumps({"status":"SENT","message_id":mid}))
except DeliveryError as exc: print(json.dumps({"status":"FAILED","error_code":exc.code}))
except Exception as exc: print(json.dumps({"status":"FAILED","error_class":type(exc).__name__}))'''
    def send(body):
        result = subprocess.run(["docker", "exec", "porota_production_observer",
            "python", "-B", "-c", child, body], capture_output=True, text=True,
            timeout=19, check=False)
        data = json.loads(result.stdout) if result.returncode == 0 else {}
        if data.get("status") == "SENT" and str(data.get("message_id", "")).isdigit():
            return {"status": "SENT", "message_id": str(data["message_id"])}
        if data.get("status") == "NOT_CONFIGURED":
            return {"status": "NOT_CONFIGURED"}
        return {"status": "FAILED"}
    return deliver_transition_once(ROOT/'data/paper_v17/market_watch_delivery.sqlite',event_id,body,send)


def prepare_alert(decision):
    if decision["notification"] == "NONE":
        return ""
    codes = decision["new_alerts"] if decision["notification"] == "ALERT" else decision["recovered"]
    validation_codes=list(codes)+list(decision.get('recovered',[]))
    if not codes or len(validation_codes) > 40 or any(not isinstance(code, str) or
            len(code) > 100 or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:-" for ch in code)
            for code in validation_codes):
        raise ValueError("ALERT_CODES_INVALID")
    event_id=decision.get('transition_id')
    if not isinstance(event_id,str) or not re.fullmatch('[0-9a-f]{64}',event_id):
        raise ValueError('TRANSITION_ID_REQUIRED')
    body = ("POROTA RC6 | " + decision["notification"] + "\n" +
        ", ".join(codes) + "\n" + decision["generated_at"] +
        "\nID: " + event_id + "\nPAPER/SHADOW ONLY. Ver evidencia en GitHub Actions.")
    if decision['notification']=='ALERT' and decision.get('recovered'):
        body+='\nRECOVERY: '+', '.join(decision['recovered'])
    return base64.urlsafe_b64encode(json.dumps({'transition_id':event_id,'body':body},sort_keys=True).encode()).decode()


def finalize_delivery(decision, receipt):
    """Only an actual Telegram message_id makes a transition acknowledged."""
    status = receipt.get("status", "UNVERIFIED")
    if status == "SENT" and (not str(receipt.get("message_id", "")).isdigit() or int(receipt['message_id'])<=0):
        status = "UNVERIFIED"
    if decision["notification"] == "NONE":
        status = "NO_TRANSITION"
    active = set(decision["alerted"])
    if status != "SENT":
        if decision["notification"] == "ALERT":
            active -= set(decision["new_alerts"])
        elif decision["notification"] == "RECOVERY":
            active |= set(decision["recovered"])
    decision["acknowledged_codes"] = sorted(active)
    decision["delivery_status"] = status
    if status == "SENT":
        decision["telegram_message_id"] = str(receipt["message_id"])
        decision['delivery_deduplicated']=receipt.get('deduplicated') is True
    return decision


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
    old_alerted = set(previous_decision.get("acknowledged_codes", [])) if comparable and previous_decision else set()
    new_alerts = sorted(confirmed - old_alerted)
    recovered = sorted(old_alerted - confirmed - (p1_now - p1_prev))
    alerted = sorted(confirmed | (old_alerted & p1_now))
    status = "P0" if p_now else "P1" if p1_now else current.get("severity", "GREEN")
    notification='ALERT' if new_alerts else 'RECOVERY' if recovered else 'NONE'
    origins=dict((previous_decision or {}).get('incident_origins',{})) if comparable else {}
    for code in new_alerts:
        origins[code]=current['generated_at']
    transition_codes=new_alerts if new_alerts else recovered
    identity=[current.get('expected_deploy_sha'),notification,
              [(kind,code,origins.get(code,(previous or {}).get('generated_at')))
               for kind,codes in [('ALERT',new_alerts),('RECOVERY',recovered)] for code in codes]]
    transition_id=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest() if transition_codes else None
    return {"schema": SCHEMA, "generated_at": current["generated_at"],
            "status": status, "confirmed": sorted(confirmed),
            "alerted": alerted, "new_alerts": new_alerts,
            "recovered": recovered, "pending_confirmation": sorted(p1_now - p1_prev),
            "notification": notification, 'transition_id':transition_id,
            'incident_origins':{code:origins.get(code,(previous or {}).get('generated_at')) for code in sorted(set(alerted)|set(recovered))},
            "previous_comparable": comparable,
            "read_only": True, "real_orders_sent_verified": bool(
                current.get("observer", {}).get("real_orders_sent") == 0
                and type(current.get("observer", {}).get("real_orders_sent")) is int)}


def _read_optional(path):
    if not path:
        return None
    return load_bounded(Path(path), MAX_JSON_BYTES)


def instrument_inventory_host(root=ROOT, *, at=None, row_limit=20000, budget_seconds=2):
    """Complete bounded metadata inventory; never market/financial acceptance.

    No positions, prices, account values, free-text detail or metadata_json are
    selected. Exported contract flags cannot grant entry or freshness authority.
    """
    result = {'schema': 'rc6.instrument-audit-inventory.v1', 'read_only': True,
              'status': 'UNVERIFIED', 'entry_authority': False,
              'broker_requests': 0, 'ppi_watch_accessed': False,
              'source_database_effect': 'READ_ONLY', 'complete_inventory': False}
    connection = None
    try:
        if type(row_limit) is not int or not 1 <= row_limit <= 20000 or not 0 < budget_seconds <= 2:
            raise ValueError('INVENTORY_BUDGET_INVALID')
        database = Path(root) / DB_REL
        if database.is_symlink() or database.resolve(strict=True) != database.absolute():
            raise ValueError('INVENTORY_PATH_ALIAS')
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.005)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024**2)
        connection.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + budget_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        connection.execute('BEGIN')
        safety = connection.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        if not safety or safety[0] != 'PRODUCTION_PAPER' or type(safety[1]) is not int or safety[1] != 0:
            raise ValueError('INVENTORY_PAPER_REQUIRED')
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = ['ticker', 'instrument_type', 'market', 'currency', 'settlement',
                   'settlement_source', 'last_seen_at', 'catalog_status', 'catalog_capability']
        fields = ['c.' + x for x in columns[:7]] + ['c.status', 'c.capability']
        joins = ''
        if 'candidate_identity_v2' in tables:
            joins += ' LEFT JOIN candidate_identity_v2 i ON ' + ' AND '.join('i.'+x+'=c.'+x for x in columns[:5])
            fields += ['i.can_simulate', 'i.status', 'i.checked_at']
        else:
            fields += ['NULL'] * 3
        columns += ['candidate_can_simulate', 'candidate_status', 'candidate_checked_at']
        if 'ppi_intraday_contract_state' in tables:
            joins += (' LEFT JOIN ppi_intraday_contract_state t ON t.symbol=c.ticker'
                      ' AND t.asset_class=c.instrument_type AND t.market=c.market'
                      ' AND t.currency=c.currency AND t.settlement=c.settlement')
            fields += ['t.state', 't.last_source_at', 't.checked_at']
        else:
            fields += ['NULL'] * 3
        columns += ['intraday_contract_state', 'intraday_source_at', 'intraday_checked_at']
        public_fields = ','.join(x[2:] for x in fields[:9])
        sql = ('SELECT COUNT(*),json_group_array(json_array(' + ','.join(fields) + ')) FROM '
               '(SELECT ' + public_fields + ' FROM financial_instrument_catalog ORDER BY instrument_type,market,currency,ticker,settlement LIMIT ?) c' + joins)
        count, packed = connection.execute(sql, (row_limit + 1,)).fetchone()
        connection.rollback(); connection.close(); connection = None
        if count > row_limit:
            raise ValueError('INVENTORY_TRUNCATED_OR_DUPLICATED')
        captured = at or now_utc()
        rows = json.loads(packed)
        if len(rows) != count or len({tuple(r[:5]) for r in rows}) != count:
            raise ValueError('INVENTORY_IDENTITY_NOT_UNIQUE')
        provenance = load_bounded(Path(root) / 'data/deploy/CURRENT_STATE_V2.json') or {}
        observed = {}
        for key in ('candidate_sha', 'deploy_sha'):
            value = provenance.get(key)
            observed[key] = value if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) else None
        observed['validation_status'] = provenance.get('validation_status') if provenance.get('validation_status') in {
            'VALIDATED_RUNTIME', 'DEPLOYED_VALIDATION_PENDING'} else None
        result.update(status='OBSERVED_METADATA_ONLY', complete_inventory=True,
                      generated_at=captured.isoformat(), columns=columns, records=rows, count=count,
                      real_orders_sent=0, mode='PRODUCTION_PAPER', provenance=observed,
                      missing_optional_tables=sorted({'candidate_identity_v2','ppi_intraday_contract_state'}-tables),
                      unverified_per_identity_stages=['quote_freshness','candle','history','signal','risk',
                          'decision','fill','exit','pnl','external_alert'],
                      contract_flags_are_not_live_tradeability=True)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        result['error_class'] = type(exc).__name__
    finally:
        if connection is not None:
            connection.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    remote = actions.add_parser("probe")
    remote.add_argument("--expected-sha", required=True)
    remote.add_argument("--root", type=Path, default=ROOT)
    actions.add_parser("transport-error")
    transport=actions.add_parser('transport-diagnostic')
    transport.add_argument('--stderr',required=True,type=Path)
    actions.add_parser("shadow-diagnostic")
    actions.add_parser("shadow-diagnostic-host")
    actions.add_parser("schedule-api-host")
    actions.add_parser("observer-lifecycle-host")
    actions.add_parser("recent-critical-host")
    actions.add_parser("instrument-inventory-host")
    cadence = actions.add_parser('schedule-health')
    cadence.add_argument('--current',required=True)
    cadence.add_argument('--repo',required=True)
    alert = actions.add_parser("alert-host")
    alert.add_argument("encoded")
    prep = actions.add_parser("prepare-alert")
    prep.add_argument("--decision", required=True)
    final = actions.add_parser("finalize-delivery")
    final.add_argument("--decision", required=True)
    final.add_argument("--receipt", required=True)
    evaluation = actions.add_parser("classify")
    evaluation.add_argument("--current", required=True)
    evaluation.add_argument("--previous")
    evaluation.add_argument("--previous-decision")
    prior = actions.add_parser("select-previous")
    prior.add_argument("--current", required=True)
    prior.add_argument("--repo", required=True)
    prior.add_argument("--workflow-id", required=True, type=int)
    prior.add_argument("--run-id", required=True, type=int)
    prior.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.action == "probe":
        if not (len(args.expected_sha) == 40 and
                all(x in "0123456789abcdef" for x in args.expected_sha)):
            raise SystemExit("INVALID_EXPECTED_SHA")
        data = probe(root=args.root, expected_sha=args.expected_sha)
    elif args.action == 'schedule-health':
        data=add_schedule_health(load_bounded(Path(args.current)),args.repo)
        Path(args.current).write_text(json.dumps(data,sort_keys=True,separators=(',',':'))+'\n')
        print(json.dumps(data['schedule_health'],sort_keys=True))
        return 0
    elif args.action == "transport-error":
        data = transport_error()
    elif args.action == 'transport-diagnostic':
        with args.stderr.open('rb') as source:
            raw=source.read(8192).decode('utf-8',errors='replace')
        data=transport_diagnostic(raw,int(os.environ.get('PROBE_EXIT_CODE','255')))
    elif args.action == "shadow-diagnostic":
        data = shadow_diagnostic()
    elif args.action == "shadow-diagnostic-host":
        data = shadow_diagnostic_host()
    elif args.action == "schedule-api-host":
        data = schedule_api_host()
    elif args.action == "observer-lifecycle-host":
        data = observer_lifecycle_host()
    elif args.action == "recent-critical-host":
        data = recent_critical_host()
    elif args.action == "instrument-inventory-host":
        data = instrument_inventory_host()
    elif args.action == "alert-host":
        data = alert_host(args.encoded)
    elif args.action == "prepare-alert":
        print(prepare_alert(load_bounded(Path(args.decision), MAX_JSON_BYTES)))
        return 0
    elif args.action == "finalize-delivery":
        decision = load_bounded(Path(args.decision), MAX_JSON_BYTES)
        receipt = _read_optional(args.receipt) or {"status": "UNVERIFIED"}
        data = finalize_delivery(decision, receipt)
        Path(args.decision).write_text(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    elif args.action == "select-previous":
        current = load_bounded(Path(args.current))
        if current is None:
            raise SystemExit("CURRENT_SNAPSHOT_INVALID")
        receipt = read_previous_artifact(current, args.repo, args.workflow_id,
                                         args.run_id, args.destination)
        print("PREVIOUS_COMPARABLE=" + ("VERIFIED" if receipt else "UNAVAILABLE"))
        return 0
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

