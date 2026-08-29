"""Inicializa y audita el PAPER v17 en un contenedor aislado y sin red."""
import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

VERSION = 'v17-offline-smoke-2'
ROOT = Path('/opt/porota-trading')
TARGET_COMMIT = 'fa79089346d20d20e078d63cba928ca412a64222'
IMAGE = 'porota-trading-bot:17.0.0-rc1'
IMAGE_ID = 'sha256:2ce7653716523961a71bcb6f7fad8cd4d04db09de2bbda893657ee2d027f3ebf'
NAME = 'porota_v17_offline_smoke'
ENGINES = ('porota_trading_bot','porota_production_observer')


class Stop(Exception):
    pass


def run(args,timeout=30,**kwargs):
    return subprocess.run(args,text=True,timeout=timeout,**kwargs)


def docker(*args,timeout=30,**kwargs):
    return run(['sudo','-n','docker',*args],timeout=timeout,**kwargs)


def engines():
    template = ('{"name":{{json .Name}},"running":{{json .State.Running}},'
                '"restart":{{json .HostConfig.RestartPolicy.Name}},'
                '"image":{{json .Image}},"user":{{json .Config.User}}}')
    result=docker('inspect','--type','container','--format',template,*ENGINES,
                  capture_output=True)
    if result.returncode:
        raise Stop('DOCKER_INSPECT_FAILED')
    rows=[json.loads(line) for line in result.stdout.splitlines()]
    if len(rows)!=2 or {r.get('name') for r in rows}!={'/'+n for n in ENGINES}:
        raise Stop('UNEXPECTED_ENGINE_METADATA')
    if any(r.get('running') is not False or r.get('restart')!='no' for r in rows):
        raise Stop('ENGINES_MUST_REMAIN_STOPPED_NO_RESTART')
    return [{k:r[k] for k in ('name','running','restart','image','user')} for r in rows]


def offline_args(*extra,read_only_mount=False):
    mount='/opt/porota-trading/data/paper_v17:/app/data/paper_v17:' + ('ro' if read_only_mount else 'rw')
    args=['run',*extra,'--network','none','--no-healthcheck']
    # Docker rechaza --rm junto con cualquier política --restart.
    if '--rm' not in extra:
        args += ['--restart','no']
    return [*args,
            '--read-only','--user','botuser','--cap-drop','ALL',
            '--security-opt','no-new-privileges:true',
            '--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
            '-e','PYTHONDONTWRITEBYTECODE=1','-e','DATA_DIR=/app/data',
            '-e','PAPER_V17_DB_PATH=/app/data/paper_v17/observer_v17.db',
            '-e','PAPER_INITIAL_CAPITAL_ARS=1000000',
            '-e','PAPER_INITIAL_CAPITAL_USD=0',
            '-e','PAPER_INITIAL_CAPITAL_USD_MEP=0',
            '-e','PAPER_INITIAL_CAPITAL_USD_CCL=0','-v',mount,
            '--entrypoint','python',IMAGE]


