"""Verify IOL worker states and PAPER safety on the installed release."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


EXPECTED = {"source_sha": "1a04c5bae138bf2da59e98a69c77c408106d4b4d",
            "source_tree": "d59db6a55f4eed90bfc8428535f38545c4cce84c",
            "image_id": "sha256:62227a2add3624835f9d39a1726c721f393cc10f8b6209259dfe06111e12cf2d",
            "attempt": "46052331831-1"}
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "porota.iol.intraday-preopen-postflight.v1"

REMOTE = '''
from contextlib import closing
from datetime import datetime, timezone
import hashlib, json, os, re, sqlite3, subprocess, sys
from pathlib import Path

SERVICE = 'porota-iol-oauth-health-rc6.service'
TIMER = 'porota-iol-oauth-health-rc6.timer'
STATES = {'loaded','masked','not-found','active','inactive','failed','waiting','running','dead','exited',
          'enabled','disabled','static','success','exit-code','timeout','signal','resources','exec-condition'}
AUTH_RESULTS = {'AUTHENTICATED','REFERENCE_GD30_VERIFIED','IOL_REST_HTTP_ERROR','IOL_REST_AUTH_ON_HOLD',
                'IOL_REST_TOKEN_CUSTODY_UNVERIFIED','IOL_REST_TRANSPORT_UNAVAILABLE','IOL_REST_CONFIG_UNAVAILABLE',
                'IOL_REST_CREDENTIALS_UNCONFIGURED','IOL_REST_RESPONSE_UNVERIFIED','IOL_REST_TOKEN_UNVERIFIED'}

def run_command(command, max_bytes):
    proc = subprocess.run(command, capture_output=True, text=True, timeout=6, check=False,
                          env={'LANG':'C','LC_ALL':'C','TZ':'UTC','PATH':'/usr/bin:/bin'})
    return proc.stdout if proc.returncode == 0 and len(proc.stdout.encode()) <= max_bytes else None

def systemd(unit, properties, parse_clock):
    raw = run_command(['/usr/bin/systemctl','show','--all','--no-pager','--property='+','.join(properties),'--',unit],8192)
    if raw is None:
        return {'state':'NO_VERIFICADO'}
    row = dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
    if row.get('Id') != unit:
        return {'state':'NO_VERIFICADO'}
    result = {'state':'OBSERVED'}
    for key in ('LoadState','ActiveState','SubState','UnitFileState','Result'):
        if row.get(key) in STATES:
            result[key] = row[key]
    for key in ('LastTriggerUSec','NextElapseUSecRealtime','ExecMainStartTimestamp','ExecMainExitTimestamp'):
        stamp = parse_clock(row.get(key))
        if stamp:
            result[key] = stamp.isoformat()
    if unit == TIMER:
        result['binds_health_service'] = (row.get('Unit') or row.get('Triggers')) == SERVICE
    if unit == SERVICE and row.get('ExecMainCode') in ('1','2','3') and row.get('ExecMainStatus','').isdecimal():
        code = int(row['ExecMainStatus'])
        result['exit_status'] = code if 0 <= code <= 255 else None
    return result

def journal(started, current):
    raw = run_command(['/usr/bin/journalctl','--unit',SERVICE,'--since','@'+str(int(started.timestamp())),
                       '--lines','32','--no-pager','--output=json','--output-fields=MESSAGE,_SYSTEMD_UNIT'],128*1024)
    if raw is None:
        return {'state':'NO_VERIFICADO','health_runs':[]}
    entries, observed = [], 0
    for line in raw.splitlines():
        try:
            record = json.loads(line)
            if record.get('_SYSTEMD_UNIT') != SERVICE or not isinstance(record.get('MESSAGE'),str):
                continue
            observed += 1
            message = json.loads(record['MESSAGE'])
            timestamp = datetime.fromtimestamp(int(record['__REALTIME_TIMESTAMP']) / 1000000, timezone.utc)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError):
            continue
        if (timestamp < started or not isinstance(message,dict) or
                message.get('schema') != 'porota.rootless.job.v1' or message.get('unit') != SERVICE or
                message.get('source_sha') != current['source_sha'] or message.get('image_id') != current['image_id'] or
                type(message.get('real_orders_sent')) is not int or message['real_orders_sent'] != 0):
            continue
        commands = message.get('commands')
        if not isinstance(commands,list) or len(commands) != 1 or not isinstance(commands[0],dict):
            continue
        result = commands[0]
        code, http, reason = result.get('exit_code'), result.get('http_status'), result.get('result')
        if (result.get('source') != 'IOL_REST' or type(code) is not int or code not in (0,2) or
                not isinstance(reason,str) or reason not in AUTH_RESULTS):
            continue
        entries.append({'at':timestamp.isoformat(),'result':reason,'exit_code':code,
                        'http_status':http if type(http) is int and 100 <= http <= 599 else None})
    return {'state':'OBSERVED' if entries else 'NO_EXACT_RELEASE_HEALTH_ENTRY',
            'unit_entries_observed':observed,'health_runs':entries}

def preopen_history(gate):
    from scripts.porota_rootless_jobs import PREOPEN_BYMA_RED_REASONS, PREOPEN_RED_KEYS

    unit = 'porota-preopen-rc6.service'
    old = 'eba76703be86e6a6b1406db36e5cb4cb87b21330'
    raw = run_command(['/usr/bin/journalctl','--unit',unit,
                       '--since=2026-11-27 13:17:00 UTC','--until=2026-11-27 13:31:00 UTC',
                       '--lines=40','--no-pager','--output=json','--output-fields=MESSAGE,_SYSTEMD_UNIT'],
                      128*1024)
    if raw is None or gate.get('candidate_sha') != old:
        return {'state':'NO_VERIFICADO'}
    entries = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
            if record.get('_SYSTEMD_UNIT') != unit:
                continue
            receipt = json.loads(record['MESSAGE'])
            stamp = datetime.fromtimestamp(int(record['__REALTIME_TIMESTAMP']) / 10**6, timezone.utc)
            if (not isinstance(receipt,dict) or receipt.get('schema') != 'porota.rootless.job.v1'
                    or receipt.get('unit') != unit or receipt.get('source_sha') != old
                    or receipt.get('state') != 'BLOQUEADO' or receipt.get('child_exit_code') != 2
                    or receipt.get('failure_signature') != 'PREOPEN_RED_CHECKS'
                    or type(receipt.get('real_orders_sent')) is not int or receipt['real_orders_sent'] != 0):
                continue
            names = receipt.get('preopen_red_checks')
            if not isinstance(names,list) or not names or any(not isinstance(name,str)
                    or name not in PREOPEN_RED_KEYS for name in names):
                continue
            reasons = receipt.get('preopen_red_reasons') or {}
            byma = reasons.get('byma_morning_watch') if isinstance(reasons,dict) else None
            entry = {'recorded_at':stamp.isoformat(),'candidate_sha':old,'red_checks':names}
            if byma in PREOPEN_BYMA_RED_REASONS:
                entry['byma_reason'] = byma
            entries.append(entry)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, OverflowError):
            continue
    return {'state':'OBSERVED' if entries else 'NO_EXACT_ENTRY','records':entries}

def installed_preopen(source, root, identity):
    from scripts.porota_rootless_jobs import PREOPEN_RED_KEYS

    env = {**identity.environment(), 'POROTA_RUNTIME_BACKEND':'ROOTLESS',
           'POROTA_RC6_MARKET_PROVIDER':'IOL_REST','PYTHONDONTWRITEBYTECODE':'1',
           'PYTHONPATH':str(source),'PAPER_V17_DB_PATH':str(root / 'data/paper_v17/observer_v17.db'),
           'DOCKER_HOST':f'unix://{identity.socket}'}
    # No publish or revalidate flags. Keep production source checks, including same-day BYMA.
    result = subprocess.run(['/usr/bin/python3','-B',str(source / 'rc6_preopen.py')],
                            cwd=root, env=env, capture_output=True, check=False, timeout=35)
    if result.returncode not in (0,2) or not 0 < len(result.stdout) <= 1024*1024:
        return {'state':'NO_VERIFICADO'}
    receipt = json.loads(result.stdout)
    if (receipt.get('schema') != 'POROTA_RC6_PREOPEN_V3' or receipt.get('read_only') is not True
            or receipt.get('network_order_test_performed') is not False
            or receipt.get('direct_telegram_send') is not False or 'paper_admission_receipt' in receipt):
        return {'state':'NO_VERIFICADO'}
    checks = {}
    for name, check in receipt.get('checks',{}).items():
        if name not in PREOPEN_RED_KEYS or not isinstance(check,dict):
            continue
        state, reason = check.get('state'), check.get('reason')
        if state not in {'GREEN','AMBER','RED'}:
            continue
        checks[name] = {'state':state}
        if isinstance(reason,str) and re.fullmatch('[A-Z][A-Z0-9_]{2,96}',reason):
            checks[name]['reason'] = reason
    state = receipt.get('status')
    return {'state':state if state in {'GREEN','RED','PAUSED_NO_GO'} else 'NO_VERIFICADO',
            'exit_code':result.returncode,'checks':checks}

def inspect_committed_shadow(root):
    from cg_paper_workspace import artifact_root
    from rc6_shadow_runtime.timing import generation_budget
    from rc6_trader_dashboard.generation import read_shadow

    def code(value):
        return value if isinstance(value,str) and re.fullmatch("[A-Z][A-Z0-9_]{2,119}",value) else None

    result = read_shadow(artifact_root(root / "data/paper_v17/observer_v17.db") / "dynamic-shadow")
    report = result["report"]
    if not report:
        return {key:code(result.get(key)) for key in ("state","reason")}
    as_of = datetime.fromisoformat(report["as_of"])
    now = datetime.now(timezone.utc)
    budget,_ = generation_budget(result.get("status"))
    age=(now-as_of).total_seconds()
    funnel=report.get("operational_funnel") or {}
    engines={}
    for name,engine in report.get("engines",{}).items():
        if not code(name):
            continue
        capacity=engine.get("capacity") or {}
        engines[name]={"capacity_status":code(capacity.get("status")),
            "capacity_reasons":[code(r) for r in capacity.get("reason_codes",[]) if code(r)],
            "selected_count":len(engine.get("selected",[])),
            "telemetry_total":engine.get("telemetry_total")}
    return {"state":result["state"],"as_of":as_of.isoformat(),"age_seconds":round(age,1),
        "fresh":0<=age<=budget,"phase":code(report.get("phase")),"report_status":code(report.get("status")),
        "capacity_open_status":code(report.get("capacity_open_status")),
        "native_stages": [{**{key:code(row.get(key)) for key in ("currency","channel")},
                           "stages":{key:v for key,v in (row.get("stages") or {}).items()
                                     if code(key) and type(v) is int and v>=0}}
                          for row in funnel.get("by_currency_channel",[]) if isinstance(row,dict)],
        "funnel_status":code(funnel.get("status")), "engines":engines}


def observed_gate(private):
    from scripts.porota_operator_intent import private_read
    from rc6_paper_admission_gate import HOST_V2_RECEIPT, REVALIDATION_NAME

    try:
        raw = private_read(private / HOST_V2_RECEIPT.name)
        daily = json.loads(raw)
        digest = hashlib.sha256(raw).hexdigest()
    except (OSError, KeyError, TypeError, ValueError):
        return {"state": "DAILY_GATE_UNVERIFIED"}
    base = {key: daily.get(key) for key in ("candidate_sha", "issued_at_ar", "trade_day_ar",
                                          "status", "phase", "reason", "market_provider")}
    base["sha256"] = digest
    try:
        recovery = json.loads(private_read(private / REVALIDATION_NAME))
        if type(recovery) is not dict:
            raise ValueError("INVALID_RECOVERY")
    except (OSError, KeyError, TypeError, ValueError):
        return {"state": "RECOVERY_RECEIPT_UNVERIFIED", "daily": base}
    exact = (recovery.get("daily_gate_sha256") == digest
             and recovery.get("candidate_sha") == expected["source_sha"]
             and recovery.get("candidate_tree_sha") == expected["source_tree"]
             and recovery.get("image_id") == expected["image_id"]
             and recovery.get("market_provider") == "IOL_REST"
             and recovery.get("trade_day_ar") == base["trade_day_ar"])
    return {"state": "EXACT_IDENTITY_CHAIN" if exact else "RECOVERY_BINDING_UNVERIFIED",
            "daily": base, "recovery": {key: recovery.get(key) for key in
            ("schema", "phase", "status", "issued_at_ar", "reason", "candidate_sha", "candidate_tree_sha",
             "image_id", "market_provider", "trade_day_ar", "operator_run_id")},
            "same_t10_bytes": recovery.get("daily_gate_sha256") == digest}


def run_probe():
    assert 0 < os.getuid() == os.geteuid()
    source = Path.home() / '.local/state/porota-rc6/releases' / expected['source_sha'] / 'source'
    assert all(not path.is_symlink() for path in (source,*source.parents))
    assert source.is_dir() and source.stat().st_uid == os.getuid()
    sys.path.insert(0,str(source))
    from rc6_scheduler_snapshot import instant, systemd_clock
    from scripts.rc6_iol_installed_inventory import run_host_inventory
    from scripts.porota_rootless_host import DB_RELATIVE, Identity, database_snapshot, require
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host
    from scripts.porota_operator_intent import private_read
    from scripts.rc6_operator_paper_rearm import evaluate_rearm

    identity = Identity.current()
    root = Path('/opt/porota-trading')
    with scheduler_lock(identity):
        manager,current,_ = verified_host(root,identity)
        require(all(current.get(key) == value for key,value in expected.items()) and
                current.get('market_provider') == 'IOL_REST','AUDIT_INSTALLED_IDENTITY_MISMATCH')
        installed = instant(current.get('controller',{}).get('recorded_at'))
        require(installed is not None,'AUDIT_INSTALLED_TIME_MISSING')
        original = database_snapshot(root / DB_RELATIVE)
        require(original == current.get('database'),'AUDIT_DATABASE_IDENTITY_UNVERIFIED')
        with closing(sqlite3.connect((root / DB_RELATIVE).as_uri()+'?mode=ro',uri=True,timeout=.3)) as db:
            db.execute('PRAGMA query_only=ON')
            row = db.execute('SELECT session_state,heartbeat_at,mode,real_orders_sent '
                             'FROM observer_state WHERE id=1').fetchone()
        stamp = instant(row[1]) if row else None
        require(stamp is not None and row[2:] == ('PRODUCTION_PAPER',0) and
                0 <= (datetime.now(timezone.utc)-stamp).total_seconds() <= 120,'AUDIT_PAPER_HEARTBEAT_REQUIRED')
        namespace = {'__name__':'rc6_iol_214_reference_auth','__file__':str(source / 'scripts/rc6_iol_auth_diagnostic.py')}
        exec(compile(auth_source,'rc6_iol_214_reference_auth','exec'),namespace)
        auth = namespace['observe_auth'](manager,current,probe_asset=False)
        market_pair = {'state':'PASSIVE_ONLY','wire_attempts':0,'endpoints':{}}
        os.environ['POROTA_RUNTIME_BACKEND'] = 'ROOTLESS'
        now = datetime.now(timezone.utc)
        authorized, reason, intent = evaluate_rearm(now=now, context='host')
        rearm = {'authorized':authorized,'reason':reason}
        if authorized:
            rearm.update({key:intent[key] for key in ('operator_comment_id','rearm_run_id','intent_since_ar')})
        try:
            gate = json.loads(private_read(identity.operator / 'paper-preopen-v2.json'))
            gate = {key:gate.get(key) for key in ('candidate_sha','market_provider','status','reason',
                                                 'trade_day_ar','issued_at_ar','phase')}
        except (OSError, ValueError, TypeError):
            gate = {'status':'NO_VERIFICADO'}
        code = ('container','exec',current['roles']['observer'],'python','-B','-c',installed_probe)
        try:
            status = json.loads(manager.docker.call(*code, timeout=15))
            require(type(status.get('observer', {}).get('real_orders_sent')) is int and
                    status['observer']['real_orders_sent'] == 0, 'AUDIT_PAPER_ZERO_REQUIRED')
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            status = {'state':'NO_VERIFICADO','reason':type(exc).__name__}
        try:
            shadow_generation = inspect_committed_shadow(root)
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
            shadow_generation = {"state":"NO_VERIFICADO","error":type(error).__name__}
        common = ('Id','LoadState','ActiveState','SubState','UnitFileState')
        units = {}
        for unit,fields in ((TIMER,('Unit','Triggers','LastTriggerUSec','NextElapseUSecRealtime')),
                            (SERVICE,('Result','ExecMainCode','ExecMainStatus','ExecMainStartTimestamp','ExecMainExitTimestamp'))):
            try:
                units[unit] = systemd(unit,common+fields,systemd_clock)
            except (OSError,ValueError,subprocess.SubprocessError):
                units[unit] = {'state':'NO_VERIFICADO'}
        try:
            history = journal(installed,current)
        except (OSError,ValueError,subprocess.SubprocessError):
            history = {'state':'NO_VERIFICADO','health_runs':[]}
        preopen_rca = {'state':'NO_VERIFICADO'}
        try:
            preopen_rca = preopen_history(gate)
            preopen_current = installed_preopen(source,root,identity)
        except (OSError,ValueError,TypeError,subprocess.SubprocessError):
            preopen_current = {'state':'NO_VERIFICADO'}
        inventory = run_host_inventory(manager,current)
        for role in ('observer','dashboard'):
            inspected = manager.check_role(current,role)
            require(inspected['State'].get('Running') is True and type(inspected.get('RestartCount')) is int,
                    'AUDIT_CONTAINER_STATE_UNVERIFIED')
            inventory[role+'_restarts'] = inspected['RestartCount']
        requests = market_pair['wire_attempts'] + auth['market_requests_generated']
        require(type(requests) is int and requests in (0,1) and auth.get('oauth_grants_sent') == 0 and
                database_snapshot(root / DB_RELATIVE) == original,'AUDIT_REFERENCE_BOUNDARY_UNVERIFIED')
        return {'schema':schema, **expected,'read_only':True,'real_orders_sent':row[3],'grants_generated':0,'market_session':row[0],
                'market_requests_generated':requests,'ppi_watch_queried':False,'installed_at':installed.isoformat(),
                'opening_receipts':observed_gate(identity.operator),
                'observed_at':datetime.now(timezone.utc).isoformat(),'authentication':auth,'market_pair':market_pair,
                'units':units,'journal':history,'inventory':inventory,'operator_rearm':rearm,'preopen':gate,'runtime_paper_go':status, 'shadow_generation':shadow_generation,
                'preopen_rca':preopen_rca,'preopen_current_readonly':preopen_current,
                'auth_probe_sha256':hashlib.sha256(auth_source.encode()).hexdigest()}

try:
    print(json.dumps(run_probe(),sort_keys=True))
except Exception as exc:
    reason = str(exc) if type(exc).__name__ == 'Hold' and str(exc).startswith('AUDIT_') else type(exc).__name__
    print(json.dumps({'schema':schema, **expected,'state':'NO_VERIFICADO','reason':reason}))
    raise SystemExit(1)
'''


INSTALLED_PROBE = r'''from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from pathlib import Path

from co_market_sessions_hf6 import byma_paper_spot_phase
from rc6_iol_market_gateway import IOLGatewayClient, iol_gateway_socket_path
from rc6_paper_admission_gate import admission_status

from bisect import bisect_left
from decimal import Decimal
from time import monotonic

from be_paper_engine import PaperStore
from bf_production_paper_observer import _scanner_opening_capacity


def safe_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,119}", value) else None


def safe_read_identity(row):
    values = tuple(row[name] for name in ("symbol", "asset_class", "market", "currency", "settlement"))
    if all(isinstance(value, str) and re.fullmatch(r"[A-Z0-9.^_()/-]{1,36}", value) for value in values):
        return values
    return None


def trace_live_ready(conn, now, market_open, gate_allowed, gate_reason):
    # Keep the entire diagnostic bounded. This connection is mode=ro/query_only.
    stop = monotonic() + 3
    conn.set_progress_handler(lambda: monotonic() >= stop, 5000)
    try:
        return read_live_ready(conn, now, market_open, gate_allowed, gate_reason)
    finally:
        conn.set_progress_handler(None, 0)


def check_ready_instruments(conn, now, market_open):
    from types import SimpleNamespace
    from bu_instrument_catalog import iol_spot_catalog
    from rc6_paper_admission_gate import _iol_spot_admission_reason
    from rc6_dynamic_universe.routing import EQUITY_FAMILIES

    class ReadOnlyStore:
        def connect(self):
            state = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
            state.row_factory = sqlite3.Row
            state.execute("PRAGMA query_only=ON")
            return state

    results = []
    for row in conn.execute("""
        SELECT r.ticker,r.instrument_type,r.market,r.currency,r.settlement,r.checked_at,
               f.status AS primary_status,f.capability,f.settlement_source,f.last_seen_at
        FROM candidate_identity_v2 r
        LEFT JOIN financial_instrument_catalog f USING(ticker,instrument_type,market,currency,settlement)
        WHERE r.status='AVAILABLE' AND r.can_simulate=1
        ORDER BY r.instrument_type,r.ticker,r.market,r.currency,r.settlement LIMIT 24
    """):
        identity = tuple(row[key] for key in ("ticker","instrument_type","market","currency","settlement"))
        if not all(isinstance(v,str) and re.fullmatch(r"[A-Z0-9.^_()/-]{1,36}",v) for v in identity):
            continue
        admitted_to_scanner = identity in iol_spot_catalog(ReadOnlyStore(), now=now, identity=identity)
        specialized_hold = None
        if identity[1] not in EQUITY_FAMILIES:
            quote = SimpleNamespace(asset_class=identity[1])
            specialized_hold = _iol_spot_admission_reason(quote, ReadOnlyStore(), now)
        observations = [dict(sample) for sample in conn.execute("""
            SELECT metadata_source AS provider,count(*) AS records,max(observed_at) AS last_seen_at
            FROM market_snapshots
            WHERE symbol=? AND asset_class=? AND market=? AND currency=? AND settlement=?
              AND julianday(observed_at) BETWEEN julianday(?) AND julianday(?)
            GROUP BY metadata_source ORDER BY metadata_source
        """, (*identity,market_open.isoformat(),now.isoformat())) if safe_code(sample["provider"])]
        results.append({"identity":identity,"checked_at":row["checked_at"],"primary_status":safe_code(row["primary_status"]),
            "capability":safe_code(row["capability"]),"settlement_source":safe_code(row["settlement_source"]),
            "catalog_last_seen_at":row["last_seen_at"],"selected_by_iol_catalog":admitted_to_scanner,
            "specialized_gate":safe_code(specialized_hold),"session_snapshots":observations})
    return results


def read_ready_shadow_cache(identities, now, market_open):
    from scripts.porota_rootless_host import read_regular

    cache = Path("/app/data/market/iol_shadow_latest.json")
    try:
        data = json.loads(read_regular(cache, 8 * 1024 * 1024))
        if data.get("source") != "IOL_REST" or not isinstance(data.get("symbols"), list):
            return {"status": "IOL_REST_SHADOW_CACHE_UNVERIFIED"}
    except Exception as error:
        return {"status": "SHADOW_CACHE_UNAVAILABLE", "error": type(error).__name__}
    def stamp(value):
        try:
            at = datetime.fromisoformat(value)
            return at if at.utcoffset() is not None and at <= now else None
        except (ValueError, TypeError):
            return None
    def stamp_string(at):
        return at.isoformat() if at is not None else None
    observed = []
    venue_map = {"BYMA":"BCBA"}
    terms = {"A-24HS":"T1", "INMEDIATA":"T0"}
    for identity in identities:
        ticker, family, market, currency, term = identity
        matches = [r for r in data["symbols"] if isinstance(r,dict) and
                   (r.get("symbol"), r.get("market"), r.get("term")) == (ticker,venue_map.get(market),terms.get(term))]
        if len(matches) != 1:
            observed.append({"identity":identity, "status":"NO_EXACT_SHADOW_ROW" if not matches else "AMBIGUOUS_SHADOW_ROWS"})
            continue
        row = matches[0]
        quote = row.get("quote")
        quote = quote if isinstance(quote,dict) else {}
        captured = stamp(row.get("captured_at"))
        quote_at = stamp(quote.get("provider_observed_at"))
        book_at = stamp(quote.get("provider_book_at"))
        fresh_book = bool(book_at and captured and market_open <= book_at <= captured <= now
                          and (now - book_at).total_seconds() <= 120)
        depth = False
        try:
            bid, ask, bid_size, ask_size = [Decimal(str(quote.get(k))) for k in ("bid","ask","bid_size","ask_size")]
            depth = 0 < bid <= ask and min(bid_size, ask_size) > 0
        except ArithmeticError:
            pass
        observed.append({"identity":identity, "status":safe_code(row.get("state")) or "NO_VERIFICADO",
            "source_state":safe_code(row.get("source_state")), "quote_currency_matches":quote.get("currency")==currency,
            "captured_at":stamp_string(captured), "quote_update_at":stamp_string(quote_at), "book_at":stamp_string(book_at),
            "fresh_two_sided_book":fresh_book and depth and quote.get("currency")==currency})
    return {"status":"OBSERVED", "refreshed_at":stamp_string(stamp(data.get("refreshed_at"))),
            "last_batch_state":safe_code(data.get("cache_state")), "rows":observed}


def observe_market_owner_cache(database):
    from iol_rest_readonly_rc6 import IOLRestReadOnly
    state_dir = iol_gateway_socket_path(database).parent / "rest-session"
    client = IOLRestReadOnly(state_dir, allow_renew=False)
    try:
        before = client.store.stat()
        cached = client._cached()
        after = client.store.stat()
        if (before.st_ino,before.st_mtime_ns)!=(after.st_ino,after.st_mtime_ns):
            return {"status":"CHANGED_DURING_READ"}
        result = {"status":"OBSERVED", "updated_at":datetime.fromtimestamp(after.st_mtime,timezone.utc).isoformat()}
        for key in ("renew_at","reauth_after","retry_at"):
            timestamp=cached.get(key)
            if type(timestamp) in (float,int) and 0 < timestamp < 4102444800:
                result[key]=datetime.fromtimestamp(timestamp,timezone.utc).isoformat()
        return result
    except Exception as error:
        return {"status":"NO_VERIFICADO", "error":type(error).__name__}


def trace_scanner_and_paper(conn, now, market_open):
    # Read committed scanner selection and match native decisions to the exact saved quote.
    watches = ("YPFD", "GGAL", "AL30", "AL35", "AO29")
    plan_rollup, watch_plans = defaultdict(Counter), defaultdict(list)
    for cycle in conn.execute("""
        SELECT started_at,finished_at,detail FROM
          (SELECT id,started_at,finished_at,detail FROM universe_cycle_metrics
           WHERE finished_at IS NOT NULL AND julianday(started_at) BETWEEN julianday(?) AND julianday(?)
           ORDER BY id DESC LIMIT 64)
        ORDER BY id
    """, (market_open.isoformat(),now.isoformat())):
        try:
            plan = json.loads(str(cycle["detail"]).split("factibilidad=", 1)[1])["active_plan"]
            pinned = {tuple(x) for x in plan.get("pinned",[])}
            warm = {tuple(x) for x in plan.get("warm",[])}
            cold = {tuple(x) for x in plan.get("cold",[])}
            opening = {tuple(x) for x in plan.get("opening_identities",[])}
        except (IndexError, AttributeError, ValueError, TypeError, KeyError):
            continue
        for read in plan.get("read_scopes",[]):
            key = read.get("identity")
            request = tuple(read.get("request",[]))
            if not isinstance(key,list) or len(key)!=5 or len(request)!=3 or not all(
                    isinstance(value,str) and re.fullmatch(r"[A-Z0-9.^_()/-]{1,36}",value) for value in key):
                continue
            set_name = ("PINNED" if request in pinned else "WARM_BASKET" if request in warm
                        else "COLD_DISCOVERY" if request in cold else "OTHER")
            priority = safe_code(read.get("priority"))
            counts = plan_rollup[key[1]]
            counts[set_name] += 1
            if request in opening:
                counts["SIGNAL_BUDGET_ELIGIBLE"] += 1
            if priority == "STRATEGY_HOT":
                counts["STRATEGY_HOT"] += 1
            if key[0] in watches:
                watch_plans[key[0]].append({"identity":key,"start":cycle["started_at"],
                    "end":cycle["finished_at"],"plan":plan.get("schema"),
                    "basket":set_name,"gateway_priority":priority,
                    "signal_budget_eligible":request in opening,
                    "dynamic_selection":plan.get("dynamic_selection") is True})
    buys, buy_outcomes = [], Counter()
    for decision in conn.execute("""
        SELECT d.symbol,d.decided_at,d.strategy_version,d.decision_key,d.features_json,g.final_result,g.reason
        FROM (SELECT * FROM paper_decisions ORDER BY id DESC LIMIT 5000) d
        LEFT JOIN trade_gate_evaluations g ON g.decision_key=d.decision_key
        WHERE d.action='BUY' AND julianday(d.decided_at) BETWEEN julianday(?) AND julianday(?)
        ORDER BY d.id DESC LIMIT 60
    """, (market_open.isoformat(),now.isoformat())):
        candidate = None
        for quote_row in conn.execute("""
            SELECT * FROM market_snapshots WHERE symbol=? AND observed_at=? ORDER BY id DESC LIMIT 8
        """, (decision["symbol"],decision["decided_at"])):
            key = safe_read_identity(quote_row)
            if key is None or quote_row["metadata_source"] != "IOL_REST":
                continue
            symbol, family, market, currency, term = key
            signature = ":" + ":".join((symbol,family,term,currency,market,quote_row["observed_at"][:16])) + ":"
            if signature not in decision["decision_key"]:
                continue
            candidate = quote_row
            break
        if candidate is None:
            buy_outcomes["EXACT_IOL_QUOTE_NOT_MATCHED"] += 1
            continue
        quote = PaperStore.quote_from_snapshot(candidate)
        key = safe_read_identity(candidate)
        gate = safe_code(decision["reason"])
        final = safe_code(decision["final_result"])
        features = json.loads(decision["features_json"])
        lineage = features.get("performance_lineage") or {}
        version = str(decision["strategy_version"])
        observed = datetime.fromisoformat(candidate["observed_at"])
        scope = next((s for s in reversed(watch_plans.get(key[0],[])) if s["identity"]==list(key)
                      and datetime.fromisoformat(s["start"]) <= observed <= datetime.fromisoformat(s["end"])), None)
        buy_outcomes[final or "FINAL_GATE_NO_VERIFICADO"] += 1
        buys.append({"identity":key, "decided_at":decision["decided_at"], "trade_at":quote.trade_at,
            "book_at":quote.book_at, "decision":safe_code(features.get("reason_code")),
            "strategy":safe_code(lineage.get("strategy_id")),
            "strategy_version":version if re.fullmatch(r"[A-Za-z0-9_.-]{1,70}",version) else None,
            "fresh_at_saved_decision":not quote.time_error(observed.isoformat(),require_trade=True)
                and 0 < quote.bid <= quote.ask and min(quote.bid_size,quote.ask_size)>0,
            "final_result":final,"final_reason":gate,"scanner_scope":scope})
    return {"status":"OBSERVED", "session_start":market_open.isoformat(),
        "scans_by_family":dict(plan_rollup),
        "watch_summary":{symbol:dict(Counter(row["basket"] for row in scopes))
                         for symbol,scopes in watch_plans.items()},
        "exact_iol_buy_outcomes":dict(buy_outcomes), "exact_buy_examples":buys[:12]}


def read_live_ready(conn, now, market_open, gate_allowed, gate_reason):
    window_start = max(market_open, now - timedelta(minutes=8)).isoformat()
    catalog = [dict(row) for row in conn.execute("""
        SELECT instrument_type AS family, count(*) AS total,
          sum(CASE WHEN status='AVAILABLE' AND can_simulate=1 THEN 1 ELSE 0 END) AS runtime_ready,
          max(checked_at) AS checked_at
        FROM candidate_identity_v2 GROUP BY instrument_type ORDER BY instrument_type
    """) if safe_code(row["family"])]
    cycle_ends, cycles = [], []
    for row in conn.execute("""
        SELECT started_at,finished_at,detail FROM
          (SELECT id,started_at,finished_at,detail FROM universe_cycle_metrics ORDER BY id DESC LIMIT 12)
        WHERE finished_at IS NOT NULL ORDER BY finished_at
    """):
        try:
            recorded = json.loads(str(row["detail"]).split("factibilidad=", 1)[1])
            plan = recorded["active_plan"]
            identities = [tuple(value) for value in plan["opening_identities"]]
            scopes = {tuple(scope["identity"]): scope["priority"] for scope in plan["read_scopes"]
                      if isinstance(scope.get("identity"), (list, tuple))}
            if scopes:
                cycles.append((datetime.fromisoformat(row["started_at"]), recorded, identities, scopes))
                cycle_ends.append(datetime.fromisoformat(row["finished_at"]))
        except (IndexError, KeyError, TypeError, ValueError):
            continue

    snapshots = conn.execute("""
        SELECT * FROM market_snapshots
        WHERE julianday(observed_at) BETWEEN julianday(?) AND julianday(?)
        ORDER BY id DESC LIMIT 192
    """, (window_start, now.isoformat())).fetchall()
    decisions = defaultdict(list)
    for row in conn.execute("""
        SELECT decision_key,decided_at,symbol,action,reason,features_json FROM
          (SELECT * FROM paper_decisions ORDER BY id DESC LIMIT 1536)
        WHERE julianday(decided_at) BETWEEN julianday(?) AND julianday(?)
    """, (window_start, now.isoformat())):
        decisions[(row["symbol"], row["decided_at"])].append(row)

    statistics = defaultdict(Counter)
    sample, latest, failure_counts = [], set(), defaultdict(Counter)
    for row in snapshots:
        key = safe_read_identity(row)
        if key is None or key in latest:
            continue
        latest.add(key)
        if row["metadata_source"] != "IOL_REST":
            continue
        symbol, family, market, currency, term = key
        received = datetime.fromisoformat(row["observed_at"])
        stats = statistics[family]
        stats["with_recent_snapshot"] += 1
        candidate = conn.execute("""
            SELECT status,can_simulate,checked_at FROM candidate_identity_v2
            WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?
        """, key).fetchone()
        ready = bool(candidate is not None and candidate["status"] == "AVAILABLE" and candidate["can_simulate"] == 1)
        stats["catalog_ready"] += ready
        quote = PaperStore.quote_from_snapshot(row)
        quote.monetary_identity()
        two_sided = (Decimal(0) < quote.bid <= quote.ask and min(quote.bid_size, quote.ask_size) > 0)
        entry_error = quote.time_error(now.isoformat(), require_trade=True)
        book_error = quote.time_error(now.isoformat())
        clock_during_session = (quote.book_at and datetime.fromisoformat(quote.book_at) >= market_open
                                and quote.trade_at and datetime.fromisoformat(quote.trade_at) >= market_open)
        quote_ready = bool(two_sided and clock_during_session and not entry_error and not quote.opening_block_reason)
        book_ready = bool(two_sided and not book_error and quote.book_at
                          and datetime.fromisoformat(quote.book_at) >= market_open)
        stats["book_fresh_two_sided"] += book_ready
        stats["trade_and_book_fresh"] += quote_ready
        stats["catalog_ready_and_trade_book_fresh"] += ready and quote_ready
        stage = None
        index = bisect_left(cycle_ends, received)
        if index < len(cycles):
            cycle_start, feasibility, opening, scopes = cycles[index]
            if cycle_start <= received and key in scopes:
                permit, reason = _scanner_opening_capacity((symbol, family, term),
                    focus={"matched": opening}, feasibility=feasibility)
                stage = {"priority": safe_code(scopes[key]), "signal_budget_eligible": permit,
                         "hold": safe_code(reason), "cycle_started_at": cycle_start.isoformat()}
                stats["scanner_eligible_and_fresh"] += permit and ready and quote_ready

        prefix = ":" + ":".join((symbol, family, term, currency, market, row["observed_at"][:16])) + ":"
        actual = []
        for decision in decisions.get((symbol, row["observed_at"]), []):
            if prefix not in decision["decision_key"]:
                continue
            features = json.loads(decision["features_json"])
            gate = conn.execute("""
                SELECT final_result,reason FROM trade_gate_evaluations WHERE decision_key=?
            """, (decision["decision_key"],)).fetchone()
            actual.append({"action": safe_code(decision["action"]),
                "pre_signal_rejection": features.get("pre_signal_rejection") is True,
                "reason": safe_code(features.get("reason_code")) or safe_code(decision["reason"]),
                "final": safe_code(gate["final_result"]) if gate else None,
                "final_reason": safe_code(gate["reason"]) if gate else None})
        stats["observed_decision_on_exact_snapshot"] += bool(actual)
        sample.append({"identity": key, "observed_at": row["observed_at"],
            "trade_at": quote.trade_at, "book_at": quote.book_at, "catalog_ready": ready,
            "catalog_checked_at": candidate["checked_at"] if candidate else None,
            "entry_data_ready": quote_ready, "fresh_book_for_exit_guard": book_ready,
            "entry_clock_hold": safe_code(entry_error), "book_clock_hold": safe_code(book_error),
            "snapshot_hold": safe_code(quote.opening_block_reason), "scanner": stage, "decisions": actual})

    for row in conn.execute("""
        SELECT event_type,detail FROM paper_events
        WHERE julianday(event_at) BETWEEN julianday(?) AND julianday(?)
          AND event_type IN ('DATA_REJECTED','DATA_ERROR','DATA_TEMPORAL_HOLD')
        ORDER BY id DESC LIMIT 350
    """, (window_start, now.isoformat())):
        pieces = str(row["detail"]).rsplit(": ", 1)
        if len(pieces) == 2 and re.fullmatch(r"[A-Z0-9.^_()/-]{1,36}", pieces[0]) and safe_code(pieces[1]):
            failure_counts[pieces[1]][pieces[0]] += 1

    ready_details = check_ready_instruments(conn, now, market_open)
    return {"status": "OBSERVED", "lookback_start": window_start, "as_of": now.isoformat(),
        "catalog": catalog, "catalog_ready_details": ready_details,
        "shadow_cache": read_ready_shadow_cache([row["identity"] for row in ready_details], now, market_open),
        "owner_oauth_cache": observe_market_owner_cache(database),
        "current_phase": byma_paper_spot_phase(now),
        "market_and_decisions_by_family": dict(statistics), "instrument_rows": sample,
        "scanning_failures": dict(failure_counts), "operational_gate": {
            "allowed": gate_allowed, "reason": safe_code(gate_reason)},
        "validation": "PERSISTED_EXACT_IDENTITY_AND_INSTALLED_READONLY_CLOCK_AND_SCANNER_CHECKS"}


database = Path("/app/data/paper_v17/observer_v17.db")
now = datetime.now(timezone.utc)
fresh = now - timedelta(seconds=120)
market_open = now.replace(hour=13, minute=30, second=0, microsecond=0)
allowed, reason = admission_status()
gateway = IOLGatewayClient(socket_path=iol_gateway_socket_path(database), timeout_seconds=2).owner_status()
with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    state = dict(conn.execute("""
        SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at,last_market_data_at
        FROM observer_state WHERE id=1
    """).fetchone())
    if state["mode"] != "PRODUCTION_PAPER" or type(state["real_orders_sent"]) is not int or state["real_orders_sent"] != 0:
        raise ValueError("PAPER_ZERO_UNVERIFIED")
    rows = conn.execute("""
        SELECT symbol,asset_class,market,currency,settlement,trade_at,book_at,
               bid,ask,bid_size,ask_size,opening_block_reason,last_kind
        FROM market_snapshots WHERE julianday(observed_at) BETWEEN julianday(?) AND julianday(?)
                  AND metadata_source='IOL_REST' ORDER BY id DESC LIMIT 128
    """, (fresh.isoformat(), now.isoformat())).fetchall()
    by_family = defaultdict(Counter)
    last = set()
    for row in rows:
        key = tuple(row[name] for name in ("symbol", "asset_class", "market", "currency", "settlement"))
        if key in last:
            continue
        last.add(key)
        counts = by_family[row["asset_class"]]
        counts["observed_keys"] += 1
        def recent(stamp):
            value = datetime.fromisoformat(stamp) if stamp else None
            return value is not None and value.utcoffset() is not None and max(fresh, market_open) <= value <= now
        valid_trade = row["last_kind"] == "TRADE" and recent(row["trade_at"])
        valid_book = recent(row["book_at"]) and 0 < float(row["bid"]) <= float(row["ask"]) and min(
            float(row["bid_size"]), float(row["ask_size"])) > 0
        counts["fresh_trade_keys"] += bool(valid_trade)
        counts["fresh_two_sided_book_keys"] += bool(valid_book)
        counts["fresh_trade_and_book_keys"] += bool(valid_trade and valid_book and not row["opening_block_reason"])
    wire_cycles = []
    for row in conn.execute("""
        SELECT started_at, selected_count, successful_count, failed_count, detail
        FROM universe_cycle_metrics ORDER BY id DESC LIMIT 8
    """):
        content = str(row["detail"] or "")
        marker = "factibilidad="
        try:
            metrics = json.loads(content.split(marker, 1)[1])
            budget = metrics["active_plan"]["iol_rest_budget"]
        except (IndexError, ValueError, KeyError, TypeError):
            continue
        fields = ("calls_per_minute", "scanner_batch_limit", "scanner_calls_per_instrument",
                  "batches_per_minute", "planned_exit_calls")
        if all(type(budget.get(name)) is int and 0 <= budget[name] <= 10000 for name in fields):
            hold = metrics.get("opening_hold_reason", "")
            hold = hold if isinstance(hold,str) and re.fullmatch("[A-Z][A-Z0-9_]{2,96}",hold) else None
            wire_cycles.append({**{name:budget[name] for name in fields},
                                **{name:row[name] for name in ("started_at","selected_count","successful_count",
                                                              "failed_count")},"opening_hold_reason":hold})
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    worker_states = {}
    for table in ("paper_exit_reader_state", "paper_supervisor_state",
                  "intraday_scalping_worker_state", "paper_caucion_cash_sweep_state"):
        if table not in tables:
            worker_states[table] = {"state":"TABLE_NOT_FOUND"}
            continue
        record = conn.execute(f"SELECT state,heartbeat_at FROM {table} WHERE id=1").fetchone()
        if record is not None and isinstance(record["state"],str) and re.fullmatch("[A-Z][A-Z0-9_]{2,96}",record["state"]):
            worker_states[table] = {"state":record["state"],"heartbeat_at":record["heartbeat_at"]}
            if table == "paper_exit_reader_state":
                detail = conn.execute("SELECT detail FROM paper_exit_reader_state WHERE id=1").fetchone()[0]
                failures = re.match(r"^(\d+) errores de lectura de abiertas;",str(detail or ""))
                budget = re.search(r"; budget_round=(COMPLETE|IDLE_NO_OPEN_POSITIONS|DEGRADED|BASELINE_NOT_MEASURED|NO_VERIFICADO)$",str(detail or ""))
                worker_states[table]["read_failures"] = int(failures.group(1)) if failures else None
                worker_states[table]["budget_round"] = budget.group(1) if budget else None
            if table == "intraday_scalping_worker_state":
                scalp = conn.execute(
                    "SELECT selected,successful,failed,candidates,detail FROM intraday_scalping_worker_state WHERE id=1"
                ).fetchone()
                worker_states[table].update({key:scalp[key] for key in ("selected","successful","failed","candidates")})
                for hold_reason in ("IOL_INTERVAL_VOLUME_UNVERIFIED","IOL_FRESH_TRADE_SAMPLE_UNAVAILABLE",
                               "IOL_SCALPING_SAMPLE_READ_UNAVAILABLE"):
                    if hold_reason in str(scalp["detail"] or ""):
                        worker_states[table]["hold_reason"] = hold_reason
            elif table == "paper_caucion_cash_sweep_state":
                code = conn.execute("SELECT code FROM paper_caucion_cash_sweep_state WHERE id=1").fetchone()[0]
                if isinstance(code,str) and re.fullmatch("[A-Z][A-Z0-9_]{2,96}",code):
                    worker_states[table]["code"] = code
    extra_ledgers = {}
    for table, column in (("paper_cauciones","status"),("paper_family_lifecycle","state"),
                          ("paper_future_positions","status")):
        if table not in tables:
            extra_ledgers[table] = None
            continue
        rows = conn.execute(f"SELECT {column},count(*) FROM {table} GROUP BY {column}").fetchall()
        extra_ledgers[table] = dict((state,count) for state,count in rows
                                    if isinstance(state,str) and re.fullmatch("[A-Z][A-Z0-9_]{2,96}",state))
    latest_cycle = conn.execute("""
        SELECT started_at,finished_at,eligible_total,selected_count,successful_count,failed_count
        FROM universe_cycle_metrics ORDER BY id DESC LIMIT 1
    """).fetchone()
    cycle = dict(latest_cycle) if latest_cycle and datetime.fromisoformat(latest_cycle["started_at"]) >= market_open else None
    open_positions = dict(conn.execute("""
        SELECT asset_class, count(*) FROM paper_positions WHERE status='OPEN' GROUP BY asset_class
    """).fetchall())
    observed_fills = [dict(row) for row in conn.execute("""
        SELECT p.asset_class, f.side, count(*) AS fills
        FROM (SELECT paper_id,side,filled_at FROM paper_fills ORDER BY id DESC LIMIT 4096) f
        JOIN paper_positions p ON p.paper_id=f.paper_id
        WHERE julianday(f.filled_at) BETWEEN julianday(?) AND julianday(?)
        GROUP BY p.asset_class,f.side
    """, (market_open.isoformat(), now.isoformat())).fetchall()]
    sources = ("DATA_ERROR", "DATA_REJECTED", "DATA_TEMPORAL_HOLD", "IOL_GATEWAY_REATTACH_REQUIRED")
    events = Counter()
    for row in conn.execute("""
        SELECT event_type,detail FROM paper_events
        WHERE event_type IN (?,?,?,?) AND julianday(event_at) BETWEEN julianday(?) AND julianday(?)
        ORDER BY id DESC LIMIT 256
    """, (*sources, max(market_open, fresh).isoformat(), now.isoformat())):
        code = str(row["detail"]).rsplit(": ", 1)[-1]
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,119}", code):
            events[code] += 1
        else:
            events[row["event_type"]] += 1

    try:
        live_ready = trace_live_ready(conn, now, market_open, allowed, reason)
    except (TypeError, ValueError, ArithmeticError, sqlite3.Error) as exc:
        live_ready = {"status": "NO_VERIFICADO", "error": type(exc).__name__}

    try:
        native_funnel = trace_scanner_and_paper(conn, now, market_open)
    except (TypeError, ValueError, ArithmeticError, sqlite3.Error) as exc:
        native_funnel = {"status":"NO_VERIFICADO", "error":type(exc).__name__}

print(json.dumps({"observed_at": now.isoformat(), "market_phase": byma_paper_spot_phase(now),
    "live_ready": live_ready, "native_funnel": native_funnel, "observer": state, "go": {"allowed": allowed, "reason": reason},
    "gateway": {name: gateway.get(name) for name in ("provider", "trade_stream_state", "authenticated",
                "wire_attempts", "rate_limited_attempts")},
    "market_keys_by_family": dict(by_family), "scanner_cycle": cycle, "iol_wire_cycles": wire_cycles,
    "exit_and_engine_workers": worker_states, "specialized_paper_ledgers": extra_ledgers, "open_spot_positions_by_family": open_positions,
    "session_spot_fills_by_family": observed_fills, "recent_market_rejections": dict(events)}, sort_keys=True))
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    auth = (ROOT / "scripts/rc6_iol_auth_diagnostic.py").read_text(encoding="utf-8")
    payload = (f"expected = {EXPECTED!r}\nauth_source = {auth!r}\nschema = {SCHEMA!r}\ninstalled_probe = {INSTALLED_PROBE!r}\n" + REMOTE).encode()
    receipt = {"schema": SCHEMA, **EXPECTED, "state": "NO_VERIFICADO", "reason": "AUDIT_TRANSPORT_UNVERIFIED"}
    try:
        with connection(os.environ) as (ssh, _, _):
            proc = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"], data=payload, timeout=90)
        observed = decode_receipt(proc.stdout)
        require(observed.get("schema") == SCHEMA and all(observed.get(k) == v for k, v in EXPECTED.items()),
                "AUDIT_RELEASE_MISMATCH")
        if proc.returncode == 0:
            require(observed.get("read_only") is True and all(type(observed.get(k)) is int and observed[k] == 0
                    for k in ("real_orders_sent", "grants_generated")) and
                    type(observed.get("market_requests_generated")) is int and observed["market_requests_generated"] in (0,1,2) and
                    observed.get("auth_probe_sha256") == hashlib.sha256(auth.encode()).hexdigest(),
                    "AUDIT_PAPER_SAFETY_UNVERIFIED")
        receipt = observed
        return 0 if proc.returncode == 0 else 1
    except (ValueError, OSError):
        return 1
    finally:
        args.output.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
