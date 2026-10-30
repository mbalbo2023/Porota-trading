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

FAMILIES = ('CAUCIONES', 'FUTUROS', 'INDICES', 'ÍNDICES', 'ON', 'OBLIGACIONES')
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
    params = ','.join('?' * len(FAMILIES))
    for table, sql in (
        ('financial_instrument_catalog', f'SELECT upper(instrument_type) family,status,COUNT(*) n,MAX(last_seen_at) newest FROM financial_instrument_catalog WHERE upper(instrument_type) IN ({params}) GROUP BY upper(instrument_type),status'),
        ('candidate_identity_v2', f'SELECT upper(instrument_type) family,status,can_simulate,COUNT(*) n,MAX(checked_at) newest FROM candidate_identity_v2 WHERE upper(instrument_type) IN ({params}) GROUP BY upper(instrument_type),status,can_simulate'),
        ('contract_evidence_v2_current', f'SELECT upper(family) family,source_class,COUNT(*) n,MIN(observed_at) oldest,MAX(observed_at) newest FROM contract_evidence_v2_current WHERE upper(family) IN ({params}) GROUP BY upper(family),source_class'),
        ('paper_family_lifecycle', f'SELECT upper(family) family,state,COUNT(*) n,MAX(updated_at) newest FROM paper_family_lifecycle WHERE upper(family) IN ({params}) GROUP BY upper(family),state'),
        ('paper_positions', f'SELECT upper(asset_class) family,status,COUNT(*) n,MAX(opened_at) newest FROM paper_positions WHERE upper(asset_class) IN ({params}) GROUP BY upper(asset_class),status'),
    ):
        out[table] = rows(sql, FAMILIES) if table in tables else 'MISSING_TABLE'
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
        for r in c.execute(f'SELECT upper(instrument_type),detail FROM candidate_identity_v2 WHERE upper(instrument_type) IN ({params}) AND status != ?', (*FAMILIES, 'READY')):
            for reason in str(r[1] or '').split(';'):
                reason = reason.strip()
                counts[(r[0], code(reason))] += 1
        out['candidate_hold_codes'] = [{'family':k[0],'code':k[1],'n':v} for k,v in counts.most_common(40)]
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
        out['paper_fills_by_family'] = rows(f'SELECT upper(p.asset_class) family,f.side,COUNT(*) n,MAX(f.filled_at) newest FROM paper_positions p JOIN paper_fills f ON f.paper_id=p.paper_id WHERE upper(p.asset_class) IN ({params}) GROUP BY upper(p.asset_class),f.side',FAMILIES)
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
