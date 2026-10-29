#!/usr/bin/env python3
"""Bind a read-only A175 probe to one successful frozen Predeploy artifact.

Writes only runner-local evidence and stdin source. Never transfers an image,
starts a service, or runs a mutating host command. The workflow checks out the
candidate separately and verifies its Git object identity before invoking us.
"""
import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import urllib.request
from urllib.parse import urlsplit
import zipfile

NAMES = frozenset({'porota-frozen-candidate.json',
                   'porota-predeploy-image.tar.gz',
                   'porota-deploy-bundle-v2.tgz',
                   'porota-deploy-bundle-v2-manifest.json',
                   'porota-host-manifest-v2.json',
                   'porota-host-policy-v2.json'})
SHA = re.compile(r'[0-9a-f]{40}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')


class ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward the GitHub bearer token to the artifact blob host."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urlsplit(req.full_url)
        target = urlsplit(newurl)
        host = (target.hostname or '').lower()
        if (source.hostname != 'api.github.com' or
                not source.path.startswith('/repos/') or
                not source.path.endswith('/zip') or
                code not in (302, 303, 307) or
                target.scheme != 'https' or
                target.port not in (None, 443) or
                target.username is not None or target.password is not None or
                not host.endswith('.blob.core.windows.net')):
            raise ValueError('ARTIFACT_REDIRECT_ORIGIN_RED')
        return urllib.request.Request(newurl, headers={'Accept':'application/octet-stream'},
                                      method='GET')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def api(url, token, *, dest=None):
    if not url.startswith('https://api.github.com/repos/'):
        raise ValueError('API_URL_RED')
    req = urllib.request.Request(url,headers={
        'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'X-GitHub-Api-Version':'2022-11-28'})
    opener = urllib.request.build_opener(ArtifactRedirect()) if dest is not None else urllib.request.build_opener()
    with opener.open(req,timeout=30) as response:
        if dest is None:
            return json.load(response)
        with Path(dest).open('wb') as output:
            shutil.copyfileobj(response,output,1024*1024)


def extract_exact(zip_path, destination):
    mapping = {}
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('FROZEN_ARCHIVE_MEMBERS_RED')
            if member.is_dir() or path.name not in NAMES:
                continue  # Governed-test receipts and sizing evidence coexist.
            if path.name in mapping or member.file_size > 3*1024**3:
                raise ValueError('FROZEN_ARCHIVE_MEMBERS_RED')
            mapping[path.name] = member
        if set(mapping) != NAMES:
            raise ValueError('FROZEN_ARCHIVE_CONTENTS_RED')
        for name, member in mapping.items():
            with archive.open(member) as source, (destination/name).open('wb') as target:
                shutil.copyfileobj(source,target,1024*1024)


def prepare(candidate, tree, run_id, artifact_id, repository, token,
            candidate_root, diagnostic_root, out_dir):
    if (not SHA.fullmatch(candidate) or not SHA.fullmatch(tree) or
            not run_id.isdecimal() or not artifact_id.isdecimal() or
            not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository)):
        raise ValueError('EXACT_INPUT_RED')
    base=f'https://api.github.com/repos/{repository}/actions'
    run=api(f'{base}/runs/{run_id}',token)
    artifact=api(f'{base}/artifacts/{artifact_id}',token)
    expected_name=f'porota-predeploy-v2-{candidate}'
    if (run.get('name') != 'Porota Predeploy V2' or
            run.get('head_sha') != candidate or run.get('conclusion') != 'success' or
            artifact.get('name') != expected_name or artifact.get('expired') is not False or
            str((artifact.get('workflow_run') or {}).get('id')) != run_id or
            artifact.get('digest','') != artifact.get('digest','').lower() or
            not re.fullmatch(r'sha256:[0-9a-f]{64}',artifact.get('digest',''))):
        raise ValueError('EXACT_PREDEPLOY_ARTIFACT_RED')
    expected_digest = os.environ.get('RC6_PREDEPLOY_ARTIFACT_DIGEST')
    if (not isinstance(expected_digest, str) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', expected_digest) or
            artifact['digest'] != expected_digest):
        raise ValueError('PINNED_ARTIFACT_DIGEST_RED')
    out_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
    archive=out_dir/'artifact.zip'
    api(f'{base}/artifacts/{artifact_id}/zip',token,dest=archive)
    if digest(archive) != artifact['digest'].removeprefix('sha256:'):
        raise ValueError('PREDEPLOY_ARTIFACT_DIGEST_RED')
    extract_exact(archive,out_dir)
    frozen_raw=(out_dir/'porota-frozen-candidate.json').read_bytes()
    frozen=json.loads(frozen_raw)
    manifest=json.loads((out_dir/'porota-deploy-bundle-v2-manifest.json').read_bytes())
    host_manifest=json.loads((out_dir/'porota-host-manifest-v2.json').read_bytes())
    host_policy=json.loads((out_dir/'porota-host-policy-v2.json').read_bytes())
    if (frozen.get('candidate_sha') != candidate or
            frozen.get('candidate_tree_sha') != tree or
            frozen.get('paper_mode_required') != 'PRODUCTION_PAPER' or
            frozen.get('real_orders_sent_required') != 0 or
            frozen.get('real_order_capability_required') != 'BLOCKED' or
            frozen.get('build_once') is not True or
            not re.fullmatch(r'sha256:[0-9a-f]{64}',frozen.get('image_id','')) or
            type(frozen.get('image_size_bytes')) is not int or
            frozen['image_size_bytes'] <= 0 or
            not HASH.fullmatch(frozen.get('image_tar_sha256','')) or
            digest(out_dir/'porota-predeploy-image.tar.gz') != frozen['image_tar_sha256'] or
            digest(out_dir/'porota-deploy-bundle-v2.tgz') != manifest.get('bundle_sha256')):
        raise ValueError('FROZEN_IDENTITY_OR_HASH_RED')
    if (host_manifest.get('source_git_sha') != candidate or
            host_manifest.get('status') != 'GREEN' or
            host_policy.get('status') != 'GREEN'):
        raise ValueError('FROZEN_HOST_CONTROL_PLANE_RED')
    files={row.get('path'):row for row in manifest.get('files',[])}
    source_names=('scripts/rc6_deploy_quiescence.py',
                  'scripts/rc6_deploy_scheduler_suspend.py')
    disk_names=('scripts/rc6_disk_space_guard.py',
                'ops/policy/rc6-disk-housekeeping-v1.json')
    timer_policy_name='ops/policy/host-control-plane-reconciliation-v2.json'
    for name in (*source_names,*disk_names,timer_policy_name):
        source=candidate_root/name
        row=files.get(name,{})
        if (not source.is_file() or not HASH.fullmatch(row.get('sha256','')) or
                digest(source) != row['sha256'] or source.stat().st_size != row.get('bytes')):
            raise ValueError('FROZEN_STAGED_SOURCE_RED')
    spec=importlib.util.spec_from_file_location(
        'rc6_a160_candidate_disk_formula',candidate_root/disk_names[0])
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy_path=candidate_root/'ops/policy/rc6-disk-housekeeping-v1.json'
    timer_policy_path=candidate_root/timer_policy_name
    policy=module.load_policy(policy_path)
    required=module.required_pretransfer_free(
        (out_dir/'porota-predeploy-image.tar.gz').stat().st_size,
        frozen['image_size_bytes'],
        (out_dir/'porota-deploy-bundle-v2.tgz').stat().st_size,policy)
    prefix=[]
    for letter,name,bound in zip('QS',source_names,(230000,130000)):
        source=(candidate_root/name).read_bytes()
        compressed=gzip.compress(source,mtime=0)
        encoded=base64.b64encode(compressed).decode('ascii')
        if (not 20000 < len(source) < bound or
                not 0 < len(compressed) < 110000 or len(encoded)>180000):
            raise ValueError('STAGED_SOURCE_BOUND_RED')
        prefix.append(f'STAGED_{letter}_GZIP_B64={encoded!r}')
        prefix.append(f'STAGED_{letter}_SHA256={hashlib.sha256(source).hexdigest()!r}')
    host_script=diagnostic_root/'scripts/rc6_a178_running_preflight_ro.py'
    with (out_dir/'host-stdin.py').open('wb') as output:
        output.write(('\n'.join(prefix)+'\n').encode())
        output.write(host_script.read_bytes())
    # The runner needs these values as arguments, but uploads only aggregate
    # JSON receipts. No artifact data or source is sent to GitHub logs.
    env=Path(os.environ['GITHUB_ENV'])
    with env.open('a',encoding='utf-8') as stream:
        stream.write(f'RC6_A160_POLICY_B64={base64.b64encode(timer_policy_path.read_bytes()).decode("ascii")}\n')
        stream.write(f'RC6_A160_DISK_POLICY_B64={base64.b64encode(policy_path.read_bytes()).decode("ascii")}\n')
        stream.write(f'RC6_A160_REQUIRED_BYTES={required}\n')
    print(f'RC6_A178_ARTIFACT_BOUND=GREEN|candidate={candidate}|tree={tree}'
          f'|predeploy_run={run_id}|artifact_id={artifact_id}|artifact_digest={artifact["digest"]}'
          f'|frozen_manifest_sha256={hashlib.sha256(frozen_raw).hexdigest()}'
          f'|frozen_preload_image_id={frozen["image_id"]}'
          f'|image_tar_bytes={(out_dir/"porota-predeploy-image.tar.gz").stat().st_size}'
          f'|image_unpacked_bytes={frozen["image_size_bytes"]}'
          f'|bundle_bytes={(out_dir/"porota-deploy-bundle-v2.tgz").stat().st_size}'
          f'|required_pretransfer_bytes={required}')


if __name__ == '__main__':
    if len(sys.argv)!=8:
        raise SystemExit('EXPECTED_CANDIDATE_TREE_RUN_ARTIFACT_REPO_CANDIDATE_ROOT_DIAGNOSTIC_ROOT')
    prepare(*sys.argv[1:6],os.environ['GH_TOKEN'],
            Path(sys.argv[6]),Path(sys.argv[7]),Path(os.environ['RUNNER_TEMP'])/'rc6-a160')
