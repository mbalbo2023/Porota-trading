"""Adversarial, offline evidence-only reconstruction of bounded source data."""
from __future__ import annotations

import json
import socket
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import scripts.rc6_history_fresh_sidecar as fresh_module
import scripts.rc6_history_fresh_verify as verify_module
from scripts.rc6_history_fresh_sidecar import capture, preflight
from scripts.rc6_history_fresh_verify import verify

TARGET = ('GGAL', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS')
NOW = datetime(2026, 10, 26, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('NETWORK_FORBIDDEN_IN_FRESH_FIXTURE')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = NOW + timedelta(minutes=1)
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(fresh_module, 'datetime', FrozenDatetime)
    monkeypatch.setattr(verify_module, 'datetime', FrozenDatetime)


def _records(path):
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM fresh_captures_rc6')]


def test_ppi_unknown_currency_basis_pit_stays_in_evidence_only_sidecar(tmp_path):
    legacy = tmp_path / 'market_history.db'
    legacy.write_bytes(b'legacy-do-not-touch')
    payload = [dict(date='2026-10-23T21:00:00Z', openingPrice=100, max=110,
                    min=90, price=105, volume=12)]
    sidecar = tmp_path / 'fresh.sqlite'
    result = capture(sidecar, [TARGET], 'PPI', lambda *_: payload,
                     now=NOW, protected_paths=[legacy])
    assert legacy.read_bytes() == b'legacy-do-not-touch'
    assert result['state'] == 'EVIDENCE_ONLY_NO_GO'
    assert result['history_v2_writes'] == 0 and not result['execution_allowed']
    assert result['results'][0]['provider_currency_status'] == 'REQUEST_ONLY'
    row = _records(sidecar)[0]
    assert row['price_basis_status'] == 'UNVERIFIED'
    assert row['readiness_implication'] == 'NONE' and row['execution_allowed'] == 0
    assert row['first_known_at'] >= NOW.isoformat()
    assert row['retained_rows'] == 1 and json.loads(row['retained_rows_json']) == payload
    with sqlite3.connect(sidecar) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='history_canonical_v2'").fetchone()


def test_expired_and_unknown_dates_are_physically_absent_from_sidecar(tmp_path):
    older = {'date': '2025-10-26', 'price': 123}
    recent = {'date': '2026-10-23', 'price': 124}
    unknown = {'date': 'not-a-date', 'price': 125}
    result = capture(tmp_path / 'fresh.sqlite', [TARGET], 'DATA912',
                     lambda *_: [older, recent, unknown], now=NOW)
    row = _records(tmp_path / 'fresh.sqlite')[0]
    assert result['cutoff_day'] == '2025-10-27'
    assert row['dropped_expired'] == 1 and row['dropped_invalid'] == 1
    assert json.loads(row['retained_rows_json']) == [recent]
    assert '123' not in row['retained_rows_json'] and '125' not in row['retained_rows_json']


@pytest.mark.parametrize('source,collision', [
    ('PPI', ('GGAL', 'ACCIONES', 'BYMA', 'USD', 'A-24HS')),
    ('DATA912', ('GGAL', 'ACCIONES', 'BYMA', 'ARS', 'INMEDIATA')),
    ('IOL', ('GGAL', 'CEDEARS', 'BYMA', 'ARS', 'A-24HS')),
])
def test_provider_request_cannot_be_attributed_to_two_identities(tmp_path, source, collision):
    called = []
    with pytest.raises(ValueError, match='FRESH_PROVIDER_REQUEST_AMBIGUOUS'):
        capture(tmp_path / 'fresh.sqlite', [TARGET, collision], source,
                lambda *_: called.append(1), now=NOW)
    assert not called and not (tmp_path / 'fresh.sqlite').exists()


def test_explicit_conflicting_identity_or_currency_never_reaches_canonical(tmp_path):
    rows = [dict(date='2026-10-23', ticker='GGAL', currency='USD', price=1),
            dict(date='2026-10-23', ticker='PAMP', currency='ARS', price=2),
            dict(date='2026-10-23', ticker='GGAL', currency='ARS', price=3)]
    capture(tmp_path / 'fresh.sqlite', [TARGET], 'PPI', lambda *_: rows, now=NOW)
    row = _records(tmp_path / 'fresh.sqlite')[0]
    assert row['dropped_invalid'] == 2 and row['retained_rows'] == 1
    assert json.loads(row['retained_rows_json']) == [rows[2]]
    assert row['provider_currency_status'] == 'EXPLICIT_ROWS'
    assert row['price_basis_status'] == 'UNVERIFIED' and row['status'] == 'STAGED_REVIEW_REQUIRED'


def test_conflicting_currency_aliases_are_dropped_even_when_first_alias_matches(tmp_path):
    rows = [dict(date='2026-10-23', currency='ARS', moneda='USD', price=100),
            dict(date='2026-10-24', currency='ARS', quoteCurrency='ARS', price=101)]
    path = tmp_path / 'fresh.sqlite'
    capture(path, [TARGET], 'PPI', lambda *_: rows, now=NOW)
    capture_row = _records(path)[0]
    assert capture_row['dropped_invalid'] == 1
    assert json.loads(capture_row['retained_rows_json']) == [rows[1]]
    assert capture_row['provider_currency_status'] == 'EXPLICIT_ROWS'
    assert verify(path)['state'] == 'EVIDENCE_ONLY_NO_GO'


def test_separate_sources_never_override_each_other_or_reuse_generation(tmp_path):
    first = tmp_path / 'ppi.sqlite'
    alternative = tmp_path / 'data912.sqlite'
    capture(first, [TARGET], 'PPI', lambda *_: [dict(date='2026-10-23', price=100)], now=NOW)
    capture(alternative, [TARGET], 'DATA912',
            lambda *_: [dict(date='2026-10-23', price=999)], now=NOW)
    assert json.loads(_records(first)[0]['retained_rows_json'])[0]['price'] == 100
    assert json.loads(_records(alternative)[0]['retained_rows_json'])[0]['price'] == 999
    with pytest.raises(ValueError, match='FRESH_GENERATION_ALREADY_EXISTS'):
        capture(first, [TARGET], 'PPI', lambda *_: [], now=NOW)


def test_no_legacy_alias_or_provider_error_payload_leak(tmp_path):
    legacy = tmp_path / 'market_history.db'
    legacy.write_bytes(b'legacy')
    with pytest.raises(ValueError, match='FRESH_LEGACY_DESTINATION_FORBIDDEN'):
        capture(legacy, [TARGET], 'PPI', lambda *_: [], now=NOW)
    alias = tmp_path / 'alias.sqlite'
    alias.symlink_to(legacy)
    with pytest.raises(ValueError, match='FRESH_SIDECAR_ALIAS_FORBIDDEN'):
        capture(alias, [TARGET], 'PPI', lambda *_: [], now=NOW)
    secret = 'Authorization: Bearer should-never-appear'
    result = capture(tmp_path / 'fresh.sqlite', [TARGET], 'PPI',
                     lambda *_: (_ for _ in ()).throw(RuntimeError(secret)), now=NOW)
    assert secret not in json.dumps(result)
    assert secret not in json.dumps(_records(tmp_path / 'fresh.sqlite'))
    assert result['results'][0]['reason'] == 'FRESH_SOURCE_ERROR'


def test_conflicting_same_day_rows_stay_quarantined(tmp_path):
    rows = [dict(date='2026-10-23', price=100),
            dict(date='2026-10-23', price=105)]
    result = capture(tmp_path / 'fresh.sqlite', [TARGET], 'PPI', lambda *_: rows, now=NOW)
    assert result['results'][0]['status'] == 'QUARANTINED'
    assert result['results'][0]['reason'] == 'FRESH_DUPLICATE_DAY_CONFLICT'
    assert len(json.loads(_records(tmp_path / 'fresh.sqlite')[0]['retained_rows_json'])) == 2


def test_connector_bars_require_consistent_real_timestamp(tmp_path):
    t1 = ('GGAL', 'ACCIONES', 'BCBA', 'ARS', 'T1')
    rows = [dict(date='2026-10-23', time=1792785603, open=1, high=2, low=1,
                 close=2, volume=3),
            dict(date='2026-10-22', time=1792785603, open=1, high=2, low=1,
                 close=2, volume=3)]
    result = capture(tmp_path / 'fresh.sqlite', [t1], 'IOL_CONNECTOR', lambda *_: rows, now=NOW)
    assert result['results'][0]['retained_rows'] == 1
    assert result['results'][0]['dropped_invalid'] == 1
    assert result['results'][0]['provider_currency_status'] == 'REQUEST_ONLY'


def test_preflight_blocks_oversized_batch_without_calling_source(tmp_path):
    targets = [(f'Q{i}', 'ACCIONES', 'BYMA', 'ARS', 'A-24HS') for i in range(41)]
    with pytest.raises(ValueError, match='FRESH_TARGET_BUDGET_EXCEEDED'):
        preflight(targets, 'PPI')
    with pytest.raises(ValueError, match='FRESH_TARGET_BUDGET_EXCEEDED'):
        capture(tmp_path / 'fresh.sqlite', targets, 'PPI',
                lambda *_: pytest.fail('source called'), now=NOW)
    assert not (tmp_path / 'fresh.sqlite').exists()


def test_stale_or_advanced_clock_cannot_reopen_expired_data(tmp_path):
    called = []
    for clock in (NOW - timedelta(days=1), NOW + timedelta(days=1)):
        with pytest.raises(ValueError, match='FRESH_CLOCK_UNVERIFIED'):
            capture(tmp_path / 'fresh.sqlite', [TARGET], 'PPI',
                    lambda *_: called.append(1), now=clock)
    assert not called and not (tmp_path / 'fresh.sqlite').exists()
    capture(tmp_path / 'fresh.sqlite', [TARGET], 'PPI',
            lambda *_: [dict(date='2026-10-23', price=100)], now=NOW)
    with pytest.raises(ValueError, match='FRESH_VERIFY_CLOCK_UNVERIFIED'):
        verify(tmp_path / 'fresh.sqlite', now=NOW + timedelta(days=1))


def test_independent_verifier_rejects_expired_payload_and_fake_basis(tmp_path):
    sidecar = tmp_path / 'fresh.sqlite'
    capture(sidecar, [TARGET], 'PPI',
            lambda *_: [dict(date='2026-10-23', price=100)], now=NOW)
    assert verify(sidecar)['state'] == 'EVIDENCE_ONLY_NO_GO'
    with sqlite3.connect(sidecar) as db:
        db.execute("UPDATE fresh_captures_rc6 SET price_basis_status='RAW'")
    with pytest.raises(ValueError, match='FRESH_VERIFY_BASIS_INVENTED'):
        verify(sidecar)
    with sqlite3.connect(sidecar) as db:
        db.execute("UPDATE fresh_captures_rc6 SET price_basis_status='UNVERIFIED', "
                   "retained_rows_json=?", (json.dumps([dict(date='2020-01-01', price=100)]),))
    with pytest.raises(ValueError, match='FRESH_VERIFY_ROW_OUTSIDE_RETENTION'):
        verify(sidecar)


def test_verifier_recomputes_currency_and_duplicate_day_quarantine(tmp_path):
    path = tmp_path / 'fresh.sqlite'
    capture(path, [TARGET], 'PPI', lambda *_: [dict(date='2026-10-23', price=100)], now=NOW)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE fresh_captures_rc6 SET provider_currency_status='EXPLICIT_ROWS'")
    with pytest.raises(ValueError, match='FRESH_VERIFY_STATUS_INCONSISTENT'):
        verify(path)

    conflict = tmp_path / 'conflict.sqlite'
    capture(conflict, [TARGET], 'PPI', lambda *_: [
        dict(date='2026-10-23', price=100), dict(date='2026-10-23', price=101)], now=NOW)
    with sqlite3.connect(conflict) as db:
        db.execute("UPDATE fresh_captures_rc6 SET status='STAGED_REVIEW_REQUIRED', "
                   "reason='FRESH_REVIEW_REQUIRED'")
    with pytest.raises(ValueError, match='FRESH_VERIFY_STATUS_INCONSISTENT'):
        verify(conflict)


def test_verifier_rejects_duplicate_provider_request_key(tmp_path):
    path = tmp_path / 'fresh.sqlite'
    second = ('PAMP', 'ACCIONES', 'BYMA', 'USD', 'A-24HS')
    capture(path, [TARGET, second], 'PPI', lambda *_: [], now=NOW)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE fresh_captures_rc6 SET symbol='GGAL' WHERE symbol='PAMP'")
    with pytest.raises(ValueError, match='FRESH_VERIFY_REQUEST_AMBIGUOUS'):
        verify(path)
