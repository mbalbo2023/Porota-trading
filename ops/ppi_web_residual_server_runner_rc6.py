#!/usr/bin/env python3
"""Durable RC6 PPI Web residual runner.

Fail-closed server runner. API residual state is read from SQLite; authenticated
browser/API work runs as porotaadmin; validated PPI Web history is persisted with
explicit PPI_WEB_HISTORY provenance. FCI families are deliberately deferred.
"""
from __future__ import annotations

import hashlib, json, os, pwd, sqlite3, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=Path(os.getenv('POROTA_REPO','/opt/porota-trading'))
for p in (REPO,HERE):
    if str(p) not in sys.path: sys.path.insert(0,str(p))

from ppi_web_residual_state_rc6 import ResidualState
from ppi_history_residual_manifest_rc6 import load_from_runtime, write_jsonl
from ppi_web_history_ingest_rc6 import ingest_web_rows

RUN_ID=os.getenv('PPI_WEB_RESIDUAL_RUN_ID','PPI-WEB-RESIDUAL-20260913-001')
ROOT=Path(os.getenv('PPI_WEB_RESIDUAL_ROOT','/opt/porota-ingest/ppi-web-residual'))
ENABLED=ROOT/'ENABLED'; MANIFEST=ROOT/'residual.jsonl'; DEFERRED=ROOT/'deferred_fci.jsonl'; MANIFEST_META=ROOT/'manifest_meta.json'; STATE_DB=ROOT/'state.sqlite3'; STATUS=ROOT/'status.json'; BATCH_ROOT=ROOT/'batches'
PROFILE=Path(os.getenv('PPI_WEB_PROFILE','/home/porotaadmin/porota-browser-lab/chrome-profile'))
SECRET=Path(os.getenv('PPI_WEB_SECRET','/etc/porota/contract-evidence-web.env'))
VENV_PY=Path(os.getenv('PPI_WEB_PYTHON','/opt/porota-contract-evidence-venv/bin/python'))
CHROME=os.getenv('POROTA_CHROME_EXECUTABLE','/usr/bin/google-chrome-stable')
BROWSER_USER=os.getenv('PPI_WEB_BROWSER_USER','porotaadmin')
COLLECTOR=Path(os.getenv('PPI_WEB_COLLECTOR',str(REPO/'ops/ppi_web_direct_history_collector_rc6.py')))
REAUTH=Path(os.getenv('PPI_WEB_REAUTH',str(REPO/'rc6_ppi_web_reauth.py')))
API_UNIT=os.getenv('PPI_HISTORY_API_UNIT','porota-ppi-fullfamily-history-rc6.service')
BATCH_SIZE=max(1,int(os.getenv('PPI_WEB_BATCH_SIZE','8')))
OBSERVER_DB=Path(os.getenv('POROTA_OBSERVER_DB',str(REPO/'data/paper_v17/observer_v17.db'))).resolve()
DEFER_FAMILY_TOKENS=('FCI','FONDO')

class RuntimeSQLiteStore:
    def __init__(self,path:Path): self.path=str(path)
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def log(event:str,**fields): print(json.dumps({'event':event,'at':datetime.now(timezone.utc).isoformat(),**fields},ensure_ascii=False,sort_keys=True),flush=True)
def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def api_writer_active()->bool: return subprocess.run(['systemctl','is-active','--quiet',API_UNIT],check=False).returncode==0
def is_deferred(row:dict)->bool:
    fam=str(row.get('instrument_type','')).strip().upper(); return any(tok in fam for tok in DEFER_FAMILY_TOKENS)
def read_jsonl(path:Path)->list[dict]:
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
def rows_digest(rows:list[dict])->str:
    b=''.join(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for r in rows).encode(); return hashlib.sha256(b).hexdigest()

def safety_check():
    if not ENABLED.is_file(): raise RuntimeError('PPI_WEB_RESIDUAL_NOT_ENABLED')
    if api_writer_active(): raise RuntimeError('PPI_API_HISTORICAL_WRITER_ACTIVE')
    if not OBSERVER_DB.is_file(): raise RuntimeError('OBSERVER_DB_MISSING')
    c=sqlite3.connect(f'file:{OBSERVER_DB}?mode=ro',uri=True,timeout=20)
    try:
        c.execute('PRAGMA query_only=ON'); qc=c.execute('PRAGMA quick_check').fetchone()[0]
        row=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        active=c.execute("SELECT count(*) FROM ppi_history_ingest_tasks WHERE run_id='PPI-HIST-20260912-001' AND state IN ('PENDING','RETRYABLE','RUNNING')").fetchone()[0]
        total=c.execute("SELECT count(*) FROM ppi_history_ingest_tasks WHERE run_id='PPI-HIST-20260912-001'").fetchone()[0]
    finally: c.close()
    if qc!='ok' or not row or row[0]!='PRODUCTION_PAPER' or int(row[1] or 0)!=0: raise RuntimeError('SAFETY_STATE_NOT_PRODUCTION_PAPER_ZERO_ORDERS')
    if int(active)!=0 or int(total)!=1960: raise RuntimeError(f'API_CLOSEOUT_GATE_FAILED:active={active}:total={total}')
    for path in (PROFILE,SECRET,VENV_PY,COLLECTOR,REAUTH):
        if not path.exists(): raise RuntimeError(f'REQUIRED_PATH_MISSING:{path}')

