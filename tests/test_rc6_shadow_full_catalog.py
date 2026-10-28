"""Predeploy and native stress must exercise the full-cardinality worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


def _fresh_python(command, **kwargs):
    # Fork once from a minimal interpreter before importing/executing the
    # worker. Its ru_maxrss then covers the complete real workload without the
    # unrelated pytest parent's inherited pre-exec watermark. Nothing inside
    # the worker measurement, its command, environment or limits is changed.
    launcher = ('import subprocess,sys; '
                'raise SystemExit(subprocess.call(sys.argv[2:], '
                'timeout=float(sys.argv[1])))')
    inner_timeout = max(1, kwargs['timeout'] - 5)
    return subprocess.run([sys.executable, '-c', launcher, str(inner_timeout),
                           *command], **kwargs)


@pytest.mark.parametrize('allocation_mib', [0, 208])
def test_worker_peak_excludes_parent_highwater_but_retains_actual_peak(allocation_mib):
    # Linux preserves the fork parent's RSS highwater across exec. A large
    # repository-wide pytest process must not become the worker's measured peak.
    parent_allocation = bytearray(64 * 1024**2)
    probe = """import json, resource, sys
from pathlib import Path
allocation = bytearray(int(sys.argv[1]) * 1024**2)
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
print(json.dumps({'measured': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  'actual': int(status['VmHWM'].split()[0])}))
"""
    run = _fresh_python([sys.executable, '-c', probe, str(allocation_mib)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
    assert run.returncode == 0, run.stdout
    peak = json.loads(run.stdout)
    assert abs(peak['measured'] - peak['actual']) <= 8 * 1024, peak
    # A real over-budget child must still be reported over192MiB. Never reset
    # the watermark during or after the workload, or subtract a guessed offset.
    assert (peak['measured'] > 192 * 1024) is bool(allocation_mib), peak
    assert len(parent_allocation) == 64 * 1024**2


def test_full_catalog_two_committed_generations_under_process_memory_budget(tmp_path):
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    from tests.rc6_native_storage import native_storage, native_command
    native = native_storage(tmp_path)
    command = native_command([sys.executable, str(root/'scripts/rc6_postdeploy_stress.py'),
        '--output', str(tmp_path/'result'), '--full-catalog-check'], native)
    run=_fresh_python(command,cwd=root,env=env,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=240)
    assert run.returncode==0,run.stdout[-3000:]
    report=json.loads((tmp_path/'result/full-catalog.json').read_text())
    assert report['catalog_count']==14611 and report['ready_fixture_count']==9902
    assert len(report['cuts'])==2 and report['cuts'][1]['sequence']>report['cuts'][0]['sequence']
    assert report['process_peak_rss_kib']<=512*1024
    assert report['process_peak_rss_kib']<=192*1024
    assert report['real_orders_sent']==0 and report['provider_requests']==0

    emitted=[json.loads(line.split('RC6_FULL_CATALOG_CUT=',1)[1])
             for line in run.stdout.splitlines() if line.startswith('RC6_FULL_CATALOG_CUT=')]
    assert len(emitted)==2 and emitted[0]['generation_id']!=emitted[1]['generation_id']
    assert [row['computed_report_sha256'] for row in emitted]==[row['computed_report_sha256'] for row in report['cuts']]
    assert all(set(row['worker_timing']['stages_seconds'])==set(row['worker_timing']['stages_cpu_seconds'])
               for row in emitted)
    assert emitted[1]['previous_cut_age_seconds']<=emitted[1]['previous_cut_freshness_budget_seconds']
