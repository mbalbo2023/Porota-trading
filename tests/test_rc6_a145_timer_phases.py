"""A145 deterministic scheduler contracts; no droplet/provider/systemd mutation."""
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import iol_shadow_collector_rc6 as iol
from scripts import rc6_source_cycle_dispatch as dispatch


ROOT = Path(__file__).resolve().parents[1]
ART = ZoneInfo("America/Argentina/Buenos_Aires")


def instant(day=28, hour=10, minute=30, month=10, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=ART)


def _paper_db(path, *, mode="PRODUCTION_PAPER", orders=0):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observer_state(id INT,mode TEXT,real_orders_sent INT)")
        db.execute("INSERT INTO observer_state VALUES(1,?,?)", (mode, orders))


class StubRunner:
    def __init__(self, market):
        self.market = market
        self.calls = []
        self.response = "IOL_SHADOW_COLLECTION=COMPLETE READY=1 OBSERVED=1\n"

    def __call__(self, command, **kwargs):
        self.calls.append(Path(command[1]).name)
        assert kwargs["capture_output"] and kwargs["text"]
        assert kwargs["timeout"] <= 110
        assert kwargs["cwd"] == str(ROOT)
        outputs = {"rc6_iol_shadow_collect.py": "iol_shadow_latest.json",
                   "rc6_public_source_capture.py": "rc6_public_sources_latest.json",
                   "rc6_full_universe_evidence.py": "rc6_instrument_evidence_latest.json"}
        (self.market / outputs[self.calls[-1]]).write_text("{}")
        return SimpleNamespace(returncode=0, stdout=self.response)


def _cycle(tmp_path, runner, now):
    return dispatch.run_cycle(now=now, db=tmp_path / "paper.db",
        market_root=tmp_path / "market", lock=tmp_path / "source.lock", runner=runner)


def test_cycle_iol_public_minute_shadow_parity_evidence_5m_persistent_ttl(tmp_path):
    _paper_db(tmp_path / "paper.db")
    market = tmp_path / "market"
    market.mkdir()
    runner = StubRunner(market)
    first = _cycle(tmp_path, runner, instant())
    assert first["status"] == "COMPLETE"
    assert runner.calls == [row[1].split("/")[-1] for row in dispatch.STAGES]
    runner.calls.clear()
    second = _cycle(tmp_path, runner, instant(minute=31))
    assert second["status"] == "COMPLETE"
    assert runner.calls == ["rc6_iol_shadow_collect.py", "rc6_public_source_capture.py"]
    runner.calls.clear()
    third = _cycle(tmp_path, runner, instant(minute=35))
    assert third["status"] == "COMPLETE"
    assert runner.calls == ["rc6_iol_shadow_collect.py", "rc6_public_source_capture.py",
                            "rc6_full_universe_evidence.py"]
    runner.calls.clear()
    fourth = _cycle(tmp_path, runner, instant(minute=45))
    assert fourth["status"] == "COMPLETE"
    assert runner.calls == ["rc6_iol_shadow_collect.py", "rc6_public_source_capture.py",
                            "rc6_full_universe_evidence.py"]
    durable = json.loads((market / "rc6_source_cycle_dispatch_state.json").read_text())
    assert durable["schema"] == dispatch.STATE_SCHEMA
    assert durable["session_date"] == "2026-10-28"


def test_nontrading_holiday_closed_2027_never_launch_provider(tmp_path):
    market = tmp_path / "market"
    market.mkdir()
    runner = StubRunner(market)
    for now in (instant(hour=2), instant(day=31),
                instant(day=1, month=1, year=2027)):
        result = dispatch.run_cycle(now=now, db=tmp_path / "missing.db",
            market_root=market, lock=tmp_path / "lock", runner=runner)
        assert result["status"] == "NOT_DUE"
        assert not result["stages"]
    assert runner.calls == []


def test_paper_zero_and_symlink_state_fail_closed(tmp_path):
    _paper_db(tmp_path / "paper.db", orders=1)
    market = tmp_path / "market"
    market.mkdir()
    runner = StubRunner(market)
    with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_ZERO_REQUIRED"):
        _cycle(tmp_path, runner, instant())
    assert runner.calls == []
    with sqlite3.connect(tmp_path / "paper.db") as db:
        db.execute("UPDATE observer_state SET real_orders_sent=0")
    target = tmp_path / "foreign.json"
    target.write_text("{}")
    (market / "rc6_source_cycle_dispatch_state.json").symlink_to(target)
    with pytest.raises(dispatch.SourceCycleBlocked, match="SOURCE_CYCLE_STATE_UNSAFE"):
        _cycle(tmp_path, runner, instant())
    assert runner.calls == []


