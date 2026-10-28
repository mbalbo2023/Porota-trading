#!/usr/bin/env python3
"""RC4 storage lifecycle audit. AUDIT-ONLY: it never deletes, prunes or VACUUMs."""
from __future__ import annotations
import os, shutil
from pathlib import Path

MODE = "AUDIT_ONLY"
MUTATION_ALLOWED = False

def _size(path: Path) -> int:
    try:
        if path.is_file(): return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
    except OSError: return 0

def audit(root=None):
    base=Path(root or os.getenv('POROTA_DATA_ROOT','data')).resolve()
    usage=shutil.disk_usage(base if base.exists() else base.parent)
    pct=(usage.used/usage.total*100) if usage.total else 0.0
    level='GREEN' if pct<65 else 'YELLOW' if pct<75 else 'PLANNED_CLEANUP' if pct<85 else 'RED'
    paths={
      'observer_db':base/'paper_v17'/'observer_v17.db',
      'observer_wal':base/'paper_v17'/'observer_v17.db-wal',
      'history_db':Path(os.getenv('HIST_DB_PATH',str(base/'market_history.db'))),
      'logs':base/'logs','contract_evidence':base/'contract_evidence','backups':base/'backups',
      'sre_vector_db':Path(os.getenv('SRE_VECTOR_DB_PATH','sre_vector_db')).resolve(),
    }
    return {
      'mode':MODE,'mutation_allowed':False,'disk_used_pct':round(pct,2),'disk_state':level,
      'free_bytes':usage.free,'sizes':{k:_size(v) for k,v in paths.items()},
      'policy':{
        'raw_uncompressed_days':30,'contract_evidence_days':180,'logs_days':30,
        'keep_active_and_rollback':True,'vacuum_allowed_in_session':False,
        'docker_prune_allowed':False,'first_run':'AUDIT_ONLY'
      },
      'recommendations':[
        'Compress eligible raw/log evidence only after retention checks.',
        'Never remove active image or the retained rollback image automatically.',
        'No VACUUM during market session; WAL checkpoint only in a safe window.',
        'Deduplicate future identical History v2 versions at ingest rather than deleting history blindly.'
      ]
    }

if __name__=='__main__':
    import json; print(json.dumps(audit(),ensure_ascii=False,sort_keys=True))
