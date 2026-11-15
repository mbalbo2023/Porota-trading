"""Fail-closed, read-only financial evidence for the hourly RC6 snapshot.

The fixture is an isolated SQLite file, never a host or broker database.
"""

from datetime import datetime
import json
import sqlite3

import pytest

import ops_introspection_rc6 as introspection


def _fixture(tmp_path, monkeypatch, *, counter=0, missing_counter=False):
    path = tmp_path / "porota" / "paper_v17" / "observer_v17.db"
    path.parent.mkdir(parents=True)
    observer_counter = "" if missing_counter else ",real_orders_sent"
    observer_value = "" if missing_counter else ",?"
    with sqlite3.connect(path) as db:
        db.executescript(f"""
            CREATE TABLE observer_state (
              id INTEGER PRIMARY KEY,mode TEXT,process_state TEXT,session_state TEXT,
              heartbeat_at TEXT{observer_counter});
            CREATE TABLE paper_positions (
              paper_id TEXT PRIMARY KEY,status TEXT,opened_at TEXT,closed_at TEXT,
              net_pnl TEXT,currency TEXT,features_json TEXT,symbol TEXT,
              asset_class TEXT,market TEXT,settlement TEXT);
            CREATE TABLE paper_decisions (decided_at TEXT,reason TEXT);
            CREATE TABLE paper_events (event_at TEXT,event_type TEXT,paper_id TEXT,detail TEXT);
            CREATE TABLE paper_fills (paper_id TEXT,side TEXT);
            CREATE TABLE trade_gate_evaluations (
              evaluated_at TEXT,final_result TEXT,technical_gate TEXT,
              patrimonial_gate TEXT,decision_key TEXT,symbol TEXT,detail_json TEXT);
            CREATE TABLE scalping_candidates (evaluated_at TEXT,action TEXT);
            CREATE TABLE market_snapshots (
              id INTEGER PRIMARY KEY,last_kind TEXT,last TEXT,observed_at TEXT,
              trade_at TEXT,symbol TEXT,currency TEXT);
        """)
        statement = ("INSERT INTO observer_state ("
                     "id,mode,process_state,session_state,heartbeat_at"
                     f"{observer_counter}) VALUES(1,'PRODUCTION_PAPER',"
                     f"'RUNNING','MARKET_CLOSED','2026-11-07T12:00:00+00:00'{observer_value})")
        db.execute(statement, () if missing_counter else (counter,))
    (tmp_path / "porota" / "operation_mode.json").write_text(
        json.dumps({"mode": "PRODUCTION_PAPER"}), encoding="utf-8"
    )
    monkeypatch.setattr(introspection, "DB", str(path))
    return path


def _position(path, paper_id, *, net_pnl, currency="ARS", status="CLOSED"):
    when = datetime.now(introspection.TZ).isoformat()
    with sqlite3.connect(path) as db:
        db.execute("""INSERT INTO paper_positions
          (paper_id,status,opened_at,closed_at,net_pnl,currency,features_json)
          VALUES(?,?,?,?,?,?,?)""", (
            paper_id, status, when, when if status == "CLOSED" else None,
            net_pnl, currency, "{}",
        ))
        db.execute("INSERT INTO paper_fills VALUES(?,?)", (
            paper_id, "SELL_SIMULATED" if status == "CLOSED" else "BUY_SIMULATED",
        ))


@pytest.mark.parametrize("counter,expected", [
    (None, "real_orders_sent_unverified"),
    ("oops", "real_orders_sent_unverified"),
    ("0", "real_orders_sent_unverified"),
    (1, "real_orders_sent_nonzero"),
    (-1, "real_orders_sent_nonzero"),
])
def test_counter_missing_malformed_and_nonzero_are_critical(
    tmp_path, monkeypatch, counter, expected
):
    _fixture(tmp_path, monkeypatch, counter=counter)
    result = introspection.collect()
    assert expected in result["anomalies"]
    assert introspection.verdict(result) == "CRITICAL"


def test_counter_column_absent_does_not_become_measured_zero(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, missing_counter=True)
    result = introspection.collect()
    assert "real_orders_sent_unverified" in result["anomalies"]
    assert introspection.verdict(result) == "CRITICAL"


def test_wrong_mode_is_critical_even_if_manifest_agrees(tmp_path, monkeypatch):
    path = _fixture(tmp_path, monkeypatch, counter=0)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE observer_state SET mode='REAL_TRADING' WHERE id=1")
    (tmp_path / "porota" / "operation_mode.json").write_text(
        json.dumps({"mode": "REAL_TRADING"}), encoding="utf-8"
    )
    result = introspection.collect()
    assert "paper_mode_unverified" in result["anomalies"]
    assert introspection.verdict(result) == "CRITICAL"


