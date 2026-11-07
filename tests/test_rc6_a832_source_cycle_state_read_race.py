"""A832: source-cycle state and output metadata reads cannot follow foreign paths."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace

import pytest

from scripts import rc6_source_cycle_dispatch as dispatch


NOW = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)


def _state(path: Path, *, verified: bool = False) -> None:
    path.write_text(json.dumps({
        "schema": dispatch.STATE_SCHEMA,
        "session_date": "2026-11-05" if verified else None,
        "last_public_success_at": NOW.isoformat() if verified else None,
        "last_evidence_success_at": NOW.isoformat() if verified else None,
    }))
    path.chmod(0o644)


def test_pinned_state_read_preserves_valid_and_missing_contract(tmp_path):
    state = tmp_path / "rc6_source_cycle_dispatch_state.json"
    assert dispatch._state_read(state, NOW)["last_public_success_at"] is None
    _state(state, verified=True)
    assert dispatch._state_read(state, NOW)["last_public_success_at"] == NOW.isoformat()


def test_state_final_alias_injected_at_open_is_never_consumed(tmp_path, monkeypatch):
    state = tmp_path / "rc6_source_cycle_dispatch_state.json"
    foreign = tmp_path / "another_application.json"
    _state(state)
    _state(foreign, verified=True)
    real_open = os.open
    injected = []

    def swap_before_fd_open(path, flags, *args, **kwargs):
        if path == state.name and kwargs.get("dir_fd") is not None:
            state.unlink()
            state.symlink_to(foreign)
            injected.append(True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_fd_open)
    with pytest.raises(dispatch.SourceCycleBlocked, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_read(state, NOW)
    assert injected == [True]
    assert state.is_symlink()


def test_state_final_alias_injected_after_fd_open_is_never_consumed(tmp_path, monkeypatch):
    state = tmp_path / "rc6_source_cycle_dispatch_state.json"
    foreign = tmp_path / "another_application.json"
    _state(state)
    _state(foreign, verified=True)
    original_inode = state.stat().st_ino
    real_fstat = os.fstat
    injected = []

    def swap_after_fd_open(fd):
        meta = real_fstat(fd)
        if stat.S_ISREG(meta.st_mode) and meta.st_ino == original_inode and not injected:
            state.unlink()
            state.symlink_to(foreign)
            injected.append(True)
        return meta

    monkeypatch.setattr(os, "fstat", swap_after_fd_open)
    with pytest.raises(dispatch.SourceCycleBlocked, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_read(state, NOW)
    assert injected == [True]
    assert state.is_symlink()


def test_state_inplace_changed_during_fd_read_is_rejected(tmp_path, monkeypatch):
    state = tmp_path / "rc6_source_cycle_dispatch_state.json"
    _state(state)
    before_inode = state.stat().st_ino
    real_read = os.read
    injected = []

    def change_during_read(fd, count):
        if os.fstat(fd).st_ino == before_inode and not injected:
            state.write_text(json.dumps({"schema": dispatch.STATE_SCHEMA,
                                         "session_date": "2026-11-05",
                                         "last_public_success_at": NOW.isoformat(),
                                         "last_evidence_success_at": NOW.isoformat()}))
            injected.append(True)
        return real_read(fd, count)

    monkeypatch.setattr(os, "read", change_during_read)
    with pytest.raises(dispatch.SourceCycleBlocked, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_read(state, NOW)
    assert injected == [True]


def test_state_symlinked_parent_never_consumes_other_application_json(tmp_path):
    foreign_root = tmp_path / "another_application"
    foreign_root.mkdir()
    foreign = foreign_root / "rc6_source_cycle_dispatch_state.json"
    _state(foreign, verified=True)
    alias = tmp_path / "market"
    alias.symlink_to(foreign_root, target_is_directory=True)
    with pytest.raises(dispatch.SourceCycleBlocked, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_read(alias / foreign.name, NOW)


def test_oversize_state_rejected_before_bounded_decode(tmp_path):
    state = tmp_path / "rc6_source_cycle_dispatch_state.json"
    state.write_bytes(b"x" * 4097)
    with pytest.raises(dispatch.SourceCycleBlocked, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_read(state, NOW)


def test_dispatcher_does_not_mark_0600_child_output_published(tmp_path, monkeypatch):
    # Isolate publication mode contract; the real PAPER gate remains HOLD.
    monkeypatch.setattr(dispatch, "_paper_zero", lambda db: None)
    db = tmp_path / "paper.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE observer_state(id INT, mode TEXT, real_orders_sent INT)")
        connection.execute("INSERT INTO observer_state VALUES (1,'PRODUCTION_PAPER',0)")
    market = tmp_path / "market"
    market.mkdir()

    class Runner:
        def __call__(self, command, **kwargs):
            output = {Path(relative).name: name for _, relative, _, name in dispatch.STAGES}
            target = market / output[Path(command[1]).name]
            target.write_text("{}")
            target.chmod(0o600)
            return SimpleNamespace(returncode=0,
                                   stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1 OBSERVED=1\n")

    result = dispatch.run_cycle(now=NOW, db=db, market_root=market,
                                lock=tmp_path / "source.lock", runner=Runner())
    assert result["status"] == "DEGRADED"
    assert set(result["stages"].values()) == {"OUTPUT_UNVERIFIED"}
    assert not (market / "rc6_source_cycle_dispatch_state.json").exists()
