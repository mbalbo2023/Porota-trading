"""A207 independent 20-session proof: an administrative void is not a trade."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from cd_spot_ledger import (ADMIN_CANCEL_AUTHORITIES, ADMIN_CANCEL_AUTHORITY_A207,
                            cancel_open_paper_position)
from rc6_audit_evidence.package import (ADMIN_VOID_AUTHORITIES, EvidenceError,
                                        SCHEMA, SCHEMA_V2, export_package)
from rc6_audit_evidence.recompute import verify_package
from test_production_paper_v1634 import partial_spot
from test_rc6_paper_admin_cancel import _pending


@pytest.fixture(autouse=True)
def _external_private_destination(monkeypatch):
    # This test environment marks /tmp itself as a git workspace. Production
    # forbids publishing private evidence below any .git ancestor. Emulate an
    # external private temporary destination without weakening the real guard.
    exists = Path.exists
    monkeypatch.setattr(Path, 'exists',
                        lambda path: False if str(path) == '/tmp/.git' else exists(path))


def _sessions(end_at):
    from zoneinfo import ZoneInfo
    day = datetime.fromisoformat(end_at).astimezone(
        ZoneInfo('America/Argentina/Buenos_Aires')).date()
    return [(day - timedelta(days=i)).isoformat() for i in range(19, -1, -1)]


def _synthetic_sealed_batch(connection, paper_ids, completed_at):
    """Offline synthetic journal: prove the spot exporter requires the seal.

    This is NOT an owner approval or a production A207 administrative write.
    """
    candidate, tree, owner, exposures = ('a'*40, 'b'*40, '12345678', 'c'*64)
    targets = json.dumps({'spot': sorted(paper_ids), 'caucion': [], 'future': []},
                         sort_keys=True, separators=(',', ':'))
    digest = hashlib.sha256('|'.join((candidate, tree, owner, exposures, targets)).encode()).hexdigest()
    connection.execute('''CREATE TABLE IF NOT EXISTS paper_admin_reversal_batches(
        candidate_sha TEXT PRIMARY KEY,tree_sha TEXT NOT NULL,
        owner_comment_id TEXT NOT NULL,exposures_sha256 TEXT NOT NULL,
        targets_json TEXT NOT NULL,completed_at TEXT NOT NULL,
        audit_digest_sha256 TEXT NOT NULL)''')
    connection.execute('''INSERT INTO paper_admin_reversal_batches VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(candidate_sha) DO UPDATE SET targets_json=excluded.targets_json,
        audit_digest_sha256=excluded.audit_digest_sha256''',
        (candidate, tree, owner, exposures, targets, completed_at, digest))


def _void(partial_spot, *, days_after_open=1, seal=True):
    broker, position, quote, _ = partial_spot()
    _pending(broker, position)
    when = (datetime.fromisoformat(quote.observed_at) + timedelta(days=days_after_open)).isoformat()
    with broker.store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        cancel_open_paper_position(connection, position['paper_id'], when,
                                   ADMIN_CANCEL_AUTHORITY_A207)
        if seal:
            _synthetic_sealed_batch(connection, [position['paper_id']], when)
    return broker, position, quote, when


def _export(broker, destination, end_at):
    return export_package(broker.store.path, destination, sessions=_sessions(end_at),
                          pseudonym_key=b'a207-test-seed-' + bytes(range(32)))


def test_a207_20session_void_only_is_verified_as_nontrading_v2(partial_spot, tmp_path):
    broker, position, _, when = _void(partial_spot)
    destination = tmp_path / 'admin-v2'
    result = _export(broker, destination, when)
    report = verify_package(destination, expected_manifest_sha256=result['manifest_sha256'])

    assert ADMIN_VOID_AUTHORITIES == ADMIN_CANCEL_AUTHORITIES
    assert result['schema'] == SCHEMA_V2
    assert report['schema'] == 'rc6.independent-20session-recomputation.v2'
    assert report['counts'] == {'positions': 0, 'fills': 0,
                                'closed_positions': 0, 'open_survivors': 0,
                                'admin_voids': 1}
    assert report['currencies'] == {} and report['positions'] == []
    void = report['administrative_voids'][0]
    assert void['lifecycle'] == 'CANCELLED_ADMIN_NO_EXECUTION'
    assert void['realized_pnl'] is None and void['sell_simulated_fills'] == 0
    assert void['authority'] == ADMIN_CANCEL_AUTHORITY_A207
    assert void['source_proof'] == 'A207_SEALED_BATCH_SPOT_MEMBERSHIP_ONLY_FULL_BATCH_UNVERIFIED'
    assert void['a207_batch_commitment'].startswith('srcbatch_')
    assert void['a207_full_batch_complete'] == 'NO_VERIFICADO'
    assert position['paper_id'] not in (destination / 'voids.jsonl').read_text()
    manifest = json.loads((destination / 'manifest.json').read_text())
    assert manifest['admin_void_exclusions']['trade_pnl_effect'] == 'NONE'
    assert 'ADMIN_VOID_EXTERNAL_SOURCE_OWNER_RECONCILIATION' in manifest['missing_evidence']
    assert 'A207_BATCH_FULL_TARGET_SET_COMPLETENESS_NO_VERIFICADO' in manifest['missing_evidence']


def test_a207_multiple_admin_voids_have_exact_manifest_cardinality(partial_spot, tmp_path):
    """A207 accepts batches; one-void export must not hide a fixed rows=1."""
    broker, position, _, when = _void(partial_spot)
    source_id = position['paper_id']
    copy_id = source_id + '-SYNTHETIC-SECOND'
    # Duplicate the *entire* verified synthetic PAPER provenance, not merely
    # a terminal-status flag. Never access a live database or broker here.
    with broker.store.connect() as connection:
        for table in ('paper_positions', 'paper_fills', 'paper_exit_intents',
                      'paper_admin_cancellations', 'paper_events'):
            fields = connection.execute('PRAGMA table_info(' + table + ')').fetchall()
            names = [field[1] for field in fields if not (
                field[2].upper() == 'INTEGER' and field[5] == 1)]
            rows = connection.execute('SELECT * FROM ' + table + ' WHERE paper_id=?',
                                      (source_id,)).fetchall()
            assert rows, table
            columns = ','.join(names)
            holders = ','.join('?' for _ in names)
            for row in rows:
                values = [copy_id if name == 'paper_id' else row[name] for name in names]
                connection.execute('INSERT INTO ' + table + '(' + columns + ') VALUES('
                                   + holders + ')', values)
        _synthetic_sealed_batch(connection, [source_id, copy_id], when)
    destination = tmp_path / 'multi-void-v2'
    result = _export(broker, destination, when)
    manifest = json.loads((destination / 'manifest.json').read_text())
    verified = verify_package(destination, expected_manifest_sha256=result['manifest_sha256'])
    assert result['schema'] == SCHEMA_V2
    assert manifest['files']['voids.jsonl']['rows'] == 2
    assert manifest['counts']['admin_voids'] == 2
    assert verified['counts']['admin_voids'] == 2
    assert len(verified['administrative_voids']) == 2


def test_a207_tampered_authority_or_extra_sell_never_publishes(partial_spot, tmp_path):
    broker, position, _, when = _void(partial_spot)
    with broker.store.connect() as connection:
        connection.execute('UPDATE paper_admin_cancellations SET authority=? WHERE paper_id=?',
                           ('FORGED', position['paper_id']))
    destination = tmp_path / 'tampered'
    with pytest.raises(EvidenceError, match='ADMIN_VOID_RECEIPT_INVALID'):
        _export(broker, destination, when)
    assert not destination.exists()
    with broker.store.connect() as connection:
        connection.execute('UPDATE paper_admin_cancellations SET authority=? WHERE paper_id=?',
                           (ADMIN_CANCEL_AUTHORITY_A207, position['paper_id']))
        connection.execute('''INSERT INTO paper_fills
            (paper_id,source,side,quantity,price,costs,slippage,filled_at)
            VALUES(?,?,?,?,?,?,?,?)''',
            (position['paper_id'], 'PRODUCTION_PAPER', 'SELL_SIMULATED',
             '10', '100', '0', '0', when))
    with pytest.raises(EvidenceError, match='ADMIN_VOID_ONLY_ONE_BUY_REQUIRED'):
        _export(broker, destination, when)
    assert not destination.exists()


@pytest.mark.parametrize('tamper', ['missing', 'wrong_digest', 'wrong_membership'])
def test_a207_admin_void_requires_exact_sealed_batch_journal(partial_spot, tmp_path, tamper):
    broker, position, _, when = _void(partial_spot, seal=tamper != 'missing')
    with broker.store.connect() as connection:
        if tamper == 'wrong_digest':
            connection.execute('''UPDATE paper_admin_reversal_batches
                SET audit_digest_sha256=?''', ('d'*64,))
        elif tamper == 'wrong_membership':
            _synthetic_sealed_batch(connection, ['SOME-OTHER-SPOT-ID'], when)
    destination = tmp_path / ('journal-' + tamper)
    with pytest.raises(EvidenceError, match='A207_ADMIN_BATCH_'):
        _export(broker, destination, when)
    assert not destination.exists()


def test_a207_void_after_cohort_cutoff_remains_v1_open_survivor(partial_spot, tmp_path):
    broker, _, quote, _ = _void(partial_spot, days_after_open=3)
    destination = tmp_path / 'historical-open-v1'
    result = _export(broker, destination, quote.observed_at)
    report = verify_package(destination, expected_manifest_sha256=result['manifest_sha256'])
    assert result.get('schema', SCHEMA) == SCHEMA
    assert report['counts'] == {'positions': 1, 'fills': 1,
                                'closed_positions': 0, 'open_survivors': 1}
    assert report['positions'][0]['lifecycle'] == 'OPEN_AT_CUTOFF'
    assert not (destination / 'voids.jsonl').exists()


def test_a207_v2_void_proof_tamper_detected_by_independent_recompute(partial_spot, tmp_path):
    broker, _, _, when = _void(partial_spot)
    destination = tmp_path / 'void-tamper-v2'
    _export(broker, destination, when)
    path = destination / 'voids.jsonl'
    row = json.loads(path.read_text())
    row['realized_pnl'] = '1000000'
    path.write_text(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
    with pytest.raises(EvidenceError, match='FILE_DIGEST_MISMATCH'):
        verify_package(destination)

