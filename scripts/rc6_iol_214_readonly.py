"""Verify IOL worker states and PAPER safety on the installed release."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


EXPECTED = {'source_sha': '28516e41b5c1aae0c183c1d55c41c13bca0fae15', 'source_tree': 'fdd24f70e9cbbbf241731a26b68c7b517e86a354', 'image_id': 'sha256:95918b793f999eb495334354b4392b8be9251daeeb02b863faf86c4ff98145b5', 'attempt': '46060603986-1'}
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
        code = ('container','exec',current['roles']['observer'],'python','-B','-c',
                'deployment_at = ' + repr(installed.isoformat()) + '\\n' + installed_probe)
        try:
            status = json.loads(manager.docker.call(*code, timeout=25))
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            status = {'state':'NO_VERIFICADO','reason':str(exc) if type(exc).__name__ == 'Hold'
                      and re.fullmatch('[A-Z_]{1,100}', str(exc)) else type(exc).__name__,
                      **getattr(exc, 'public_details', {})}
        if 'wheel_coverage' not in status:
            audit_namespace = {'__name__': 'rc6_iol_wheel_readonly'}
            exec(compile(installed_probe, 'rc6_iol_wheel_coverage.py', 'exec'), audit_namespace)
            from_source = audit_namespace['diagnostic_read'](
                audit_namespace['wheel_report'], root / DB_RELATIVE)
            status = {**from_source, 'container_read': status, 'read_location': 'HOST_VERIFIED_DB'}
        if 'wheel_coverage' in status:
            require(type(status.get('observer', {}).get('real_orders_sent')) is int and
                    status['observer']['real_orders_sent'] == 0, 'AUDIT_PAPER_ZERO_REQUIRED')
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


INSTALLED_PROBE = '\nfrom contextlib import closing\nfrom datetime import datetime, timedelta, timezone\nimport json\nfrom pathlib import Path\nimport sqlite3\n\nfrom co_market_sessions_hf6 import byma_paper_spot_phase\nfrom rc6_dynamic_universe.iol_capacity import read_cycles, safe_iol_capacity\nfrom rc6_iol_intraday import read_windows\nfrom rc6_paper_admission_gate import admission_status\n\ndatabase = Path("/app/data/paper_v17/observer_v17.db")\nnow = datetime.now(timezone.utc)\nstarted = datetime.fromisoformat(deployment_at)\nopening = now.replace(hour=13, minute=30, second=0, microsecond=0)\nallowed, reason = admission_status()\nwith closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as conn:\n    conn.row_factory = sqlite3.Row\n    conn.execute("PRAGMA query_only=ON")\n    conn.execute("BEGIN")\n    state = dict(conn.execute("""SELECT mode,process_state,session_state,real_orders_sent,heartbeat_at,\n        last_market_data_at FROM observer_state WHERE id=1""").fetchone())\n    if state["mode"] != "PRODUCTION_PAPER" or type(state["real_orders_sent"]) is not int or state["real_orders_sent"] != 0:\n        raise ValueError("PAPER_ZERO_UNVERIFIED")\n    workers = {}\n    for name in ("paper_exit_reader_state", "paper_supervisor_state",\n                 "intraday_scalping_worker_state", "paper_caucion_cash_sweep_state"):\n        row = conn.execute(f"SELECT state,heartbeat_at FROM {name} WHERE id=1").fetchone()\n        workers[name] = dict(row) if row else {"state": "NO_VERIFICADO"}\n    scalp = conn.execute("""SELECT selected,confirmed_identities,candidates FROM intraday_scalping_worker_state\n        WHERE id=1""").fetchone()\n    if scalp:\n        workers["intraday_scalping_worker_state"].update(dict(scalp))\n    positions = [dict(row) for row in conn.execute("""SELECT asset_class family,count(*) positions\n        FROM paper_positions WHERE status=\'OPEN\' GROUP BY 1""")]\n    fills = [dict(row) for row in conn.execute("""SELECT p.asset_class family,f.side,count(*) count\n        FROM (SELECT paper_id,side,filled_at FROM paper_fills ORDER BY id DESC LIMIT 2048) f\n        JOIN paper_positions p ON p.paper_id=f.paper_id\n        WHERE julianday(f.filled_at) BETWEEN julianday(?) AND julianday(?) GROUP BY 1,2""",\n        (opening.isoformat(), now.isoformat()))]\n    scanner = [dict(row) for row in conn.execute("""SELECT asset_class family,symbol,market,currency,settlement,\n        max(observed_at) latest_observed_at FROM\n        (SELECT * FROM market_snapshots ORDER BY id DESC LIMIT 1024)\n        WHERE metadata_source=\'IOL_REST\'\n        AND julianday(observed_at) BETWEEN julianday(?) AND julianday(?)\n        GROUP BY 1,2,3,4,5 ORDER BY 1,2,5 LIMIT 40""",\n        ((now - timedelta(minutes=20)).isoformat(), now.isoformat()))]\n    cycles = [c for c in read_cycles(conn) if c.get("started_at")\n        and datetime.fromisoformat(c["started_at"]) >= started]\n    capacity = safe_iol_capacity(cycles, as_of=now)\n    windows = [{"identity": key, "intervals": len(window["points"])}\n               for key, window in sorted(read_windows(conn, now).items())][:40]\n    evaluated = conn.execute("SELECT COUNT(*) FROM scalping_candidates WHERE evaluated_at>=?",\n                             (started.isoformat(),)).fetchone()[0]\n\n    sent_labels = [dict(row) for row in conn.execute("""\n        SELECT CASE\n          WHEN kind=\'INTROSPECTION\' AND instr(body,\'PPI perdió la sesión durante la rueda.\')>0\n          THEN \'INTROSPECTION_PPI_SESSION_LABEL\'\n          WHEN kind IN (\'OPERATIONAL_HEALTH_RED\',\'OPERATIONAL_HEALTH_RECOVERY\')\n            AND instr(body,\'PPI_PRODUCTION_\')>0 THEN \'PPI_OPERATIONAL_TRANSITION\'\n          WHEN kind=\'CLOSING_SUMMARY\' AND instr(body,\'Fuente histórica EOD PPI:\')>0 THEN \'CLOSING_SUMMARY_PPI_EOD_LABEL\'\n          WHEN kind LIKE \'IOL_MCP_%\' THEN \'LEGACY_IOL_MCP_PPI_LABEL\'\n          ELSE \'OTHER_PPI_MENTION\' END AS label, state, count(*) AS count,\n          max(created_at) AS latest_created_at,max(sent_at) AS latest_sent_at\n        FROM (SELECT kind,state,body,created_at,sent_at FROM paper_notification_outbox ORDER BY id DESC LIMIT 1000)\n        WHERE julianday(created_at) >= julianday(?) AND upper(body) LIKE \'%PPI%\'\n        GROUP BY 1,2 ORDER BY 1,2\n    """,(opening.isoformat(),))]\n    cycle_tail = []\n    for row in conn.execute("""SELECT started_at,finished_at,eligible_total,selected_count,\n        successful_count,failed_count,detail FROM universe_cycle_metrics ORDER BY id DESC LIMIT 10"""):\n        raw = row["detail"] or ""\n        start = raw.find("{")\n        if start < 0:\n            continue\n        try:\n            data = json.loads(raw[start:])\n            budget = data["active_plan"]["iol_rest_budget"]\n        except (KeyError, TypeError, ValueError):\n            continue\n        item = {key:row[key] for key in ("started_at","finished_at","eligible_total",\n                "selected_count","successful_count","failed_count")}\n        item["budget"] = {key:budget.get(key) for key in ("calls_per_minute","scanner_batch_limit",\n            "scanner_calls_per_instrument","batches_per_minute","planned_exit_calls")}\n        item["selection"] = "DYNAMIC" if data["active_plan"].get("dynamic_selection") else "BASELINE"\n        cycle_tail.append(item)\n\nhistory = Path("/app/data/market_history.db")\ncold = {}\ntry:\n    with closing(sqlite3.connect(history.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as conn:\n        conn.row_factory = sqlite3.Row\n        conn.execute("PRAGMA query_only=ON")\n        cold["local_cursors"] = [dict(row) for row in conn.execute(\n            "SELECT cursor,as_of,state FROM local_eod_cursor_v1 ORDER BY as_of DESC LIMIT 4")]\n        cold["last_local_receipts"] = [dict(row) for row in conn.execute("""\n            SELECT version_id,published_at,source_known_at FROM local_eod_receipts_v1\n            ORDER BY rowid DESC LIMIT 3""")]\nexcept sqlite3.Error as exc:\n    cold["state"] = "NO_VERIFICADO"\n    cold["reason"] = type(exc).__name__\n\nprint(json.dumps({"observed_at": now.isoformat(), "observer": state,\n    "market_phase": byma_paper_spot_phase(now), "go": {"allowed": allowed, "reason": reason},\n    "exit_and_engine_workers": workers, "open_spot_positions_by_family": positions,\n    "session_spot_fills_by_family": fills, "recent_iol_instruments": scanner,\n    "iol_scalping_windows": windows, "iol_dynamic_capacity": {key: capacity.get(key)\n        for key in ("status","reason_codes","safe_limit","measured_identities","completed_cycles")},\n    "completed_scanner_cycles_seen": len(cycles), "scalping_evaluations_since_install": evaluated,\n    "telegram_provider_labels": sent_labels, "local_eod_history": cold, "recent_iol_scanner_cycles": cycle_tail},\n    sort_keys=True))\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    auth = (ROOT / "scripts/rc6_iol_auth_diagnostic.py").read_text(encoding="utf-8")
    wheel = (ROOT / "scripts/rc6_iol_wheel_coverage.py").read_text(encoding="utf-8")
    payload = (f"expected = {EXPECTED!r}\nauth_source = {auth!r}\nschema = {SCHEMA!r}\ninstalled_probe = {wheel!r}\n" + REMOTE).encode()
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
                    type(observed.get("market_requests_generated")) is int and observed["market_requests_generated"] == 0 and
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
