"""Small native prerequisites after an authentic positive economic Actions run.

This grants no material gate, backing allocation or production qualification.
Original captured controls remain evidence, never local cleanup authority.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import runpy
import sys
import zipfile

ROOT = Path(__file__).absolute().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import rc6_development_checks as development
from scripts import rc6_development_readout as readout
from scripts import rc6_material_pr_admission as admission
from scripts import rc6_controlled_governed_runner as governed
from scripts.rc6_actions_custody import Github, canonical, digest, require, verify_owner

PLAN = ROOT / 'ops/policy/rc6-native-prerequisites-v1.json'
WORKFLOW = '.github/workflows/rc6-unified-candidate-tests.yml'
ECONOMIC_JOB = 'Verify bounded development regressions on one exact Source'
NATIVE_JOB = 'Verify small native prerequisites after exact positive economic CI'


def literal_inventory(sha):
    inventory = {}
    for row in governed.git(ROOT,'ls-tree','-r','-z',sha).split(b'\0'):
        if not row:continue
        head,name = row.split(b'\t',1)
        mode,kind,blob = head.decode().split()
        require(kind == 'blob' and mode in ('100644','100755') and os.fsdecode(name) not in inventory,
            'NATIVE_PREREQUISITES_LITERAL_TREE_REQUIRED')
        inventory[os.fsdecode(name)] = (mode,blob)
    require(0 < len(inventory) <= governed.MAX_SOURCE_MEMBERS,'NATIVE_PREREQUISITES_FULL_INVENTORY_REQUIRED')
    return inventory


def verify_standard_run(github, run_id, *, sha, gate, job_name, completed, branch=None):
    run = github.request('/actions/runs/' + str(run_id) + '/attempts/1')
    require(run.get('id') == run_id and run.get('head_sha') == sha and run.get('run_attempt') == 1
        and run.get('event') == 'workflow_dispatch' and run.get('path') == WORKFLOW
        and run.get('actor', {}).get('login') == 'mbalbo2023'
        and run.get('repository', {}).get('full_name') == admission.REPO
        and run['repository'].get('id') == admission.REPO_ID
        and run.get('head_repository', {}).get('full_name') == admission.REPO
        and run['head_repository'].get('id') == admission.REPO_ID
        and run.get('display_title') == 'RC6 material ' + gate + ' @ ' + sha,
        'NATIVE_PREREQUISITES_CANONICAL_EXACT_RUN_REQUIRED')
    if branch is not None:
        require(run.get('head_branch') == branch,'NATIVE_PREREQUISITES_RUN_BRANCH_REBOUND')
    if completed:
        require(run.get('status') == 'completed' and run.get('conclusion') == 'success',
            'NATIVE_PREREQUISITES_POSITIVE_TERMINAL_RUN_REQUIRED')
    else:
        require(run.get('status') == 'in_progress' and run.get('conclusion') is None,
            'NATIVE_PREREQUISITES_CURRENT_LIVE_RUN_REQUIRED')
    jobs = github.request('/actions/runs/' + str(run_id) + '/attempts/1/jobs?per_page=100')
    require(type(jobs.get('jobs')) is list and len(jobs['jobs']) == jobs.get('total_count') <= 100,
        'NATIVE_PREREQUISITES_COMPLETE_JOB_SET_REQUIRED')
    selected = [row for row in jobs['jobs'] if row.get('name') == job_name]
    require(len(selected) == 1, 'NATIVE_PREREQUISITES_EXACT_NATIVE_JOB_REQUIRED')
    job = selected[0]
    require(job.get('run_attempt') == 1 and job.get('run_id') == run_id and job.get('labels') == ['ubuntu-24.04']
        and job.get('runner_group_name') == 'GitHub Actions'
        and (job.get('status') == 'completed' and job.get('conclusion') == 'success'
            if completed else job.get('status') == 'in_progress' and job.get('conclusion') is None),
        'NATIVE_PREREQUISITES_STANDARD_RUNNER_REQUIRED')
    return {'run_id':run_id, 'job_id':job['id'], 'runner_group_name':job['runner_group_name'],
        'labels':job['labels'], 'source_sha':sha, 'run_attempt':1}


def require_control_path(control):
    control = Path(control)
    require(control.is_absolute() and str(control) == os.path.abspath(control)
        and not control.is_relative_to(ROOT) and not ROOT.is_relative_to(control)
        and not any(path.is_symlink() for path in (control,*control.parents))
        and not control.exists(),
        'NATIVE_PREREQUISITES_CONTROL_OUTSIDE_SOURCE_REQUIRED')
    return control


def verify_native_branch(github, branch, *, sha, tree):
    require(type(branch) is str and re.fullmatch('[A-Za-z0-9_/.-]{1,160}',branch)
        and '..' not in branch and not branch.startswith('/'), 'NATIVE_PREREQUISITES_EXACT_OWN_BRANCH_REQUIRED')
    reference = github.request('/git/ref/heads/'+branch)
    commit = github.request('/git/commits/'+sha)
    require(reference.get('ref') == 'refs/heads/'+branch and reference.get('object',{}).get('sha') == sha
        and commit.get('sha') == sha and commit.get('tree',{}).get('sha') == tree,
        'NATIVE_PREREQUISITES_FRESH_BRANCH_OR_TREE_REBOUND')
    return {'branch':branch,'source_sha':sha,'source_tree':tree}


def verify_positive_economics(raw, origin, *, source_sha, source_tree, owner_session=None):
    require(origin.get('source_sha') == source_sha and origin.get('source_tree') == source_tree
        and origin.get('conclusion') == 'success', 'NATIVE_PREREQUISITES_SAME_SOURCE_ORIGIN_REQUIRED')
    summary = readout.inspect_sealed(raw, origin)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {item.filename:archive.read(item) for item in archive.infolist()}
    result = json.loads(files['result.json'])
    require(result.get('status') == 'PASS_DEVELOPMENT_ONLY' and result.get('fullSource_unchanged') is True
        and result.get('identities_equal') is True and result.get('real_orders_sent') == 0
        and result.get('plan_sha256') == digest((ROOT/'ops/policy/rc6-development-checks-v1.json').read_bytes())
        and [row.get('epoch') for row in result['epochs']] == ['311','312'],
        'NATIVE_PREREQUISITES_ACTUAL_DUAL_POSITIVE_REQUIRED')
    identities = []
    from scripts import rc6_heavy_test_preflight as preflight
    require(digest(development.lifecycle.DRIVER.read_bytes()) == development.lifecycle.DRIVER_SHA256,
        'NATIVE_PREREQUISITES_ORIGINAL_MANAGER_REBOUND')
    manager = runpy.run_path(str(development.lifecycle.DRIVER))
    for row in result['epochs']:
        require(row.get('passed') is True and row.get('retirement_error') is None
            and row.get('junit_validation_error') is None, 'NATIVE_PREREQUISITES_EPOCH_RED')
        kernel, cleanup = row.get('kernel', {}), row.get('cleanup') or {}
        require(manager['managed_phase_green'](kernel), 'NATIVE_PREREQUISITES_ACTUAL_FIN_REQUIRED')
        require(cleanup.get('actual_owned_fin_closed') is True and cleanup.get('namespace_removed') is True
            and cleanup.get('original_namespace_removed') is True
            and type(cleanup.get('foreign_paths_removed')) is int
            and cleanup['foreign_paths_removed'] == 0, 'NATIVE_PREREQUISITES_ORIGINAL_CLEANUP_REQUIRED')
        manifest_raw = files[row['epoch']+'/manifest.json']
        manifest = json.loads(manifest_raw)
        preflight.validate_binding(manifest['binding'])
        require(manifest['binding']['producer'] == 'DEVELOPMENT_CONTROLS_'+row['epoch']
            and manifest['binding']['attempt_id'] == str(origin['run_id'])+'-1'
            and manifest['binding']['workload_fingerprint'] == result['plan_sha256']
            and (owner_session is None or manifest['binding']['owner_id'] == owner_session),
            'NATIVE_PREREQUISITES_ORIGINAL_PRODUCER_REBOUND')
        require(cleanup.get('capture_manifest_sha256') == digest(manifest_raw)
            and cleanup.get('namespace_nonce') == manifest.get('namespace_nonce')
            and cleanup.get('binding') == manifest.get('binding')
            and manifest.get('phase_green') is True and manifest.get('owned_fin_kind') == 'NATIVE_WAIT4_OWNED_FIN'
            and manifest.get('kernel_sha256') == preflight.digest(kernel),
            'NATIVE_PREREQUISITES_ORIGINAL_FIN_MANIFEST_REBOUND')
        fin_matches = [item for item in manifest['files'] if item['relative_source'] ==
            'producer-owned-fin-development-'+row['epoch']+'.json']
        require(len(fin_matches) == 1, 'NATIVE_PREREQUISITES_ORIGINAL_FIN_CONTROL_REQUIRED')
        fin = json.loads(files[row['epoch']+'/'+fin_matches[0]['capture_file']])
        require(fin.get('schema') == 'porota.rc6.generated-fixture-owned-fin.v1'
            and fin.get('kernel') == kernel and fin.get('binding') == manifest['binding']
            and fin.get('namespace_nonce') == manifest['namespace_nonce']
            and fin.get('manager_sha256') == development.lifecycle.DRIVER_SHA256
            and fin.get('actual_owned_fin_closed') is fin.get('phase_green') is True
            and fin.get('global_or_other_producer_FIN_claimed') is False,
            'NATIVE_PREREQUISITES_ORIGINAL_FIN_CONTROL_REBOUND')
        matches = [item for item in manifest['files'] if item['relative_source'] == 'junit.xml']
        require(len(matches) == 1, 'NATIVE_PREREQUISITES_NATIVE_JUNIT_REQUIRED')
        facts = development.junit_facts(files[row['epoch']+'/'+matches[0]['capture_file']])
        require(not any(facts[key] for key in ('failures','errors','skipped'))
            and facts == {**row['junit'], 'identities':[tuple(item) for item in row['junit']['identities']]},
            'NATIVE_PREREQUISITES_JUNIT_FACTS_REBOUND')
        identities.append(facts['identities'])
    require(identities[0] == identities[1], 'NATIVE_PREREQUISITES_DUAL_IDENTITIES_CHANGED')
    before,after = (json.loads(files[name]) for name in ('source-before.index.json','source-after.index.json'))
    require(all(index.get('source_sha') == source_sha and index.get('source_tree') == source_tree
        and index.get('physical_namespace_exact_to_literal_tree') is True and index.get('overlay_count') == 0
        for index in (before,after)), 'NATIVE_PREREQUISITES_ORIGINAL_SOURCE_INDEX_REBOUND')
    expected = literal_inventory(source_sha)
    require(all(type(index.get('files')) is dict and set(index['files']) == set(expected)
        and all((record.get('git_mode'),record.get('git_blob')) == expected[name]
            for name,record in index['files'].items()) for index in (before,after)),
        'NATIVE_PREREQUISITES_FULL_LITERAL_SOURCE_INVENTORY_REBOUND')
    governed.compare_source(before,after)
    return summary


def prove_filter_in_child(*, output, source_sha, source_tree, owner_session, plan_sha256):
    """Keep irreversible NNP/seccomp in the owned NONROOT child, never issuer."""
    from scripts import rc6_native_namespace_filter as native_filter
    owned = development.lifecycle
    binding = {'candidate_sha':source_sha,'candidate_tree':source_tree,'producer':'NATIVE_FILTER_PREREQUISITES',
        'attempt_id':os.environ['GITHUB_RUN_ID']+'-1','owner_id':owner_session,'runner_class':'github-hosted/ubuntu-24.04',
        'workload_fingerprint':plan_sha256}
    namespace = owned.create_namespace(development.short_control_parent(ROOT),binding)
    command = [sys.executable,'-I','-B',str(ROOT/'scripts/rc6_native_namespace_filter.py'),'--probe',
        '--source-sha',source_sha,'--source-tree',source_tree,'--plan-sha256',plan_sha256,
        '--output',str(namespace.path/'filter')]
    environment = {key:os.environ[key] for key in ('PATH','LANG','LC_ALL') if key in os.environ}
    kernel,fin = owned.execute_owned(namespace,command,cwd=ROOT,environ=environment,
        log_relative='native-filter.log',timeout_seconds=30,fin_label='native-filter')
    required = ['native-filter.log','producer-owned-fin-native-filter.json']
    if kernel['returncode'] == 0:
        required.extend('filter/'+name for name in ('supervisor-filter.json','worker-native.json',
            'native-owned-fin.json','probe.json'))
    captured = owned.capture_required_evidence(namespace,fin,output,required)
    cleanup = owned.cleanup_namespace(namespace,fin,captured)
    require(owned._phase_green(fin), 'NATIVE_PREREQUISITES_FILTER_NATIVE_CHILD_RED')
    match = next(row for row in captured.files if row['relative_source'] == 'filter/probe.json')
    proof = json.loads((captured.path/match['capture_file']).read_bytes())
    source_binding = {'source_sha':source_sha,'source_tree':source_tree,'plan_sha256':plan_sha256}
    require(proof.get('status') == 'PASS_NATIVE_FILTER_ONLY' and proof.get('source_binding') == source_binding,
        'NATIVE_PREREQUISITES_ORIGINAL_FILTER_PROOF_REBOUND')
    native_filter.validate_inheritance_evidence(proof['worker'],proof['supervisor'],source_binding=source_binding)
    return {'proof':proof,'outer_kernel':kernel,'outer_cleanup':cleanup,
        'capture_manifest_sha256':captured.manifest_sha256,'irreversible_filter_installed_in_issuer':False}


def admit_native_prerequisites(args, source, control):
    require_control_path(control)
    require(Path(source) == ROOT and os.getuid() == os.geteuid() > 0
        and os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('GITHUB_REPOSITORY') == admission.REPO
        and os.environ.get('GITHUB_REPOSITORY_ID') == str(admission.REPO_ID)
        and os.environ.get('GITHUB_ACTOR') == 'mbalbo2023'
        and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
        and os.environ.get('GITHUB_RUN_ATTEMPT') == '1', 'NATIVE_PREREQUISITES_EXPLICIT_NONROOT_ACTIONS_REQUIRED')
    require(governed.git(ROOT,'rev-parse','HEAD').decode().strip() == args.source_sha
        and governed.git(ROOT,'rev-parse','HEAD^{tree}').decode().strip() == args.source_tree,
        'NATIVE_PREREQUISITES_FROZEN_SOURCE_REQUIRED')
    development.require_canonical_umask()
    event, custody = admission.actual_event('native-prerequisites')
    require(all(event['inputs'].get(key) == getattr(args,key) for key in
        ('source_sha','source_tree','owner_session','launch_receipt_url')),
        'NATIVE_PREREQUISITES_DISPATCH_SCOPE_REBOUND')
    github = Github(os.environ.get('GH_TOKEN'))
    plan_raw = PLAN.read_bytes()
    owner_check = lambda:verify_owner(github,args.launch_receipt_url,sha=args.source_sha,tree=args.source_tree,
        owner=args.owner_session,plan_sha256=digest(plan_raw),authorization_field='RC6_NATIVE_PREREQUISITES_AUTHORIZATION')
    owner_hash = owner_check()
    owner = github.request('/issues/comments/'+args.launch_receipt_url.rsplit('-',1)[1])
    fields = admission.fields(owner['body'])
    transfer = admission.administrative_anchors(args.owner_session,get=github.request)
    branch = admission.fields(transfer[471]['body']).get('BRANCH')
    require(fields.get('BRANCH') == fields.get('SOURCE_BRANCH') == branch
        and event.get('ref','').removeprefix('refs/heads/') == branch
        and os.environ.get('GITHUB_REF') == 'refs/heads/'+branch,
        'NATIVE_PREREQUISITES_TRANSFER_BRANCH_OR_EVENT_REBOUND')
    def check():
        receipt_hash = owner_check()
        verify_native_branch(github,branch,sha=args.source_sha,tree=args.source_tree)
        verify_standard_run(github,int(os.environ['GITHUB_RUN_ID']),sha=args.source_sha,
            gate='native-prerequisites',job_name=NATIVE_JOB,completed=False,branch=branch)
        return receipt_hash
    check()
    twin = github.request('/issues/comments/'+fields['OWNER_RECEIPT_473'])
    require(admission.fields(twin['body']).get('DEVELOPMENT_ORIGIN_JSON') == fields.get('DEVELOPMENT_ORIGIN_JSON')
        and fields.get('DEVELOPMENT_ORIGIN_JSON') is not None, 'NATIVE_PREREQUISITES_DUAL_EXACT_ORIGIN_REQUIRED')
    origin = admission.document(fields['DEVELOPMENT_ORIGIN_JSON'])
    require(type(origin) is dict and set(origin) == {'artifact_id','run_id','source_sha','source_tree',
        'bytes','sha256','conclusion'} and all(type(origin[key]) is int and origin[key] > 0
            for key in ('artifact_id','run_id','bytes')) and origin['bytes'] <= 8*1024**2
        and re.fullmatch('[0-9a-f]{64}',origin['sha256'] or ''), 'NATIVE_PREREQUISITES_BOUNDED_EXACT_ORIGIN_REQUIRED')
    economic_run = verify_standard_run(github,origin['run_id'],sha=args.source_sha,
        gate='development-checks',job_name=ECONOMIC_JOB,completed=True,branch=branch)
    meta = github.request('/actions/artifacts/'+str(origin['artifact_id']))
    require(meta.get('digest') == 'sha256:'+origin['sha256'] and meta.get('size_in_bytes') == origin['bytes']
        and meta.get('expired') is False and meta.get('workflow_run',{}).get('id') == origin['run_id']
        and meta['workflow_run'].get('head_sha') == args.source_sha,
        'NATIVE_PREREQUISITES_AUTHENTIC_ORIGINAL_ARTIFACT_REQUIRED')
    check()
    raw = github.request('/actions/artifacts/'+str(origin['artifact_id'])+'/zip',binary=True,maximum=origin['bytes'])
    economic = verify_positive_economics(raw,origin,source_sha=args.source_sha,source_tree=args.source_tree,
        owner_session=args.owner_session)
    current_run = verify_standard_run(github,int(os.environ['GITHUB_RUN_ID']),sha=args.source_sha,
        gate='native-prerequisites',job_name=NATIVE_JOB,completed=False,branch=branch)
    dedup = admission.dedup_admission(args.source_sha,'native-prerequisites')
    check()
    return {'owner_check':check, 'owner_body_sha256':owner_hash, 'event_custody':custody,
        'economic_origin':origin, 'economic_readout':economic, 'economic_run':economic_run,
        'current_run':current_run, 'dedup':dedup, 'plan_sha256':digest(plan_raw)}


def main():
    parser = argparse.ArgumentParser()
    for key in ('source-sha','source-tree','owner-session','launch-receipt-url'):
        parser.add_argument('--'+key,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    try:
        output = require_control_path(args.output)
    except ValueError as error:
        # A rejected Source/alias path is never used even for a RED control.
        print(str(error),file=sys.stderr,flush=True)
        return 1
    result = {'schema':'porota.rc6.native-prerequisites.v1','status':'RED',
        'source_sha':args.source_sha,'source_tree':args.source_tree,'G0_G8_qualification':False,
        'quota_enforcement_proved':False,'product_resource_profile_proved':False,
        'backing_allocated':False,'material_gate_launched':False,'real_orders_sent':0,
        'recurring_additional_cost_usd':0}
    try:
        admitted = admit_native_prerequisites(args,ROOT,output)
        output.mkdir(mode=0o700)
        result.update({key:value for key,value in admitted.items() if key != 'owner_check'})
        from scripts import rc6_capacity_comparison as capacity
        result['fresh_readonly_runner'] = capacity.readonly_runner_observation(output)
        from scripts import rc6_heavy_test_preflight as preflight
        physical = result['fresh_readonly_runner'].get('live_filesystem')
        require(type(physical) is dict and physical.get('filesystem_type') == 'ext4'
            and physical.get('allocation_unit_bytes') == 4096
            and type(physical.get('free_bytes')) is int and physical['free_bytes'] >= 4*1024**3+8*1024**2
            and type(physical.get('total_inodes')) is int and physical['total_inodes'] > 0
            and type(physical.get('free_inodes')) is int
            and physical['total_inodes'] <= 10*physical['free_inodes'] <= 10*physical['total_inodes'],
            'NATIVE_PREREQUISITES_FRESH_SMALL_CONTROL_STORAGE_REQUIRED')
        before = governed.source_pin(ROOT,args.source_sha,args.source_tree)
        admitted['owner_check']()
        result['namespace_filter'] = prove_filter_in_child(output=output/'filter',source_sha=args.source_sha,
            source_tree=args.source_tree,owner_session=args.owner_session,plan_sha256=admitted['plan_sha256'])
        from scripts import rc6_privileged_custody as privileged
        from scripts import rc6_capacity_calibration as calibration
        code_hashes = {name:digest((ROOT/name).read_bytes()) for name in calibration.CODE_MEMBERS}
        result['custody_contract'] = privileged.native_contract(args.source_sha,args.source_tree,code_hashes)
        admitted['owner_check']()
        witness_binding = {'candidate_sha':args.source_sha,'candidate_tree':args.source_tree,
            'producer':'NATIVE_CUSTODY_PREREQUISITES','attempt_id':os.environ['GITHUB_RUN_ID']+'-1',
            'owner_id':args.owner_session,'runner_class':'github-hosted/ubuntu-24.04',
            'workload_fingerprint':admitted['plan_sha256']}
        witness = privileged.prove_custody(source=ROOT,source_sha=args.source_sha,source_tree=args.source_tree,
            code_hashes=code_hashes,parent=development.short_control_parent(ROOT),binding=witness_binding,progress=None,
            capture_parent=output/'custody-raw')
        result['custody'] = privileged.validate_witness(witness,binding=witness_binding)
        admitted['owner_check']()
        governed.compare_source(before,governed.source_pin(ROOT,args.source_sha,args.source_tree))
        result.update(status='PASS_SMALL_NATIVE_ONLY',fullSource_unchanged=True)
    except (ValueError,OSError,KeyError,TypeError,zipfile.BadZipFile) as error:
        if not output.exists():
            development.preserve_admission_rejection(output,error,source_sha=args.source_sha,
                source_tree=args.source_tree,plan_sha256=digest(PLAN.read_bytes()))
        reason = str(error)
        result.update(error_class=type(error).__name__,errno=getattr(error,'errno',None),
            error_signature=reason if re.fullmatch('[A-Z][A-Z0-9_]*(?::[0-9]+)?',reason) else type(error).__name__)
    with (output/'native-prerequisites.json').open('xb') as stream:
        stream.write(canonical(result))
    print(canonical(result).decode(),flush=True)
    return int(result['status'] != 'PASS_SMALL_NATIVE_ONLY')


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    raise SystemExit(main())
