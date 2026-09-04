#!/usr/bin/env python3
"""Host-level RC4 general backup job with persistent operational evidence.

Runs outside Docker because it must capture both data/ and sre_vector_db. Secrets
remain excluded by the underlying backup implementation.
"""
from __future__ import annotations
import argparse, json, sqlite3, tarfile
from datetime import datetime, timezone
from pathlib import Path
from scripts.v17_host_general_backup import create_backup

def now_iso(): return datetime.now(timezone.utc).isoformat()

def record(db,state,detail,success=False):
    try:
        c=sqlite3.connect(str(db),timeout=30)
        c.execute('''CREATE TABLE IF NOT EXISTS operational_jobs(
          job_key TEXT PRIMARY KEY,last_run_at TEXT,last_success_at TEXT,state TEXT,detail TEXT)''')
        prev=c.execute("SELECT last_success_at FROM operational_jobs WHERE job_key='HOST_GENERAL_BACKUP'").fetchone()
        last_success=now_iso() if success else (prev[0] if prev else None)
        c.execute('''INSERT INTO operational_jobs(job_key,last_run_at,last_success_at,state,detail)
          VALUES('HOST_GENERAL_BACKUP',?,?,?,?) ON CONFLICT(job_key) DO UPDATE SET
          last_run_at=excluded.last_run_at,last_success_at=excluded.last_success_at,
          state=excluded.state,detail=excluded.detail''',(now_iso(),last_success,state,str(detail)[:1000]))
        c.commit(); c.close()
    except Exception:
        pass

def manifest_counts(archive):
    with tarfile.open(archive,'r:gz') as t:
        member=t.extractfile('porota-trading/BACKUP_MANIFEST.json')
        if member is None: return 0,0
        data=json.loads(member.read().decode('utf-8'))
    files=data.get('files') or []
    return sum(str(x).startswith('data/') for x in files),sum(str(x).startswith('sre_vector_db/') for x in files)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source',default='/opt/porota-trading'); ap.add_argument('--destination',default='/opt/porota-backups'); ap.add_argument('--db',default='/opt/porota-trading/data/paper_v17/observer_v17.db'); args=ap.parse_args()
    try:
        result=create_backup(Path(args.source),Path(args.destination),include_secrets=False)
        data_n,vector_n=manifest_counts(result['archive'])
        detail=f"archive={Path(result['archive']).name}; size={result['size_bytes']}; sha256={result['sha256']}; data_files={data_n}; sre_vector_db_files={vector_n}; secrets=NO"
        record(args.db,'VERDE',detail,True)
        print(json.dumps({'state':'VERDE','archive':result['archive'],'sha256':result['sha256'],'data_files':data_n,'sre_vector_db_files':vector_n,'secrets_included':False},sort_keys=True))
        return 0
    except Exception as exc:
        record(args.db,'ROJO',f'{type(exc).__name__}:{str(exc)[:700]}',False)
        print(json.dumps({'state':'ROJO','error':type(exc).__name__},sort_keys=True)); return 1
if __name__=='__main__': raise SystemExit(main())
