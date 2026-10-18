"""One exclusive sequential A/B; preserve both raw receipts and fail on drift."""
import json,os,subprocess,sys
from pathlib import Path
outputs=[]
for arm,root in [('baseline','fix2base-624'),('candidate','fix2-624')]:
 out=f'paired-compact624-{arm}'
 assert not Path(out).exists(), 'NEVER_REUSE_A_PRIOR_OUTPUT'
 with open(out+'.log','w') as log:
  subprocess.run([sys.executable,'-B','bench_checkpoint624.py',root,'shared624.db',out],
                 env=dict(os.environ,PYTHONHASHSEED='0'),stdout=log,stderr=subprocess.STDOUT,check=True)
 outputs.append(json.loads((Path(out)/'receipt.json').read_text()))
 print(json.dumps({'arm':arm,'completed':True,'ticks':len(outputs[-1]['ticks'])}),flush=True)
b,a=outputs
assert all(a[k]==b[k] for k in ('database','inode','database_sha256','python','native_backend','source_clock_policy'))
rows=[]
for x,y in zip(b['ticks'],a['ticks']):
 assert x['source_as_of']==y['source_as_of'] and x['computed']['sha256']==y['computed']['sha256']
 assert x['computed']['raw_bytes']==y['computed']['raw_bytes'] and x['funnel']==y['funnel']
 rows.append({'source_as_of':x['source_as_of'],'raw_bytes':x['computed']['raw_bytes'],'canonical_sha256':x['computed']['sha256'],
              'baseline_cpu':x['cpu'],'candidate_cpu':y['cpu'],'baseline_peak_rss_kib':x['peak_rss_kib'],
              'candidate_peak_rss_kib':y['peak_rss_kib']})
result={'schema':'rc6.compact624.paired-ab.v1','status':'GREEN_EQUIVALENCE_ONLY','native_host_acceptance':False,
        'cpu_constraints':'same local host, sequential processes, no Docker CPU/memory certificate',
        'database_sha256':a['database_sha256'],'inode':a['inode'],'rows':rows,
        'baseline_backend_sha256':__import__('hashlib').sha256(Path('fix2base-624/rc6_shadow_runtime/_rc6_json_bound.so').read_bytes()).hexdigest(),
        'candidate_backend_sha256':__import__('hashlib').sha256(Path('fix2-624/rc6_shadow_runtime/_rc6_json_bound.so').read_bytes()).hexdigest()}
assert result['baseline_backend_sha256']==result['candidate_backend_sha256']
Path('paired-compact624-summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
