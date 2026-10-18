"""Finite predeclared allocation hypothesis; RED never authorizes native retry."""
import json
import os
from pathlib import Path
import subprocess
import sys

receipts = []
for arm, root in [('baseline', 'fix2-624'), ('candidate', 'fix5-624')]:
    output = 'paired-progressive-preopen624-' + arm
    assert not Path(output).exists(), 'NO_REUSE_OF_PRIOR_EXPERIMENT_OUTPUT'
    with open(output+'.log', 'w') as log:
        subprocess.run([sys.executable, '-B', 'bench_progressive_blocks624.py', root, 'shared624.db', output],
                       env=dict(os.environ, PYTHONHASHSEED='0'), stdout=log,
                       stderr=subprocess.STDOUT, check=True)
    receipts.append(json.loads((Path(output)/'receipt.json').read_text()))
    print(json.dumps({'arm': arm, 'completed': True, 'cuts': len(receipts[-1]['ticks'])}), flush=True)
before, after = receipts
assert all(before[k] == after[k] for k in ('database', 'inode', 'database_sha256',
                                         'python', 'native_backend', 'backend_sha256', 'source_clock_policy'))
rows = []
for b, a in zip(before['ticks'], after['ticks']):
    assert all(b[k] == a[k] for k in ('source_as_of', 'funnel', 'ready_count', 'engines',
                                     'raw_independent_verification', 'checkpoint_raw_sha256', 'latest_canonical_sha256'))
    assert b['computed']['raw_bytes'] == a['computed']['raw_bytes']
    assert b['computed']['sha256'] == a['computed']['sha256']
    rows.append({'source_as_of': b['source_as_of'], 'raw_bytes': b['computed']['raw_bytes'],
                 'sha256': b['computed']['sha256'], 'baseline_cpu': b['cpu'], 'candidate_cpu': a['cpu'],
                 'baseline_peak_rss_kib': b['peak_rss_kib'], 'candidate_peak_rss_kib': a['peak_rss_kib']})
baseline_peak = max(v['peak_rss_kib'] for v in before['ticks'])
candidate_peak = max(v['peak_rss_kib'] for v in after['ticks'])
cpu_ratio = sum(v['cpu'] for v in after['ticks']) / sum(v['cpu'] for v in before['ticks'])
admitted = candidate_peak <= .60*baseline_peak and candidate_peak <= 192*1024 and cpu_ratio <= 1.05
result = {'schema': 'rc6.progressive624.paired-admission.v1',
          'status': 'GREEN_EQUIVALENCE_AND_ADMISSION' if admitted else 'RED_HYPOTHESIS_INSUFFICIENT',
          'native_host_acceptance': False, 'local_docker_limit_certificate': False,
          'all_eight_full_audits_independently_verified_before_pruning': True,
          'baseline_peak_rss_kib': baseline_peak, 'candidate_peak_rss_kib': candidate_peak,
          'rss_ratio': candidate_peak/baseline_peak, 'total_cpu_ratio': cpu_ratio,
          'predeclared_limits': {'rss_ratio_max': .60, 'absolute_peak_rss_kib_max': 192*1024,
                                 'total_cpu_ratio_max': 1.05},
          'database_sha256': before['database_sha256'], 'database_inode': before['inode'], 'rows': rows}
Path('paired-progressive-preopen624-summary.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result), flush=True)
raise SystemExit(0 if admitted else 2)
