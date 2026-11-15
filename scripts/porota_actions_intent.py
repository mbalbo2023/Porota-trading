#!/usr/bin/env python3
"""Authenticate an exact #624 owner action and persist rootless PAPER intent."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from scripts import porota_actions_host as host
from scripts import porota_actions_release as release
from scripts.porota_predeploy_impact import classify

PRODUCT = 'deploy/rc6-pr69-isolated-20260915'
OWNER = 'mbalbo2023'
STATUS = {'pause': 'PAPER_PAUSED_BY_OPERATOR', 'resume': 'PAPER_REARM_AUTHORIZED'}
require = host.require


def dispatch_identity(env):
    require(env.get('GITHUB_REPOSITORY') == release.REPOSITORY
            and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and env.get('GITHUB_REF') == 'refs/heads/' + PRODUCT
            and env.get('GITHUB_ACTOR') == env.get('GITHUB_TRIGGERING_ACTOR') == OWNER,
            'OWNER_PRODUCT_DISPATCH_REQUIRED')
    require(isinstance(env.get('GITHUB_SHA'), str)
            and re.fullmatch(r'[0-9a-f]{40}', env['GITHUB_SHA'])
            and isinstance(env.get('GITHUB_RUN_ID'), str)
            and re.fullmatch(r'[1-9][0-9]*', env['GITHUB_RUN_ID']), 'DISPATCH_IDENTITY_INVALID')
    return env['GITHUB_SHA'], int(env['GITHUB_RUN_ID'])


def timestamp(value):
    require(isinstance(value, str) and bool(value), 'GITHUB_TIMESTAMP_INVALID')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(moment.tzinfo is not None, 'GITHUB_TIMESTAMP_INVALID')
    return moment


def validate_runtime_source(repository, source, tree, deployed, product):
    if deployed == product:
        require(release.promotion_source(repository, source) == (source, tree),
                'OWNER_COMMENT_SCOPE_MISMATCH')
        return
    require(release.git(repository, 'rev-parse', source + '^{tree}') == tree
            and release.git(repository, 'rev-parse', deployed + '^{tree}') == tree,
            'RUNTIME_RELEASE_SOURCE_MISMATCH')
    require(release.git(repository, 'merge-base', source, deployed) == source
            and release.git(repository, 'merge-base', deployed, product) == deployed,
            'RUNTIME_NOT_ANCESTOR_PRODUCT')
    impact = classify(repository, deployed, product)
    require(impact['classification'] == 'DOCUMENTATION_ONLY'
            and impact['documentation_validated'] is True, 'RUNTIME_HAS_APPLICATION_CHANGES')


def authorize(operation, comment_id, repository, *, env=None):
    env = os.environ if env is None else env
    product, run_id = dispatch_identity(env)
    require(operation in STATUS and type(comment_id) is int and comment_id > 0, 'OWNER_ACTION_INVALID')
    current = release.api('/git/ref/heads/' + PRODUCT)
    require(current.get('object', {}).get('sha') == product, 'SOURCE_NOT_CURRENT_PRODUCT')
    require(release.git(repository, 'rev-parse', 'HEAD') == product, 'CHECKOUT_NOT_CURRENT_PRODUCT')
    comment = release.api('/issues/comments/' + str(comment_id))
    require(type(comment.get('id')) is int and comment['id'] == comment_id
            and comment.get('issue_url') == 'https://api.github.com/repos/' + release.REPOSITORY + '/issues/624'
            and (comment.get('user') or {}).get('login') == OWNER
            and comment.get('author_association') == 'OWNER'
            and comment.get('created_at') == comment.get('updated_at'), 'OWNER_COMMENT_INVALID')
    issued = timestamp(comment.get('created_at'))
    prefix = 'RC6_624_PAUSE_PAPER' if operation == 'pause' else 'RC6_624_APPROVE_PAPER_REARM'
    body = comment.get('body')
    require(isinstance(body, str), 'OWNER_COMMENT_SCOPE_MISMATCH')
    runtime_run = runtime_attempt = None
    if operation == 'pause':
        source, tree = release.promotion_source(repository)
        expected = f'{prefix} candidate={source} tree={tree} deploy={product}'
        require(body.strip() == expected, 'OWNER_COMMENT_SCOPE_MISMATCH')
    else:
        match = re.fullmatch(prefix + r' candidate=([0-9a-f]{40}) tree=([0-9a-f]{40}) deploy='
                             + product + r' runtime_run=([1-9][0-9]{0,15})', body.strip())
        require(match is not None, 'OWNER_COMMENT_SCOPE_MISMATCH')
        source, tree = match.group(1), match.group(2)
        runtime_run = int(match.group(3))
        runtime = release.api('/actions/runs/' + str(runtime_run))
        require(type(runtime.get('id')) is int and runtime['id'] == runtime_run
                and type(runtime.get('run_attempt')) is int and runtime['run_attempt'] > 0
                and runtime.get('status') == 'completed' and runtime.get('conclusion') == 'success'
                and isinstance(runtime.get('head_sha'), str)
                and re.fullmatch(r'[0-9a-f]{40}', runtime['head_sha'])
                and runtime.get('path') == '.github/workflows/porota-deploy-v2-promote.yml'
                and runtime.get('event') in {'push', 'workflow_dispatch'}
                and (runtime.get('head_repository') or {}).get('full_name') == release.REPOSITORY
                and (runtime.get('actor') or {}).get('login') == OWNER
                and (runtime.get('triggering_actor') or {}).get('login') == OWNER
                and timestamp(runtime.get('updated_at')) < issued, 'CANONICAL_RUNTIME_NOT_PROVEN')
        validate_runtime_source(repository, source, tree, runtime['head_sha'], product)
        runtime_attempt = runtime['run_attempt']
    return {'schema': 'porota.operator.authorization.v1', 'status': 'AUTHORIZED',
            'operation': operation, 'source_sha': source, 'source_tree': tree, 'product_sha': product,
            'operator_comment_id': comment_id, 'run_id': run_id,
            'runtime_run': runtime_run, 'runtime_attempt': runtime_attempt}


def validate_result(result, authorization, returncode):
    require(returncode == 0, 'REMOTE_INTENT_COMMAND_FAILED')
    require(set(result) <= {'status', 'operator_comment_id', 'run_id', 'idempotent'}
            and result.get('status') == STATUS[authorization['operation']]
            and type(result.get('operator_comment_id')) is int
            and result['operator_comment_id'] == authorization['operator_comment_id']
            and type(result.get('run_id')) is int and result['run_id'] > 0
            and (result['run_id'] == authorization['run_id'] or
                 result.get('idempotent') is True and result['run_id'] < authorization['run_id']),
            'PERSISTED_INTENT_NOT_VERIFIED')


def apply(authorization, repository, *, env=None):
    env = os.environ if env is None else env
    product, run_id = dispatch_identity(env)
    require(authorization.get('schema') == 'porota.operator.authorization.v1'
            and authorization.get('status') == 'AUTHORIZED'
            and authorization.get('product_sha') == product
            and authorization.get('run_id') == run_id, 'AUTHORIZATION_RUN_MISMATCH')
    result = {'schema': 'porota.operator.result.v1', 'operation': authorization['operation'],
              'source_sha': authorization['source_sha'], 'source_tree': authorization['source_tree'],
              'operator_comment_id': authorization['operator_comment_id'], 'actions_run_id': run_id,
              'runtime': 'NO_VERIFICADO', 'intent_status': 'NO_VERIFICADO'}
    with tempfile.TemporaryDirectory(prefix='porota-intent-') as temporary:
        source = Path(temporary) / 'source'
        (source / 'scripts').mkdir(parents=True)
        for name in ('porota_operator_intent.py', 'rc6_operator_paper_rearm.py', 'porota_rootless_host.py'):
            shutil.copyfile(Path(repository) / 'scripts' / name, source / 'scripts' / name)
        required = sum(path.stat().st_size for path in source.rglob('*') if path.is_file()) + 65536
        with host.connection(env) as (ssh, scp, target):
            created = host.ssh_run(ssh, ['python3', '-I', '-B', '-c', host.STAGE_CREATE,
                                         str(required), 'staging-only'])
            require(created.returncode == 0, 'INTENT_STAGING_FAILED')
            stage = created.stdout.decode().strip()
            host.validate_stage(stage)
            started = finished = False
            try:
                sent = subprocess.run([*scp, '-r', str(source), target + ':' + stage + '/'],
                                      capture_output=True, timeout=60)
                require(sent.returncode == 0, 'INTENT_TRANSFER_FAILED')
                command = ['/usr/bin/env', 'POROTA_RUNTIME_BACKEND=ROOTLESS', 'python3', '-B',
                           stage + '/source/scripts/porota_operator_intent.py',
                           authorization['operation'], '--candidate-sha', authorization['source_sha'],
                           '--source-tree', authorization['source_tree'],
                           '--comment-id', str(authorization['operator_comment_id']), '--run-id', str(run_id)]
                if authorization['operation'] == 'resume':
                    command += ['--runtime-run', str(authorization['runtime_run']),
                                '--runtime-attempt', str(authorization['runtime_attempt'])]
                started = True
                try:
                    remote = host.ssh_run(ssh, command, timeout=90)
                    require(remote.returncode != 255, 'SSH_TRANSPORT_FAILED')
                except ValueError as exc:
                    result.update(staging_name=Path(stage).name,
                                  transfer_cleanup='RETAINED_COMMAND_OUTCOME_UNKNOWN')
                    raise host.HostFailure('INTENT_COMMAND_OUTCOME_UNKNOWN', result) from exc
                finished = True
                observed = host.decode_receipt(remote.stdout)
                validate_result(observed, authorization, remote.returncode)
                result.update(intent_status=observed['status'], persisted_run_id=observed['run_id'],
                              idempotent=observed.get('idempotent') is True)
                return result
            finally:
                if not (started and not finished):
                    try:
                        cleaned = host.ssh_run(ssh, ['python3', '-I', '-B', '-c', host.STAGE_REMOVE, stage])
                        require(cleaned.returncode == 0, 'INTENT_CLEANUP_INCOMPLETE')
                    except ValueError as exc:
                        result['transfer_cleanup'] = 'INCOMPLETE'
                        raise host.HostFailure('INTENT_CLEANUP_INCOMPLETE', result) from exc
                    result['transfer_cleanup'] = 'COMPLETE'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('authorize', 'apply'))
    parser.add_argument('--operation', choices=('pause', 'resume'), required=True)
    parser.add_argument('--comment-id', type=int, required=True)
    parser.add_argument('--repository', type=Path, default=Path.cwd())
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    try:
        # Recheck current product and unedited comment immediately before SSH;
        # a prior successful step is not a reusable authorization document.
        result = authorize(args.operation, args.comment_id, args.repository)
        if args.phase == 'apply':
            result = apply(result, args.repository)
        code = 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r'[A-Z_]+', str(exc)) else type(exc).__name__
        result = dict(exc.receipt) if isinstance(exc, host.HostFailure) else {}
        result.update(state='BLOQUEADO', reason=reason)
        code = 1
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(result, sort_keys=True) + '\n')
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
