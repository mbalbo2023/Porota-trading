"""Predeploy and native stress must exercise the full-cardinality worker."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_full_catalog_two_committed_generations_under_process_memory_budget(tmp_path):
    root=Path(__file__).resolve().parents[1]
    env=dict(os.environ,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    run=subprocess.run([sys.executable,str(root/'scripts/rc6_postdeploy_stress.py'),
        '--output',str(tmp_path/'result'),'--full-catalog-check'],cwd=root,env=env,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=240)
    assert run.returncode==0,run.stdout[-3000:]
    report=json.loads((tmp_path/'result/full-catalog.json').read_text())
    assert report['catalog_count']==14611 and report['ready_fixture_count']==9902
    assert len(report['cuts'])==2 and report['cuts'][1]['sequence']>report['cuts'][0]['sequence']
    assert report['process_peak_rss_kib']<=512*1024
    assert report['real_orders_sent']==0 and report['provider_requests']==0

    emitted=[json.loads(line.split('RC6_FULL_CATALOG_CUT=',1)[1])
             for line in run.stdout.splitlines() if line.startswith('RC6_FULL_CATALOG_CUT=')]
    assert len(emitted)==2 and emitted[0]['generation_id']!=emitted[1]['generation_id']
    assert [row['computed_report_sha256'] for row in emitted]==[row['computed_report_sha256'] for row in report['cuts']]
    assert all(set(row['worker_timing']['stages_seconds'])==set(row['worker_timing']['stages_cpu_seconds'])
               for row in emitted)
    assert emitted[1]['previous_cut_age_seconds']<=emitted[1]['previous_cut_freshness_budget_seconds']
