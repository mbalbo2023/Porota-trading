"""A866: local SQLite PAPER/zero is negative evidence, not an identity seal."""

from datetime import datetime
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from scripts import rc6_source_cycle_dispatch as dispatch


def _local_paper_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE observer_state(id INT,mode TEXT,real_orders_sent INT)")
        conn.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")


def test_local_zero_without_independently_attested_sqlite_identity_remains_hold(tmp_path):
    db = tmp_path / "paper.db"
    _local_paper_db(db)
    with pytest.raises(dispatch.SourceCycleBlocked, match="^PAPER_DATABASE_IDENTITY_UNVERIFIED$"):
        dispatch._paper_zero(db)


def test_due_source_cycle_must_not_launch_any_child_on_unsealed_local_zero(tmp_path):
    db = tmp_path / "paper.db"
    _local_paper_db(db)
    market = tmp_path / "market"
    market.mkdir()
    calls = []

    def must_not_launch(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("SOURCE_CYCLE_CHILD_EXECUTED_WITHOUT_DB_CUSTODY")

    with pytest.raises(dispatch.SourceCycleBlocked, match="^PAPER_DATABASE_IDENTITY_UNVERIFIED$"):
        dispatch.run_cycle(
            now=datetime(2026, 10, 28, 10, 30,
                         tzinfo=ZoneInfo("America/Argentina/Buenos_Aires")),
            db=db, market_root=market,
            lock=tmp_path / "source.lock", runner=must_not_launch,
        )
    assert calls == []
    assert not (market / "rc6_source_cycle_dispatch_state.json").exists()


def test_cli_emits_blocked_exit_two_without_false_paper_zero_receipt(tmp_path, monkeypatch, capsys):
    db = tmp_path / "paper.db"
    _local_paper_db(db)
    market = tmp_path / "market"
    market.mkdir()
    run = dispatch.run_cycle
    due = datetime(2026, 10, 28, 10, 30,
                   tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))

    def bounded_run(**_kwargs):
        return run(now=due, db=db, market_root=market,
                   lock=tmp_path / "source.lock",
                   runner=lambda *_a, **_kw: (_ for _ in ()).throw(
                       AssertionError("UNSEALED_SOURCE_LAUNCH")))

    monkeypatch.setattr(dispatch, "run_cycle", bounded_run)
    assert dispatch.main([]) == 2
    line = capsys.readouterr().out.strip()
    assert '"status": "BLOCKED"' in line
    assert "PAPER_DATABASE_IDENTITY_UNVERIFIED" in line
    assert "real_orders_sent" not in line
