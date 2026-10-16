"""Nightly PAPER input contracts: exact audit methods and isolated SQLite/JSON.

No provider, broker, Telegram or runtime service is contacted.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bh_paper_gemini import GeminiPaperGate
from scripts import rc6_zero_known_error_runtime_audit as audit


def snapshot():
    now = datetime.now(timezone.utc).isoformat()
    frozen = json.loads(Path('DASHBOARD_TRUTH_MATRIX_RC6.json').read_text(encoding='utf-8'))
    families = dict(audit.BASELINE_READY)
    ready = sum(families.values())
    workers = {
        "scalping": {"state": "WAITING_MARKET", "heartbeat_at": now,
                     "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
        "caucion_cash_sweep": {"state": "WAITING_WINDOW", "heartbeat_at": now,
                              "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
        "exit_supervisor": {"state": "RUNNING", "heartbeat_at": now,
                            "heartbeat_age_seconds": 0, "real_orders_sent": 0, "routes": []},
    }
    return {
        "database": {
            "observer": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                         "heartbeat_at": now, "ppi_auth": "OK"},
            "readiness": {"source": "candidate_identity_v2", "ready": ready,
                          "by_family": families, "rows": []},
            "invalid_candidate_states": 0, "residual_candidate_state_violations": 0,
            "generic_pending_residuals": 0, "workers": workers,
        },
        "dashboard_truth": {
            "mode": "PRODUCTION_PAPER", "execution": "SIMULATED", "real_orders_sent": 0,
            "readiness": {"source": "candidate_identity_v2", "ready": ready},
            "history": {"governs_readiness": False},
            "scalping": {"mode": "ACTIVE_PAPER"},
            "iol": {"allowed_states": sorted(audit.ALLOWED_IOL_STATES)},
        },
        "iol": {"cache_state": "CACHE_FRESH", "last_known_good_at": now},
        "route_inventory": {"route_count": frozen['route_count'], "registered_paths": frozen['registered_paths'], "routes": []},
        "route_matrix_matches_artifact": True,
        "http_status": {path: 200 for path in audit.CORE_GET_PATHS},
    }


@pytest.fixture
def paper_snapshot(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    return snapshot()


def test_complete_known_zero_paper_state_remains_accepted(paper_snapshot):
    audit._assert_snapshot(paper_snapshot)


@pytest.mark.parametrize("component", ["observer", "scalping", "caucion_cash_sweep",
                                       "exit_supervisor", "dashboard_truth"])
@pytest.mark.parametrize("counter", [None, False, 0.5, "0", 1])
def test_unknown_or_nonzero_counter_cannot_certify_paper(paper_snapshot, component, counter):
    if component == "observer":
        row = paper_snapshot["database"]["observer"]
    elif component == "dashboard_truth":
        row = paper_snapshot["dashboard_truth"]
    else:
        row = paper_snapshot["database"]["workers"][component]
    row["real_orders_sent"] = counter
    with pytest.raises(RuntimeError, match="REAL_ORDER_COUNTER_INVALID"):
        audit._assert_snapshot(paper_snapshot)


@pytest.mark.parametrize("component", ["observer", "scalping", "caucion_cash_sweep",
                                       "exit_supervisor"])
def test_future_heartbeat_is_not_fresh(paper_snapshot, component):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    if component == "observer":
        paper_snapshot["database"]["observer"]["heartbeat_at"] = future
    else:
        paper_snapshot["database"]["workers"][component].update(
            heartbeat_at=future, heartbeat_age_seconds=audit._age_seconds(future))
    with pytest.raises((AssertionError, RuntimeError)):
        audit._assert_snapshot(paper_snapshot)


@pytest.mark.parametrize("counter", [None, 0.5, 1])
def test_readonly_sqlite_snapshot_preserves_invalid_persisted_worker_counter(
        tmp_path, monkeypatch, counter):
    path = tmp_path / "observer.db"
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,"
                   "process_state TEXT,session_state TEXT,ppi_auth TEXT,"
                   "real_orders_sent INTEGER,heartbeat_at TEXT)")
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER','RUNNING',"
                   "'MARKET_CLOSED','OK',0,?)", (now,))
        db.execute("CREATE TABLE candidate_identity_v2(instrument_type TEXT,status TEXT,"
                   "can_simulate INTEGER,detail TEXT)")
        db.execute("INSERT INTO candidate_identity_v2 VALUES('ACCIONES','AVAILABLE',1,'READY')")
        for table in ("intraday_scalping_worker_state", "paper_caucion_cash_sweep_state"):
            db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY,state TEXT,"
                       "heartbeat_at TEXT,real_orders_sent INTEGER,routes_json TEXT)")
        db.execute("INSERT INTO intraday_scalping_worker_state VALUES(1,'WAITING_MARKET',?,?,?)",
                   (now, counter, "[]"))
        db.execute("INSERT INTO paper_caucion_cash_sweep_state VALUES(1,'WAITING_WINDOW',?,0,'[]')",
                   (now,))
        # This canonical worker has no persisted real-order counter.
        db.execute("CREATE TABLE paper_supervisor_state(id INTEGER PRIMARY KEY,state TEXT,"
                   "heartbeat_at TEXT,detail TEXT)")
        db.execute("INSERT INTO paper_supervisor_state VALUES(1,'RUNNING',?,'PAPER')", (now,))
    monkeypatch.setattr(audit, "database_path", lambda: path)
    result = audit._database_snapshot()
    assert result["workers"]["scalping"]["real_orders_sent"] == counter
    with pytest.raises(RuntimeError, match="REAL_ORDER_COUNTER_INVALID"):
        audit._require_zero_orders(result["workers"]["scalping"]["real_orders_sent"], "scalping")
    assert result["workers"]["exit_supervisor"]["real_orders_sent"] == 0


@pytest.mark.parametrize("score", [0, 1, 0.9])
def test_actual_json_numbers_remain_valid_gemini_scores(score):
    response = SimpleNamespace(text=json.dumps(
        {"decision": "APPROVE", "score": score, "veto": False, "reason": "OFFLINE", "risks": []}))
    assert GeminiPaperGate._parse(response, "OFFLINE")["score"] == score


@pytest.mark.parametrize("score", ["0.9", "0", "1e-1"])
def test_json_string_is_not_a_number_even_when_float_can_parse_it(score):
    response = SimpleNamespace(text=json.dumps(
        {"decision": "APPROVE", "score": score, "veto": False, "reason": "OFFLINE", "risks": []}))
    with pytest.raises(ValueError, match="no es numérico"):
        GeminiPaperGate._parse(response, "OFFLINE")

