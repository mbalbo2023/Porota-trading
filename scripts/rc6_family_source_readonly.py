"""Bounded diagnostic: readonly DB + explicit public metadata, no project imports."""
import collections
import datetime as dt
import hashlib
import json
import pathlib
import re
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

# Full raw family inventory, including provider-specific US aliases.
FAMILY_SQL = "CASE upper(instrument_type) WHEN 'ON' THEN 'OBLIGACIONES' WHEN 'ETF' THEN 'ETFS' ELSE upper(instrument_type) END"
AUDIT_STAGE = 'START'
CODES = re.compile(r'^[A-Z0-9_:;,. /-]{1,160}$')

def code(value):
    if value is None or isinstance(value, (int, bool)): return value
    return value if CODES.fullmatch(str(value)) else 'REDACTED_NON_CODE'

def audit(path):
    global AUDIT_STAGE
    AUDIT_STAGE = 'DB_CONNECT'
    now = dt.datetime.now(dt.timezone.utc)
    deadline = time.monotonic() + 15
    c = sqlite3.connect(pathlib.Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    c.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    out = {'observed_at_utc': now.isoformat(), 'observed_at_art': now.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).isoformat(), 'db_access':'mode=ro;query_only=ON', 'provider_calls':0, 'order_calls':0}
    def rows(sql, args=()):
        global AUDIT_STAGE
        match = re.search(r'FROM ([a-z_0-9]+)', sql)
        AUDIT_STAGE = 'SELECT_' + (match.group(1).upper() if match else 'KNOWN_QUERY')
        return [dict(x) for x in c.execute(sql, args)]
    obs = rows('SELECT mode,real_orders_sent,ppi_auth,process_state,session_state,heartbeat_at,last_market_data_at FROM observer_state WHERE id=1')
    if not obs or obs[0]['mode'] != 'PRODUCTION_PAPER' or type(obs[0]['real_orders_sent']) is not int or obs[0]['real_orders_sent'] != 0:
        raise RuntimeError('PAPER_SAFETY_NOT_VERIFIED')
    out['observer'] = {k:(v if k.endswith('_at') else code(v)) for k,v in obs[0].items()}
    for table, sql in (
        ('financial_instrument_catalog', f'SELECT upper(instrument_type) family,status,COUNT(*) n,MAX(last_seen_at) newest FROM financial_instrument_catalog GROUP BY upper(instrument_type),status'),
        ('candidate_identity_v2', f'SELECT upper(instrument_type) family,status,can_simulate,COUNT(*) n,MAX(checked_at) newest FROM candidate_identity_v2 GROUP BY upper(instrument_type),status,can_simulate'),
        ('contract_evidence_v2_current', f'SELECT upper(family) family,source_class,COUNT(*) n,MIN(observed_at) oldest,MAX(observed_at) newest FROM contract_evidence_v2_current GROUP BY upper(family),source_class'),
        ('paper_family_lifecycle', f'SELECT upper(family) family,state,COUNT(*) n,MAX(updated_at) newest FROM paper_family_lifecycle GROUP BY upper(family),state'),
        ('paper_positions', f'SELECT upper(asset_class) family,status,COUNT(*) n,MAX(opened_at) newest FROM paper_positions GROUP BY upper(asset_class),status'),
    ):
        out[table] = rows(sql) if table in tables else 'MISSING_TABLE'
    if 'candidate_identity_v2' in tables:
        out['normalized_exact_identity_counts'] = rows(f'''SELECT family,COUNT(*) identities,
            SUM(any_simulate) identities_with_any_simulate,
            SUM(all_simulate) identities_with_all_simulate,
            SUM(CASE WHEN alias_count>1 THEN 1 ELSE 0 END) alias_collisions,
            SUM(CASE WHEN any_simulate!=all_simulate THEN 1 ELSE 0 END) alias_readiness_conflicts
            FROM (SELECT {FAMILY_SQL} family,ticker,market,currency,settlement,
              MAX(can_simulate) any_simulate,MIN(can_simulate) all_simulate,
              COUNT(DISTINCT upper(instrument_type)) alias_count
              FROM candidate_identity_v2 GROUP BY {FAMILY_SQL},ticker,market,currency,settlement)
            GROUP BY family''')
    if 'financial_instrument_catalog' in tables:
        AUDIT_STAGE = 'CAUCION_AND_ON_CONTRACT_FLAGS'
        out['caucion_contract_flags'] = []
        for r in c.execute("SELECT ticker,market,currency,settlement,status,capability,metadata_json FROM financial_instrument_catalog WHERE instrument_type='CAUCIONES' ORDER BY ticker,market,currency,settlement LIMIT 30"):
            row = dict(r)
            try: meta = json.loads(row.pop('metadata_json') or '{}')
            except (ValueError, TypeError): meta = {}
            policy = meta.get('paper_caucion_contract_v1') or {}
            if not isinstance(policy,dict): policy = {}
            row = {k:code(v) for k,v in row.items()}
            row['paper_contract_present'] = bool(policy)
            row['fee_authority_present'] = bool(policy.get('fee_authority'))
            row['fee_authority_sha256'] = hashlib.sha256(str(policy.get('fee_authority','')).encode()).hexdigest()
            row['live_ppi_fill_policy'] = policy.get('paper_fill_policy') == 'LIVE_PPI_BID_PARTICIPATION_CAP'
            out['caucion_contract_flags'].append(row)
        out['ymcjo_identity'] = []
        for r in c.execute("SELECT ticker,instrument_type,market,currency,settlement,settlement_source,status,capability,last_seen_at,metadata_json FROM financial_instrument_catalog WHERE ticker='YMCJO' ORDER BY instrument_type,market,currency,settlement LIMIT 12"):
            row = dict(r)
            try: meta=json.loads(row.pop('metadata_json') or '{}')
            except (ValueError,TypeError): meta={}
            row = {k:(v if k.endswith('_at') else code(v)) for k,v in row.items()}
            row['unit_fields'] = {k:meta[k] for k in ('units_per_lot','quantity_step','minimum_quantity','cash_multiplier','_iol_units_per_lot') if k in meta and isinstance(meta[k],(int,float))}
            row['unit_fields_present'] = [k for k in ('units_per_lot','quantity_step','minimum_quantity','cash_multiplier','_iol_units_per_lot') if k in meta]
            out['ymcjo_identity'].append(row)
    if 'candidate_identity_v2' in tables:
        AUDIT_STAGE = 'CANDIDATE_HOLD_CODES'
        # Canonical reason codes only. Never export full detail/metadata.
        counts = collections.Counter()
        for r in c.execute('SELECT upper(instrument_type),detail FROM candidate_identity_v2 WHERE can_simulate != 1'):
            for reason in str(r[1] or '').split(';'):
                reason = reason.strip()
                counts[(r[0], code(reason))] += 1
        out['candidate_hold_codes'] = [{'family':k[0],'code':k[1],'n':v} for k,v in counts.most_common(40)]
    if {'contract_evidence_v2_current','contract_evidence_v2_snapshots'} <= tables:
        AUDIT_STAGE = 'CAUCION_STATIC_SOURCE_KEY_DIFFERENCES'
        current = rows('''SELECT cur.ticker,cur.market,cur.currency,cur.settlement,cur.source_class,
            cur.snapshot_id,cur.evidence_hash,cur.observed_at,s.evidence_json
            FROM contract_evidence_v2_current cur JOIN contract_evidence_v2_snapshots s
              ON s.snapshot_id=cur.snapshot_id
            WHERE cur.family='CAUCIONES' ORDER BY cur.ticker,cur.source_class LIMIT 40''')
        groups = collections.defaultdict(list)
        out['caucion_current_evidence_fields'] = []
        def parsed(value):
            try: result=json.loads(value or '{}')
            except (ValueError,TypeError): return None
            return result if isinstance(result,dict) else None
        def safe_key(value):
            return value if re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{0,120}',str(value)) else 'REDACTED_KEY'
        def flattened(value,prefix=''):
            result={}
            for key,item in value.items():
                key=(prefix+'.' if prefix else '')+safe_key(key)
                if isinstance(item,dict): result.update(flattened(item,key))
                else: result[key]=item
            return result
        for row in current:
            payload=parsed(row.pop('evidence_json'))
            fields=flattened(payload) if payload is not None else {}
            row['field_names']=sorted(fields)[:100]
            row['payload_valid']=payload is not None
            out['caucion_current_evidence_fields'].append(row)
            identity=tuple(row[k] for k in ('ticker','market','currency','settlement'))
            groups[identity].append((row['source_class'],fields))
        out['caucion_cross_source_different_fields']=[]
        for identity,source_fields in groups.items():
            keys=set().union(*(fields.keys() for _,fields in source_fields))
            differing=[]
            for key in sorted(keys):
                vals=[json.dumps(fields[key],sort_keys=True) for _,fields in source_fields if key in fields and fields[key] is not None]
                if len(set(vals))>1: differing.append(key)
            out['caucion_cross_source_different_fields'].append({
                'identity':identity,'sources':[src for src,_ in source_fields],
                'different_field_names':differing[:100],
                'derived_official_rule_present':any(src=='DERIVED_OFFICIAL_RULE' for src,_ in source_fields)})
        if 'contract_evidence_v2_changes' in tables:
            changes=rows('''SELECT ch.change_id,ch.ticker,ch.market,ch.currency,ch.settlement,
                ch.source_class,ch.previous_hash,ch.current_hash,ch.detected_at,ch.status,
                prev.evidence_json previous_json,curr.evidence_json current_json
                FROM contract_evidence_v2_changes ch
                LEFT JOIN contract_evidence_v2_snapshots prev
                  ON prev.family=ch.family AND prev.ticker=ch.ticker AND prev.market=ch.market
                  AND prev.currency=ch.currency AND prev.settlement=ch.settlement
                  AND prev.source_class=ch.source_class AND prev.evidence_hash=ch.previous_hash
                LEFT JOIN contract_evidence_v2_snapshots curr
                  ON curr.family=ch.family AND curr.ticker=ch.ticker AND curr.market=ch.market
                  AND curr.currency=ch.currency AND curr.settlement=ch.settlement
                  AND curr.source_class=ch.source_class AND curr.evidence_hash=ch.current_hash
                WHERE ch.family='CAUCIONES' AND ch.status='CHANGED_REVIEW_REQUIRED'
                ORDER BY ch.change_id DESC LIMIT 40''')
            for row in changes:
                prev=parsed(row.pop('previous_json'));curr=parsed(row.pop('current_json'))
                row['both_payloads_valid']=prev is not None and curr is not None
                prev=flattened(prev or {});curr=flattened(curr or {})
                row['changed_field_names']=[k for k in sorted(set(prev)|set(curr)) if prev.get(k)!=curr.get(k)][:100]
            out['caucion_pending_change_fields']=changes
    if 'paper_caucion_live_book' in tables:
        out['caucion_live_book'] = rows('SELECT ticker,market,currency,settlement,term_days,quoted_at,state,source,observed_at FROM paper_caucion_live_book ORDER BY ticker,market,currency,settlement LIMIT 30')
        for row in out['caucion_live_book']:
            for field in ('ticker','market','currency','settlement','state','source'): row[field] = code(row[field])
            try:
                quoted = dt.datetime.fromisoformat(row['quoted_at'].replace('Z','+00:00'))
                row['age_seconds'] = (now-quoted).total_seconds() if quoted.tzinfo else None
            except (ValueError, TypeError, AttributeError): row['age_seconds'] = None
        out['caucion_live_book_total'] = c.execute('SELECT COUNT(*) FROM paper_caucion_live_book').fetchone()[0]
    else: out['caucion_live_book'] = 'MISSING_TABLE'
    for table, sql in (
        ('paper_caucion_cash_sweep_state','SELECT heartbeat_at,state,last_attempt_at,code,real_orders_sent FROM paper_caucion_cash_sweep_state WHERE id=1'),
        ('paper_caucion_cash_sweep_attempts','SELECT evaluated_at,status,code FROM paper_caucion_cash_sweep_attempts ORDER BY evaluated_at DESC LIMIT 12'),
        ('paper_cauciones','SELECT status,COUNT(*) n,MIN(opened_at) first_open,MAX(opened_at) last_open,MAX(maturity_at) last_maturity FROM paper_cauciones GROUP BY status'),
    ):
        out[table] = rows(sql) if table in tables else 'MISSING_TABLE'
        if isinstance(out[table], list):
            for row in out[table]:
                for key in ('state','status','code'):
                    if key in row: row[key] = code(row[key])
    if 'paper_fills' in tables and 'paper_positions' in tables:
        out['paper_fills_by_family'] = rows(f'SELECT upper(p.asset_class) family,f.side,COUNT(*) n,MAX(f.filled_at) newest FROM paper_positions p JOIN paper_fills f ON f.paper_id=p.paper_id GROUP BY upper(p.asset_class),f.side')
    out['workers'] = {}
    for table in ('intraday_scalping_worker_state','paper_supervisor_state','paper_exit_reader_state','candle_worker_state'):
        if table not in tables:
            out['workers'][table] = 'MISSING_TABLE'
            continue
        cols={r[1] for r in c.execute(f'PRAGMA table_info({table})')}
        selected=[k for k in ('heartbeat_at','state','status','code','last_attempt_at','last_cycle_at','checked_at','real_orders_sent') if k in cols]
        if selected:
            value=rows('SELECT '+','.join(selected)+' FROM '+table+(' WHERE id=1' if 'id' in cols else '')+' LIMIT 1')
            for row in value:
                for k,v in list(row.items()):
                    if not k.endswith('_at'):row[k]=code(v)
            out['workers'][table]=value
        else: out['workers'][table]='NO_ALLOWLISTED_COLUMNS'
    if 'trade_gate_evaluations' in tables:
        recent=rows('SELECT evaluated_at,final_result,reason FROM trade_gate_evaluations ORDER BY id DESC LIMIT 200')
        counts=collections.Counter((code(r['final_result']),code(r['reason'])) for r in recent)
        out['latest_200_gate_evaluations']={'sample_size':len(recent),
            'latest_at':recent[0]['evaluated_at'] if recent else None,
            'counts':[{'final_result':k[0],'reason':k[1],'n':v} for k,v in counts.most_common(40)]}
    if 'paper_decisions' in tables:
        out['latest_500_decisions']=rows('SELECT source,strategy_version,action,COUNT(*) n,MAX(decided_at) newest FROM (SELECT source,strategy_version,action,decided_at FROM paper_decisions ORDER BY id DESC LIMIT 500) GROUP BY source,strategy_version,action')
        for row in out['latest_500_decisions']:
            row['source']=code(row['source']);row['action']=code(row['action'])
            row['strategy_version_sha256']=hashlib.sha256(str(row.pop('strategy_version')).encode()).hexdigest()
    c.close()
    return out

if __name__ == '__main__':
    try:
        result = audit(sys.argv[1])
        AUDIT_STAGE = 'INSTALLED_SOURCE_METADATA'
        root = pathlib.Path('/opt/porota-trading')
        result['installed_source_hashes'] = {}
        for rel in ('dj_caucion_live_ppi_rc6.py','di_caucion_cash_sweep_runtime_hf6.py','bd_ppi_readonly_guard.py'):
            p=root/rel
            if p.is_file() and p.stat().st_size<2000000:
                result['installed_source_hashes'][rel]=hashlib.sha256(p.read_bytes()).hexdigest()
        p=root/'data/deploy/porota-deploy-bundle-v2-manifest.json'
        if p.is_file() and p.stat().st_size<2000000:
            m=json.loads(p.read_text())
            result['bundle_manifest']={k:m[k] for k in ('candidate_sha','candidate_tree','source_sha','tree_sha','bundle_sha256') if k in m}
        print('RC6_FAMILY_SOURCE_READONLY='+json.dumps(result,sort_keys=True,separators=(',',':')))
    except Exception as exc:
        print('RC6_FAMILY_SOURCE_READONLY_ERROR='+type(exc).__name__+'|stage='+AUDIT_STAGE)
        raise SystemExit(2)
