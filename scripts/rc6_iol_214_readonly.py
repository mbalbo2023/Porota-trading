"""Observe the installed #216 release and its existing PAPER gate."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


EXPECTED = {"source_sha": "6da112dcc58268ac5ea3f93e5dddf4f87dabd141",
            "source_tree": "8ea511a9437d0a950df10838d7cc7a7118adcf5a",
            "image_id": "sha256:04e3dc288079358b93b2d0c3f77f6da1de63f53db304613695cb2a5c5173f20e",
            "attempt": "46014207358-1"}
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "porota.iol.installed-216-paper-go.v1"

REMOTE = '''
from contextlib import closing
from datetime import datetime, timezone
import hashlib, json, os, sqlite3, subprocess, sys
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
        inventory = run_host_inventory(manager,current)
        for role in ('observer','dashboard'):
            inspected = manager.check_role(current,role)
            require(inspected['State'].get('Running') is True and type(inspected.get('RestartCount')) is int,
                    'AUDIT_CONTAINER_STATE_UNVERIFIED')
            inventory[role+'_restarts'] = inspected['RestartCount']
        requests = market_pair['wire_attempts']
        require(type(requests) is int and requests in (0,1,2) and auth.get('oauth_grants_sent') == 0 and
                database_snapshot(root / DB_RELATIVE) == original,'AUDIT_REFERENCE_BOUNDARY_UNVERIFIED')
        return {'schema':schema, **expected,'read_only':True,'real_orders_sent':row[3],'grants_generated':0,'market_session':row[0],
                'market_requests_generated':requests,'ppi_watch_queried':False,'installed_at':installed.isoformat(),
                'observed_at':datetime.now(timezone.utc).isoformat(),'authentication':auth,'market_pair':market_pair,
                'units':units,'journal':history,'inventory':inventory,'operator_rearm':rearm,'preopen':gate,'runtime_paper_go':status,
                'auth_probe_sha256':hashlib.sha256(auth_source.encode()).hexdigest()}

try:
    print(json.dumps(run_probe(),sort_keys=True))
except Exception as exc:
    reason = str(exc) if type(exc).__name__ == 'Hold' and str(exc).startswith('AUDIT_') else type(exc).__name__
    print(json.dumps({'schema':schema, **expected,'state':'NO_VERIFICADO','reason':reason}))
    raise SystemExit(1)
'''


INSTALLED_PROBE = 'from collections import Counter, defaultdict\nfrom contextlib import closing\nfrom datetime import datetime, timedelta, timezone\nimport json\nimport re\nimport sqlite3\nfrom pathlib import Path\n\nfrom co_market_sessions_hf6 import byma_paper_spot_phase\nfrom rc6_iol_market_gateway import IOLGatewayClient, iol_gateway_socket_path\nfrom rc6_paper_admission_gate import admission_status\n\ndatabase = Path("/app/data/paper_v17/observer_v17.db")\nnow = datetime.now(timezone.utc)\nfresh = now - timedelta(seconds=120)\nmarket_open = now.replace(hour=13, minute=30, second=0, microsecond=0)\nallowed, reason = admission_status()\ngateway = IOLGatewayClient(socket_path=iol_gateway_socket_path(database), timeout_seconds=2).owner_status()\nwith closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:\n    conn.row_factory = sqlite3.Row\n    conn.execute("PRAGMA query_only=ON")\n    state = dict(conn.execute("""\n        SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at,last_market_data_at\n        FROM observer_state WHERE id=1\n    """).fetchone())\n    if state["mode"] != "PRODUCTION_PAPER" or type(state["real_orders_sent"]) is not int or state["real_orders_sent"] != 0:\n        raise ValueError("PAPER_ZERO_UNVERIFIED")\n    rows = conn.execute("""\n        SELECT symbol,asset_class,market,currency,settlement,trade_at,book_at,\n               bid,ask,bid_size,ask_size,opening_block_reason,last_kind\n        FROM market_snapshots WHERE julianday(observed_at) BETWEEN julianday(?) AND julianday(?)\n                  AND metadata_source=\'IOL_REST\' ORDER BY id DESC LIMIT 128\n    """, (fresh.isoformat(), now.isoformat())).fetchall()\n    by_family = defaultdict(Counter)\n    last = set()\n    for row in rows:\n        key = tuple(row[name] for name in ("symbol", "asset_class", "market", "currency", "settlement"))\n        if key in last:\n            continue\n        last.add(key)\n        counts = by_family[row["asset_class"]]\n        counts["observed_keys"] += 1\n        def recent(stamp):\n            value = datetime.fromisoformat(stamp) if stamp else None\n            return value is not None and value.utcoffset() is not None and max(fresh, market_open) <= value <= now\n        valid_trade = row["last_kind"] == "TRADE" and recent(row["trade_at"])\n        valid_book = recent(row["book_at"]) and 0 < float(row["bid"]) <= float(row["ask"]) and min(\n            float(row["bid_size"]), float(row["ask_size"])) > 0\n        counts["fresh_trade_keys"] += bool(valid_trade)\n        counts["fresh_two_sided_book_keys"] += bool(valid_book)\n        counts["fresh_trade_and_book_keys"] += bool(valid_trade and valid_book and not row["opening_block_reason"])\n    latest_cycle = conn.execute("""\n        SELECT started_at,finished_at,eligible_total,selected_count,successful_count,failed_count\n        FROM universe_cycle_metrics ORDER BY id DESC LIMIT 1\n    """).fetchone()\n    cycle = dict(latest_cycle) if latest_cycle and datetime.fromisoformat(latest_cycle["started_at"]) >= market_open else None\n    open_positions = dict(conn.execute("""\n        SELECT asset_class, count(*) FROM paper_positions WHERE status=\'OPEN\' GROUP BY asset_class\n    """).fetchall())\n    observed_fills = [dict(row) for row in conn.execute("""\n        SELECT p.asset_class, f.side, count(*) AS fills\n        FROM (SELECT paper_id,side,filled_at FROM paper_fills ORDER BY id DESC LIMIT 4096) f\n        JOIN paper_positions p ON p.paper_id=f.paper_id\n        WHERE julianday(f.filled_at) BETWEEN julianday(?) AND julianday(?)\n        GROUP BY p.asset_class,f.side\n    """, (market_open.isoformat(), now.isoformat())).fetchall()]\n    sources = ("DATA_ERROR", "DATA_REJECTED", "DATA_TEMPORAL_HOLD", "IOL_GATEWAY_REATTACH_REQUIRED")\n    events = Counter()\n    for row in conn.execute("""\n        SELECT event_type,detail FROM paper_events\n        WHERE event_type IN (?,?,?,?) AND julianday(event_at) BETWEEN julianday(?) AND julianday(?)\n        ORDER BY id DESC LIMIT 256\n    """, (*sources, max(market_open, fresh).isoformat(), now.isoformat())):\n        code = str(row["detail"]).rsplit(": ", 1)[-1]\n        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,119}", code):\n            events[code] += 1\n        else:\n            events[row["event_type"]] += 1\n\nprint(json.dumps({"observed_at": now.isoformat(), "market_phase": byma_paper_spot_phase(now),\n    "observer": state, "go": {"allowed": allowed, "reason": reason},\n    "gateway": {name: gateway.get(name) for name in ("provider", "trade_stream_state", "authenticated",\n                "wire_attempts", "rate_limited_attempts")},\n    "market_keys_by_family": dict(by_family), "scanner_cycle": cycle, "open_spot_positions_by_family": open_positions,\n    "session_spot_fills_by_family": observed_fills, "recent_market_rejections": dict(events)}, sort_keys=True))\n'

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
