"""Collect passive IOL OAuth and timer evidence from installed release #214."""

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
SCHEMA = "porota.iol.installed-214-passive-auth.v1"

REMOTE = '''
from datetime import datetime, timezone
import hashlib, json, os, subprocess, sys
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
    from scripts.porota_rootless_host import DB_RELATIVE, Identity, database_snapshot, require
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host

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
        namespace = {'__name__':'rc6_iol_214_passive_auth','__file__':str(source / 'scripts/rc6_iol_auth_diagnostic.py')}
        exec(compile(auth_source,'rc6_iol_214_passive_auth','exec'),namespace)
        auth = namespace['observe_auth'](manager,current,probe_asset=False)
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
        require(auth.get('independent_health',{}).get('asset',{}).get('state') == 'NOT_PROBED_READ_ONLY_EVIDENCE' and
                auth.get('oauth_grants_sent') == 0 and database_snapshot(root / DB_RELATIVE) == original,
                'AUDIT_PASSIVE_BOUNDARY_UNVERIFIED')
        return {'schema':schema, **expected,'read_only':True,'real_orders_sent':0,'grants_generated':0,
                'market_requests_generated':0,'ppi_watch_queried':False,'installed_at':installed.isoformat(),
                'observed_at':datetime.now(timezone.utc).isoformat(),'authentication':auth,
                'units':units,'journal':history,'auth_probe_sha256':hashlib.sha256(auth_source.encode()).hexdigest()}

try:
    print(json.dumps(run_probe(),sort_keys=True))
except Exception as exc:
    reason = str(exc) if type(exc).__name__ == 'Hold' and str(exc).startswith('AUDIT_') else type(exc).__name__
    print(json.dumps({'schema':schema, **expected,'state':'NO_VERIFICADO','reason':reason}))
    raise SystemExit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    auth = (ROOT / "scripts/rc6_iol_auth_diagnostic.py").read_text(encoding="utf-8")
    payload = (f"expected = {EXPECTED!r}\nauth_source = {auth!r}\nschema = {SCHEMA!r}\n" + REMOTE).encode()
    receipt = {"schema": SCHEMA, **EXPECTED, "state": "NO_VERIFICADO", "reason": "AUDIT_TRANSPORT_UNVERIFIED"}
    try:
        with connection(os.environ) as (ssh, _, _):
            proc = ssh_run(ssh, ["/usr/bin/python3", "-I", "-B", "-"], data=payload, timeout=60)
        observed = decode_receipt(proc.stdout)
        require(observed.get("schema") == SCHEMA and all(observed.get(k) == v for k, v in EXPECTED.items()),
                "AUDIT_RELEASE_MISMATCH")
        if proc.returncode == 0:
            require(observed.get("read_only") is True and all(type(observed.get(k)) is int and observed[k] == 0
                    for k in ("real_orders_sent", "grants_generated", "market_requests_generated")) and
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
