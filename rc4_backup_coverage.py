"""RC4 read-only backup coverage matrix for every persistent store."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from pathlib import Path


def _age(value, now=None):
    if not value: return None
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        ref=now or datetime.now(timezone.utc)
        return max(0,(ref-dt.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _latest(connection, kind):
    try:
        row=connection.execute("""SELECT created_at,path,size_bytes,sha256,restore_test,state,detail
          FROM backup_runs WHERE kind=? ORDER BY created_at DESC,id DESC LIMIT 1""",(kind,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def matrix(store, *, now=None):
    observer=Path(store.path)
    history=Path(os.getenv('HIST_DB_PATH','data/market_history.db')).resolve()
    vector=Path(os.getenv('SRE_VECTOR_DB_PATH','sre_vector_db')).resolve()
    with store.connect() as c:
        obs=_latest(c,'DAILY')
        hist=_latest(c,'HISTORY_DAILY')
        host_job=None
        try:
            row=c.execute("SELECT last_run_at,last_success_at,state,detail FROM operational_jobs WHERE job_key='HOST_GENERAL_BACKUP'").fetchone()
            host_job=dict(row) if row else None
        except Exception:
            pass
    rows=[]
    for key,path,record,cadence in (
        ('observer_v17.db',observer,obs,20*3600),
        ('market_history.db',history,hist,20*3600),
    ):
        age=_age((record or {}).get('created_at'),now)
        if not path.exists(): status='NOT_CREATED'
        elif not record: status='NO_BACKUP'
        elif str(record.get('state'))!='VERDE' or str(record.get('restore_test'))!='OK': status='ERROR'
        elif age is None or age>cadence*1.5: status='STALE_BACKUP'
        else: status='PROTECTED'
        rows.append({'storage':key,'path':str(path),'exists':path.exists(),'status':status,
                     'last_backup_at':(record or {}).get('created_at'),'age_seconds':age,
                     'backup_path':(record or {}).get('path'),'size_bytes':(record or {}).get('size_bytes'),
                     'sha256':(record or {}).get('sha256'),'restore_test':(record or {}).get('restore_test'),
                     'detail':(record or {}).get('detail') or ''})
    vector_status='NOT_CREATED' if not vector.exists() else 'HOST_SNAPSHOT_EVIDENCE_MISSING'
    host_detail=str((host_job or {}).get('detail') or '')
    vector_evidenced='sre_vector_db_files=' in host_detail
    if host_job and str(host_job.get('state')).upper() in {'VERDE','OK'} and host_job.get('last_success_at') and vector_evidenced:
        age=_age(host_job.get('last_success_at'),now)
        vector_status='PROTECTED_BY_HOST_GENERAL' if age is not None and age<=36*3600 else 'STALE_BACKUP'
    rows.append({'storage':'sre_vector_db','path':str(vector),'exists':vector.exists(),
                 'status':vector_status,'last_backup_at':(host_job or {}).get('last_success_at'),
                 'age_seconds':_age((host_job or {}).get('last_success_at'),now),
                 'backup_path':None,'size_bytes':None,'sha256':None,'restore_test':None,
                 'detail':(host_job or {}).get('detail') or 'No se declara consistente sin evidencia del backup general del host.'})
    rows.append({'storage':'host_general_backup','path':'HOST','exists':True,
                 'status':str((host_job or {}).get('state') or 'NO_EVIDENCE'),
                 'last_backup_at':(host_job or {}).get('last_success_at'),
                 'age_seconds':_age((host_job or {}).get('last_success_at'),now),
                 'backup_path':None,'size_bytes':None,'sha256':None,'restore_test':None,
                 'detail':(host_job or {}).get('detail') or 'No hay evidencia periódica persistida en operational_jobs.'})
    return rows
