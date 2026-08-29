"""Canary del runtime PAPER completo sin red, secretos, cuentas u órdenes."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import time

VERSION='v17-runtime-offline-canary-2'
ROOT=Path('/opt/porota-trading')
COMMIT='fa79089346d20d20e078d63cba928ca412a64222'
IMAGE='porota-trading-bot:17.0.0-rc1'
IMAGE_ID='sha256:2ce7653716523961a71bcb6f7fad8cd4d04db09de2bbda893657ee2d027f3ebf'
NAME='porota_v17_runtime_offline_canary'
ENGINES=('porota_trading_bot','porota_production_observer')


class Stop(Exception): pass


def run(args,timeout=30,**kwargs):
    return subprocess.run(args,text=True,timeout=timeout,**kwargs)


def docker(*args,timeout=30,**kwargs):
    return run(['sudo','-n','docker',*args],timeout=timeout,**kwargs)


def engine_rows():
    template=('{"name":{{json .Name}},"running":{{json .State.Running}},'
              '"restart":{{json .HostConfig.RestartPolicy.Name}},"image":{{json .Image}}}')
    result=docker('inspect','--type','container','--format',template,*ENGINES,capture_output=True)
    if result.returncode: raise Stop('ENGINE_INSPECT_FAILED')
    rows=[json.loads(line) for line in result.stdout.splitlines()]
    if len(rows)!=2 or {r.get('name') for r in rows}!={'/'+n for n in ENGINES}:
        raise Stop('UNEXPECTED_ENGINE_METADATA')
    if any(r.get('running') is not False or r.get('restart')!='no' for r in rows):
        raise Stop('ENGINES_MUST_REMAIN_STOPPED_NO_RESTART')
    return rows


def base_run_args(*extra,read_only=False):
    mount='/opt/porota-trading/data/paper_v17:/app/data/paper_v17:' + ('ro' if read_only else 'rw')
    args=['run',*extra,'--network','none','--no-healthcheck']
    if '--rm' not in extra: args+=['--restart','no']
    return [*args,'--read-only','--user','botuser','--cap-drop','ALL',
        '--security-opt','no-new-privileges:true','--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
        '--tmpfs','/home/botuser/.config:rw,noexec,nosuid,size=8m',
        '-e','PYTHONDONTWRITEBYTECODE=1','-e','DATA_DIR=/app/data',
        '-e','PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db',
        '-e','PAPER_INITIAL_CAPITAL_ARS=1000000','-e','PAPER_INITIAL_CAPITAL_USD=0',
        '-e','PAPER_INITIAL_CAPITAL_USD_MEP=0','-e','PAPER_INITIAL_CAPITAL_USD_CCL=0',
        '-e','SERVER_TIMEZONE=America/Argentina/Buenos_Aires',
        '-v',mount,'--entrypoint','python',IMAGE]


AUDIT_CODE="""import json,sqlite3
c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro&immutable=1',uri=True)
c.execute('pragma query_only=on')
def one(sql):
 r=c.execute(sql).fetchone(); return dict(r) if r else None
c.row_factory=sqlite3.Row
tables=['paper_positions','paper_fills','paper_cauciones','paper_decisions','market_snapshots']
counts={t:c.execute('select count(*) from '+t).fetchone()[0] for t in tables}
print(json.dumps({'observer':one('select * from observer_state where id=1'),
 'supervisor':one('select * from paper_supervisor_state where id=1'),
 'exit_reader':one('select * from paper_exit_reader_state where id=1'),
 'notifications':one('select * from paper_notification_worker where id=1'),
 'candles':one('select * from candle_worker_state where id=1'),'counts':counts},default=str))"""

LIVE_QUERY="""import json,sqlite3
c=sqlite3.connect('/app/data/paper_v17/observer_v17.db'); c.row_factory=sqlite3.Row
def state(table,column='state'):
 r=c.execute('select '+column+' value from '+table+' where id=1').fetchone(); return r['value'] if r else None
