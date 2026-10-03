"""Exact contract evidence export and offline reconciliation replay.

Export imports only stdlib, uses mode=ro/query_only and never imports Porota.
Replay imports the chosen source only on a GitHub-hosted runner with a fresh
temporary SQLite fixture. No full production DB, account or trade export.
"""
from __future__ import annotations
import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

PRODUCT='e9cf3fdbd9e6a71a0aa72365ff3b2727378db10f'
FAMILIES="('OPCIONES','ON','FUTUROS')"
KEY=('ticker','instrument_type','market','currency','settlement')
META=('_discovery_source','_availability_source','_provider_instrument_type','_contract_bridge',
      '_contract_conflicts','financial_contract_v17','paper_family_contract_v1',
      'isin','cajaValoresCode','nominalInPrice','type','operable','isActive','isTradable')
FORBIDDEN=('authorization','cookie','api_key','api_secret','password','access_token','refresh_token','account_number','private_key')


def sanitized(value):
    if isinstance(value,str) and value[:1] in ('{','['):
        try:value=json.loads(value)
        except ValueError:return
    if isinstance(value,dict):
        for key,item in value.items():
            if any(token in str(key).lower() for token in FORBIDDEN):
                raise ValueError('SENSITIVE_FIELD_NOT_EXPORTED')
            sanitized(item)
    elif isinstance(value,list):
        for item in value:sanitized(item)