def smoke():
    directory=ROOT/'data/paper_v17'
    database=directory/'observer_v17.db'
    report={'diagnostic':VERSION,'generated_at':datetime.now(timezone.utc).isoformat(),
            'status':'NOT_STARTED','stage':'PREFLIGHT','network':'none',
            'credentials_mounted':False,'account_queried':False,'orders_sent':0,
            'temporary_container_created':False,'temporary_container_removed':False,
            'database_created':False,'database_opened':False,'legacy_data_mounted':False,
            'legacy_data_touched':False,'engines_started':False,'promotion_allowed':False}
    temporary=False
    try:
        if os.geteuid()!=1000 or os.getegid()!=1000:
            raise Stop('CALLER_MUST_BE_UID_GID_1000')
        if run(['git','-C',str(ROOT),'rev-parse','HEAD'],capture_output=True).stdout.strip()!=TARGET_COMMIT:
            raise Stop('UNEXPECTED_SOURCE_COMMIT')
        if run(['git','-C',str(ROOT),'status','--porcelain','--untracked-files=no'],
               capture_output=True).stdout.strip():
            raise Stop('TRACKED_WORKTREE_NOT_CLEAN')
        info=directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or
                (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))!=(1000,1000,0o750)):
            raise Stop('V17_DIRECTORY_INVALID')
        entries={path.name for path in directory.iterdir()}
        expected_existing={'observer_v17.db','observer_v17.db.initialization.lock'}
        fresh=not entries
        resume=(entries==expected_existing and database.is_file() and not database.is_symlink())
        if not fresh and not resume:
            raise Stop('V17_DIRECTORY_CONTENT_REVIEW_REQUIRED')
        report['resumed_existing_dataset']=resume
        if resume:
            report['database_created']=True
            report['database_opened']=True
        inspected=docker('image','inspect','--format','{"id":{{json .Id}},"user":{{json .Config.User}}}',
                         IMAGE,capture_output=True)
        if inspected.returncode:
            raise Stop('CANDIDATE_IMAGE_MISSING')
        image=json.loads(inspected.stdout)
        if image!={'id':IMAGE_ID,'user':'botuser'}:
            raise Stop('CANDIDATE_IMAGE_CHANGED')
        report['image']=image
        report['engines_before']=engines()
        if docker('container','inspect',NAME,capture_output=True).returncode==0:
            raise Stop('TEMPORARY_CONTAINER_NAME_IN_USE')
        if fresh:
            report['stage']='START_OFFLINE_WORKER'
            started=docker(*offline_args('-d','--name',NAME),
                           'bv_paper_runtime.py','--candle-worker',capture_output=True)
            if started.returncode:
                raise Stop('OFFLINE_WORKER_START_FAILED')
            temporary=True
            report['temporary_container_created']=True
            query=("import sqlite3; c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True); "
                   "r=c.execute('select state from candle_worker_state where id=1').fetchone(); print(r[0] if r else '')")
            ready=False
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                check=docker('exec',NAME,'python','-c',query,capture_output=True)
                if check.returncode==0 and check.stdout.strip()=='RUNNING':
                    ready=True
                    break
                state=docker('inspect','--format','{{json .State.Running}}',NAME,capture_output=True)
                if state.returncode or state.stdout.strip()!='true':
                    break
                time.sleep(.5)
            if not ready:
                raise Stop('OFFLINE_WORKER_NOT_RUNNING')
            report['database_created']=database.exists()
            report['database_opened']=report['database_created']
            report['worker_running_observed']=True
            report['stage']='STOP_OFFLINE_WORKER'
            stopped=docker('stop','--timeout','15',NAME,capture_output=True,timeout=30)
            if stopped.returncode:
                raise Stop('OFFLINE_WORKER_STOP_FAILED')
            exit_code=docker('inspect','--format','{{json .State.ExitCode}}',NAME,
                             capture_output=True).stdout.strip()
            if exit_code!='0':
                raise Stop('OFFLINE_WORKER_BAD_EXIT')
            if docker('rm',NAME,capture_output=True).returncode:
                raise Stop('TEMPORARY_CONTAINER_REMOVE_FAILED')
            temporary=False
            report['temporary_container_removed']=True
        report['stage']='READ_ONLY_AUDIT'
        audit_code="""import json,sqlite3
p='file:/app/data/paper_v17/observer_v17.db?mode=ro'
c=sqlite3.connect(p,uri=True); c.execute('pragma query_only=on')
names=[r[1] for r in c.execute('pragma table_info(paper_workspace)')]
identity=dict(zip(names,c.execute('select * from paper_workspace').fetchone()))
tables=['paper_positions','paper_fills','paper_learning_samples','market_snapshots','paper_cauciones','paper_decisions','candle_versions','paper_sale_receivables']
counts={t:c.execute('select count(*) from '+t).fetchone()[0] for t in tables}
worker=c.execute('select state from candle_worker_state where id=1').fetchone()
print(json.dumps({'identity':identity,'counts':counts,'worker_state':worker[0] if worker else None},sort_keys=True))"""
        audited=docker(*offline_args('--rm',read_only_mount=True),'-c',audit_code,
                       capture_output=True,timeout=60)
        if audited.returncode:
            raise Stop('READ_ONLY_AUDIT_FAILED')
        audit=json.loads(audited.stdout)
        identity=audit['identity']
        capital=json.loads(identity['initial_capital_json'])
        if (identity.get('namespace')!='POROTA_PAPER_V17_FRESH' or
                identity.get('origin')!='FRESH_EMPTY' or identity.get('imported_legacy')!=0 or
                capital!={'ARS':'1E+6','USD':'0','USD_CCL':'0','USD_MEP':'0'} or
                audit.get('worker_state')!='STOPPED' or any(audit['counts'].values())):
            raise Stop('FRESH_DATABASE_AUDIT_FAILED')
        dbinfo=database.lstat()
        if (not stat.S_ISREG(dbinfo.st_mode) or stat.S_ISLNK(dbinfo.st_mode) or
                dbinfo.st_uid!=1000 or dbinfo.st_gid!=1000 or dbinfo.st_nlink!=1):
            raise Stop('NEW_DATABASE_FILE_INVALID')
        report['database_created']=True
        report['database']={'path':str(database),'uid':dbinfo.st_uid,'gid':dbinfo.st_gid,
                            'mode':format(stat.S_IMODE(dbinfo.st_mode),'04o'),'bytes':dbinfo.st_size,
                            'links':dbinfo.st_nlink}
        report['workspace']={'namespace':identity['namespace'],'dataset_id':identity['dataset_id'],
                             'created_at':identity['created_at'],'origin':identity['origin'],
                             'imported_legacy':identity['imported_legacy'],'initial_capital':capital}
        report['empty_tables']=audit['counts']
        report['worker_state']=audit['worker_state']
        report['engines_after']=engines()
        report['status']='OFFLINE_FRESH_DATASET_VERIFIED'
        report['stage']='DONE'
    except Stop as exc:
        report.update(status='STOPPED',reason=str(exc))
    except (OSError,subprocess.SubprocessError,ValueError,TypeError,KeyError) as exc:
        report.update(status='STOPPED',reason=type(exc).__name__)
        if isinstance(exc,OSError): report['os_errno']=exc.errno
    finally:
        if database.exists() and not database.is_symlink():
            report['database_created']=True
            report['database_opened']=True
        if temporary:
            docker('stop','--timeout','5',NAME,capture_output=True,timeout=15)
            removed=docker('rm','-f',NAME,capture_output=True)
            report['temporary_container_removed']=removed.returncode==0
        report['completed_at']=datetime.now(timezone.utc).isoformat()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline-smoke',action='store_true',required=True)
    parser.parse_args(argv)
    output=json.dumps(smoke(),ensure_ascii=False,indent=2,allow_nan=False)
    print(output,flush=True)
    print('\033]52;c;'+base64.b64encode(output.encode()).decode()+'\a',end='',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