print(json.dumps({'observer':state('observer_state','process_state'),
 'orders':state('observer_state','real_orders_sent'),
 'supervisor':state('paper_supervisor_state'),
 'exit_reader':state('paper_exit_reader_state'),
 'notifications':state('paper_notification_worker'),
 'candles':state('candle_worker_state')}))"""


def canary():
    directory=ROOT/'data/paper_v17'; database=directory/'observer_v17.db'
    report={'diagnostic':VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),
        'status':'NOT_STARTED','stage':'PREFLIGHT','network':'none','credentials_mounted':False,
        'environment_mounted':False,'legacy_data_mounted':False,'account_queried':False,
        'orders_sent':0,'temporary_container_created':False,'temporary_container_removed':False,
        'engines_started':False,'promotion_allowed':False}
    temporary=False
    try:
        if os.geteuid()!=1000 or os.getegid()!=1000: raise Stop('CALLER_MUST_BE_UID_GID_1000')
        head=run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True)
        if head.returncode or head.stdout.strip()!=COMMIT: raise Stop('UNEXPECTED_SOURCE_COMMIT')
        if run(['git','-C',str(ROOT),'status','--porcelain','--untracked-files=no'],capture_output=True).stdout.strip():
            raise Stop('TRACKED_WORKTREE_NOT_CLEAN')
        info=database.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or
                info.st_uid!=1000 or info.st_gid!=1000 or info.st_nlink!=1):
            raise Stop('V17_DATABASE_INVALID')
        image=docker('image','inspect','--format','{"id":{{json .Id}},"user":{{json .Config.User}}}',
                     IMAGE,capture_output=True)
        if image.returncode or json.loads(image.stdout)!={'id':IMAGE_ID,'user':'botuser'}:
            raise Stop('CANDIDATE_IMAGE_CHANGED')
        report['engines_before']=engine_rows()
        if docker('container','inspect',NAME,capture_output=True).returncode==0:
            raise Stop('CANARY_CONTAINER_NAME_IN_USE')
        report['stage']='START_RUNTIME'
        started=docker(*base_run_args('-d','--name',NAME),'bv_paper_runtime.py',capture_output=True)
        if started.returncode: raise Stop('RUNTIME_START_FAILED')
        temporary=True; report['temporary_container_created']=True
        ready=False; stable=0; deadline=time.monotonic()+40
        allowed={'observer':{'STARTING','WAITING_MARKET','READY_PREOPEN','RUNNING','DEGRADED'},
                 'supervisor':{'RUNNING'},
                 'exit_reader':{'WAITING_MARKET','READY','COOLDOWN','ERROR','DEGRADED'},
                 'notifications':{'NOT_CONFIGURED','RUNNING'},
                 'candles':{'RUNNING','BACKLOG'}}
        while time.monotonic()<deadline:
            checked=docker('exec',NAME,'python','-c',LIVE_QUERY,capture_output=True)
            if checked.returncode==0:
                values=json.loads(checked.stdout)
                active=(values.get('orders')==0 and all(values.get(key) in states
                        for key,states in allowed.items()))
                stable=stable+1 if active else 0
                if stable>=2:
                    ready=True; report['runtime_live_states']=values; break
            state=docker('inspect','--format','{{json .State.Running}}',NAME,capture_output=True)
            if state.returncode or state.stdout.strip()!='true': break
            time.sleep(1)
        if not ready: raise Stop('RUNTIME_NOT_READY')
        time.sleep(8)
        report['stage']='STOP_RUNTIME'
        if docker('stop','--timeout','20',NAME,capture_output=True,timeout=35).returncode:
            raise Stop('RUNTIME_STOP_FAILED')
        exit_code=docker('inspect','--format','{{json .State.ExitCode}}',NAME,capture_output=True)
        if exit_code.returncode or exit_code.stdout.strip()!='0': raise Stop('RUNTIME_BAD_EXIT')
        if docker('rm',NAME,capture_output=True).returncode: raise Stop('RUNTIME_REMOVE_FAILED')
        temporary=False; report['temporary_container_removed']=True
        report['stage']='IMMUTABLE_AUDIT'
        audited=docker(*base_run_args('--rm',read_only=True),'-c',AUDIT_CODE,
                       capture_output=True,timeout=60)
        if audited.returncode: raise Stop('RUNTIME_AUDIT_FAILED')
        audit=json.loads(audited.stdout)
        expected={'observer':'STOPPED','supervisor':'STOPPED','exit_reader':'STOPPED',
                  'notifications':'STOPPED','candles':'STOPPED'}
        states={name:(audit[name] or {}).get('state') for name in expected if name!='observer'}
        states['observer']=(audit['observer'] or {}).get('process_state')
        if states!=expected or any(audit['counts'].values()) or (audit['observer'] or {}).get('real_orders_sent')!=0:
            raise Stop('RUNTIME_STATE_AUDIT_FAILED')
        report['stopped_states']=states; report['empty_trading_tables']=audit['counts']
        report['engines_after']=engine_rows()
        report['status']='OFFLINE_RUNTIME_VERIFIED'; report['stage']='DONE'
    except Stop as exc: report.update(status='STOPPED',reason=str(exc))
    except (OSError,subprocess.SubprocessError,ValueError,TypeError,KeyError) as exc:
        report.update(status='STOPPED',reason=type(exc).__name__)
        if isinstance(exc,OSError): report['os_errno']=exc.errno
    finally:
        if temporary:
            docker('stop','--timeout','5',NAME,capture_output=True,timeout=15)
            removed=docker('rm','-f',NAME,capture_output=True)
            report['temporary_container_removed']=removed.returncode==0
        report['completed_at']=datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-offline',action='store_true',required=True); parser.parse_args(argv)
    output=json.dumps(canary(),ensure_ascii=False,indent=2,allow_nan=False)
    print(output,flush=True)
    print('\033]52;c;'+base64.b64encode(output.encode()).decode()+'\a',end='',flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
