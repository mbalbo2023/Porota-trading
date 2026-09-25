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

def test_operational_catalog_includes_all_available_current_families(monkeypatch, tmp_path):
    db = tmp_path / "observer.db"
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE financial_instrument_catalog (ticker TEXT, status TEXT, instrument_type TEXT)")
        conn.executemany("INSERT INTO financial_instrument_catalog VALUES (?,?,?)", [
            ("GGAL", "AVAILABLE", "ACCIONES"), ("AAPL", "AVAILABLE", "CEDEARS"),
            ("BAD", "DISABLED", "ACCIONES"), ("BONO", "AVAILABLE", "BONOS")])
    monkeypatch.delenv("POROTA_IOL_SHADOW_UNIVERSE", raising=False)
    monkeypatch.setattr(runtime, "DEFAULT_DB", str(db))
    assert runtime._operational_universe() == ["AAPL", "BONO", "GGAL"]


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
        ["AAPL", "BONO", "GGAL"], "CONFIGURED_OPERATIONAL_SUBSET"
    )


def test_configured_universe_accepts_available_non_equity_family(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "observer.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE financial_instrument_catalog (ticker TEXT, status TEXT, instrument_type TEXT)")
        conn.execute("INSERT INTO financial_instrument_catalog VALUES (?,?,?)", ("BONO", "AVAILABLE", "BONOS"))
    monkeypatch.setattr(runtime, "DEFAULT_DB", str(db))
    monkeypatch.setenv("POROTA_IOL_SHADOW_UNIVERSE", "BONO,SPY")
    assert runtime._operational_universe_with_source() == (
        ["BONO"], "CONFIGURED_OPERATIONAL_SUBSET"
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


def test_empty_operational_catalog_blocks_configured_fallback_symbols(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "observer.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE financial_instrument_catalog (ticker TEXT, status TEXT, instrument_type TEXT)")
    monkeypatch.setattr(runtime, "DEFAULT_DB", str(db))
    monkeypatch.setenv("POROTA_IOL_SHADOW_UNIVERSE", "GGAL")
    assert runtime._operational_universe_with_source() == (
        [], "CONFIGURED_UNIVERSE_REJECTED_BY_SCOPE"
    )
    monkeypatch.delenv("POROTA_IOL_SHADOW_UNIVERSE")
    assert runtime._operational_universe_with_source() == ([], "OPERATIONAL_CATALOG_EMPTY")


def test_rotation_cycle_resets_seen_and_advances_cursor(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(runtime, "DEFAULT_ROOT", tmp_path)
    monkeypatch.setattr(runtime, "BATCH_SIZE", 2)
    universe = ["A", "B", "C", "D"]
    fingerprint = runtime._fingerprint(universe)
    (tmp_path / "iol_shadow_rotation.json").write_text(json.dumps({
        "schema_version": 2,
        "universe_size": len(universe),
        "universe_fingerprint": fingerprint,
        "cycle_id": 7,
        "next_index": 2,
        "seen": universe,
    }), encoding="utf-8")

    first, start, state = runtime._rotation(universe, fingerprint)
    assert first == ["A", "B"]
    assert start == 0
    assert state["cycle_id"] == 8
    assert state["seen"] == []
    committed = runtime._commit_rotation(universe, fingerprint, start, first, state)
    assert committed["next_index"] == 2
    assert committed["seen"] == ["A", "B"]

    second, start, state = runtime._rotation(universe, fingerprint)
    assert second == ["C", "D"]
    assert start == 2
    assert state["cycle_id"] == 8
    committed = runtime._commit_rotation(universe, fingerprint, start, second, state)
    assert committed["next_index"] == 0
    assert committed["seen"] == universe
