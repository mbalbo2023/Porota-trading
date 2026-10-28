"""Offline proofs of bounded, non-mutating EOD idle checkpoints (A145)."""
from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

import rc6_local_eod_history as local
from tests.test_rc6_issue600_regressions import NOW, sample, source


def snapshot(path):
    with sqlite3.connect(path) as db:
        return (
            db.execute('SELECT * FROM local_eod_cursor_v1').fetchall(),
            db.execute('SELECT * FROM local_eod_receipts_v1 ORDER BY version_id').fetchall(),
            db.execute('SELECT * FROM history_local_daily_v1 ORDER BY known_at').fetchall(),
        )


def test_ready_idle_neither_initializes_nor_rewrites_cold(source, tmp_path, monkeypatch):
    sample(source)
    cold = tmp_path / 'cold.db'
    first = local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    assert first['state'] == 'LOCAL_EOD_READY' and first['imported'] == 1
    before = snapshot(cold)

    def forbidden(*args, **kwargs):
        pytest.fail('idle path must not initialize COLD, acquire its writer or commit')

    monkeypatch.setattr(local, 'init_schema', forbidden)
    monkeypatch.setattr(local, '_ColdStore', forbidden)
    idle = local.consolidate(source.path, cold, as_of=NOW + timedelta(seconds=1),
                             free_bytes=0, before_commit=forbidden)
    assert idle['state'] == 'LOCAL_EOD_READY' and idle['idle_fast_path'] is True
    assert idle['cursor'] == first['cursor'] and idle['imported'] == idle['rejected'] == 0
    assert idle['provider_requests'] == 0 and idle['real_routes'] == 'NOT_CALLED'
    assert idle['real_orders_sent'] == 0 and idle['history_v2_writes'] is False
    assert snapshot(cold) == before


def test_ready_still_imports_late_hot_revision_and_then_goes_idle(source, tmp_path):
    sample(source)
    cold = tmp_path / 'cold.db'
    first = local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    sample(source, price='101', known=(NOW + timedelta(seconds=1)).isoformat())
    second = local.consolidate(source.path, cold, as_of=NOW + timedelta(seconds=2),
                                free_bytes=local.RESERVE)
    assert second['state'] == 'LOCAL_EOD_READY' and second['imported'] == 1
    assert second['cursor'] > first['cursor'] and not second.get('idle_fast_path')
    with sqlite3.connect(cold) as db:
        assert db.execute('SELECT COUNT(*) FROM local_eod_receipts_v1').fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM history_local_daily_v1').fetchone()[0] == 2
    idle = local.consolidate(source.path, cold, as_of=NOW + timedelta(seconds=3),
                              free_bytes=local.RESERVE)
    assert idle['idle_fast_path'] is True and idle['cursor'] == second['cursor']


def test_backlog_always_uses_existing_bounded_writer(source, tmp_path):
    sample(source)
    sample(source, price='101', known=(NOW - timedelta(seconds=1)).isoformat())
    cold = tmp_path / 'cold.db'
    first = local.consolidate(source.path, cold, as_of=NOW,
                               batch_size=1, free_bytes=local.RESERVE)
    assert first['state'] == 'BACKLOG' and first['cursor'] == 1
    assert local._committed_ready_cursor(cold, local.fingerprint({
        'path': str(source.path), 'device': Path(source.path).stat().st_dev,
        'inode': Path(source.path).stat().st_ino})) is None
    second = local.consolidate(source.path, cold, as_of=NOW + timedelta(seconds=1),
                                batch_size=1, free_bytes=local.RESERVE)
    assert second['state'] == 'LOCAL_EOD_READY' and second['cursor'] == 2
    assert second['imported'] == 1 and not second.get('idle_fast_path')


def test_hot_append_between_cold_probe_and_final_check_is_not_skipped(source, tmp_path, monkeypatch):
    sample(source)
    cold = tmp_path / 'cold.db'
    local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    original = local._committed_ready_cursor

    def concurrent_revision(*args):
        checkpoint = original(*args)
        assert checkpoint == 1
        sample(source, price='102', known=(NOW + timedelta(seconds=1)).isoformat())
        return checkpoint

    monkeypatch.setattr(local, '_committed_ready_cursor', concurrent_revision)
    second = local.consolidate(source.path, cold, as_of=NOW + timedelta(seconds=2),
                                free_bytes=local.RESERVE)
    assert second['state'] == 'LOCAL_EOD_READY' and second['imported'] == 1
    assert second['cursor'] == 2 and not second.get('idle_fast_path')
    with sqlite3.connect(cold) as db:
        assert db.execute('SELECT COUNT(*) FROM local_eod_receipts_v1').fetchone()[0] == 2


def test_ready_never_overrides_paper_runtime_admission(source, tmp_path):
    sample(source)
    cold = tmp_path / 'cold.db'
    local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    before = snapshot(cold)
    with source.connect() as db:
        db.execute('UPDATE observer_state SET session_state=?', ('MARKET_OPEN',))
    blocked = local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    assert blocked['state'] == 'BLOCKED' and blocked['reason'] == 'RUNTIME_CLOSED_UNVERIFIED'
    with source.connect() as db:
        db.execute('UPDATE observer_state SET session_state=?,real_orders_sent=?', ('MARKET_CLOSED', 1))
    with pytest.raises(ValueError, match='LOCAL_EOD_PAPER_SAFETY_REQUIRED'):
        local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    assert snapshot(cold) == before


def test_ready_cold_cursor_ahead_of_hot_fails_closed(source, tmp_path):
    sample(source)
    cold = tmp_path / 'cold.db'
    local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    with sqlite3.connect(cold) as db:
        db.execute('UPDATE local_eod_cursor_v1 SET cursor=999')
        db.execute('INSERT INTO local_eod_receipts_v1 SELECT source_id,999,payload_hash, '
                   'published_at,source_known_at,identity_json,body_json '
                   'FROM local_eod_receipts_v1 WHERE version_id=1')
    blocked = local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    assert blocked['state'] == 'BLOCKED' and blocked['reason'] == 'LOCAL_EOD_HOT_CURSOR_REGRESSION'


def test_missing_ready_receipt_never_authorizes_idle(source, tmp_path):
    sample(source)
    cold = tmp_path / 'cold.db'
    local.consolidate(source.path, cold, as_of=NOW, free_bytes=local.RESERVE)
    with sqlite3.connect(cold) as db:
        db.execute('DELETE FROM local_eod_receipts_v1')
    # The checkpoint cannot justify skipping COLD initialization. The normal
    # writer is entered; no claim that a damaged ledger is repaired here.
    assert local._committed_ready_cursor(cold, local.fingerprint({
        'path': str(source.path), 'device': Path(source.path).stat().st_dev,
        'inode': Path(source.path).stat().st_ino})) is None
