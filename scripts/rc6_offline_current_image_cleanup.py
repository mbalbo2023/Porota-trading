#!/usr/bin/env python3
"""Reconcile stale stable tag and reclaim its unreferenced image pretransfer.

Runs only under the existing canonical deploy lock after a failed COLD2 left
the owned observer/dashboard stopped. No container, DB, volume or PPI action.
"""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re
import shutil
import sys
import sqlite3
import stat
import subprocess

ROOT = Path('/opt/porota-trading')
STABLE = 'porota-trading-bot:17.0.0-rc6'
OWNED = ('porota_production_observer', 'porota_production_dashboard')
CRITICAL = 'porota_critical_approval_rc6'
IMAGE = re.compile(r'sha256:[a-f0-9]{64}')
CANDIDATE_TAG = re.compile(r'(?:porota-predeploy-v2:|porota-trading-bot:17\.0\.0-rc6-candidate-)[a-f0-9]{40}')


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=20)
    if result.returncode or len(result.stdout) > 1024*1024:
        raise ValueError('OFFLINE_CURRENT_COMMAND_FAILED')
    return result.stdout.strip()


def docker(*args):
    return command('docker', *args)


def inspect(name):
    rows = json.loads(docker('inspect', name))
    if type(rows) is not list or len(rows) != 1:
        raise ValueError('OFFLINE_CURRENT_INSPECT_SHAPE')
    return rows[0]


def references():
    ids = docker('ps','-aq','--no-trunc').splitlines()
    if len(ids)>128 or any(re.fullmatch(r'[a-f0-9]{12,64}', cid) is None for cid in ids):
        raise ValueError('OFFLINE_CURRENT_CONTAINER_BOUND')
    return {inspect(cid)['Image'] for cid in ids}


def image(id_or_tag):
    rows=json.loads(docker('image','inspect',id_or_tag))
    if type(rows) is not list or len(rows)!=1 or IMAGE.fullmatch(str(rows[0].get('Id'))) is None:
        raise ValueError('OFFLINE_CURRENT_IMAGE_SHAPE')
    return rows[0]


def ppi_units():
    lines=command('systemctl','list-unit-files','--no-legend').splitlines()
    names=sorted({row.split()[0] for row in lines if row.split() and
                  'ppi' in row.split()[0].lower() and 'watch' in row.split()[0].lower()})
    if len(names)>16:
        raise ValueError('OFFLINE_CURRENT_PPI_BOUND')
    return {name:tuple(subprocess.run(['systemctl','is-'+mode,name],
                     capture_output=True,text=True,timeout=5).stdout.strip()
                     for mode in ('active','enabled')) for name in names}


