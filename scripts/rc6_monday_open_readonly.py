#!/usr/bin/env python3
"""One bounded, observational RC6 PAPER/0 receipt for 02-Nov-2026.

Run by a manual GitHub Action over pinned SSH. It never imports a broker,
contacts a market provider, writes a host file/SQLite row, or grants entry.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

ART = ZoneInfo("America/Argentina/Buenos_Aires")
DAY = date(2026, 11, 2)
SHA = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
TICKER = re.compile(r"[A-Z0-9._-]{1,18}\Z")
DROPLET_ID = "594077619"
DROPLET_REGION = "nyc1"
METADATA = "http://169.254.169.254/metadata/v1/"
WINDOWS = {
    "T45": (clock_time(9, 48), clock_time(10, 20)),
    "T10": (clock_time(10, 24), clock_time(10, 45)),
    "SPOT": (clock_time(10, 40), clock_time(11, 25)),
    "CEDEAR": (clock_time(11, 40), clock_time(12, 15)),
    "SCALPING": (clock_time(12, 15), clock_time(16, 0)),
    "CAUCION": (clock_time(16, 32), clock_time(16, 54)),
    "POSTCLOSE": (clock_time(18, 12), clock_time(18, 45)),
}
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
PRODUCT = Path("/opt/porota-trading")
DEPLOY = Path("/opt/porota-trading/data/deploy")
OPERATOR = Path("/opt/porota-operator")
MARKET = Path("/opt/porota-trading/data/market")
TIMERS = {
    "T45": ("porota-preopen-rc6",),
    "T10": ("porota-preopen-rc6", "porota-snapshot-preopen-rc6"),
    "SPOT": ("porota-preopen-rc6", "porota-iol-shadow-collector-rc6"),
    "CEDEAR": ("porota-iol-shadow-collector-rc6",),
    "SCALPING": ("porota-iol-shadow-collector-rc6",),
    "CAUCION": ("porota-iol-shadow-collector-rc6",),
    "POSTCLOSE": ("porota-snapshot-postclose-rc6", "porota-iol-shadow-collector-rc6"),
}


class ProbeBlocked(ValueError):
    pass


def verify_droplet_identity(opener=None):
    """Same bounded link-local metadata pin as RC6 product A205."""
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    observed = []
    for key in ("id", "region"):
        try:
            with opener.open(urllib.request.Request(METADATA + key, method="GET"),
                             timeout=3) as response:
                if response.status != 200:
                    raise ProbeBlocked("DROPLET_METADATA_RED")
                raw = response.read(65)
        except OSError:
            raise ProbeBlocked("DROPLET_METADATA_UNAVAILABLE") from None
        if not 0 < len(raw) <= 64:
            raise ProbeBlocked("DROPLET_METADATA_INVALID")
        try:
            observed.append(raw.decode("ascii").strip())
        except UnicodeDecodeError:
            raise ProbeBlocked("DROPLET_METADATA_INVALID") from None
    if tuple(observed) != (DROPLET_ID, DROPLET_REGION):
        raise ProbeBlocked("DROPLET_IDENTITY_MISMATCH")
    return {"droplet_id": DROPLET_ID, "region": DROPLET_REGION}


def request_from_environment():
    return {
        "phase": os.environ["RC6_PHASE"],
        "trade_day_ar": os.environ["RC6_TRADE_DAY_AR"],
        "candidate_sha": os.environ["RC6_EXPECTED_SHA"],
        "candidate_tree_sha": os.environ["RC6_EXPECTED_TREE"],
        "image_id": os.environ["RC6_EXPECTED_IMAGE"],
        "family": os.environ["RC6_FAMILY"],
        "ticker": os.environ["RC6_TICKER"],
        "currency": os.environ["RC6_CURRENCY"],
        "settlement": os.environ["RC6_SETTLEMENT"],
        "source_commit": os.environ["GITHUB_SHA"],
        "source_sha256": os.environ["RC6_SOURCE_SHA256"],
    }


def validate_request(request, now):
    if not isinstance(request, dict) or set(request) != {
        "phase", "trade_day_ar", "candidate_sha", "candidate_tree_sha",
        "image_id", "family", "ticker", "currency", "settlement",
        "source_commit", "source_sha256",
    }:
        raise ProbeBlocked("REQUEST_SHAPE_INVALID")
    if (not isinstance(now, datetime) or now.tzinfo is None or
            now.utcoffset() is None):
        raise ProbeBlocked("CLOCK_INVALID")
    local = now.astimezone(ART)
    if request["trade_day_ar"] != DAY.isoformat() or local.date() != DAY:
        raise ProbeBlocked("TRADE_DAY_MISMATCH")
    phase = request["phase"]
    if phase not in WINDOWS:
        raise ProbeBlocked("PHASE_INVALID")
    lower, upper = WINDOWS[phase]
    if not lower <= local.time().replace(tzinfo=None) < upper:
        raise ProbeBlocked("PHASE_WINDOW_CLOSED")
    if any(not isinstance(request[key], str) or not SHA.fullmatch(request[key])
           for key in ("candidate_sha", "candidate_tree_sha", "source_commit")):
        raise ProbeBlocked("SHA_INVALID")
    if not isinstance(request["image_id"], str) or not IMAGE.fullmatch(request["image_id"]):
        raise ProbeBlocked("IMAGE_ID_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", str(request["source_sha256"])):
        raise ProbeBlocked("SOURCE_HASH_INVALID")
    family, ticker = request["family"], request["ticker"]
    if phase in {"T45", "T10", "POSTCLOSE"}:
        if (family, ticker, request["currency"], request["settlement"]) != (
                "NONE", "NONE", "NONE", "NONE"):
            raise ProbeBlocked("NON_INSTRUMENT_PHASE_IDENTITY_INVALID")
    else:
        allowed = {
            "SPOT": {"ACCIONES"},
            "CEDEAR": {"CEDEARS"},
            "SCALPING": {"ACCIONES", "CEDEARS"},
            "CAUCION": {"CAUCIONES"},
        }[phase]
        if (family not in allowed or not isinstance(ticker, str)
                or not TICKER.fullmatch(ticker)
                or request["currency"] not in {"ARS", "USD"}
                or request["settlement"] not in {"A-24HS", "INMEDIATA"}):
            raise ProbeBlocked("EXACT_IDENTITY_INVALID")
        if phase == "CAUCION" and (request["currency"], request["settlement"]) != (
                "ARS", "INMEDIATA"):
            raise ProbeBlocked("CAUCION_IDENTITY_INVALID")
        if phase != "CAUCION" and request["settlement"] != "A-24HS":
            raise ProbeBlocked("SPOT_SETTLEMENT_INVALID")
    return local


def _read_json(path, maximum=65536, *, root_owned=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or not 0 < info.st_size <= maximum
                or (root_owned and (info.st_uid != 0 or info.st_mode & 0o022))):
            raise ProbeBlocked("RECEIPT_FILE_UNTRUSTED")
        raw = os.read(fd, maximum + 1)
        if len(raw) != info.st_size:
            raise ProbeBlocked("RECEIPT_CHANGED")
        return json.loads(raw)
    finally:
        os.close(fd)


def validate_release(request, marker, frozen, observer, dashboard):
    sha, tree, image = (request["candidate_sha"], request["candidate_tree_sha"],
                        request["image_id"])
    if (marker.get("candidate_sha") != sha
            or frozen.get("candidate_sha") != sha
            or frozen.get("candidate_tree_sha") != tree
            or marker.get("image_id") != image
            or marker.get("validation_status") != "VALIDATED_RUNTIME"
            or marker.get("mode") != "PRODUCTION_PAPER"
            or type(marker.get("real_orders_sent")) is not int
            or marker["real_orders_sent"] != 0
            or marker.get("real_order_routes") != "NOT_CALLED"
            or marker.get("ppi_watch_untouched") is not True
            or frozen.get("real_order_capability_required") != "BLOCKED"
            or frozen.get("paper_mode_required") != "PRODUCTION_PAPER"
            or frozen.get("build_once") is not True
            or any(not item["running"] or item["image"] != image
                   for item in (observer, dashboard))
            or observer["build_sha"] != sha
            or observer["tree_sha"] != tree
            or not observer["readonly_rootfs"]):
        raise ProbeBlocked("INSTALLED_RELEASE_IDENTITY_OR_PAPER0_RED")


def validate_t10(request, receipt, now):
    try:
        issued = datetime.fromisoformat(receipt["issued_at_ar"])
        issued = (issued.astimezone(ART) if issued.tzinfo is not None
                  and issued.utcoffset() == now.utcoffset() else None)
        minute = issued.hour * 60 + issued.minute
        age = (now - issued).total_seconds()
        good = (receipt.get("schema") == "RC6_PAPER_PREOPEN_GATE_V2"
                and receipt.get("candidate_sha") == request["candidate_sha"]
                and receipt.get("trade_day_ar") == DAY.isoformat()
                and receipt.get("status") == "GO_PAPER_SCALPING"
                and receipt.get("phase") == "T_MINUS_10"
                and issued.date() == DAY and 620 <= minute < 630
                and 0 <= age <= 7 * 3600)
    except (KeyError, TypeError, ValueError, AttributeError):
        good = False
    return "VERIFIED_T10_GO" if good else "HOLD_T10_RECEIPT"


def _command(argv, *, timeout=2):
    try:
        result = subprocess.run(argv, check=True, text=True, capture_output=True,
                                timeout=timeout)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise ProbeBlocked("HOST_READONLY_COMMAND_FAILED") from None


def _container(name):
    values = _command(["docker", "inspect", "--format",
                       "{{.State.Running}}|{{.Image}}|{{.HostConfig.ReadonlyRootfs}}",
                       name]).split("|")
    if len(values) != 3:
        raise ProbeBlocked("CONTAINER_STATE_INVALID")
    if name == "porota_production_observer":
        env = json.loads(_command(["docker", "inspect", "--format",
                                   "{{json .Config.Env}}", name]))
        selected = {}
        for item in env:
            if "=" in item:
                key, value = item.split("=", 1)
                if key in {"POROTA_BUILD_SHA", "POROTA_CANDIDATE_TREE_SHA",
                           "PAPER_SCALPING_MODE", "PAPER_CAUCION_SWEEP_MODE"}:
                    selected[key] = value
    else:
        selected = {}
    return {
        "running": values[0] == "true", "image": values[1],
        "readonly_rootfs": values[2] == "true",
        "build_sha": selected.get("POROTA_BUILD_SHA"),
        "tree_sha": selected.get("POROTA_CANDIDATE_TREE_SHA"),
        "scalping_mode": selected.get("PAPER_SCALPING_MODE"),
        "caucion_mode": selected.get("PAPER_CAUCION_SWEEP_MODE"),
    }


def _installed_source(expected_tree):
    """Pin the host checkout before importing its read-only operator helpers."""
    actual = _command(["git", "-C", str(PRODUCT), "rev-parse", "HEAD^{tree}"])
    if actual != expected_tree:
        raise ProbeBlocked("HOST_SOURCE_TREE_MISMATCH")
    for relative in ("scripts/rc6_operator_paper_rearm.py",
                     "am_us_equity_calendar_rc6.py"):
        path = PRODUCT / relative
        if path.is_symlink() or not path.is_file():
            raise ProbeBlocked("HOST_HELPER_UNTRUSTED")
        tracked = _command(["git", "-C", str(PRODUCT), "rev-parse",
                            "HEAD:" + relative])
        working = _command(["git", "hash-object", str(path)])
        if tracked != working:
            raise ProbeBlocked("HOST_HELPER_DRIFT")
    return actual


def _timer(base):
    output = _command(["systemctl", "show", base + ".timer",
                       "--property=UnitFileState,ActiveState,LastTriggerUSec,NextElapseUSecRealtime"])
    timer = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    output = _command(["systemctl", "show", base + ".service",
                       "--property=Result,ExecMainStatus"])
    service = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    return {"enabled": timer.get("UnitFileState") == "enabled",
            "active": timer.get("ActiveState") == "active",
            "last_trigger": timer.get("LastTriggerUSec", "")[:50],
            "next_trigger": timer.get("NextElapseUSecRealtime", "")[:50],
            "service_result": service.get("Result", "")[:24],
            "exit_code": service.get("ExecMainStatus", "")[:8]}


def _age_seconds(value, now):
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if instant.tzinfo is None:
            return None
        return round((now - instant.astimezone(ART)).total_seconds(), 1)
    except (TypeError, ValueError):
        return None


def _one(db, sql, args=()):
    return db.execute(sql, args).fetchone()


SNAPSHOT_SQL = """SELECT source,observed_at,book_at,trade_at,last_kind,
  metadata_source,opening_block_reason,bid,ask,bid_size,ask_size
  FROM market_snapshots WHERE symbol=? AND asset_class=? AND market=?
  AND currency=? AND settlement=? ORDER BY id DESC LIMIT 1"""


def _require_snapshot_index(db, identity):
    columns = [row[2] for row in db.execute("PRAGMA index_info(idx_snapshot_identity)")]
    if columns != ["symbol", "asset_class", "settlement", "currency", "market", "id"]:
        raise ProbeBlocked("SNAPSHOT_EXACT_INDEX_MISSING")
    plan = " ".join(str(row[3]) for row in db.execute(
        "EXPLAIN QUERY PLAN " + SNAPSHOT_SQL, identity))
    if "idx_snapshot_identity" not in plan or "SCAN market_snapshots" in plan:
        raise ProbeBlocked("SNAPSHOT_QUERY_WOULD_SCAN")


def _db_snapshot(request, now):
    if not DB.is_file() or DB.is_symlink():
        raise ProbeBlocked("OBSERVER_DB_UNTRUSTED")
    phase = request["phase"]
    with sqlite3.connect(DB.as_uri() + "?mode=ro", uri=True, timeout=.25) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=250")
        deadline = time.monotonic() + .35
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        row = _one(db, """SELECT mode,real_orders_sent,process_state,session_state,
          ppi_auth,heartbeat_at FROM observer_state WHERE id=1""")
        if (not row or row["mode"] != "PRODUCTION_PAPER"
                or type(row["real_orders_sent"]) is not int
                or row["real_orders_sent"] != 0):
            raise ProbeBlocked("OBSERVER_PAPER0_RED")
        result = {"observer": {
            "process": str(row["process_state"])[:40],
            "session": str(row["session_state"])[:40],
            "ppi_auth": str(row["ppi_auth"])[:40],
            "heartbeat_age_seconds": _age_seconds(row["heartbeat_at"], now),
            "real_orders_sent": 0}}
        workers = {}
        for key, table in (
                ("scalping", "intraday_scalping_worker_state"),
                ("caucion", "paper_caucion_cash_sweep_state"),
                ("supervisor", "paper_supervisor_state"),
                ("exit_reader", "paper_exit_reader_state")):
            worker = _one(db, "SELECT * FROM " + table + " WHERE id=1")
            workers[key] = ({"state": str(worker["state"])[:40],
                             "heartbeat_age_seconds": _age_seconds(worker["heartbeat_at"], now)}
                            if worker else {"state": "NO_ROW", "heartbeat_age_seconds": None})
            if key == "caucion" and worker:
                workers[key].update(
                    code=str(worker["code"])[:80],
                    request_id_matches=(worker["request_id"] ==
                                        "paper-caucion-cash-sweep:2026-11-02:ARS:v1"),
                    real_orders_sent=worker["real_orders_sent"],
                    routes_empty=worker["routes_json"] == "[]")
                if worker["real_orders_sent"] != 0 or worker["routes_json"] != "[]":
                    raise ProbeBlocked("CAUCION_REAL_ORDER_ROUTE_RED")
        result["workers"] = workers
        if phase in {"T45", "T10", "POSTCLOSE"}:
            return result
        identity = (request["ticker"], request["family"], "BYMA",
                    request["currency"], request["settlement"])
        row = _one(db, """SELECT status,capability,settlement_source,last_seen_at
          FROM financial_instrument_catalog WHERE ticker=? AND instrument_type=?
          AND market=? AND currency=? AND settlement=?""", identity)
        result["catalog"] = ({"status": row["status"], "capability": row["capability"],
                               "settlement_source": row["settlement_source"],
                               "last_seen_age_seconds": _age_seconds(row["last_seen_at"], now)}
                              if row else {"status": "NO_EXACT_ROW"})
        row = _one(db, """SELECT status,can_simulate,checked_at
          FROM candidate_identity_v2 WHERE ticker=? AND instrument_type=?
          AND market=? AND currency=? AND settlement=?""", identity)
        result["pit"] = ({"status": row["status"], "can_simulate": row["can_simulate"] == 1,
                          "checked_age_seconds": _age_seconds(row["checked_at"], now)}
                         if row else {"status": "NO_EXACT_ROW"})
        if phase == "CAUCION":
            row = _one(db, """SELECT state,source,quoted_at,observed_at
              FROM paper_caucion_live_book WHERE ticker=? AND market=?
              AND currency=? AND settlement=?""",
                       (identity[0], identity[2], identity[3], identity[4]))
            result["book"] = ({"state": row["state"],
                               "source_verified": row["source"] == "PPI_MARKETDATA_BOOK_READONLY",
                               "quote_age_seconds": _age_seconds(row["quoted_at"], now),
                               "receipt_age_seconds": _age_seconds(row["observed_at"], now)}
                              if row else {"state": "NO_EXACT_ROW"})
        else:
            _require_snapshot_index(db, identity)
            row = _one(db, SNAPSHOT_SQL, identity)
            if row:
                try:
                    from decimal import Decimal
                    book_valid = (Decimal(row["bid"]) > 0
                                  and Decimal(row["ask"]) >= Decimal(row["bid"])
                                  and Decimal(row["bid_size"]) > 0
                                  and Decimal(row["ask_size"]) > 0)
                except (TypeError, ValueError, ArithmeticError):
                    book_valid = False
                result["quote"] = {
                    "source": row["source"], "observed_age_seconds": _age_seconds(row["observed_at"], now),
                    "book_age_seconds": _age_seconds(row["book_at"], now),
                    "trade_age_seconds": _age_seconds(row["trade_at"], now),
                    "last_kind": row["last_kind"], "book_shape_valid": book_valid,
                    "ppi_catalog_provenance": str(row["metadata_source"] or "").startswith("PPI_CATALOG:"),
                    "opening_blocked": bool(row["opening_block_reason"])}
            else:
                result["quote"] = {"state": "NO_EXACT_ROW"}
            if phase == "SCALPING":
                row = _one(db, """SELECT state,last_source_at,checked_at
                  FROM ppi_intraday_contract_state WHERE symbol=? AND asset_class=?
                  AND market=? AND currency=? AND settlement=?""", identity)
                result["intraday"] = (
                    {"state": row["state"],
                     "source_age_seconds": _age_seconds(row["last_source_at"], now),
                     "checked_age_seconds": _age_seconds(row["checked_at"], now)}
                    if row else {"state": "NO_EXACT_ROW"})
                row = _one(db, """SELECT event_at,first_received_at,last_verified_at,source
                  FROM ppi_intraday_points WHERE symbol=? AND asset_class=?
                  AND market=? AND currency=? AND settlement=?
                  ORDER BY event_at DESC LIMIT 1""", identity)
                result["latest_point"] = (
                    {"event_age_seconds": _age_seconds(row["event_at"], now),
                     "first_received_age_seconds": _age_seconds(row["first_received_at"], now),
                     "last_verified_age_seconds": _age_seconds(row["last_verified_at"], now),
                     "source": row["source"]}
                    if row else {"state": "NO_EXACT_ROW"})
        return result


def _source_status():
    path = MARKET / "rc6_source_cycle_dispatch_state.json"
    try:
        raw = _read_json(path, maximum=8192)
        if raw.get("schema") != "rc6.source-cycle-dispatch.v1":
            raise ProbeBlocked("SOURCE_CYCLE_SCHEMA_INVALID")
        return {key: str(raw.get(key))[:50] for key in (
            "session_date", "last_public_success_at", "last_evidence_success_at")}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"state": "SOURCE_CYCLE_RECEIPT_UNVERIFIED"}


def _recent(age, maximum):
    return isinstance(age, (int, float)) and 0 <= age <= maximum


def review_evidence(request, evidence):
    """Name missing observations; even an empty list is never entry authority."""
    phase = request["phase"]
    missing = []
    if evidence["operator_intent"] != "ACTIVE_VERIFIED" and phase != "POSTCLOSE":
        missing.append("OPERATOR_INTENT_UNVERIFIED")
    if phase not in {"T45", "POSTCLOSE"} and evidence["t10_receipt"] != "VERIFIED_T10_GO":
        missing.append("T10_EXACT_RECEIPT_MISSING")
    for timer, state in evidence["timers"].items():
        if not state["enabled"] or not state["active"]:
            missing.append("TIMER_INACTIVE:" + timer)
        if phase in {"T45", "T10", "POSTCLOSE"} and DAY.isoformat() not in state["last_trigger"]:
            missing.append("TIMER_NOT_TRIGGERED_TODAY:" + timer)
        if phase in {"T45", "T10"} and state["service_result"] != "success":
            missing.append("PREOPEN_SERVICE_UNVERIFIED:" + timer)
    if phase == "POSTCLOSE":
        snapshot = evidence["timers"]["porota-snapshot-postclose-rc6"]
        if snapshot["service_result"] != "success":
            missing.append("POSTCLOSE_SERVICE_UNVERIFIED")
        return sorted(set(missing))
    if phase == "T45":
        return sorted(set(missing))
    observer = evidence["observer"]
    if not _recent(observer["heartbeat_age_seconds"], 60):
        missing.append("OBSERVER_HEARTBEAT_STALE")
    if phase == "T10":
        return sorted(set(missing))
    if (observer["process"] != "RUNNING" or observer["session"] != "MARKET_OPEN"
            or observer["ppi_auth"] not in {"OK", "AUTHENTICATED"}):
        missing.append("OBSERVER_OPEN_PPI_UNVERIFIED")
    for key, state in (("supervisor", "RUNNING"), ("exit_reader", "READY")):
        worker = evidence["workers"][key]
        if worker["state"] != state or not _recent(worker["heartbeat_age_seconds"], 20):
            missing.append(key.upper() + "_ADMISSION_UNVERIFIED")
    if evidence["source_cycle"].get("session_date") != DAY.isoformat():
        missing.append("SOURCE_CYCLE_TODAY_UNVERIFIED")
    catalog = evidence["catalog"]
    expected = ("READY_PAPER_CAUCION_PLACING" if phase == "CAUCION"
                else "READY_PAPER_SPOT")
    if (catalog.get("status") != "AVAILABLE" or catalog.get("capability") != expected
            or catalog.get("settlement_source") != "PPI_FIELD"):
        missing.append("EXACT_PPI_CATALOG_UNVERIFIED")
    pit = evidence["pit"]
    if pit.get("status") != "AVAILABLE" or pit.get("can_simulate") is not True:
        missing.append("EXACT_PIT_IDENTITY_UNVERIFIED")
    if phase == "CAUCION":
        worker = evidence["workers"]["caucion"]
        if (evidence["modes"]["caucion"] != "ACTIVE_PAPER"
                or not _recent(worker["heartbeat_age_seconds"], 300)
                or worker["state"] not in {"WAITING_WINDOW", "HOLD", "PLACED_SIMULATED"}):
            missing.append("CAUCION_WORKER_UNVERIFIED")
        book = evidence["book"]
        if (book.get("state") != "BOOK_READY"
                or book.get("source_verified") is not True
                or not _recent(book.get("quote_age_seconds"), 90)
                or not _recent(book.get("receipt_age_seconds"), 90)):
            missing.append("CAUCION_EXACT_LIVE_BID_UNVERIFIED")
    else:
        quote = evidence["quote"]
        if (quote.get("source") != "PRODUCTION_PAPER"
                or quote.get("ppi_catalog_provenance") is not True
                or quote.get("last_kind") != "TRADE"
                or quote.get("book_shape_valid") is not True
                or quote.get("opening_blocked") is not False
                or not _recent(quote.get("observed_age_seconds"), 120)
                or not _recent(quote.get("book_age_seconds"), 120)
                or not _recent(quote.get("trade_age_seconds"), 900)):
            missing.append("EXACT_PPI_TRADE_BOOK_UNVERIFIED")
        if request["family"] == "CEDEARS" and evidence.get("underlying_us_session") != "OPEN":
            missing.append("CEDEAR_US_SESSION_NOT_OPEN")
        if phase == "SCALPING":
            worker = evidence["workers"]["scalping"]
            intraday = evidence["intraday"]
            point = evidence["latest_point"]
            if (evidence["modes"]["scalping"] != "ACTIVE_PAPER"
                    or worker["state"] != "RUNNING"
                    or not _recent(worker["heartbeat_age_seconds"], 300)):
                missing.append("SCALPING_WORKER_MODE_UNVERIFIED")
            if (intraday.get("state") != "CONFIRMED_INTERVAL_VOLUME"
                    or not _recent(intraday.get("source_age_seconds"), 120)
                    or not _recent(intraday.get("checked_age_seconds"), 120)
                    or not _recent(point.get("event_age_seconds"), 120)
                    or not _recent(point.get("first_received_age_seconds"), 2700)
                    or not _recent(point.get("last_verified_age_seconds"), 120)):
                missing.append("SCALPING_PPI_POINTS_VOLUME_UNVERIFIED")
    return sorted(set(missing))


def probe(request, now):
    local = validate_request(request, now)
    try:
        marker = _read_json(DEPLOY / "CURRENT_STATE_V2.json")
        frozen = _read_json(DEPLOY / "porota-frozen-candidate.json", maximum=16384)
        observer = _container("porota_production_observer")
        dashboard = _container("porota_production_dashboard")
        validate_release(request, marker, frozen, observer, dashboard)
        _installed_source(request["candidate_tree_sha"])
        evidence = _db_snapshot(request, local)
        from scripts.rc6_operator_paper_rearm import evaluate_rearm
        intent_ok, intent_reason, proof = evaluate_rearm(now=local, context="host")
        if intent_ok and proof and proof.get("candidate_sha") != request["candidate_sha"]:
            raise ProbeBlocked("OPERATOR_RELEASE_IDENTITY_RED")
        if request["phase"] in {"T45", "POSTCLOSE"}:
            gate = ("NOT_DUE" if request["phase"] == "T45"
                    else "NOT_APPLICABLE_POSTCLOSE")
        else:
            directory = os.lstat(OPERATOR)
            if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0
                    or directory.st_mode & 0o022):
                raise ProbeBlocked("PREOPEN_DIRECTORY_UNTRUSTED")
            gate = validate_t10(
                request, _read_json(OPERATOR / "paper-preopen-v2.json",
                                    maximum=4096, root_owned=True), local)
        evidence["operator_intent"] = "ACTIVE_VERIFIED" if intent_ok else intent_reason
        evidence["t10_receipt"] = gate
        evidence["modes"] = {
            "scalping": observer["scalping_mode"],
            "caucion": observer["caucion_mode"]}
        evidence["timers"] = {name: _timer(name) for name in TIMERS[request["phase"]]}
        evidence["source_cycle"] = _source_status()
        if request["phase"] == "CEDEAR" or (
                request["phase"] == "SCALPING" and request["family"] == "CEDEARS"):
            from am_us_equity_calendar_rc6 import cedear_opening_gate
            allowed, reason = cedear_opening_gate("CEDEARS", local)
            evidence["underlying_us_session"] = "OPEN" if allowed else reason
        missing = review_evidence(request, evidence)
        return {
            "schema": "rc6.monday-open-readonly.v1",
            "status": "OBSERVED_HOLD" if missing else "OBSERVED_ONLY",
            "entry_authority": False,
            "real_orders_sent": 0,
            "host_mutations": 0,
            "trade_day_ar": DAY.isoformat(),
            "phase": request["phase"],
            "identity": ([request["family"], request["ticker"], "BYMA",
                          request["currency"], request["settlement"]]
                         if request["family"] != "NONE" else None),
            "host_observed_at_ar": local.isoformat(),
            "installed_sha": request["candidate_sha"],
            "installed_tree_sha": request["candidate_tree_sha"],
            "installed_image_id": request["image_id"],
            "probe_source_commit": request["source_commit"],
            "probe_source_sha256": request["source_sha256"],
            "evidence": evidence,
            "missing_observations": missing,
            "limits": "NO_BUY_OR_FILL_AUTHORITY;RECHECK_AT_TRANSACTION;OPEN_SOURCE_REQUIRED",
        }
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error,
            subprocess.SubprocessError, AttributeError) as exc:
        if isinstance(exc, ProbeBlocked):
            raise
        raise ProbeBlocked("HOST_EVIDENCE_UNVERIFIED") from None


def main():
    if sys.argv[1:] == ["--verify-droplet"]:
        try:
            pinned = verify_droplet_identity()
            print("RC6_DROPLET_IDENTITY=GREEN|id=" + pinned["droplet_id"] +
                  "|region=" + pinned["region"])
            return 0
        except ProbeBlocked as exc:
            print("RC6_DROPLET_IDENTITY=RED|reason=" + str(exc))
            return 2
    if sys.argv[1:] == ["--encode-request"]:
        request = request_from_environment()
        # Runner does not pretend it is the Monday host. Validate identity and
        # shape with the Monday window, then host independently checks its clock.
        phase_time = WINDOWS.get(request["phase"], (clock_time(0),))[0]
        validate_request(request, datetime.combine(DAY, phase_time, ART))
        print(base64.b64encode(json.dumps(request, sort_keys=True,
              separators=(",", ":")).encode()).decode())
        return 0
    try:
        raw = base64.b64decode(os.environ["RC6_MONDAY_REQUEST_B64"], validate=True)
        if not 0 < len(raw) <= 2048:
            raise ProbeBlocked("REQUEST_SIZE_INVALID")
        request = json.loads(raw)
        result = probe(request, datetime.now(ART))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "OBSERVED_ONLY" else 2
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, ProbeBlocked) else "REQUEST_OR_PROBE_UNVERIFIED"
        print(json.dumps({"schema": "rc6.monday-open-readonly.v1",
                          "status": "BLOCKED", "reason": reason,
                          "entry_authority": False, "real_orders_sent": 0,
                          "host_mutations": 0}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
