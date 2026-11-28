from datetime import datetime, timedelta, timezone

import pytest

from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_shadow_runtime.timing import PUBLICATION_HISTORY_FILE, PUBLICATION_HISTORY_SCHEMA
from scripts.rc6_installed_runtime_metrics import CONTAINER_PROBE


@pytest.fixture
def probe():
    namespace = {'__name__': 'rc6_metrics_test'}
    exec(compile(CONTAINER_PROBE, '<rc6-runtime-probe>', 'exec'), namespace)
    return namespace


def test_cgroup_interval_is_based_on_deltas_and_excludes_inactive_cache(probe):
    first = {'cpu_stat': {'usage_usec': 50_000_000, 'nr_periods': 100, 'nr_throttled': 56},
             'memory_events': {'oom_kill': 0}, 'memory_current': 980}
    last = {'cpu_stat': {'usage_usec': 50_500_000, 'nr_periods': 110, 'nr_throttled': 58},
            'memory_events': {'oom_kill': 0}, 'memory_current': 1000, 'memory_stat': {'inactive_file': 550}}
    projection = probe['cgroup_delta'](first, last, 1)
    assert projection['state'] == 'OBSERVED'
    assert projection['cpu_cores_mean'] == .5
    assert projection['cpu_throttled_period_fraction'] == .2
    assert projection['memory_current_delta_bytes'] == 20
    assert projection['memory_less_inactive_file_bytes'] == 450
    assert projection['memory_event_delta']['oom_kill'] == 0
    last['cpu_stat']['usage_usec'] = 0
    assert probe['cgroup_delta'](first, last, 1)['state'] == 'NO_VERIFICADO'


def test_wal_size_distinguishes_absent_and_unavailable_without_writing(tmp_path, probe):
    database = tmp_path / 'paper.db'
    assert probe['wal_size'](database) is None
    database.touch()
    assert probe['wal_size'](database) == 0
    wal = database.with_name('paper.db-wal')
    wal.write_bytes(b'fake')
    assert probe['wal_size'](database) == 4
    assert probe['wal_window'](4, 0)['delta_bytes'] == -4
    wal.unlink()
    wal.symlink_to(database)
    assert probe['wal_size'](database) is None


def test_publication_window_fences_restart_pid_pointer_and_current_session(tmp_path, probe):
    now = datetime(2026, 11, 27, 15, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, _):
            return now

    probe['datetime'] = Clock
    start = now - timedelta(minutes=3)
    worker = {'pid': 504, 'pid_alive': True, 'started_at': start.isoformat()}
    observations = []
    for sequence, age, duration in [(1, 90, 10), (2, 60, 30)]:
        source = now - timedelta(seconds=age)
        observations.append({'generation_id': str(sequence) * 64, 'sequence': sequence,
            'manifest_sha256': 'a' * 64, 'phase': 'OPEN', 'source_as_of': source.isoformat(),
            'publish_completed_at': (source + timedelta(seconds=duration)).isoformat(), 'total_seconds': duration})
    latest = observations[-1]
    status = {'status': 'SHADOW_OBSERVING', 'as_of': latest['source_as_of'],
              'real_orders_sent': 0, 'real_routes': 'NOT_CALLED'}
    sha, tree = 'b' * 40, 'c' * 40
    with EvidenceFiles(tmp_path / 'shadow') as files:
        files.write(PUBLICATION_HISTORY_FILE, {'schema': PUBLICATION_HISTORY_SCHEMA, 'worker_pid': worker['pid'],
                    'source_sha': sha, 'source_tree': tree, 'observations': observations})
        files.write('CURRENT.json', {key: latest[key] for key in ('generation_id', 'sequence', 'manifest_sha256')})
        result = probe['publication_window'](files, worker, status, sha, tree)
        assert result['open_cycle_samples'] == 2
        assert result['open_cycle_p95_seconds'] == 29
        assert result['last_generation']['generation_id'] == latest['generation_id']
        assert probe['publication_window'](files, {**worker, 'pid': 505}, status, sha, tree)['state'] == 'NO_VERIFICADO'
        restarted = {**worker, 'started_at': (start + timedelta(seconds=120)).isoformat()}
        assert probe['publication_window'](files, restarted, status, sha, tree)['state'] == 'NO_VERIFICADO'
        assert probe['publication_window'](files, worker, {**status, 'status': 'FAIL_CLOSED'}, sha, tree)[
            'state'] == 'NO_VERIFICADO'
