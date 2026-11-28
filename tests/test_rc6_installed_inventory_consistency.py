from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import time
from types import SimpleNamespace

import rc6_preopen as preopen
from scripts import porota_rootless_jobs as jobs
from scripts import rc6_iol_installed_inventory as inventory


def test_history_inventory_uses_one_snapshot_during_a_wal_commit(tmp_path, monkeypatch):
    db = tmp_path / 'history.db'
    with closing(sqlite3.connect(db)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE history_canonical_v2(source TEXT)')
        writer.execute("INSERT INTO history_canonical_v2 VALUES ('IOL_REST')")
        writer.commit()
        probe = inventory._canonical_sources

        def concurrent_commit(reader, deadline):
            writer.execute("INSERT INTO history_canonical_v2 VALUES ('DATA912')")
            writer.commit()
            return probe(reader, deadline)

        monkeypatch.setattr(inventory, '_canonical_sources', concurrent_commit)
        result = inventory._observe_database(db, ('history_canonical_v2', 'history_versions_v2'), time.monotonic() + 4)
        assert writer.execute('SELECT COUNT(*) FROM history_canonical_v2').fetchone() == (2,)
    assert result['query_only'] is True
    assert result['rows'] == {'history_canonical_v2': 1}
    assert result['canonical_by_source'] == [{'source': 'IOL_REST', 'rows': 1}]
    assert result['state'] == 'PARTIAL'
    assert result['query_failures'] == {'versions_by_source': 'INVENTORY_DB_TABLE_MISSING'}
    assert result['snapshot_started_at'] <= result['snapshot_finished_at']


def test_bounded_history_breakdown_retains_available_count_as_partial(tmp_path, monkeypatch):
    db = tmp_path / 'history.db'
    with closing(sqlite3.connect(db)) as writer:
        for table in inventory.HISTORY_TABLES:
            writer.execute(f'CREATE TABLE {table}(source TEXT)')
        writer.execute("INSERT INTO history_canonical_v2 VALUES ('IOL_REST')")
        writer.commit()
    monkeypatch.setattr(inventory, '_canonical_sources', lambda *_: (None, 'INVENTORY_DB_QUERY_BUDGET'))
    result = inventory._observe_database(db, inventory.HISTORY_TABLES, time.monotonic() + 4)
    assert result['state'] == 'PARTIAL'
    assert result['rows']['history_canonical_v2'] == 1
    assert result['canonical_by_source'] is None
    assert result['canonical_by_source_failure'] == 'INVENTORY_DB_QUERY_BUDGET'


def test_iol_preopen_names_stale_observer_and_separates_missing_gateway(tmp_path, monkeypatch):
    db = tmp_path / 'observer.db'
    monkeypatch.setattr(preopen, 'DB', db)
    monkeypatch.setenv('POROTA_RC6_MARKET_PROVIDER', 'IOL_REST')
    with closing(sqlite3.connect(db)) as writer:
        writer.execute('CREATE TABLE observer_state(id, mode, process_state, session_state, ppi_auth, '
                       'real_orders_sent, heartbeat_at)')
        now = datetime.now(timezone.utc)
        writer.execute("INSERT INTO observer_state VALUES (1, 'PRODUCTION_PAPER', 'RUNNING', "
                       "'PREOPEN', 'NOT_ATTEMPTED', 0, ?)", ((now - timedelta(minutes=3)).isoformat(),))
        writer.commit()
        assert preopen.observer_db()['reason'] == 'IOL_OBSERVER_HEARTBEAT_STALE'
        writer.execute('UPDATE observer_state SET heartbeat_at=?', (now.isoformat(),))
        writer.commit()

    def unavailable():
        raise RuntimeError('private diagnostic')

    monkeypatch.setattr(preopen, '_iol_owner_status', unavailable)
    unavailable_receipt = preopen.observer_db()
    assert unavailable_receipt['reason'] == 'IOL_OWNER_STATUS_UNAVAILABLE'
    assert unavailable_receipt['bounded_readonly_probe'] == 'GREEN'
    assert 'private diagnostic' not in str(unavailable_receipt)
    monkeypatch.setattr(preopen, '_iol_owner_status', lambda: {
        'registered': {'scanner': {}, 'exit_reader': {}}, 'trade_stream_configured': True,
    })
    assert preopen.observer_db()['state'] == 'GREEN'


def test_critical_scheduler_guard_uses_bindings_and_keeps_preopen_result_separate():
    timers = {name: {'binding': 'MATCH', 'load': 'loaded', 'active': 'active',
                     'service': {'load': 'loaded', 'active': 'inactive'}}
              for name in preopen.REQUIRED_TIMERS}
    timers['porota-preopen-rc6.timer']['service']['active'] = 'failed'
    assert inventory._critical_timer_bindings(timers)['state'] == 'VALIDADO_RUNTIME'
    wrong_binding, failed = preopen.REQUIRED_TIMERS[:2]
    timers[wrong_binding]['binding'] = 'MISMATCH'
    timers[failed]['service']['active'] = 'failed'
    result = inventory._critical_timer_bindings(timers)
    assert result['state'] == 'BLOQUEADO'
    assert result['units'][wrong_binding]['reason'] == 'CRITICAL_TIMER_TARGET_MISMATCH'
    assert result['units'][failed]['reason'] == 'CRITICAL_SERVICE_FAILED'


def test_scheduled_t10_records_actual_reason_and_release_without_reusing_previous_session(tmp_path):
    state = tmp_path / 'private'
    state.mkdir(mode=0o700)
    identity = SimpleNamespace(state=state)
    current = {'source_sha': 'a' * 40, 'source_tree': 'b' * 40, 'image_id': 'sha256:' + 'c' * 64,
               'attempt': '1-1', 'market_provider': 'IOL_REST'}
    started = datetime.fromisoformat('2026-11-27T10:20:07-03:00')
    child = {'phase': 'T_MINUS_10', 'status': 'RED', 'generated_at_ar': started.isoformat(),
             'dry_equivalent': False, 'read_only': False, 'network_order_test_performed': False,
             'red_checks': ['observer_db', 'byma_morning_watch'],
             'checks': {'observer_db': {'state': 'RED', 'reason': 'IOL_OBSERVER_HEARTBEAT_STALE'},
                        'byma_morning_watch': {'state': 'RED', 'reason': 'PRIVATE_PROVIDER_TEXT'}},
             'paper_admission_receipt': {'status': 'NO_GO_PAPER_SCALPING'}}
    jobs.persist_scheduled_t10(identity, current, started, child, 2)
    timer = {'service': {'started_at': started.isoformat(), 'execution': 'COMPLETED', 'exit_status': 2}}
    same = inventory._scheduled_t10(identity, current, timer, started + timedelta(seconds=30))
    assert same['state'] == 'RED'
    assert same['last_receipt']['preopen_red_reasons'] == {'observer_db': 'IOL_OBSERVER_HEARTBEAT_STALE'}
    assert same['last_receipt']['admission_result'] == 'NO_GO_PAPER_SCALPING'
    assert same['systemd_corroborated'] is True
    other_attempt = inventory._scheduled_t10(identity, {**current, 'attempt': '1-2'}, timer, started)
    assert other_attempt['reason'] == 'SCHEDULED_T10_OTHER_RELEASE'
    saturday = inventory._scheduled_t10(identity, current, timer, started + timedelta(days=1))
    assert saturday['state'] == 'NOT_DUE'
    assert saturday['reason'] == 'BYMA_NON_OPERATIONAL_DAY'
    assert saturday['matches_session'] is False
