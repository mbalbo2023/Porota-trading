#!/usr/bin/env python3
"""Import sanitized trusted-browser evidence into Contract Evidence v2."""
from __future__ import annotations
import argparse, json, os, sqlite3, uuid
from pathlib import Path
from cp_contract_evidence_v2_hf6 import start_run, finish_run, record_snapshot, normalize_family

DB=os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')
class Store:
    def __init__(self,path): self.path=path
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row; return c

def candidates(store):
    with store.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'candidate_universe' not in tables: return {}
        out={}
        for r in c.execute("SELECT ticker,instrument_type,market,settlement,status FROM candidate_universe WHERE status='AVAILABLE'"):
            d=dict(r); out.setdefault(str(d.get('ticker') or '').upper(),[]).append(d)
        return out

def import_capture(store,path):
    raw=json.loads(Path(path).read_text(encoding='utf-8'))
    jobs=list(dict.fromkeys(raw.get('jobs') or [])); auth=str(raw.get('auth_status') or 'UNKNOWN')
    cmap=candidates(store); total=changed=conflicts=0; notes=[]
    run_ids={}
    for job in jobs:
        rid='rc4-'+uuid.uuid4().hex; run_ids[job]=rid; start_run(store,run_id=rid,job_key=job,detail='trusted-device browser read-only')
    state='VERDE'
    if auth!='AUTHENTICATED_TRUSTED_DEVICE':
        state='BLOCKED_AUTH' if 'AUTH' in auth else 'BLOCKED_NOT_CONFIGURED'
        for job,rid in run_ids.items(): finish_run(store,run_id=rid,state=state,detail=auth)
        return {'state':state,'auth_status':auth,'records':0,'changed':0,'conflicts':0,'real_orders_sent':0}
    for url,item in (raw.get('endpoints') or {}).items():
        kind=item.get('kind')
        if kind=='InstrumentosOperables':
            for ev in item.get('rows') or []:
                ticker=str(ev.get('ticker') or '').upper()
                for ident in cmap.get(ticker,[]):
                    evidence={k:v for k,v in ev.items() if v not in (None,'',[],{}) and k!='ticker'}
                    try:
                        r=record_snapshot(store,family=ident.get('instrument_type'),ticker=ticker,
                          market=ident.get('market'),settlement=ident.get('settlement'),source_class='PPI_AUTHENTICATED_XHR',source_ref=url,evidence=evidence)
                        total+=1; changed+=int(bool(r.get('changed')))
                    except Exception as exc: notes.append(type(exc).__name__)
        elif kind=='DatosTecnicos':
            ev=item.get('row') or {}; ticker=str(ev.get('ticker') or '').upper()
            for ident in cmap.get(ticker,[]):
                if normalize_family(ident.get('instrument_type')) not in {'BONOS','LETRAS','ON','LEBAC','NOBAC'}: continue
                evidence={k:v for k,v in ev.items() if v not in (None,'',[],{}) and k!='ticker'}
                try:
                    r=record_snapshot(store,family=ident.get('instrument_type'),ticker=ticker,
                      market=ident.get('market'),settlement=ident.get('settlement'),source_class='PPI_AUTHENTICATED_XHR',source_ref=url,evidence=evidence)
                    total+=1; changed+=int(bool(r.get('changed')))
                except Exception as exc: notes.append(type(exc).__name__)
        elif kind=='SubyacenteOpciones':
            notes.append('OPTION_UNDERLYING_CATALOG_OBSERVED_NOT_CONTRACT')
        elif kind=='SCHEMA_ONLY':
            notes.append('SCHEMA_ONLY_NO_CONTRACT_FIELDS')
    final='VERDE' if not notes else 'AMARILLO'
    detail=f'records={total}; changed={changed}; notes={",".join(sorted(set(notes)))[:1000]}'
    for job,rid in run_ids.items(): finish_run(store,run_id=rid,state=final,records=total,changed=changed,conflicts=conflicts,detail=detail)
    return {'state':final,'auth_status':auth,'records':total,'changed':changed,'conflicts':conflicts,'notes':sorted(set(notes)),'real_orders_sent':0}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--input',required=True); args=ap.parse_args()
    result=import_capture(Store(DB),args.input); print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