def test_source_lock_is_global_and_nonblocking(tmp_path):
    _paper_db(tmp_path / "paper.db")
    market = tmp_path / "market"
    market.mkdir()
    runner = StubRunner(market)
    lock = tmp_path / "source.lock"
    with lock.open("w") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(dispatch.SourceCycleBlocked, match="SOURCE_CYCLE_LOCK_BUSY"):
            _cycle(tmp_path, runner, instant())
    assert runner.calls == []


def test_legacy_collector_zero_exit_not_promoted_to_success(tmp_path):
    _paper_db(tmp_path / "paper.db")
    market = tmp_path / "market"
    market.mkdir()
    runner = StubRunner(market)
    runner.response = "IOL_SHADOW_COLLECTION=NOT_DUE_OUTSIDE_MARKET\n"
    result = _cycle(tmp_path, runner, instant())
    assert result["status"] == "DEGRADED"
    assert result["stages"]["iol"] == "COLLECTOR_NOT_COMPLETED"


def test_verified_art_phases_and_com19024_snapshot_switch():
    assert dispatch.phase_status(instant(hour=8, minute=30), "morning")[0]
    assert not dispatch.phase_status(instant(hour=9), "morning")[0]
    assert dispatch.phase_status(instant(hour=10, minute=30), "open")[0]
    assert not dispatch.phase_status(instant(hour=17), "open")[0]
    assert dispatch.phase_status(instant(hour=17, minute=8), "postclose_snapshot")[0]
    assert not dispatch.phase_status(instant(hour=18, minute=8), "postclose_snapshot")[0]
    nov2 = instant(day=2, month=11, hour=18, minute=8)
    assert dispatch.phase_status(nov2, "postclose_snapshot")[0]
    assert not dispatch.phase_status(nov2.replace(hour=17), "postclose_snapshot")[0]
    assert dispatch.phase_status(instant(hour=23, minute=20), "db_postclose")[0]
    assert not dispatch.phase_status(instant(hour=17, minute=20), "db_postclose")[0]


def test_collector_family_late_windows_fail_closed_for_ambiguous_identity():
    old = instant(day=30, hour=17, minute=30)
    new = instant(day=2, month=11, hour=17, minute=30)
    assert not iol.is_operational_market_window(old, family="ACCIONES")
    assert iol.is_operational_market_window(new, family="ACCIONES")
    assert iol.is_operational_market_window(new, family="CEDEARS")
    assert iol.is_operational_market_window(new, family="OPCIONES")
    assert not iol.is_operational_market_window(new, family="CAUCIONES")
    assert not iol.is_operational_market_window(new, family="BONOS")
    assert not iol.is_operational_market_window(new, family="FUTUROS")
    assert not iol.is_operational_market_window(new, family="ETF")
    assert not iol.is_operational_market_window(new)  # mixed basket identity
    assert not iol.is_operational_market_window(instant(day=2, month=1, year=2027),
                                                family="ACCIONES")


def test_host_calendar_wiring_keeps_a125_a138_and_oauth_scope():
    policy = json.loads((ROOT / "ops/policy/host-control-plane-reconciliation-v2.json").read_text())
    active = {Path(name).name: row for name, row in policy["units"].items()
              if row.get("role") == "ACTIVE_TIMER" and row.get("active") is True}
    assert len(active) == 16
    assert active["porota-iol-oauth-health-rc6.timer"]["suspend_during_deploy"] is False
    assert all(row.get("suspend_during_deploy", True) is True for name, row in active.items()
               if name != "porota-iol-oauth-health-rc6.timer")
    oauth = (ROOT / "systemd/porota-iol-oauth-health-rc6.timer").read_text()
    assert "OnUnitActiveSec=10min" in oauth
    assert "09:30:00" in oauth and "10:20:00" in oauth
    preopen = (ROOT / "systemd/porota-preopen-rc6.timer").read_text()
    assert "10:20:00" in preopen  # A125 remains outside schedule edit
    byma = (ROOT / "systemd/porota-byma-morning-watch-rc6.timer").read_text()
    assert "Persistent=false" in byma
    verify = (ROOT / "ops/systemd/porota-rc6-candle-inmarket-verify.timer").read_text()
    assert "OnUnitActiveSec=" not in verify and "OnBootSec=" not in verify
    candle = (ROOT / "systemd/porota-candle-integrity-rc6.timer").read_text()
    assert "23:45:00" in candle and "OnUnitActiveSec=" not in candle
    db = (ROOT / "systemd/porota-full-db-integrity-rc6.timer").read_text()
    assert "23:20:00" in db and "17:20:00" not in db
    snapshot = (ROOT / "systemd/porota-snapshot-postclose-rc6.timer").read_text()
    assert "17:08:00" in snapshot and "18:08:00" in snapshot