def admission():
    state_path=ROOT/'data/deploy/CURRENT_STATE_V2.json'
    if state_path.is_symlink() or not state_path.is_file() or state_path.stat().st_size>65536:
        raise ValueError('OFFLINE_CURRENT_STATE_UNSAFE')
    state=json.loads(state_path.read_text())
    expected=state.get('image_id')
    validation = state.get('validation_status')
    if (validation not in {'DEPLOYED_VALIDATION_PENDING', 'VALIDATED_RUNTIME'} or
            state.get('mode')!='PRODUCTION_PAPER' or
            type(state.get('real_orders_sent')) is not int or state['real_orders_sent']!=0 or
            state.get('real_order_routes')!='NOT_CALLED' or
            IMAGE.fullmatch(str(expected)) is None):
        raise ValueError('OFFLINE_CURRENT_PROVENANCE_RED')
    owned={name:inspect(name) for name in OWNED}
    critical=inspect(CRITICAL)
    if (any(row['Image']!=expected or row['State']['Status']!='exited' or
            row['State']['ExitCode']!=0 for row in owned.values()) or
            critical['Image']!=expected or critical['State']['Status']!='running' or
            (critical['State'].get('Health') or {}).get('Status')!='healthy' or
            critical['State'].get('OOMKilled') is not False or
            type(critical.get('RestartCount')) is not int or critical['RestartCount']!=0):
        raise ValueError('OFFLINE_CURRENT_OWNED_IDENTITY_RED')
    capability=docker('exec',CRITICAL,'python','-c',
        "from fm_critical_approval_unix_runtime_rc6 import UnixGithubIssuesClient; "
        "h=UnixGithubIssuesClient().health(); "
        "assert h.get('status')=='ok' and h.get('capability')=='issues_only'; "
        "print('RC6_CRITICAL_ISSUES_ONLY=GREEN')")
    if capability!='RC6_CRITICAL_ISSUES_ONLY=GREEN':
        raise ValueError('OFFLINE_CURRENT_CRITICAL_CAPABILITY')
    intraday = None
    if validation == 'VALIDATED_RUNTIME':
        # An install can fail before CURRENT_STATE is advanced. The exact
        # stopped fix-forward receipt, not the stale SQLite RUNNING marker,
        # proves this old validated image and its unchanged PAPER carryover.
        sys.path.insert(0, str(ROOT))
        from scripts import rc6_deploy_quiescence as drain
        folder = ROOT/'data/deploy'
        paths = list(folder.glob('quiescence-*.json'))
        if len(paths) > 128 or any(p.is_symlink() or p.stat().st_size > 65536 for p in paths):
            raise ValueError('OFFLINE_CURRENT_RECEIPT_INVENTORY_UNSAFE')
        eligible = []
        for path in paths:
            prior = json.loads(path.read_text())
            if (path.name != 'quiescence-' + str(prior.get('candidate_sha')) + '.json' or
                    not re.fullmatch(r'[a-f0-9]{40}', str(prior.get('candidate_sha')))):
                raise ValueError('OFFLINE_CURRENT_RECEIPT_IDENTITY_RED')
            if (prior.get('status') != 'FIX_FORWARD_VALIDATION_REQUIRED' or
                    prior.get('installation_started') is not True or
                    prior.get('market_open_suspended') is not True or
                    prior.get('open_paper_carryover') is not True or
                    set(prior.get('containers', {})) != set(OWNED) or
                    any(prior['containers'][name].get('id') != owned[name]['Id'] or
                        prior['containers'][name].get('image') != owned[name]['Image']
                        for name in OWNED)):
                continue
            eligible.append((prior['recorded_at'], prior))
        eligible.sort(key=lambda row:row[0])
        if (not eligible or eligible[-1][0] < state.get('recorded_at', '') or
                len(eligible) > 1 and eligible[-1][0] == eligible[-2][0]):
            raise ValueError('OFFLINE_CURRENT_EXACT_FAILURE_RECEIPT_MISSING')
        prior = eligible[-1][1]
        drain.exact_clean_stopped(prior)
        observed = drain.admit(stopped=True, allow_market_open_suspend=True,
                               allow_open_paper_carryover=True,
                               allow_postclose_pending=prior.get('paused_postclose') is True,
                               stopped_receipt=prior)
        carry = drain.spot_carryover_snapshot()
        if (observed not in {'STOPPED', 'RUNNING'} or
                carry != prior.get('spot_carryover') or
                type(carry.get('open_count')) is not int or
                not 0 < carry['open_count'] <= 16):
            raise ValueError('OFFLINE_CURRENT_CARRYOVER_RED')
        if prior.get('paused_postclose') is True and (
                drain.pending_caucion_snapshot() != prior.get('caucion_carryover')):
            raise ValueError('OFFLINE_CURRENT_CAUCION_CARRYOVER_RED')
        intraday = (prior['candidate_sha'], carry['sha256'])
    else:
        db=ROOT/'data/paper_v17/observer_v17.db'
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=.2) as c:
            c.execute('PRAGMA query_only=ON')
            c.execute('PRAGMA busy_timeout=200')
            row=c.execute('SELECT mode,real_orders_sent,process_state,session_state FROM observer_state WHERE id=1').fetchone()
            if (not row or row[0]!='PRODUCTION_PAPER' or type(row[1]) is not int or row[1]!=0 or
                    row[2] not in {'STOPPED','WAITING_MARKET'} or row[3] not in {'CLOSED','MARKET_CLOSED'}):
                raise ValueError('OFFLINE_CURRENT_PAPER_RED')
            for table, value in (('paper_positions','OPEN'),('paper_cauciones','OPEN'),
                                 ('paper_future_positions','ACTIVE')):
                if c.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1',(value,)).fetchone():
                    raise ValueError('OFFLINE_CURRENT_POSITION_OPEN')
    return {'current_image':expected,'owned':{name:(row['Id'],row['Image'],
            row['State']['Status'],row['State']['ExitCode']) for name,row in owned.items()},
            'critical':(critical['Id'],critical['Image'],critical['State']['Status']),
            'candidate_sha':state['candidate_sha'],'ppi':ppi_units(),
            'intraday_proof':intraday}


