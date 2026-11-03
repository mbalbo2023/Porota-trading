"""A207 host protocol simulation: drain, seal, atomic SQL, commit, flat gate.

No Docker, external network, broker or real host is accessed.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from scripts import rc6_deploy_quiescence as q
from scripts import rc6_paper_admin_cancel as admin
from scripts import rc6_paper_deploy_preflight as preflight
from test_rc6_a207_paper_admin_reversal import _make_book
from test_production_paper_v1634 import partial_spot


def _host_book(partial_spot, tmp_path, monkeypatch):
    broker, targets, _ = _make_book(partial_spot, tmp_path)
    root = tmp_path / 'host'
    db = root / 'data/paper_v17/observer_v17.db'
    db.parent.mkdir(parents=True)
    # Fixture-only consistent SQLite copy (WAL pages included).
    with sqlite3.connect(broker.store.path) as source, sqlite3.connect(db) as target:
        source.backup(target)
    with sqlite3.connect(db) as c:
        c.execute("""UPDATE observer_state SET process_state='WAITING_MARKET',
            session_state='MARKET_CLOSED',heartbeat_at=?""",
            (datetime.now(timezone.utc).isoformat(),))
    monkeypatch.setattr(q, 'ROOT', root)
    monkeypatch.setattr(preflight, 'DB', db)
    monkeypatch.setattr(admin, 'DATABASE', db)
    monkeypatch.setattr(admin, 'RECEIPT_ROOT', root/'data/deploy')
    # CI runners execute pytest unprivileged.  Simulate only the fixed
    # root-owned receipt stat seam (the real host still requires uid=0 and
    # mode 0600); keep every other real filesystem stat unchanged.
    if os.geteuid() != 0:
        original_stat = Path.stat
        sealed_path = root/'data/deploy'/('quiescence-' + 'a'*40 + '.json')

        def root_receipt_stat(path, *args, **kwargs):
            actual = original_stat(path, *args, **kwargs)
            if path != sealed_path:
                return actual
            class RootSealedStat:
                st_uid = 0
                def __getattr__(self, name):
                    return getattr(actual, name)
            return RootSealedStat()

        monkeypatch.setattr(Path, 'stat', root_receipt_stat)
    states = {name: {'Id': ('a' if i==0 else 'b')*64,
                     'Image': ('sha256:' + ('c' if i==0 else 'd')*64),
                     'State': {'Status':'running','ExitCode':0,
                               'OOMKilled':False,'StartedAt':'2026-10-30T19:00:00Z',
                               'FinishedAt':'0001-01-01T00:00:00Z'}}
              for i,name in enumerate(q.OWNED)}
    def fake_command(args):
        if args[1] == 'inspect':
            return json.dumps([states[args[2]]])
        if args[1] == 'stop':
            states[args[-1]]['State']['Status'] = 'exited'
            states[args[-1]]['State']['FinishedAt'] = datetime.now(timezone.utc).isoformat()
            return ''
        raise AssertionError('UNEXPECTED_HOST_COMMAND')
    monkeypatch.setattr(q, 'command', fake_command)
    monkeypatch.setattr(q, 'wait_for_admin_resources', lambda: (300_000_000, 0.0))
    monkeypatch.setattr(q, 'critical_admission', lambda: {'status':'GREEN'})
    def exact_stop(proof):
        for name in q.OWNED:
            assert states[name]['State']['Status'] == 'exited'
            assert states[name]['Id'] == proof['containers'][name]['id']
    monkeypatch.setattr(admin, 'verify_host_stopped', exact_stop)

    sha, tree, owner_id = 'a'*40, 'b'*40, '1234567'
    options = dict(allow_a207_admin=True,a207_owner_comment_id=owner_id,
                   a207_owner_tree=tree)
    return db, root, states, sha, options, targets


def test_a207_exact_owner_stopped_host_atomic_handoff(partial_spot, tmp_path, monkeypatch):
    db, root, states, sha, options, targets = _host_book(partial_spot, tmp_path, monkeypatch)
    receipt = q.run(sha, 'drain', **options)
    assert receipt['status'] == 'ADMIN_DRAINED_PENDING'
    assert receipt['installation_started'] is False
    with pytest.raises(ValueError, match='A207_EXCLUSIVE_VERIFIED_AUTHORITY_REQUIRED'):
        q.run(sha, 'admin-begin', allow_a207_admin=True)
    receipt = q.run(sha, 'admin-begin', **options)
    assert receipt['status'] == 'ADMIN_WRITE_AUTHORIZED'
    assert receipt['installation_started'] is True
    assert receipt['a207_targets'] == targets
    receipt_path = root/'data/deploy'/f'quiescence-{sha}.json'
    assert receipt_path.stat().st_uid == 0
    assert receipt_path.stat().st_mode & 0o077 == 0
    applied = admin.execute_a207_batch(db, receipt_path, sha, apply=True)
    assert applied['status'] == 'CANCELLED_ADMIN'
    assert applied['counts'] == {'spot':1,'caucion':1,'future':1}
    assert q.run(sha, 'admin-commit', **options)['status'] == 'DRAINED'
    assert q.admit(stopped=True) == 'WAITING_MARKET'
    assert preflight.inspect_book(db, barrier={'status':'ABSENT'})['status'] == 'FLAT_GREEN'
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM paper_admin_reversal_batches').fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


@pytest.mark.parametrize('crash_after_sql', [False, True])
def test_a207_failed_attempt_resumes_only_accounting_no_runtime_restart(
        partial_spot, tmp_path, monkeypatch, crash_after_sql):
    db, root, states, sha, options, targets = _host_book(partial_spot, tmp_path, monkeypatch)
    q.run(sha, 'drain', **options)
    q.run(sha, 'admin-begin', **options)
    receipt_path = root/'data/deploy'/f'quiescence-{sha}.json'
    if crash_after_sql:
        assert admin.execute_a207_batch(db, receipt_path, sha, apply=True)['status'] == 'CANCELLED_ADMIN'
    # Failure cleanup marks this deploy as consumed; it MUST NOT restart an
    # older image that cannot parse A207's new terminal states.
    original = q.run(sha, 'recover')
    assert original['status'] == 'FIX_FORWARD_VALIDATION_REQUIRED'
    assert original['a207_resume_from'] == 'ADMIN_WRITE_AUTHORIZED'
    assert all(states[name]['State']['Status'] == 'exited' for name in q.OWNED)
    with pytest.raises(ValueError, match='QUIESCENCE_NOT_DRAINED'):
        q.run(sha, 'install')
    sealed = q.run(sha, 'admin-resume', **options)
    assert sealed['status'] == 'FIX_FORWARD_VALIDATION_REQUIRED'
    assert sealed['admin_cancel_status'] == 'A207_ATOMIC_ACCOUNTING_REVERSED'
    assert sealed['runtime_health'] == 'A207_ACCOUNTING_RECOVERED_OLD_RUNTIME_HELD'
    assert sealed['a207_resume_result'] == ('ALREADY_CLOSED' if crash_after_sql
                                            else 'CANCELLED_ADMIN')
    assert q.run(sha, 'admin-resume', **options)['a207_resume_result'] == sealed['a207_resume_result']
    assert q.admit(stopped=True) == 'WAITING_MARKET'
    assert all(states[name]['State']['Status'] == 'exited' for name in q.OWNED)
    assert preflight.inspect_book(db, barrier={'status':'ABSENT'})['status'] == 'FLAT_GREEN'
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT COUNT(*) FROM paper_admin_reversal_batches').fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 0


def test_a207_open_market_admin_reversal_keeps_session_and_paper_zero(
        partial_spot, tmp_path, monkeypatch):
    db, root, states, sha, options, targets = _host_book(partial_spot, tmp_path, monkeypatch)
    options['allow_market_open_suspend'] = True
    with sqlite3.connect(db) as c:
        c.execute("UPDATE observer_state SET process_state='RUNNING', "
                  "session_state='MARKET_OPEN',heartbeat_at=?",
                  (datetime.now(timezone.utc).isoformat(),))
    q.run(sha, 'drain', **options)
    proof = q.run(sha, 'admin-begin', **options)
    assert proof['market_open_suspended'] is True
    receipt = root/'data/deploy'/f'quiescence-{sha}.json'
    assert admin.execute_a207_batch(db, receipt, sha, apply=True)['status'] == 'CANCELLED_ADMIN'
    assert q.run(sha, 'admin-commit', **options)['status'] == 'DRAINED'
    assert q.admit(stopped=True, allow_market_open_suspend=True,
                   stopped_receipt=proof) == 'RUNNING'
    assert all(states[name]['State']['Status'] == 'exited' for name in q.OWNED)
    assert preflight.inspect_book(db, barrier={'status':'ABSENT'})['status'] == 'FLAT_GREEN'


def test_a207_postcommit_failure_never_requests_a_second_ledger_write(
        partial_spot, tmp_path, monkeypatch):
    db, root, states, sha, options, _ = _host_book(partial_spot, tmp_path, monkeypatch)
    q.run(sha,'drain',**options)
    q.run(sha,'admin-begin',**options)
    receipt=root/'data/deploy'/f'quiescence-{sha}.json'
    assert admin.execute_a207_batch(db,receipt,sha,apply=True)['status']=='CANCELLED_ADMIN'
    q.run(sha,'admin-commit',**options)
    recovered=q.run(sha,'recover')
    assert recovered['status']=='FIX_FORWARD_VALIDATION_REQUIRED'
    assert recovered.get('a207_resume_from') is None
    assert recovered['admin_cancel_status']=='A207_ATOMIC_ACCOUNTING_REVERSED'
    assert all(states[name]['State']['Status']=='exited' for name in q.OWNED)
    assert preflight.inspect_book(db,barrier={'status':'ABSENT'})['status']=='FLAT_GREEN'
    with pytest.raises(ValueError,match='QUIESCENCE_A207_RESUME_NOT_SEALED'):
        q.run(sha,'admin-resume',**options)


@pytest.mark.parametrize('tamper', [None, 'unfinished', 'owner', 'tree',
                                    'batch_missing', 'still_open',
                                    'spot_cost', 'caucion_principal',
                                    'future_cash', 'future_event_detail'])
def test_a207_next_candidate_requires_independent_sealed_accounting_before_adoption(
        partial_spot, tmp_path, monkeypatch, tamper):
    db, root, states, sha, options, _ = _host_book(partial_spot, tmp_path, monkeypatch)
    q.run(sha, 'drain', **options)
    q.run(sha, 'admin-begin', **options)
    receipt_path = root/'data/deploy'/f'quiescence-{sha}.json'
    if tamper != 'unfinished':
        assert admin.execute_a207_batch(db, receipt_path, sha,
                                        apply=True)['status'] == 'CANCELLED_ADMIN'
    q.run(sha, 'recover')
    if tamper != 'unfinished':
        q.run(sha, 'admin-resume', **options)
    proof = json.loads(receipt_path.read_text())
    if tamper == 'owner':
        proof['a207_owner_approval'] = '987654'
    elif tamper == 'tree':
        proof['a207_approval_tree'] = 'c'*40
    elif tamper == 'batch_missing':
        with sqlite3.connect(db) as c:
            c.execute('DROP TABLE paper_admin_reversal_batches')
    elif tamper == 'still_open':
        with sqlite3.connect(db) as c:
            c.execute("UPDATE paper_positions SET status='OPEN' WHERE status='CANCELLED_ADMIN'")
    elif tamper == 'spot_cost':
        with sqlite3.connect(db) as c:
            c.execute("UPDATE paper_positions SET entry_cost='999999' WHERE status='CANCELLED_ADMIN'")
    elif tamper == 'caucion_principal':
        with sqlite3.connect(db) as c:
            c.execute("UPDATE paper_cauciones SET principal='999999' WHERE status='CANCELLED_ADMIN'")
    elif tamper == 'future_cash':
        with sqlite3.connect(db) as c:
            c.execute("UPDATE paper_family_lifecycle SET ledger_total='1' WHERE family='FUTUROS'")
    elif tamper == 'future_event_detail':
        with sqlite3.connect(db) as c:
            c.execute("UPDATE paper_family_lifecycle_events SET detail_json='{}' WHERE to_state='ADMIN_CANCEL'")
    if tamper is None:
        # The predecessor check runs before any new bundle reaches the host.
        # Installed legacy code may not have these candidate-only modules.
        import builtins
        original_import = builtins.__import__

        def reject_new_candidate_module(name, *args, **kwargs):
            assert name not in {'scripts.rc6_paper_admin_cancel',
                                'scripts.rc6_paper_deploy_preflight',
                                'bt_caucion_paper', 'rc6_paper_family_lifecycle',
                                'cd_spot_ledger'}
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', reject_new_candidate_module)
        assert q.verify_a207_stopped_predecessor(proof, db)
    else:
        with pytest.raises(ValueError, match='A207_'):
            q.verify_a207_stopped_predecessor(proof, db)
