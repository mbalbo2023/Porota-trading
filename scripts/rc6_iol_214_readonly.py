"""Inventory installed release #214 and observe its existing IOL session."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.porota_actions_host import connection, decode_receipt, require, ssh_run


EXPECTED = {"source_sha": "eba76703be86e6a6b1406db36e5cb4cb87b21330",
            "source_tree": "5ac31c4b9cf2c16f477c08a6b5b3424f644a2667",
            "image_id": "sha256:274b143fd323ab0b79aff90890f54a7f5ce0ed84c3323fedd385ce85f28e83d2",
            "attempt": "45921311817-1"}
ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "porota.iol.installed-214-preopen-market-pair.v1"

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

def observe_trade_subscription(manager, current, auth_module):
    from iol_rest_readonly_rc6 import IOLRestReadOnly
    client = IOLRestReadOnly(manager.identity.state / 'iol-rest', allow_renew=False)
    _, cached = auth_module['_inspect_health_cache'](client)
    if cached is None:
        return {'state':'NO_CURRENT_CACHED_BEARER','grants':0}

    command = (
        "/usr/bin/docker", "--host", "unix://" + str(manager.identity.socket),
        "container", "exec", "-i", current["roles"]["observer"], "python", "-B", "-c", stream_probe,
    )
    try:
        proc = subprocess.run(command, input=cached['access_token'], text=True, capture_output=True,
                              env=manager.identity.environment(), cwd="/", timeout=23, check=False)
        if proc.returncode != 0 or len(proc.stdout) > 2048:
            return {'state':'NO_VERIFICADO','reason':'IOL_STREAM_EXEC_UNVERIFIED','grants':0}
        return json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'state':'NO_VERIFICADO','reason':'IOL_STREAM_RECEIPT_UNVERIFIED','grants':0}

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
        stream = (observe_trade_subscription(manager, current, namespace) if row[0] == 'MARKET_CLOSED'
                  else {'state':'NOT_PROBED_DURING_SESSION','grants':0})
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
                'units':units,'journal':history,'inventory':inventory,'operator_rearm':rearm,'preopen':gate,'iol_trade_stream':stream,
                'auth_probe_sha256':hashlib.sha256(auth_source.encode()).hexdigest()}

try:
    print(json.dumps(run_probe(),sort_keys=True))
except Exception as exc:
    reason = str(exc) if type(exc).__name__ == 'Hold' and str(exc).startswith('AUDIT_') else type(exc).__name__
    print(json.dumps({'schema':schema, **expected,'state':'NO_VERIFICADO','reason':reason}))
    raise SystemExit(1)
'''


STREAM_PROBE = '\nimport json, sys, time\nfrom websockets.sync.client import connect\n\ntoken = sys.stdin.read()\nreceipt = {"state": "NO_VERIFICADO", "phase": "TRANSPORT", "grants": 0, "stream_connections": 1}\n\ndef exchange(ws, expected, action, **fields):\n    ws.send(json.dumps({"action": action, **fields}, separators=(",", ":")))\n    deadline = time.monotonic() + 3\n    while time.monotonic() < deadline:\n        reply = json.loads(ws.recv(timeout=max(0, deadline - time.monotonic())))\n        if (reply.get("type"), reply.get("code"), reply.get("msg")) == ("Success", 200, expected):\n            return\n        if reply.get("type") == "Error":\n            code = reply.get("code")\n            receipt["provider_code"] = code if type(code) is int else None\n            raise ValueError("IOL_STREAM_REJECTED")\n    raise TimeoutError\n\ntry:\n    with connect("wss://stream-marketdata.invertironline.com", proxy=None, open_timeout=5,\n                 close_timeout=1, ping_interval=None, max_size=64 * 1024) as ws:\n        receipt["phase"] = "AUTH"\n        exchange(ws, "Connected", "auth_token", token=token)\n        receipt["phase"] = "SUBSCRIPTION"\n        exchange(ws, "Subscribed", "subscribe", quotes=[], order_book=[], trades=["GGAL|BCBA|T1"])\n        receipt["phase"] = "HEARTBEAT"\n        exchange(ws, "Pong", "ping")\n        receipt["phase"] = "UNSUBSCRIBE"\n        exchange(ws, "Unsubscribed", "unsubscribe", quotes=[], order_book=[], trades=["GGAL|BCBA|T1"])\n        ws.send(json.dumps({"action": "disconnect"}))\n        receipt.update(state="SUBSCRIPTION_VERIFIED", phase="COMPLETE", instrument="GGAL", market="BCBA", term="T1")\nexcept Exception as error:\n    receipt["reason"] = type(error).__name__\nprint(json.dumps(receipt, sort_keys=True))\n'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    auth = (ROOT / "scripts/rc6_iol_auth_diagnostic.py").read_text(encoding="utf-8")
    payload = (f"expected = {EXPECTED!r}\nauth_source = {auth!r}\nschema = {SCHEMA!r}\nstream_probe = {STREAM_PROBE!r}\n" + REMOTE).encode()
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
