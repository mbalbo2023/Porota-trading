#!/usr/bin/env python3
"""Import sanitized trusted-browser evidence into Contract Evidence v2."""
from __future__ import annotations
import argparse, json, os, sqlite3, uuid
from pathlib import Path
from cp_contract_evidence_v2_hf6 import start_run, finish_run, record_snapshot, normalize_family
from rc6_ppi_contract_normalizer import separate_unbound_fee_estimate

DB=os.getenv('PAPER_V17_DB_PATH','/app/data/paper_v17/observer_v17.db')
class Store:
    def __init__(self,path): self.path=path
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row; return c

MAX_PRIMARY_ROWS=20_000
PPI_SETTLEMENT_SOURCES=frozenset({'PPI_FIELD','REQUEST_CANDIDATE'})
UNKNOWN_IDENTITY=frozenset({'','UNKNOWN','*'})

def candidates(store):
    """Unique complete PPI-primary five-key per ticker, across ALL statuses.

    XHR InstrumentosOperables and DatosTecnicos may have only a ticker: never
    distribute their terms to multiple currencies, markets or settlements.
    Complementary IOL/BYMA catalog rows cannot authenticate PPI identity.
    A stale/held second PPI row still makes the ticker ambiguous.
    """
    with store.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'financial_instrument_catalog' not in tables:
            return {}
        rows=c.execute("""SELECT ticker,instrument_type,market,currency,settlement,
                   settlement_source,status FROM financial_instrument_catalog
          WHERE settlement_source IN ('PPI_FIELD','REQUEST_CANDIDATE')
          LIMIT ?""",(MAX_PRIMARY_ROWS+1,)).fetchall()
    if len(rows)>MAX_PRIMARY_ROWS:
        raise ValueError('PPI_CONTRACT_CATALOG_BOUND_EXCEEDED')
    by_ticker={}
    for row in rows:
        identity={k:str(v or '').strip().upper() for k,v in dict(row).items()}
        ticker=identity['ticker']
        if ticker:
            by_ticker.setdefault(ticker,[]).append(identity)
    out={}
    for ticker,all_primary in by_ticker.items():
        out[ticker]=[]
        if len(all_primary)!=1:
            continue
        ident=all_primary[0]
        if (ident['status']!='AVAILABLE'
                or any(ident[k] in UNKNOWN_IDENTITY for k in
                       ('ticker','instrument_type','market','currency','settlement'))):
            continue
        out[ticker]=[ident]
    return out

def _unique_ppi_identity(cmap,ticker,provider_row):
    """Fail closed on ambiguous catalog or explicit contradictory XHR hints."""
    identities=cmap.get(ticker,[])
    if len(identities)!=1:
        return None
    identity=identities[0]
    for field in ('market','currency','settlement'):
        value=provider_row.get(field)
        # PPI InstrumentosOperables currency is a nested descriptor with an
        # ID/symbol, NOT authenticated ARS/USD_MEP/USD_CCL. Keep it as source
        # evidence, but never pretend it is a canonical monetary identity.
        if isinstance(value,str) and value.strip() and value.strip().upper() not in (
                'UNKNOWN',identity[field]):
            return None
    return identity

def _source_evidence(row,*,exclude_fees=False):
    data=dict(row)
    if exclude_fees:
        data,unbound_fees=separate_unbound_fee_estimate(data)
    else:
        unbound_fees={}
    evidence={k:v for k,v in data.items()
              if k not in {'ticker','currency','market','settlement'}
              and v not in (None,'',[],{})}
    if row.get('currency') not in (None,'',[],{}):
        evidence['provider_currency']=row['currency']
    evidence['evidence_scope']='PARTIAL_AUTHENTICATED_XHR'
    evidence['readiness_guard']='NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN'
    return evidence,unbound_fees

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
            rows=item.get('rows') or []
            tickers=[str(ev.get('ticker') or '').strip().upper() for ev in rows
                     if isinstance(ev,dict)]
            for ev in rows:
                if not isinstance(ev,dict):
                    continue
                ticker=str(ev.get('ticker') or '').strip().upper()
                if not ticker:
                    continue
                if tickers.count(ticker)!=1:
                    notes.append('PPI_XHR_DUPLICATE_TICKER_'+ticker)
                    conflicts+=1
                    continue
                ident=_unique_ppi_identity(cmap,ticker,ev)
                if ident is None:
                    notes.append('PPI_FULL_IDENTITY_ABSENT_AMBIGUOUS_OR_MISMATCH_'+ticker)
                    conflicts+=1
                    continue
                evidence,unbound=_source_evidence(ev,exclude_fees=True)
                if unbound:
                    notes.append('PPI_XHR_FEE_ESTIMATE_UNBOUND_'+ticker)
                try:
                    r=record_snapshot(store,family=ident['instrument_type'],ticker=ticker,
                      market=ident['market'],currency=ident['currency'],
                      settlement=ident['settlement'],source_class='PPI_AUTHENTICATED_XHR',
                      source_ref=url,evidence=evidence)
                    total+=1; changed+=int(bool(r.get('changed')))
                except Exception as exc:
                    notes.append('PPI_CONTRACT_IMPORT_'+type(exc).__name__)
        elif kind=='DatosTecnicos':
            ev=item.get('row') or {}
            if not isinstance(ev,dict):
                continue
            ticker=str(ev.get('ticker') or '').strip().upper()
            if not ticker:
                continue
            ident=_unique_ppi_identity(cmap,ticker,ev)
            if ident is None:
                notes.append('PPI_FULL_IDENTITY_ABSENT_AMBIGUOUS_OR_MISMATCH_'+ticker)
                conflicts+=1
                continue
            if normalize_family(ident['instrument_type']) not in {'BONOS','LETRAS','ON','LEBAC','NOBAC'}:
                notes.append('PPI_TECHNICAL_NON_FIXED_INCOME_'+ticker)
                continue
            evidence,_=_source_evidence(ev)
            try:
                r=record_snapshot(store,family=ident['instrument_type'],ticker=ticker,
                  market=ident['market'],currency=ident['currency'],
                  settlement=ident['settlement'],source_class='PPI_AUTHENTICATED_XHR',
                  source_ref=url,evidence=evidence)
                total+=1; changed+=int(bool(r.get('changed')))
            except Exception as exc:
                notes.append('PPI_CONTRACT_IMPORT_'+type(exc).__name__)
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
