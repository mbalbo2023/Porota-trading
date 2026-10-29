#!/usr/bin/env python3
"""Exact, bounded A175 receipt/source/lifecycle diagnosis; no host writes."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import urllib.request

ROOT=Path('/opt/porota-trading')
LOCK=Path('/run/lock/porota-rc6-deploy-v2.lock')
BARRIER=Path('/run/lock/porota-rc6-deploy-v2-attempt.json')
CANDIDATE='18ba517a846c6798f0d12f39072465fc02fa03b5'
TREE='66c67bff66dd9f036cd7aa8de5ba312e04e53be6'
DEPLOY='3c5210fef9d4f1d4c95b286b0dbc61b6654fe055'
IMAGE='sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'
Q_SHA='5ddf0d5e340f7cac96bcbb339eeaeea3ed4aad635a2201fd55fffe375c961022'
S_SHA='64a8afc3591c42d4e58f3acb34905043c0ffe323b695ecf3337f99b66d6b0b6e'
MARKER_SHA='42055f5225def0560453b2c40b5e4695082fa3235fc42a4596210cf0c1b8b161'
INSTALLED_Q_SHA='284c5cd7e32bd9672138031204fe0ff5d24277bc9e84ec2ea6dcfc06e51ec170'
INSTALLED_S_SHA='b6cba76fd46be859ede6936f2a173915e0f93770db45be0bfa96e9d2dc68a72a'
IDS={'porota_production_observer':'72ed632d0514ec1835e3dcb7983aa2b13cfbcf2b46c5a62132e5c739f7df86eb',
     'porota_production_dashboard':'9b01cf17b7c3e46d96474d998840fc0e4c4c4f828dd825bb44744710b99686bb'}
LIFECYCLE=('Status','StartedAt','FinishedAt','ExitCode','OOMKilled')
CODE=re.compile(r'[A-Z][A-Z0-9_]{2,99}\Z')


def metadata(field):
    if field not in {'id','region'}:raise ValueError('METADATA_FIELD_RED')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://169.254.169.254/metadata/v1/'+field,timeout=2) as response:
        if response.status!=200:raise ValueError('METADATA_STATUS_RED')
        return response.read(64).decode('ascii').strip()


def lock_existing():
    if os.geteuid()!=0:raise ValueError('ROOT_REQUIRED')
    parent=LOCK.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=0:
        raise ValueError('MUTEX_PARENT_RED')
    fd=os.open(LOCK,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        info=os.fstat(fd); path=LOCK.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or
                stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or
                (info.st_dev,info.st_ino)!=(path.st_dev,path.st_ino)):
            raise ValueError('MUTEX_IDENTITY_RED')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:BARRIER.lstat()
        except FileNotFoundError:pass
        else:raise ValueError('BARRIER_PRESENT')
        return fd
    except BaseException:
        os.close(fd);raise


def sealed(path,limit):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=limit:
        raise ValueError('RECEIPT_PATH_RED')
    raw=path.read_bytes()
    def unique(rows):
        value={}
        for key,item in rows:
            if key in value:raise ValueError('DUPLICATE_KEY_RED')
            value[key]=item
        return value
    value=json.loads(raw,object_pairs_hook=unique,
        parse_constant=lambda _ : (_ for _ in ()).throw(ValueError('NONFINITE_RED')))
    if type(value) is not dict:raise ValueError('RECEIPT_OBJECT_RED')
    return value,hashlib.sha256(raw).hexdigest()


def docker(name):
    result=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=10)
    if result.returncode or len(result.stdout)>65536:
        raise ValueError('DOCKER_INSPECT_RED')
    rows=json.loads(result.stdout)
    if type(rows) is not list or len(rows)!=1:
        raise ValueError('DOCKER_ROW_RED')
    value=rows[0]
    return {'id':value['Id'],'image':value['Image'],
            'lifecycle':{key:value['State'].get(key) for key in LIFECYCLE}}


def run():
    droplet={'id':metadata('id'),'region':metadata('region')}
    if droplet!={'id':'594077619','region':'nyc1'}:
        raise ValueError('DROPLET_IDENTITY_RED')
    fd=lock_existing()
    try:
        base=ROOT/'data/deploy'
        q,qsha=sealed(base/('quiescence-'+CANDIDATE+'.json'),65536)
        s,ssha=sealed(base/('scheduler-suspend-'+CANDIDATE+'.json'),65536)
        marker,msha=sealed(base/'CURRENT_STATE_V2.json',65536)
        frozen,fsha=sealed(base/'porota-frozen-candidate.json',16384)
        if (qsha!=Q_SHA or ssha!=S_SHA or msha!=MARKER_SHA or
                q.get('candidate_sha')!=CANDIDATE or
                q.get('status')!='FIX_FORWARD_VALIDATION_REQUIRED' or
                q.get('installation_started') is not True or
                q.get('a174_pending_adopted') is not True or
                q.get('a174_origin_candidate')!='b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0' or
                q.get('a174_origin_sha256')!='364c9bb9f013da603345c90219e7c889d5b04ecff951afe08f4bfc7ec03c1528' or
                q.get('inflight')!='ABSENT_OWNED_RUNTIME_STOPPED' or
                q.get('runtime_health')!='NEW_A175_CANDIDATE_NOT_GO' or
                q.get('real_orders_sent')!=0 or q.get('ppi_watch_accessed') is not False or
                s.get('candidate_sha')!=CANDIDATE or s.get('status')!='SUSPENDED' or
                s.get('a174_running_origin') is not True or
                s.get('paused_from')!='b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0' or
                s.get('rescue_failure_disposition')!='A175_NEW_CANDIDATE_UNVALIDATED_HELD' or
                len(s.get('timers') or [])!=16 or
                marker.get('candidate_sha')!=CANDIDATE or
                marker.get('deploy_sha')!=DEPLOY or marker.get('image_id')!=IMAGE or
                marker.get('validation_status')!='DEPLOYED_VALIDATION_PENDING' or
                marker.get('mode')!='PRODUCTION_PAPER' or
                marker.get('real_orders_sent')!=0 or
                marker.get('real_order_routes')!='NOT_CALLED' or
                marker.get('ppi_watch_untouched') is not True or
                frozen.get('candidate_sha')!=CANDIDATE or
                frozen.get('candidate_tree_sha')!=TREE):
            raise ValueError('A175_PROVENANCE_RED')
        source={}
        for name,expected in (('rc6_deploy_quiescence.py',INSTALLED_Q_SHA),
                              ('rc6_deploy_scheduler_suspend.py',INSTALLED_S_SHA)):
            path=ROOT/'scripts'/name
            if path.is_symlink() or not path.is_file():raise ValueError('SOURCE_PATH_RED')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if digest!=expected:raise ValueError('INSTALLED_SOURCE_RED')
            source[name]=digest
        intent=q.get('candidate_image_intent') or {}
        runtime=q.get('candidate_runtime_after_failure') or {}
        observed={name:docker(name) for name in IDS}
        for name in IDS:
            row=observed[name]
            if (row['id']!=IDS[name] or row['image']!=IMAGE or
                    row['lifecycle']['Status']!='running'):
                raise ValueError('DOCKER_EXACT_IDENTITY_RED')
        flat=runtime.get('flat_book') or {}
        sanitized={'schema':'rc6.a176.a175-internals-readonly.v1','status':'PROVEN',
            'droplet':droplet,'host_mutations':0,'operational_go':False,
            'candidate_sha':CANDIDATE,'candidate_tree':TREE,'deploy_sha':DEPLOY,
            'quiescence_sha256':qsha,'scheduler_sha256':ssha,
            'marker_sha256':msha,'frozen_sha256':fsha,'installed_source_sha256':source,
            'quiescence':{'status':q['status'],'runtime_health':q['runtime_health'],
                'inflight':q['inflight'],'a174_pending_adopted':q['a174_pending_adopted'],
                'operator_origin':{key:(q.get('a174_previous_proof') or {}).get(key)
                    for key in ('origin_candidate','origin_sha256','operator_receipt_sha256','operator_operation_id')},
                'intent':{key:intent.get(key) for key in
                    ('image_id','manifest_sha256','candidate_tree_sha','image_tar_sha256','recorded_at')},
                'runtime_status':runtime.get('status'),
                'failure_flat_book':{key:flat.get(key) for key in
                    ('source_sha','real_orders_sent','process_state','session_state')},
                'critical_capability':(runtime.get('critical_admission') or {}).get('capability'),
                'recorded_containers':{name:{'id':(runtime.get('containers') or {}).get(name,{}).get('id'),
                    'image':(runtime.get('containers') or {}).get(name,{}).get('image'),
                    'lifecycle':(runtime.get('containers') or {}).get(name,{}).get('lifecycle')}
                    for name in IDS}},
            'scheduler':{key:s.get(key) for key in
                ('policy_sha256','paused_from','prior_receipt_sha256',
                 'a174_origin_sha256','operator_pause_receipt_sha256',
                 'rescue_failure_disposition')},
            'marker':{key:marker.get(key) for key in
                ('candidate_sha','deploy_sha','image_id','validation_status',
                 'mode','real_orders_sent','real_order_routes','ppi_watch_untouched')},
            'frozen':{key:frozen.get(key) for key in
                ('candidate_sha','candidate_tree_sha','image_id','image_tar_sha256')},
            'docker':observed}
        return sanitized
    finally:os.close(fd)


if __name__=='__main__':
    try:print('RC6_A176_A175_INTERNALS_RO='+json.dumps(run(),sort_keys=True,separators=(',',':')))
    except Exception as exc:
        value=str(exc).split(':',1)[0]
        print('RC6_A176_A175_INTERNALS_RO=RED|'+(value if CODE.fullmatch(value) else type(exc).__name__.upper()))
        raise SystemExit(2)
