from pathlib import Path
from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP
from iol_shadow_collector_rc6 import CollectionPolicy
import scripts.rc6_iol_shadow_collect as runtime

def test_iol_collector_policy_is_bounded():
    policy=CollectionPolicy()
    policy.validate()
    assert policy.min_interval_seconds >= 1
    assert policy.max_calls_per_minute <= 40

def test_iol_adapter_denies_mutating_tools(tmp_path: Path):
    client=OAuthStoreReadOnlyMCP(tmp_path/"missing.json")
    try:
        client.call("place_order", {})
    except PermissionError as exc:
        assert "IOL_SHADOW_TOOL_DENIED" in str(exc)
    else:
        raise AssertionError("mutation unexpectedly admitted")

def test_configured_operational_universe_is_unique(monkeypatch):
    monkeypatch.setenv("POROTA_IOL_SHADOW_UNIVERSE", "aapl,ggal,AAPL")
    universe=runtime._operational_universe()
    assert universe == ["AAPL", "GGAL"]

def test_operational_catalog_includes_actions_and_cedears(monkeypatch, tmp_path):
    db = tmp_path / "observer.db"
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE financial_instrument_catalog (ticker TEXT, status TEXT, instrument_type TEXT)")
        conn.executemany("INSERT INTO financial_instrument_catalog VALUES (?,?,?)", [
            ("GGAL", "AVAILABLE", "ACCIONES"), ("AAPL", "AVAILABLE", "CEDEARS"),
            ("BAD", "DISABLED", "ACCIONES"), ("BONO", "AVAILABLE", "BONOS")])
    monkeypatch.delenv("POROTA_IOL_SHADOW_UNIVERSE", raising=False)
    monkeypatch.setattr(runtime, "DEFAULT_DB", str(db))
    assert runtime._operational_universe() == ["AAPL", "GGAL"]


def test_systemd_collector_exposes_repository_root_to_python():
    service = (Path(__file__).parents[1] / "systemd" / "porota-iol-shadow-collector-rc6.service").read_text(encoding="utf-8")
    assert "Environment=PYTHONPATH=/opt/porota-trading" in service
    assert "rc6_iol_shadow_collect.py && /bin/chmod 0644 /opt/porota-trading/data/market/iol_shadow_latest.json" in service


def test_configured_universe_is_intersected_with_available_operational_families(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "observer.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE financial_instrument_catalog (ticker TEXT, status TEXT, instrument_type TEXT)")
        conn.executemany("INSERT INTO financial_instrument_catalog VALUES (?,?,?)", [
            ("GGAL", "AVAILABLE", "ACCIONES"),
            ("AAPL", "AVAILABLE", "CEDEARS"),
            ("BONO", "AVAILABLE", "BONOS"),
            ("OLD", "DISABLED", "ACCIONES"),
        ])
    monkeypatch.setattr(runtime, "DEFAULT_DB", str(db))
    monkeypatch.setenv("POROTA_IOL_SHADOW_UNIVERSE", "aapl,ggal,BONO,SPY,AAPL")
    assert runtime._operational_universe_with_source() == (
        ["AAPL", "GGAL"], "CONFIGURED_OPERATIONAL_SUBSET"
    )


def test_configured_universe_with_no_allowed_symbols_fails_closed(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "observer.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE financial_instrument_catalog (ticker TEXT, status TEXT, instrument_type TEXT)")
        conn.execute("INSERT INTO financial_instrument_catalog VALUES (?,?,?)", ("BONO", "AVAILABLE", "BONOS"))
    monkeypatch.setattr(runtime, "DEFAULT_DB", str(db))
    monkeypatch.setenv("POROTA_IOL_SHADOW_UNIVERSE", "BONO,SPY")
    assert runtime._operational_universe_with_source() == (
        [], "CONFIGURED_UNIVERSE_REJECTED_BY_SCOPE"
    )


def test_cycle_progress_counts_only_ready_rows_fresh_for_shadow_decisions(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    import json
    monkeypatch.setattr(runtime, "DEFAULT_ROOT", tmp_path)
    now = datetime.now(timezone.utc)
    (tmp_path / "iol_shadow_latest.json").write_text(json.dumps({
        "symbols": [
            {"symbol": "GGAL", "state": "READY", "captured_at": now.isoformat()},
            {"symbol": "YPFD", "state": "READY", "captured_at": (now - timedelta(seconds=121)).isoformat()},
        ],
        "telemetry": {},
    }), encoding="utf-8")
    payload = runtime._publish_progress(
        2, ["GGAL", "YPFD"], "OBSERVER_OPERATIONAL_CATALOG", "fingerprint",
        {"seen": ["GGAL", "YPFD"], "cycle_id": 3},
        {"state": "READY"},
    )
    assert payload["progress"]["completed"] == 2
    assert payload["progress"]["fresh"] == 1
    assert payload["progress"]["freshness_max_age_seconds"] == 120