def ensure_manifest()->tuple[list[dict],list[dict]]:
    live=load_from_runtime(OBSERVER_DB); runnable=[r for r in live if not is_deferred(r)]; deferred=[r for r in live if is_deferred(r)]
    ROOT.mkdir(parents=True,exist_ok=True)
    live_run_hash=rows_digest(runnable); live_def_hash=rows_digest(deferred)
    if MANIFEST.exists() or DEFERRED.exists() or MANIFEST_META.exists():
        if not (MANIFEST.exists() and DEFERRED.exists() and MANIFEST_META.exists()): raise RuntimeError('PARTIAL_IMMUTABLE_MANIFEST_SET')
        meta=json.loads(MANIFEST_META.read_text(encoding='utf-8'))
        if sha256_file(MANIFEST)!=meta.get('runnable_file_sha256') or sha256_file(DEFERRED)!=meta.get('deferred_file_sha256'): raise RuntimeError('IMMUTABLE_MANIFEST_FILE_CHANGED')
        if live_run_hash!=meta.get('runnable_rows_sha256') or live_def_hash!=meta.get('deferred_rows_sha256'): raise RuntimeError('API_RESIDUAL_CHANGED_AFTER_MANIFEST_FREEZE')
        return read_jsonl(MANIFEST),read_jsonl(DEFERRED)
    tmp=ROOT/'.residual.jsonl.tmp'; dtmp=ROOT/'.deferred_fci.jsonl.tmp'; write_jsonl(tmp,runnable); write_jsonl(dtmp,deferred); os.replace(tmp,MANIFEST); os.replace(dtmp,DEFERRED)
    meta={'created_at':datetime.now(timezone.utc).isoformat(),'run_id':RUN_ID,'runnable_count':len(runnable),'deferred_fci_count':len(deferred),'runnable_rows_sha256':live_run_hash,'deferred_rows_sha256':live_def_hash,'runnable_file_sha256':sha256_file(MANIFEST),'deferred_file_sha256':sha256_file(DEFERRED),'fci_policy':'DEFERRED_PENDING_CHECKPOINT_NOT_DONE_EMPTY'}
    MANIFEST_META.write_text(json.dumps(meta,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8'); return runnable,deferred

def browser_command(script:Path,*args:str)->list[str]:
    return ['runuser','-u',BROWSER_USER,'--','env',f'HOME={pwd.getpwnam(BROWSER_USER).pw_dir}',f'PYTHONPATH={REPO}:{HERE}',str(VENV_PY),str(script),*args]
def run_json_command(cmd:list[str],timeout:int)->tuple[int,dict]:
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=timeout,check=False); payload={}
    for line in reversed(p.stdout.splitlines()):
        try:
            obj=json.loads(line)
            if isinstance(obj,dict): payload=obj; break
        except Exception: pass
    return p.returncode,payload
def reauthenticate():
    rc,out=run_json_command(browser_command(REAUTH,'--profile',str(PROFILE),'--secret',str(SECRET),'--chrome',CHROME),150); status=str(out.get('status','UNKNOWN')); log('reauth',rc=rc,status=status,credentials_exposed=False,real_orders_sent=0)
    if rc!=0 or status!='AUTHENTICATED_TRUSTED_DEVICE': raise RuntimeError(f'PPI_WEB_REAUTH_FAILED:{status}')
def make_batch_dir(index:int)->Path:
    BATCH_ROOT.mkdir(parents=True,exist_ok=True); path=Path(tempfile.mkdtemp(prefix=f'batch-{index:06d}-',dir=BATCH_ROOT)); pw=pwd.getpwnam(BROWSER_USER); os.chown(path,pw.pw_uid,pw.pw_gid); os.chmod(path,0o700); return path
def write_targets(path:Path,tasks:list[dict]):
    path.write_text(''.join(json.dumps(t,ensure_ascii=False,sort_keys=True)+'\n' for t in tasks),encoding='utf-8'); pw=pwd.getpwnam(BROWSER_USER); os.chown(path,pw.pw_uid,pw.pw_gid); os.chmod(path,0o600)
def collect(tasks:list[dict],batch_index:int)->dict:
    if any(is_deferred(t) for t in tasks): raise RuntimeError('FCI_TASK_ENTERED_RUNNABLE_BATCH')
    directory=make_batch_dir(batch_index); targets=directory/'targets.jsonl'; output=directory/'capture.json'; write_targets(targets,tasks)
    cmd=browser_command(COLLECTOR,'--profile',str(PROFILE),'--output',str(output),'--targets-jsonl',str(targets),'--chrome',CHROME)
    rc,summary=run_json_command(cmd,max(240,35*len(tasks)))
    if str(summary.get('auth_status'))=='BLOCKED_AUTH_SESSION_EXPIRED': reauthenticate(); rc,summary=run_json_command(cmd,max(240,35*len(tasks)))
    if rc!=0 or str(summary.get('auth_status'))!='AUTHENTICATED_TRUSTED_DEVICE': raise RuntimeError(f"PPI_WEB_COLLECT_FAILED:{summary.get('auth_status','UNKNOWN')}")
    if not output.is_file(): raise RuntimeError('PPI_WEB_CAPTURE_MISSING')
    result=json.loads(output.read_text(encoding='utf-8'))
    if result.get('canonical_write')!='DENY' or int(result.get('real_orders_sent',-1))!=0: raise RuntimeError('PPI_WEB_COLLECTOR_SAFETY_CONTRACT_BROKEN')
    return result
def identity(row:dict)->tuple[str,str,str,str]: return tuple(str(row.get(k,'')).strip().upper() for k in ('symbol','instrument_type','market','settlement'))
def best_rows_by_identity(capture:dict)->dict[tuple[str,str,str,str],list[dict]]:
    best={}
    for item in capture.get('captures',[]):
        if not isinstance(item,dict): continue
        key=identity(item); rows=item.get('rows') if isinstance(item.get('rows'),list) else []
        if len(rows)>len(best.get(key,[])): best[key]=rows
    return best
def results_by_identity(capture:dict)->dict[tuple[str,str,str,str],dict]:
    return {identity(x):x for x in capture.get('target_results',[]) if isinstance(x,dict)}
def classify(provider_rows:int,valid_rows:int,rejected_rows:int)->str:
    if provider_rows<=0 or valid_rows<=0: return 'DONE_EMPTY'
    if rejected_rows>0 or valid_rows<provider_rows: return 'DONE_PARTIAL'
    return 'DONE_VALID'
def process_batch(state:ResidualState,tasks:list[dict],capture:dict):
    rows_by_key=best_rows_by_identity(capture); result_by_key=results_by_identity(capture); store=RuntimeSQLiteStore(OBSERVER_DB); today=datetime.now(timezone.utc).date(); date_from=today-timedelta(days=365)
    for task in tasks:
        key=identity(task); rows=rows_by_key.get(key,[]); cres=result_by_key.get(key,{})
        if not rows:
            if cres.get('result')=='PROVIDER_EMPTY' and int(cres.get('history_http') or 0)==200:
                state.finish(RUN_ID,task,'DONE_EMPTY',provider_rows=0,valid_rows=0,detail='PPI_WEB_RESOLVED_PROVIDER_EMPTY'); continue
            detail=f"PPI_WEB_UNRESOLVED:{cres.get('result','NO_TARGET_RESULT')}; discovery_http={cres.get('discovery_http')}; detail_http={cres.get('detail_http')}; history_http={cres.get('history_http')}"
            state.finish(RUN_ID,task,'ERROR',provider_rows=int(cres.get('provider_rows') or 0),valid_rows=0,detail=detail); continue
        try:
            result=ingest_web_rows(store,symbol=task['symbol'],instrument_type=task['instrument_type'],market=task['market'],settlement=task['settlement'],rows=rows,requested_from=date_from,requested_to=today)
            terminal=classify(result['provider_rows'],result['valid_rows'],result['rejected_rows']); detail=f"source=PPI_WEB_HISTORY; rejected={result['rejected_rows']}; canonical_updates={result['canonical_updates']}; protected_by_precedence={result['protected_by_precedence']}"
            state.finish(RUN_ID,task,terminal,provider_rows=result['provider_rows'],valid_rows=result['valid_rows'],detail=detail)
        except Exception as exc: state.finish(RUN_ID,task,'ERROR',detail=f'{type(exc).__name__}:{str(exc)[:900]}')
def main()->int:
    safety_check(); rows,deferred=ensure_manifest(); manifest_hash=sha256_file(MANIFEST); state=ResidualState(STATE_DB,STATUS); state.seed(RUN_ID,manifest_hash,rows); recovered=state.recover_orphan_running(RUN_ID)
    log('runner_start',run_id=RUN_ID,residual=len(rows),deferred_fci=len(deferred),recovered=recovered,manifest_sha256=manifest_hash,api_writer_active=False,real_orders_sent=0)
    batch_index=0
    while True:
        state.heartbeat(RUN_ID,status='RUNNING'); tasks=state.claim_many(RUN_ID,BATCH_SIZE)
        if not tasks: break
        batch_index+=1
        try: process_batch(state,tasks,collect(tasks,batch_index))
        except Exception: state.heartbeat(RUN_ID,status='RUNNING'); raise
    state.complete_if_terminal(RUN_ID); summary=state.publish(RUN_ID); log('runner_complete',**summary,deferred_fci=len(deferred),real_orders_sent=0); return 0
if __name__=='__main__': raise SystemExit(main())
