"""A831: no pathname chmod/replace may escape the source owner's directory.

Adversarial OFFHOST fixtures only: never touch host, providers or real orders.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts import rc6_source_cycle_dispatch as dispatch
from scripts import rc6_full_universe_evidence as evidence


ART = ZoneInfo("America/Argentina/Buenos_Aires")


def _paper_db(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE observer_state(id INT,mode TEXT,real_orders_sent INT)")
        db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_dispatcher_never_chmods_external_sentinel_after_lstat(tmp_path, monkeypatch):
    # Isolate publication behavior, not physical PAPER identity admission.
    monkeypatch.setattr(dispatch, "_paper_zero", lambda db: None)
    market = tmp_path / "market"
    market.mkdir()
    db = tmp_path / "paper.db"
    _paper_db(db)
    foreign = tmp_path / "another_application_secret.txt"
    foreign.write_text("KEEP_PRIVATE", encoding="utf-8")
    foreign.chmod(0o600)
    original_chmod = Path.chmod
    injected = []

    def swap_before_chmod(path, mode, *args, **kwargs):
        if path == market / "rc6_public_sources_latest.json":
            path.unlink()
            path.symlink_to(foreign)
            injected.append(True)
        return original_chmod(path, mode, *args, **kwargs)

    class Runner:
        def __call__(self, command, **kwargs):
            output = {"rc6_public_source_capture.py": "rc6_public_sources_latest.json",
                      "rc6_iol_shadow_collect.py": "iol_shadow_latest.json",
                      "rc6_full_universe_evidence.py": "rc6_instrument_evidence_latest.json"}
            target = market / output[Path(command[1]).name]
            temporary = target.with_suffix(".writing")
            temporary.write_text("{}", encoding="utf-8")
            os.replace(temporary, target)
            return SimpleNamespace(returncode=0,
                                   stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1 OBSERVED=1\n")

    monkeypatch.setattr(Path, "chmod", swap_before_chmod)
    result = dispatch.run_cycle(now=datetime(2026, 11, 5, 10, 30, tzinfo=ART),
                                db=db, market_root=market,
                                lock=tmp_path / "lock", runner=Runner())
    assert result["status"] == "COMPLETE"
    assert injected == []  # No pathname chmod after an lstat/rename boundary.
    assert _mode(foreign) == 0o600
    assert foreign.read_text(encoding="utf-8") == "KEEP_PRIVATE"


def test_evidence_publication_never_chmods_external_alias(tmp_path, monkeypatch):
    market = tmp_path / "market"
    market.mkdir()
    monkeypatch.setattr(evidence, "ROOT", market)
    target = market / "rc6_instrument_evidence_latest.json"
    foreign = tmp_path / "another_application_secret.txt"
    foreign.write_text("KEEP_PRIVATE", encoding="utf-8")
    foreign.chmod(0o600)
    original_chmod = os.chmod
    injected = []

    def swap_evidence_temp(path, mode, *args, **kwargs):
        if Path(path).name.startswith(".rc6-evidence-"):
            Path(path).unlink()
            Path(path).symlink_to(foreign)
            injected.append(True)
        return original_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", swap_evidence_temp)
    evidence.atomic(target, {"schema": "fixt", "ñ": 1})
    assert injected == []
    assert _mode(foreign) == 0o600
    assert foreign.read_text(encoding="utf-8") == "KEEP_PRIVATE"
    assert target.is_file() and not target.is_symlink()
    assert _mode(target) == 0o644


def test_state_write_rejects_symlinked_parent_without_foreign_write(tmp_path):
    foreign = tmp_path / "another_application"
    foreign.mkdir()
    sentinel = foreign / "rc6_source_cycle_dispatch_state.json"
    sentinel.write_text('{"owner":"another app"}', encoding="utf-8")
    market = tmp_path / "market"
    market.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_write(market / sentinel.name, {"schema": dispatch.STATE_SCHEMA},
                              expected_parent=market)
    assert sentinel.read_text(encoding="utf-8") == '{"owner":"another app"}'
    assert sorted(p.name for p in foreign.iterdir()) == [sentinel.name]


def test_state_write_parent_swap_mid_fchmod_cleans_anchored_temp(tmp_path, monkeypatch):
    market = tmp_path / "market"
    market.mkdir()
    relocated = tmp_path / "owned_before_swap"
    foreign = tmp_path / "another_application"
    foreign.mkdir()
    sentinel = foreign / "rc6_source_cycle_dispatch_state.json"
    sentinel.write_text('{"owner":"another app"}', encoding="utf-8")
    original_fchmod = os.fchmod
    injected = []

    def swap_parent(fd, mode):
        original_fchmod(fd, mode)
        if not injected:
            market.rename(relocated)
            market.symlink_to(foreign, target_is_directory=True)
            injected.append(True)

    monkeypatch.setattr(os, "fchmod", swap_parent)
    with pytest.raises(ValueError, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_write(market / sentinel.name, {"schema": dispatch.STATE_SCHEMA},
                              expected_parent=market)
    assert injected == [True]
    assert sentinel.read_text(encoding="utf-8") == '{"owner":"another app"}'
    assert not list(relocated.iterdir())  # FD-relative cleanup still in old owned inode.


def test_state_writer_does_not_create_unprovisioned_parent(tmp_path):
    market = tmp_path / "new_parent" / "market"
    with pytest.raises(ValueError, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_write(market / "rc6_source_cycle_dispatch_state.json",
                              {"schema": dispatch.STATE_SCHEMA}, expected_parent=market)
    assert not market.exists()


def test_evidence_and_state_keep_existing_compact_json_wire_bytes(tmp_path, monkeypatch):
    market = tmp_path / "market"
    market.mkdir()
    monkeypatch.setattr(evidence, "ROOT", market)
    doc = {"schema": "audit", "ñ": [1, 2], "z": True}
    path = market / "rc6_instrument_evidence_latest.json"
    evidence.atomic(path, doc)
    assert path.read_bytes() == (json.dumps(doc, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":")) + "\n").encode()
    state = {"schema": dispatch.STATE_SCHEMA, "session_date": "2026-11-05",
             "last_public_success_at": None, "last_evidence_success_at": None}
    state_path = market / "rc6_source_cycle_dispatch_state.json"
    dispatch._state_write(state_path, state, expected_parent=market)
    assert state_path.read_bytes() == (json.dumps(state, sort_keys=True,
                                   separators=(",", ":")) + "\n").encode()


def test_state_requires_independent_exact_parent_owner(tmp_path):
    market = tmp_path / "market"
    market.mkdir()
    foreign = tmp_path / "another_application"
    foreign.mkdir()
    sentinel = foreign / "rc6_source_cycle_dispatch_state.json"
    sentinel.write_text('{"owner":"another app"}', encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_CYCLE_STATE_UNSAFE"):
        dispatch._state_write(sentinel, {"schema": dispatch.STATE_SCHEMA},
                              expected_parent=market)
    assert sentinel.read_text(encoding="utf-8") == '{"owner":"another app"}'