def test_exact_integer_zero_does_not_raise_counter_anomaly(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch, counter=0)
    result = introspection.collect()
    assert not any(item.startswith("real_orders_sent_") for item in result["anomalies"])


@pytest.mark.parametrize("bad_value", ["oops", None, "NaN", "Infinity", "1e9999999"])
def test_bad_closed_pnl_never_turns_into_zero_or_partial_sum(
    tmp_path, monkeypatch, bad_value
):
    path = _fixture(tmp_path, monkeypatch)
    _position(path, "good", net_pnl="3.00")
    _position(path, "bad", net_pnl=bad_value)
    result = introspection.collect()
    assert result["trading"]["closed_today"] == 2
    assert result["trading"]["net_pnl_today"] is None
    assert "closed_pnl_unverified" in result["anomalies"]
    assert result["currencies_today"] == [
        {"currency": "ARS", "status": "CLOSED", "count": 2, "net_pnl": None}
    ]
    assert "currency_pnl_unverified" in result["anomalies"]
    assert introspection.verdict(result) == "CRITICAL"


def test_mixed_currencies_never_produce_a_synthetic_single_pnl(tmp_path, monkeypatch):
    path = _fixture(tmp_path, monkeypatch)
    _position(path, "ars", net_pnl="3.00", currency="ARS")
    _position(path, "usd", net_pnl="-5", currency="USD")
    result = introspection.collect()
    assert result["trading"]["net_pnl_today"] is None
    assert "closed_pnl_mixed_currency_unverified" in result["anomalies"]
    assert result["currencies_today"] == [
        {"currency": "ARS", "status": "CLOSED", "count": 1, "net_pnl": 3.0},
        {"currency": "USD", "status": "CLOSED", "count": 1, "net_pnl": -5.0},
    ]
    result["changes"] = []
    result["verdict"] = introspection.verdict(result)
    assert "- PnL neto hoy: NO_VERIFICADO" in introspection.render(result)


def test_valid_ars_pnl_still_reports_exact_expected_amount(tmp_path, monkeypatch):
    path = _fixture(tmp_path, monkeypatch)
    _position(path, "one", net_pnl="1.23")
    _position(path, "two", net_pnl="2.77")
    result = introspection.collect()
    assert result["trading"]["net_pnl_today"] == 4.0
    assert result["currencies_today"] == [
        {"currency": "ARS", "status": "CLOSED", "count": 2, "net_pnl": 4.0}
    ]
    assert not any("pnl_" in item for item in result["anomalies"])


def test_administrative_cancellation_cannot_show_artificial_net_pnl(tmp_path, monkeypatch):
    path = _fixture(tmp_path, monkeypatch)
    _position(path, "cancelled", net_pnl="9", status="CANCELLED_ADMIN")
    result = introspection.collect()
    assert result["currencies_today"] == [
        {"currency": "ARS", "status": "CANCELLED_ADMIN", "count": 1, "net_pnl": None}
    ]
    assert "currency_pnl_unverified" in result["anomalies"]
    assert introspection.verdict(result) == "CRITICAL"


@pytest.mark.parametrize("status,net_pnl", [
    ("OPEN", "99"),
    ("CORRUPT", None),
])
def test_unclosed_or_unknown_status_cannot_fabricate_realized_pnl(
    tmp_path, monkeypatch, status, net_pnl
):
    path = _fixture(tmp_path, monkeypatch)
    _position(path, "unverified", status=status, net_pnl=net_pnl)
    result = introspection.collect()
    assert result["currencies_today"] == [
        {"currency": "ARS", "status": status, "count": 1, "net_pnl": None}
    ]
    assert "currency_pnl_unverified" in result["anomalies"]
    assert introspection.verdict(result) == "CRITICAL"


@pytest.mark.parametrize("status", ["OPEN", "CANCELLED_ADMIN"])
def test_legitimate_nonclosed_paper_row_without_pnl_is_zero(
    tmp_path, monkeypatch, status
):
    path = _fixture(tmp_path, monkeypatch)
    _position(path, "pending", status=status, net_pnl=None)
    result = introspection.collect()
    assert result["currencies_today"] == [
        {"currency": "ARS", "status": status, "count": 1, "net_pnl": 0.0}
    ]
    assert "currency_pnl_unverified" not in result["anomalies"]


def test_empty_ledger_remains_measured_zero_with_integer_safe_counter(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    result = introspection.collect()
    assert result["trading"]["net_pnl_today"] == 0.0
    assert result["currencies_today"] == []
    assert not any("pnl_" in item for item in result["anomalies"])