def export_contracts(db,state_path):
    state=json.loads(Path(state_path).read_text())
    if state.get('deploy_sha')!=PRODUCT or state.get('mode')!='PRODUCTION_PAPER' or state.get('real_orders_sent')!=0:
        raise ValueError('SOURCE_OR_PAPER_DRIFT')
    path=Path(db).resolve(strict=True)
    os.nice(15)
    started=time.monotonic();deadline=started+25
    con=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)
    con.row_factory=sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON');con.execute('PRAGMA busy_timeout=250');con.execute('PRAGMA cache_size=-4096')
        con.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
        if str(con.execute('PRAGMA journal_mode').fetchone()[0]).lower()!='wal':raise ValueError('EXISTING_WAL_REQUIRED')
        con.execute('BEGIN')
        safety=dict(con.execute('SELECT mode,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone())
        if safety.get('mode')!='PRODUCTION_PAPER' or safety.get('real_orders_sent')!=0:raise ValueError('PAPER_INVARIANT')
        info={}
        for table in ('financial_instrument_catalog','contract_evidence_v2_current','contract_evidence_v2_snapshots','contract_evidence_v2_changes'):
            info[table]=[dict(r) for r in con.execute('PRAGMA table_info('+table+')')]
            if not info[table]:raise ValueError('REQUIRED_TABLE_MISSING:'+table)
        scalar=','.join('"'+r['name']+'"' for r in info['financial_instrument_catalog'] if r['name']!='metadata_json')
        meta='json_object('+','.join("'"+k+"',json_extract(metadata_json,'$."+k+"')" for k in META)+') AS metadata_json'
        # Preserve all members of the diagnosed cohorts, including legacy ON
        # siblings. Exclude complementary shadow catalogues unrelated to fixes.
        where="instrument_type='ON' OR (status='AVAILABLE' AND ((instrument_type='OPCIONES' AND capability='CONTRACT_EVIDENCE_REVIEW_REQUIRED') OR (instrument_type='OBLIGACIONES' AND capability IN ('CONTRACT_EVIDENCE_REVIEW_REQUIRED','NEEDS_NOMINAL_UNITS')) OR instrument_type='FUTUROS'))"
        join=' AND '.join('s.'+k+'=ch.'+k for k in ('family','ticker','market','currency','settlement','source_class'))
        wanted=("SELECT snapshot_id FROM contract_evidence_v2_current WHERE family IN "+FAMILIES+
            " UNION SELECT s.snapshot_id FROM contract_evidence_v2_changes ch JOIN contract_evidence_v2_snapshots s ON "+join+
            " AND s.evidence_hash IN (ch.previous_hash,ch.current_hash) WHERE ch.status='CHANGED_REVIEW_REQUIRED' AND ch.family IN "+FAMILIES)
        queries={
            'financial_instrument_catalog':'SELECT '+scalar+','+meta+' FROM financial_instrument_catalog WHERE '+where,
            'contract_evidence_v2_current':'SELECT * FROM contract_evidence_v2_current WHERE family IN '+FAMILIES,
            'contract_evidence_v2_changes':"SELECT * FROM contract_evidence_v2_changes WHERE status='CHANGED_REVIEW_REQUIRED' AND family IN "+FAMILIES,
            'contract_evidence_v2_snapshots':'SELECT s.* FROM ('+wanted+') wanted JOIN contract_evidence_v2_snapshots s ON s.snapshot_id=wanted.snapshot_id',
        }
        result=dict(schema_version=1,product_sha=PRODUCT,captured_at=datetime.now(timezone.utc).isoformat(),
            observer=safety,tables={},schemas=info,database_writes=0,broker_calls=0,full_database_export=False,
            scope='CURRENT_AND_UNRESOLVED_EXACT_CONTRACT_SNAPSHOTS',catalog_metadata_fields=META)
        total=0
        for table,sql in queries.items():
            print('CONTRACT_EXPORT_STAGE='+table,file=sys.stderr)
            rows=[];cur=con.execute(sql)
            while True:
                if time.monotonic()>deadline:raise TimeoutError('EXPORT_DEADLINE')
                batch=cur.fetchmany(200)
                if not batch:break
                for row in batch:
                    value=dict(row);sanitized(value);rows.append(value)
                if total+len(rows)>40000:raise ValueError('EXPORT_ROW_LIMIT')
            total+=len(rows);result['tables'][table]=rows
        result['elapsed_seconds']=round(time.monotonic()-started,3)
        result['complete']=True
        raw=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()
        if len(raw)>24000000:raise ValueError('EXPORT_BYTE_LIMIT')
        return raw
    finally:con.close()


class LocalStore:
    def __init__(self,path):self.path=path;self.events=[]
    def connect(self):
        con=sqlite3.connect(self.path,timeout=1);con.row_factory=sqlite3.Row;return con
    def event(self,*args):self.events.append(args)


def seed_local(store,payload):
    with store.connect() as con:
        for table,columns in payload['schemas'].items():
            if re.fullmatch(r'[a-z0-9_]+',table) is None:raise ValueError('UNSAFE_TABLE')
            definitions=[]
            for col in columns:
                if re.fullmatch(r'[a-z0-9_]+',col['name']) is None or col['type'].upper() not in ('TEXT','INTEGER','REAL','BLOB','NUMERIC',''):
                    raise ValueError('UNSAFE_COLUMN')
                definitions.append('"'+col['name']+'" '+col['type'])
            con.execute('CREATE TABLE "'+table+'" ('+','.join(definitions)+')')
            rows=payload['tables'][table]
            fields=[c['name'] for c in columns]
            con.executemany('INSERT INTO "'+table+'" VALUES('+','.join('?' for _ in fields)+')',
                [tuple(row[k] for k in fields) for row in rows])
        con.executescript('''
        CREATE TABLE instrument_catalog(instrument_type,ticker,description,market,settlement,downloaded_at,raw_json);
        CREATE TABLE candidate_universe(ticker,instrument_type,settlement,market,can_simulate,status,detail,last_checked_at);
        CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_time,component,event,symbol,detail);
        CREATE TABLE complementary_contract_retry(ticker,instrument_type,market,currency,settlement,source,observed_at,state,reason,last_attempt_at,attempts,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement,source));
        ''')


def run_one(source,payload,root,tag):
    from unittest.mock import patch
    import bu_instrument_catalog as catalog
    import rc6_contract_bridge as bridge
    from cp_contract_evidence_v2_hf6 import pending_material_changes
    store=LocalStore(str(root/(tag+'.db')));seed_local(store,payload)
    clock=datetime.fromisoformat(payload['captured_at'])
    with store.connect() as con:pending=pending_material_changes(con)
    exact_complements=bridge.complements_from_store(store,now=clock)
    tree=ast.parse(Path(source).read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_reconcile_complementary_catalog')
    ns=dict(Path=Path,os=os,json=json,financial_catalog=catalog,complementary_discovery=lambda _:[],
        now_iso=lambda:payload['captured_at'],COMPLEMENTARY_CONTRACT_TTL_SECONDS=86400)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),ns)
    with patch.dict(os.environ,{'POROTA_MARKET_DATA_ROOT':str(root/'empty-market-root')}),patch('rc6_contract_bridge.complements_from_store',return_value=exact_complements):
        promoted=ns[fn.name](store)
    with store.connect() as con:
        rows=[dict(r) for r in con.execute('SELECT * FROM candidate_identity_v2 ORDER BY instrument_type,ticker,market,currency,settlement')]
        after=[dict(r) for r in con.execute('SELECT * FROM financial_instrument_catalog')]
    original={tuple(r[k] for k in KEY):r for r in payload['tables']['financial_instrument_catalog']}
    # V2 may present non-target current evidence: ignore out-of-scope inserted
    # observational rows for delta accounting, but never count them as proof.
    targeted=[r for r in rows if tuple(r[k] for k in KEY) in original]
    if len(targeted)!=len(original):raise ValueError('REPLAY_TARGET_SCOPE_MISMATCH')
    for r in after:
        previous=original.get(tuple(r[k] for k in KEY))
        if previous and any(r[k]!=previous[k] for k in (*KEY,'last_seen_at','settlement_source','description','run_id')):
            raise ValueError('PPI_IDENTITY_CHANGED')
    return dict(rows=targeted,promoted_counter=promoted,material_pending_count=len(pending),
        material_pending_identities=sorted(pending),complement_states=dict(Counter(r['contract_bridge']['status'] for r in exact_complements)))


def replay(args):
    payload=json.loads(Path(args.input).read_text())
    if not payload.get('complete') or payload.get('product_sha')!=PRODUCT or payload.get('database_writes')!=0:
        raise ValueError('INPUT_NOT_VERIFIED')
    if payload['observer']['mode']!='PRODUCTION_PAPER' or payload['observer']['real_orders_sent']!=0:
        raise ValueError('INPUT_SAFETY')
    sys.path.insert(0,str(Path(args.candidate).resolve()))
    from cp_contract_evidence_v2_hf6 import evidence_hash
    for row in payload['tables']['contract_evidence_v2_snapshots']:
        if evidence_hash(json.loads(row['evidence_json']))!=row['evidence_hash']:
            raise ValueError('SNAPSHOT_HASH_MISMATCH')
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        baseline=run_one(Path(args.baseline)/'bf_production_paper_observer.py',payload,root,'baseline')
        candidate=run_one(Path(args.candidate)/'bf_production_paper_observer.py',payload,root,'candidate')
    before={tuple(r[k] for k in KEY):r for r in baseline['rows']}
    ready=lambda r:r['status']=='AVAILABLE' and r['can_simulate']==1
    gained=[r for r in candidate['rows'] if ready(r) and not ready(before[tuple(r[k] for k in KEY)])]
    lost=[r for r in candidate['rows'] if not ready(r) and ready(before[tuple(r[k] for k in KEY)])]
    if lost:raise ValueError('UNEXPECTED_READY_REGRESSION')
    if any(r['instrument_type']=='ON' for r in gained):raise ValueError('LEGACY_ALIAS_PROMOTED')
    pending={tuple(r) for r in candidate['material_pending_identities']}
    for row in gained:
        family='ON' if row['instrument_type']=='OBLIGACIONES' else row['instrument_type']
        if (family,row['ticker'],row['market'],row['currency'],row['settlement']) in pending:
            raise ValueError('MATERIAL_CHANGE_BYPASSED')
    result=dict(schema_version=1,product_sha=PRODUCT,input_captured_at=payload['captured_at'],
        input_sha256=hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
        export_elapsed_seconds=payload['elapsed_seconds'],export_counts={k:len(v) for k,v in payload['tables'].items()},
        scope='FROZEN_V2_RECONCILIATION_ONLY_NOT_DEPLOYED',targeted_rows=len(before),
        baseline_ready=sum(ready(r) for r in baseline['rows']),candidate_ready=sum(ready(r) for r in candidate['rows']),
        newly_ready=len(gained),newly_ready_by_family=dict(Counter(r['instrument_type'] for r in gained)),
        still_blocked_by_family=dict(Counter(r['instrument_type'] for r in candidate['rows'] if not ready(r))),
        material_pending_count=candidate['material_pending_count'],material_pending_identities=candidate['material_pending_identities'],
        source_database_writes=0,broker_calls=0,production_changed=False,
        limitations=['Offline V2-only reconciliation on an explicit catalog subset; not the whole runtime.',
            'No quote, economics, signal or execution gate is bypassed; ready does not guarantee a trade.',
            'Actual runtime counts remain unmodified and require later deployment validation.'])
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    (out/'replay-summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    (out/'replay-newly-ready.json').write_text(json.dumps(gained,indent=2,ensure_ascii=False))
    (out/'replay-full.json').write_text(json.dumps(dict(baseline=baseline,candidate=candidate),ensure_ascii=False))
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='mode',required=True)
    exp=sub.add_parser('export');exp.add_argument('--db',required=True);exp.add_argument('--state',required=True)
    rep=sub.add_parser('replay');rep.add_argument('--input',required=True);rep.add_argument('--baseline',required=True);rep.add_argument('--candidate',required=True);rep.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.mode=='export':sys.stdout.buffer.write(export_contracts(args.db,args.state))
    else:replay(args)