def cleanup():
    lock=ROOT/'data/deploy/.deploy-quiescence.lock'
    if lock.is_symlink() or not lock.is_file() or not stat.S_ISREG(lock.lstat().st_mode):
        raise ValueError('OFFLINE_CURRENT_LOCK_UNSAFE')
    with lock.open('rb') as stream:
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=admission()
        current=before['current_image']
        stale=image(STABLE)
        stale_id=stale['Id']
        if stale_id==current:
            return {'status':'GREEN','stable':'ALREADY_CURRENT','removed_tags':[],
                    'real_orders_sent':0,'ppi_watch_changed':False,
                    'disk_after':shutil.disk_usage('/').free}
        tags=stale.get('RepoTags') or []
        digests=stale.get('RepoDigests') or []
        candidate_tags=[tag for tag in tags if tag!=STABLE]
        repos={tag.split(':',1)[0] for tag in tags}
        stale_shas={tag.rsplit(':',1)[-1].removeprefix('17.0.0-rc6-candidate-')
                    for tag in candidate_tags}
        if (STABLE not in tags or not candidate_tags or
                any(CANDIDATE_TAG.fullmatch(tag) is None for tag in candidate_tags) or
                len(stale_shas)!=1 or before['candidate_sha'] in stale_shas or
                any(re.fullmatch(r'(porota-predeploy-v2|porota-trading-bot)@sha256:[a-f0-9]{64}',d) is None
                    or d.split('@',1)[0] not in repos for d in digests) or
                stale_id in references() or current not in references() or
                image(current)['Id']!=current):
            raise ValueError('OFFLINE_CURRENT_STALE_NOT_EXCLUSIVE')
        free_before=shutil.disk_usage('/').free
        if admission()!=before or stale_id in references():
            raise ValueError('OFFLINE_CURRENT_ADMISSION_CHANGED')
        # Retagging is an atomic pointer change in Docker metadata. The exact
        # already-owned current image exists; no image build or service restart.
        docker('tag',current,STABLE)
        if image(STABLE)['Id']!=current or admission()!=before:
            raise ValueError('OFFLINE_CURRENT_TAG_NOT_RECONCILED')
        removed=[]
        for tag in candidate_tags:
            if (admission()!=before or stale_id in references() or
                    tag not in (image(stale_id).get('RepoTags') or [])):
                raise ValueError('OFFLINE_CURRENT_IMAGE_OWNERSHIP_CHANGED')
            docker('image','rm',tag)
            removed.append(tag)
        if admission()!=before or image(STABLE)['Id']!=current:
            raise ValueError('OFFLINE_CURRENT_POSTSTATE_CHANGED')
        return {'status':'GREEN','stable':'RECONCILED_TO_CURRENT',
                'current_image':current,'stale_image':stale_id,'removed_tags':removed,
                'disk_before':free_before,'disk_after':shutil.disk_usage('/').free,
                'real_orders_sent':0,'ppi_watch_changed':False}


if __name__=='__main__':
    print(json.dumps(cleanup(),sort_keys=True))
