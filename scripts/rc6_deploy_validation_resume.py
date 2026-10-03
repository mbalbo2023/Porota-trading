#!/usr/bin/env python3
"""Resume only the post-promotion tail of the proven NOT_DUE failure class.

Runs on a GitHub-hosted runner. Product source, frozen image, observer/dashboard
containers and trading DB are never replaced. The canonical validation/cleanup
tail is extracted from an allowlisted exact workflow blob, not reimplemented.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

WORKFLOW = '.github/workflows/porota-deploy-v2-promote.yml'
SUPPORTED_WORKFLOW_BLOB = '2b5ae4db11d466f6b12375ec5cff2ed30312eddc'
PRODUCT_BRANCH = 'deploy/rc6-pr69-isolated-20260915'
FIX_BRANCH = 'fix/rc6-preopen-not-due-20261003'
ROOT = Path(__file__).resolve().parents[1]
OLD_GATE = '''            grep -Fq '\"status\": \"GREEN\"' <<< "$PREOPEN_OUTPUT"
            grep -Fq '\"read_only\": true' <<< "$PREOPEN_OUTPUT"
            grep -Fq '\"network_order_test_performed\": false' <<< "$PREOPEN_OUTPUT"
            echo "RC6_PREOPEN_${phase}=GREEN"'''
NEW_GATE = '''            printf '%s\\n' "$PREOPEN_OUTPUT" | \\
              PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO" python3 \\
                "$REPO/scripts/rc6_deploy_preopen_gate.py" \\
                --phase "$phase" --return-code "$PREOPEN_RC"
            echo "RC6_PREOPEN_${phase}=RESULT_ACCEPTED"'''


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def output(command, **kwargs):
    return run(command, capture_output=True, **kwargs).stdout.strip()


def git_blob(text):
    value = text.encode()
    return hashlib.sha1(b'blob ' + str(len(value)).encode() + b'\0' + value).hexdigest()


def patch_workflow(source):
    require(git_blob(source) == SUPPORTED_WORKFLOW_BLOB, 'UNREVIEWED_WORKFLOW_VERSION')
    require(source.count(OLD_GATE) == 1, 'PREOPEN_ANCHOR_NOT_UNIQUE')
    result = source.replace(OLD_GATE, NEW_GATE)
    require(OLD_GATE not in result and result.count('scripts/rc6_deploy_preopen_gate.py') == 1,
            'CANONICAL_GATE_PATCH_FAILED')
    return result


def executed_log(text):
    lines = []; muted = False
    for line in text.splitlines():
        line = re.sub(r'^\d{4}-\d\d-\d\dT\S+Z ', '', line)
        if line.startswith('##[group]Run '):
            muted = True
        elif muted and line.startswith('##[endgroup]'):
            muted = False
        elif not muted:
            lines.append(line)
    return '\n'.join(lines)


def validate_failure(log):
    text = executed_log(log)
    for marker in ('POROTA_FROZEN_ARTIFACT_VERIFY=GREEN',
                   'RC6_FULL_CONTRACT_RECONCILIATION=GREEN',
                   'POROTA_ZERO_KNOWN_ERROR_RUNTIME_AUDIT=GREEN|immediate',
                   'RC6_BYMA_MORNING_REFRESH=GREEN',
                   '"status": "NOT_DUE"', '"reason": "BYMA_NON_OPERATIONAL_DAY"',
                   '##[error]Process completed with exit code 1.'):
        require(marker in text, 'FAILURE_SIGNATURE_MISSING:' + marker)
    require('RC6_PREOPEN_T_MINUS_45=RED' not in text, 'PREOPEN_PROCESS_FAILED')
    require('RC6_EXIT139_SOAK=GREEN' not in text, 'FAILURE_WAS_AFTER_PREOPEN')
    require(re.search(r'^PPI_WATCH_BEFORE=$', text, re.M) is not None,
            'RESUME_V1_REQUIRES_PROVEN_EMPTY_PPI_WATCH_INVENTORY')
    images = re.findall(r'POROTA_BUILD_ONCE_PROMOTION=GREEN\|expected_image=(sha256:[0-9a-f]{64})\|loaded_image=(sha256:[0-9a-f]{64})', text)
    require(len(images) == 1, 'PROMOTED_IMAGE_NOT_UNIQUE')
    return images[0]


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require(urllib.parse.urlsplit(newurl).scheme == 'https', 'NON_TLS_REDIRECT')
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header('Authorization')
        return new


class GitHubAPI:
    def __init__(self, repo):
        require(repo == 'mbalbo2023/Porota-trading', 'REPOSITORY_MISMATCH')
        self.base = 'https://api.github.com/repos/' + repo
        self.headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        self.opener = urllib.request.build_opener(SafeRedirect())
    def stream(self, path, target, limit):
        size = 0
        request = urllib.request.Request(self.base + path, headers=self.headers)
        with self.opener.open(request, timeout=120) as response, target.open('wb') as handle:
            while chunk := response.read(1024*1024):
                size += len(chunk); require(size <= limit, 'DOWNLOAD_LIMIT')
                handle.write(chunk)
    def get(self, path):
        request = urllib.request.Request(self.base + path, headers=self.headers)
        with self.opener.open(request, timeout=45) as response:
            return json.loads(response.read(10*1024*1024))


def check_control(api, config):
    ref = api.get('/git/ref/heads/' + PRODUCT_BRANCH)
    require(ref['object']['sha'] == config['product'], 'PRODUCT_HEAD_DRIFT')
    issue = api.get('/issues/' + str(config['tracker']))
    require(issue['state'] == 'open', 'TRACKER_NOT_OPEN')
    comments = []
    for page in range(1, 20):
        items = api.get(f"/issues/{config['tracker']}/comments?per_page=100&page={page}")
        comments.extend(items)
        if len(items) < 100:
            break
    else:
        raise RuntimeError('OWNERSHIP_PAGINATION_LIMIT')
    own = [c for c in comments if c['id'] == config['ownership_comment']]
    require(len(own) == 1 and config['product'] in own[0]['body'], 'OWNERSHIP_ATTESTATION_MISSING')
    for comment in comments:
        if comment['id'] > config['ownership_comment']:
            body = comment.get('body', '').upper()
            require(not ('DEPLOY_OWNER' in body and any(x in body for x in ('ACQUIRED', 'RELEASED', 'TRANSFER'))),
                    'LATER_OWNERSHIP_CHANGE')
    for status in ('in_progress', 'queued', 'waiting'):
        for item in api.get('/actions/runs?per_page=100&status=' + status).get('workflow_runs', []):
            if item['id'] == int(os.environ['GITHUB_RUN_ID']):
                continue
            require(item.get('path') not in {WORKFLOW, '.github/workflows/porota-deploy-v2-validation-resume.yml'},
                    'ANOTHER_DEPLOY_OR_RESUME_ACTIVE')


def frozen_material(api, config, directory):
    predeploy = api.get('/actions/runs/' + str(config['predeploy_run']))
    require(predeploy['name'] == 'Porota Predeploy V2' and predeploy['conclusion'] == 'success', 'PREDEPLOY_NOT_GREEN')
    artifact = api.get('/actions/artifacts/' + str(config['artifact']))
    require(not artifact['expired'], 'ARTIFACT_EXPIRED')
    require(artifact['name'] == 'porota-predeploy-v2-' + config['candidate'], 'ARTIFACT_NAME_MISMATCH')
    require(artifact['workflow_run']['id'] == config['predeploy_run'], 'ARTIFACT_RUN_MISMATCH')
    require(artifact.get('digest') == config['digest'], 'ARTIFACT_DIGEST_MISMATCH')
    archive = directory / 'frozen.zip'
    api.stream(f"/actions/artifacts/{config['artifact']}/zip", archive, 1500*1024*1024)
    def sha(path):
        h = hashlib.sha256()
        with path.open('rb') as f:
            while chunk := f.read(1024*1024): h.update(chunk)
        return h.hexdigest()
    require('sha256:' + sha(archive) == config['digest'], 'DOWNLOADED_ARTIFACT_DIGEST_MISMATCH')
    wanted = ('porota-frozen-candidate.json', 'porota-deploy-bundle-v2-manifest.json',
              'porota-predeploy-image.tar.gz', 'porota-deploy-bundle-v2.tgz')
    with zipfile.ZipFile(archive) as z:
        for name in wanted:
            members = [i for i in z.infolist() if Path(i.filename).name == name and not i.is_dir()]
            require(len(members) == 1 and members[0].file_size <= 1500*1024*1024, 'ARTIFACT_MEMBER_INVALID:' + name)
            with z.open(members[0]) as src, (directory/name).open('wb') as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
    frozen = json.loads((directory/wanted[0]).read_text())
    manifest = json.loads((directory/wanted[1]).read_text())
    require(frozen['candidate_sha'] == config['candidate'] and frozen['candidate_tree_sha'] == config['tree'], 'FROZEN_IDENTITY_MISMATCH')
    require(frozen['build_once'] is True and frozen['paper_mode_required'] == 'PRODUCTION_PAPER'
            and type(frozen['real_orders_sent_required']) is int and frozen['real_orders_sent_required'] == 0,
            'FROZEN_SAFETY_MISMATCH')
    require(frozen['image_tar_sha256'] == sha(directory/wanted[2]), 'IMAGE_TAR_MISMATCH')
    require(manifest['status'] == 'GREEN' and manifest['bundle_sha256'] == sha(directory/wanted[3]), 'BUNDLE_MISMATCH')
    config.update(image_tar_sha=frozen['image_tar_sha256'],
                  frozen_sha256=sha(directory/wanted[0]), manifest_sha256=sha(directory/wanted[1]))
    return frozen


REMOTE_PROBE = r'''
import base64,hashlib,json,os,sqlite3,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
c=json.loads(base64.b64decode(sys.argv[1])); root=Path('/opt/porota-trading')
def check(v,m):
 if not v: raise RuntimeError(m)
def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def cmd(a): return subprocess.check_output(a,text=True,timeout=20).strip()
state=read(root/'data/deploy/CURRENT_STATE_V2.json')
check(state['candidate_sha']==c['candidate'] and state['deploy_sha']==c['product'],'RUNTIME_PROVENANCE_DRIFT')
check(state['image_id']==c['runtime_image'],'RUNTIME_IMAGE_DRIFT')
expected='VALIDATED_RUNTIME' if c.get('final_probe') else 'DEPLOYED_VALIDATION_PENDING'
check(state.get('validation_status')==expected,'UNEXPECTED_VALIDATION_STATE')
check(state.get('mode')=='PRODUCTION_PAPER' and type(state.get('real_orders_sent')) is int and state['real_orders_sent']==0,'MODE_ORDERS')
check(state.get('real_order_routes')=='NOT_CALLED' and state.get('build_once') is True,'SAFETY_PROVENANCE')
check(sha(root/'data/deploy/porota-frozen-candidate.json')==c['frozen_sha256'],'FROZEN_METADATA_DRIFT')
m=root/'data/deploy/porota-deploy-bundle-v2-manifest.json'
check(sha(m)==c['manifest_sha256'],'BUNDLE_MANIFEST_DRIFT')
manifest=read(m);check(manifest.get('status')=='GREEN' and manifest.get('files'),'MANIFEST_NOT_GREEN')
for item in manifest['files']:
 p=Path(item['path']); check(not p.is_absolute() and '..' not in p.parts,'UNSAFE_MANIFEST_PATH')
 f=root/p;check(f.is_file() and not f.is_symlink() and sha(f)==item['sha256'],'INSTALLED_FILE_DRIFT:'+str(p))
containers={}
for name in ('porota_production_observer','porota_production_dashboard','porota_critical_approval_rc6'):
 info=json.loads(cmd(['docker','inspect',name]))[0]
 s=info['State'];check(s['Status']=='running' and not s['OOMKilled'] and info['RestartCount']==0,'CONTAINER_NOT_STABLE:'+name)
 if name!='porota_critical_approval_rc6' or c.get('final_probe'):
  check(info['Image']==c['runtime_image'],'CONTAINER_IMAGE_DRIFT:'+name)
 if name=='porota_critical_approval_rc6': check(s.get('Health',{}).get('Status')=='healthy','CRITICAL_NOT_HEALTHY')
 if name!='porota_critical_approval_rc6':
  for mount in info.get('Mounts',[]):
   dest=mount['Destination']
   check(not (dest=='/app' or (dest.startswith('/app/') and not (dest=='/app/data' or dest.startswith('/app/data/')))),'APP_SOURCE_MOUNT')
 containers[name]={'image_id':info['Image'],'state':s['Status'],'restarts':info['RestartCount'],'oom_killed':s['OOMKilled'],'health':s.get('Health',{}).get('Status')}
check(cmd(['docker','image','inspect','-f','{{.Id}}','porota-trading-bot:17.0.0-rc6'])==c['runtime_image'],'STABLE_TAG_DRIFT')
units=cmd(['systemctl','list-unit-files','--no-legend'])
ppi=[line.split()[0] for line in units.splitlines() if line.split() and 'ppi' in line.split()[0].lower() and 'watch' in line.split()[0].lower()]
check(ppi==[],'PPI_WATCH_INVENTORY_DRIFT')
base=root/'data/deploy/zero-known-error-immediate.json'; baseline=read(base)
check(baseline.get('status')=='GREEN' and baseline.get('label')=='immediate','ORIGINAL_AUDIT_MISSING')
check(baseline.get('schema')=='porota-rc6-zero-known-error-runtime-audit-v2','AUDIT_SCHEMA')
stamp=datetime.fromisoformat(baseline['generated_at'].replace('Z','+00:00'))
check(datetime.fromisoformat(c['original_started'].replace('Z','+00:00'))<=stamp<=datetime.fromisoformat(c['original_finished'].replace('Z','+00:00')),'ORIGINAL_BASELINE_TIMESTAMP')
db=sqlite3.connect(f'file:{root}/data/paper_v17/observer_v17.db?mode=ro',uri=True,timeout=2)
db.execute('PRAGMA query_only=ON'); db.execute('PRAGMA busy_timeout=1000')
deadline=time.monotonic()+8;db.set_progress_handler(lambda:int(time.monotonic()>deadline),10000)
mode,orders,heartbeat=db.execute('SELECT mode,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone()
check(mode=='PRODUCTION_PAPER' and orders==0,'LIVE_MODE_ORDERS')
heartbeat=datetime.fromisoformat(heartbeat.replace('Z','+00:00'))
check(heartbeat.tzinfo and 0<=(datetime.now(timezone.utc)-heartbeat).total_seconds()<=300,'OBSERVER_HEARTBEAT')
rows=db.execute("SELECT upper(instrument_type),SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE' THEN 1 ELSE 0 END) FROM candidate_identity_v2 GROUP BY upper(instrument_type)").fetchall();db.close()
ready={name:int(n or 0) for name,n in rows}
check(sum(ready.values())>=baseline['database']['readiness']['ready'],'READINESS_REGRESSION')
print(json.dumps({'status':'GREEN','validation_state':state,'installed_files_checked':len(manifest['files']),
 'containers':containers,'ppi_watch_units':ppi,'mode':mode,'real_orders_sent':orders,'ready':sum(ready.values()),
 'ready_by_family':ready,'baseline_sha256':sha(base),'free_bytes':__import__('shutil').disk_usage('/').free,
 'observed_at':datetime.now(timezone.utc).isoformat()},sort_keys=True))
'''


def ssh_command():
    key = str(Path.home()/'.ssh/porota_rc6_resume')
    return ['ssh', '-i', key, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
            '-o', 'ConnectionAttempts=2', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4',
            '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=' + str(Path.home()/'.ssh/known_hosts')]


def probe(config, directory):
    encoded = base64.b64encode(json.dumps(config).encode()).decode()
    result = output(ssh_command()+[os.environ['DO_USER']+'@'+os.environ['DO_HOST'],
                    'sudo -n timeout 45 python3 - ' + shlex.quote(encoded)], input=REMOTE_PROBE, timeout=90)
    data=json.loads(result)
    (directory/('final-runtime.json' if config.get('final_probe') else 'preflight-runtime.json')).write_text(json.dumps(data,indent=2)+'\n')
    print('RESUME_RUNTIME_' + ('FINAL' if config.get('final_probe') else 'PREFLIGHT') + '=' + result,flush=True)
    return data


def make_remote_script(source, config):
    patched=patch_workflow(source)
    start='          for phase in T_MINUS_45 T_MINUS_10; do\n'
    require(patched.count(start)==1,'TAIL_START_AMBIGUOUS')
    after=patched.split(start,1)[1]
    require(after.count('\n          REMOTE\n')==1,'TAIL_END_AMBIGUOUS')
    raw=start+after.split('\n          REMOTE\n',1)[0]+'\n'
    require(all(not line.strip() or line.startswith('          ') for line in raw.splitlines()),'TAIL_INDENTATION')
    tail='\n'.join(line[10:] if line.startswith('          ') else line for line in raw.splitlines())+'\n'
    tail=tail.replace('"$REPO/scripts/rc6_deploy_preopen_gate.py"','"$REMOTE_DIR/rc6_deploy_preopen_gate.py"')
    state_start='sudo -n python3 - "$REPO/data/deploy/CURRENT_STATE_V2.json"'
    require(tail.count(state_start)==1,'STATE_WRITER_NOT_UNIQUE')
    i=tail.index(state_start); j=tail.index('\nPY\n',i)+4
    state_block=tail[i:j]
    tail=tail[:i]+tail[j:]
    require(state_block.count('import os,tempfile')==1,'STATE_WRITE_ANCHOR')
    state_block=state_block.replace('import os,tempfile',
        'payload.update('+repr({'deployment_run_id':config['failed_run'],
                               'validation_resume_run_id':int(os.environ['GITHUB_RUN_ID']),
                               'predeploy_run_id':config['predeploy_run'],
                               'predeploy_artifact_id':config['artifact'],
                               'predeploy_artifact_digest':config['digest'],
                               'validation_resume_reason':'PREOPEN_NOT_DUE_WRAPPER_FALSE_RED'})+')\nimport os,tempfile')
    success='echo "RC6_DEPLOY_V2=GREEN|candidate=$CANDIDATE_SHA|deploy=$DEPLOY_SHA|image=$RUNTIME_IMAGE_ID|expected_image=$EXPECTED_IMAGE_ID"\n'
    require(tail.count(success)==1,'SUCCESS_ANCHOR')
    tail=tail.replace(success,'')
    forbidden=('docker load','docker build ', 'porota_mode_manager.py','docker stop porota_production_',
               'docker start porota_production_', 'ck_contract_evidence_runner_hf6.py', 'git reset', 'PRAGMA quick_check')
    require(not any(word in tail for word in forbidden),'REPLAY_WOULD_REPROMOTE_OR_MUTATE_TRADING')
    values={'REPO':'/opt/porota-trading','REMOTE_DIR':config['remote_dir'],
            'CANDIDATE_SHA':config['candidate'],'CANDIDATE_TREE':config['tree'],
            'DEPLOY_SHA':config['product'],'RUNTIME_IMAGE_ID':config['runtime_image'],
            'EXPECTED_IMAGE_ID':config['expected_image'],'IMAGE_TAR_SHA':config['image_tar_sha'],
            'STABLE_IMAGE':'porota-trading-bot:17.0.0-rc6',
            'FROZEN_IMAGE':'porota-predeploy-v2:'+config['candidate'],
            'POST_STATE':'STATE_FAST|PRODUCTION_PAPER|0',
            'OBSERVER_RESTART_BASE':'0','DASHBOARD_RESTART_BASE':'0','PPI_WATCH_BEFORE':'',
            'AUDIT_BASE':'/app/data/deploy/zero-known-error-immediate.json'}
    prefix='set -Eeuo pipefail\n'+'\n'.join(k+'='+shlex.quote(v) for k,v in values.items())+'\n'
    prefix+='cd "$REPO"\n'
    prefix+='DISK_BEFORE="$(df -PB1 / | awk \'NR==2 {print $4}\')"\n'
    # Repeat full current read-only audit against ORIGINAL baseline before tail.
    prefix+='sudo -n docker exec porota_production_dashboard timeout 300 python /app/scripts/rc6_zero_known_error_runtime_audit.py --label resume-immediate --baseline "$AUDIT_BASE" --output /app/data/deploy/zero-known-error-resume-immediate.json\n'
    return prefix+tail+'\n# Only all successful tail checks may certify runtime.\n'+state_block+'\necho "RC6_DEPLOY_V2_VALIDATION_RESUME=GREEN"\n'


def publish_fix(source, directory):
    target=directory/'fix-worktree'
    branch_ref='refs/heads/'+FIX_BRANCH
    exists=subprocess.run(['git','ls-remote','--exit-code','origin',branch_ref],text=True,capture_output=True)
    require(exists.returncode==2,'FIX_BRANCH_EXISTS_OR_REMOTE_ERROR')
    config=json.loads((directory/'resume-config.json').read_text())
    run(['git','worktree','add','--detach',str(target),config['product']])
    for name in ('scripts/rc6_deploy_preopen_gate.py','tests/test_rc6_deploy_preopen_gate.py'):
        (target/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target/name)
    (target/WORKFLOW).write_text(patch_workflow(source))
    run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_rc6_deploy_preopen_gate.py','-v'],cwd=target)
    run(['git','diff','--check'],cwd=target)
    paths=[WORKFLOW,'scripts/rc6_deploy_preopen_gate.py','tests/test_rc6_deploy_preopen_gate.py']
    run(['git','add','--',*paths],cwd=target)
    require(set(output(['git','diff','--cached','--name-only'],cwd=target).splitlines())==set(paths),'FIX_SCOPE_DRIFT')
    run(['git','-c','user.name=Porota Actions','-c','user.email=151845499+mbalbo2023@users.noreply.github.com',
         'commit','-m','fix(deploy): validate NOT_DUE with calendar and fail-closed preopen contract'],cwd=target)
    run(['git','push','origin','HEAD:'+branch_ref],cwd=target)
    sha=output(['git','rev-parse','HEAD'],cwd=target)
    (directory/'permanent-fix.json').write_text(json.dumps({'branch':FIX_BRANCH,'sha':sha,'base':config['product'],'scope':paths},indent=2)+'\n')
    print('PERMANENT_FIX_COMMITTED='+sha,flush=True)


def prepare(args):
    directory=Path(args.directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    config={k:getattr(args,k) for k in ('product','candidate','tree','failed_run','predeploy_run','artifact','digest','tracker','ownership_comment')}
    for key in ('product','candidate','tree'):
        require(re.fullmatch('[0-9a-f]{40}',config[key]),'INVALID_SHA')
    require(re.fullmatch('sha256:[0-9a-f]{64}',config['digest']),'INVALID_DIGEST')
    api=GitHubAPI(os.environ['GITHUB_REPOSITORY']);check_control(api,config)
    original=api.get('/actions/runs/'+str(config['failed_run']))
    require(original['status']=='completed' and original['conclusion']=='failure' and original['head_sha']==config['product'] and original['path']==WORKFLOW,'ORIGINAL_RUN_MISMATCH')
    commit=api.get('/git/commits/'+config['product']);require(len(commit['parents'])==2 and commit['parents'][1]['sha']==config['candidate'] and commit['tree']['sha']==config['tree'],'MERGE_IDENTITY_MISMATCH')
    require(api.get('/git/commits/'+config['candidate'])['tree']['sha']==config['tree'],'CANDIDATE_TREE_MISMATCH')
    jobs=api.get(f"/actions/runs/{config['failed_run']}/jobs?per_page=100")['jobs']
    jobs=[j for j in jobs if j['name']=='promote' and j['conclusion']=='failure'];require(len(jobs)==1,'FAILED_JOB_NOT_UNIQUE')
    failures=[s['name'] for s in jobs[0]['steps'] if s.get('conclusion')=='failure']
    require(failures==['Promote exact frozen candidate and verify'],'UNSUPPORTED_FAILED_STEP')
    logfile=directory/'original-job.log';api.stream('/actions/jobs/'+str(jobs[0]['id'])+'/logs',logfile,8*1024*1024)
    expected,runtime=validate_failure(logfile.read_text())
    config.update(expected_image=expected,runtime_image=runtime,original_started=original['run_started_at'],original_finished=original['updated_at'],remote_dir='/tmp/porota-deploy-v2-resume-'+os.environ['GITHUB_RUN_ID'])
    source=run(['git','show',config['product']+':'+WORKFLOW],capture_output=True).stdout
    patch_workflow(source)
    frozen_material(api,config,directory)
    require(json.loads((directory/'porota-frozen-candidate.json').read_text())['image_id']==expected,'EXPECTED_IMAGE_MISMATCH')
    script=make_remote_script(source,config)
    (directory/'resume.sh').write_text(script)
    run(['bash','-n',str(directory/'resume.sh')])
    (directory/'resume-config.json').write_text(json.dumps(config,indent=2)+'\n')
    publish_fix(source,directory)
    print('RESUME_PREPARE=GREEN|no_host_mutation=true',flush=True)


def finalize(args):
    directory=Path(args.directory).resolve();config=json.loads((directory/'resume-config.json').read_text())
    api=GitHubAPI(os.environ['GITHUB_REPOSITORY']);check_control(api,config)
    probe(config,directory)
    check_control(api,config)
    host=os.environ['DO_USER']+'@'+os.environ['DO_HOST'];ssh=ssh_command()
    run(ssh+[host,'test ! -e '+shlex.quote(config['remote_dir'])+' && mkdir -m 700 '+shlex.quote(config['remote_dir'])],timeout=60)
    scp=['scp','-i',str(Path.home()/'.ssh/porota_rc6_resume'),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=15']
    for path in (directory/'porota-frozen-candidate.json',directory/'porota-deploy-bundle-v2-manifest.json',ROOT/'scripts/rc6_deploy_preopen_gate.py'):
        run(scp+[str(path),host+':'+config['remote_dir']+'/'+path.name],timeout=60)
    with (directory/'resume.sh').open() as source, (directory/'resume-runtime.log').open('w') as log:
        p=subprocess.Popen(ssh+[host,'timeout --signal=TERM --kill-after=30 1800 bash -s'],stdin=source,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in p.stdout:
            print(line,end='',flush=True);log.write(line);log.flush()
        rc=p.wait()
    require(rc==0,'RESUME_RUNTIME_FAILED_RC='+str(rc))
    config['final_probe']=True;final=probe(config,directory)
    require(final['validation_state'].get('validation_resume_run_id')==int(os.environ['GITHUB_RUN_ID']),'FINAL_RESUME_RUN_MISMATCH')
    print('RESUME_FINAL=VALIDADO_RUNTIME|same_artifact=true|no_rebuild=true|no_repromotion=true',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','finalize'])
    parser.add_argument('--directory',default='/tmp/rc6-validation-resume')
    for name in ('product','candidate','tree','digest'):
        parser.add_argument('--'+name)
    for name in ('failed_run','predeploy_run','artifact','tracker','ownership_comment'):
        parser.add_argument('--'+name.replace('_','-'),type=int)
    args=parser.parse_args()
    if args.mode=='prepare': prepare(args)
    else: finalize(args)

if __name__=='__main__':
    main()
