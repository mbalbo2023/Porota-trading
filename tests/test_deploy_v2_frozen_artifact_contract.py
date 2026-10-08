from pathlib import Path

import yaml


WORKFLOW = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")


def test_frozen_artifact_is_exported_only_after_paper_safety():
    paper = WORKFLOW.index("- name: PAPER safety source gate")
    export = WORKFLOW.index("- name: Export frozen candidate artifacts")
    assert paper < export


def test_g7_live_capacity_precedes_build_and_artifact_allocation():
    steps = yaml.safe_load(WORKFLOW)['jobs']['artifact-gate']['steps']
    build = next(step['run'] for step in steps if step['name']=='Build candidate exactly once')
    export = next(step['run'] for step in steps if step['name']=='Export frozen candidate artifacts')
    assert build.index('predeploy_capacity_gate(')<build.index('begin-build')<build.index('docker build')
    assert export.index('predeploy_capacity_gate(')<export.index('docker save')
    assert "stage='build'" in build and "stage='artifact-export'" in export


def test_g7_locked_bootstrap_has_own_temporary_storage_and_no_external_home_cache():
    steps = yaml.safe_load(WORKFLOW)['jobs']['artifact-gate']['steps']
    governed = next(step['run'] for step in steps if 'verify authenticated external G6' in step['name'])
    assert 'mkdir -m 700 "${POROTA_PREDEPLOY_TMP}/bootstrap-temp"' in governed
    assert 'export TMPDIR="${POROTA_PREDEPLOY_TMP}/bootstrap-temp"' in governed
    assert governed.index('g7_bootstrap_capacity environment-bootstrap')<governed.index(' create --scope ')
    build = governed.index('g7_bootstrap_capacity locked-build')
    runtime = governed.index('g7_bootstrap_capacity locked-runtime')
    commands = [line for line in governed.splitlines() if 'python -m pip install' in line]
    assert len(commands)==2 and all('--no-cache-dir' in command and '--require-hashes' in command for command in commands)
    assert build<governed.index(commands[0])<runtime<governed.index(commands[1])


def test_frozen_artifact_uses_exact_pr_head_and_cannot_be_skipped():
    assert "ref: ${{ env.CANDIDATE_SHA }}" in WORKFLOW
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha }}" in WORKFLOW
    assert 'docker save "$IMAGE" | gzip -1 > "${POROTA_PREDEPLOY_TMP}/porota-predeploy-image.tar.gz"' in WORKFLOW
    assert "porota-deploy-bundle-v2.tgz" in WORKFLOW

    export = WORKFLOW.index("- name: Export frozen candidate artifacts")
    contract = WORKFLOW.index("- name: Frozen candidate fail-closed contract")
    upload = WORKFLOW.index("- name: Upload predeploy evidence")
    export_step = WORKFLOW[export:contract]
    contract_step = WORKFLOW[contract:upload]
    assert "if:" not in export_step
    assert "if:" not in contract_step


def test_ready_for_review_retriggers_final_predeploy():
    workflow = yaml.safe_load(WORKFLOW)
    assert workflow.get('on', workflow.get(True))['pull_request']['types'] == ['ready_for_review']
    condition = workflow['jobs']['artifact-gate']['if']
    assert "github.event.action == 'ready_for_review'" in condition
    assert "github.event.number == 476" in condition
    assert "github.event.pull_request.draft == false" in condition


def test_governed_pytest_isolated_from_runner_entrypoints():
    steps = yaml.safe_load(WORKFLOW)['jobs']['artifact-gate']['steps']
    admission = next(index for index, step in enumerate(steps) if step.get('id') == 'prerequisite_gates')
    workspace = next(index for index, step in enumerate(steps) if step.get('id') == 'private_workspace')
    build = next(index for index, step in enumerate(steps) if step['name'] == 'Build candidate exactly once')
    assert admission < workspace < build and 'if:' not in steps[admission]['run']
    assert 'rc6_material_pr_admission.py' in steps[admission]['run']
    assert '--gate predeploy' in steps[admission]['run']
    assert 'rc6_architectural_gates.py' in steps[admission]['run']
    assert '--target-gate G7' in steps[admission]['run']
    governed = next(step['run'] for step in steps if 'verify authenticated external G6' in step['name'])
    assert 'supervise-pytest' not in governed and 'python -m pytest' not in governed
    assert 'import_verified_governed' in governed
    assert "verified['source_sha']==os.environ['CANDIDATE_SHA']" in governed
    assert "verified['source_tree']==os.environ['CANDIDATE_TREE']" in governed
    assert 'POROTA_G7_PYTEST_FIN_CLAIMED=false' in governed
    assert 'POROTA_G7_SOURCE_SUITE_REEXECUTED=false' in governed
    verifier = Path('scripts/rc6_architectural_gates.py').read_text()
    assert 'ordered=validate_chain(' in verifier
    assert 'native=verify_native_evidence(row,archive)' in verifier
    assert 'kernel.get("owned_cleanup_management_bound_seconds")==5' in verifier
    assert "governed_runner.compare_source(before,after)" in verifier
    assert "original['compare_records157'](records_before,records_after)" in verifier
    assert 'G7_EXTERNAL_G6_ARCHIVE_CHANGED' in verifier
    assert 'G7_EXTERNAL_G6_NATIVE_ORIGIN_REBOUND' in verifier


def test_initial_checkouts_cannot_fetch_full_history_before_owner_and_capacity_admission():
    carrier=yaml.safe_load(Path('.github/workflows/rc6-unified-candidate-tests.yml').read_text())
    predeploy=yaml.safe_load(WORKFLOW)
    for workflow in (carrier,predeploy):
        job=next(iter(workflow['jobs'].values()))
        checkout=next(step for step in job['steps'] if step.get('uses','').startswith('actions/checkout@'))
        assert checkout['with']['fetch-depth']==1 and checkout['with']['persist-credentials'] is False
    steps=predeploy['jobs']['artifact-gate']['steps']
    prior=next(index for index,step in enumerate(steps) if step.get('id')=='prerequisite_gates')
    fetch=next(index for index,step in enumerate(steps) if 'Complete Git history' in step['name'])
    source=next(index for index,step in enumerate(steps) if step['name']=='Freeze checkout byte provenance before build')
    assert prior<fetch<source
    code=Path('scripts/rc6_material_carrier.py').read_text()
    function=code[code.index('def complete_full_git('):code.index('def need(')]
    assert function.index('preparation_capacity(')<function.index("'fetch','--unshallow'")
    assert "label='fullGit-guarded-fetch'" in function and "SHALLOW_BOOTSTRAP_MUST_NOT_QUALIFY_AS_FULL_GIT" in function
    main=code[code.index('def main():'):]
    assert main.index('auth=authority(a)')<main.index('complete_full_git(a,run,root)')<main.index('interpreters=installed_env(')
    verifier=Path('scripts/rc6_architectural_gates.py').read_text()
    g7=verifier[verifier.index('def complete_predeploy_full_git('):verifier.index('def receipt_base(')]
    assert g7.index('predeploy_capacity_gate(')<g7.index("'--unshallow'")
    assert "'pytest_launched':False" in g7 and "'G7_PYTEST_FIN_claimed':False" in g7

def test_capacity_diagnosis_is_dispatch_only_and_never_reaches_candidate_or_tooling_jobs():
    workflow=yaml.safe_load(Path('.github/workflows/rc6-unified-candidate-tests.yml').read_text())
    trigger=workflow.get('on',workflow.get(True))
    modes=trigger['workflow_dispatch']['inputs']['gate']['options']
    assert 'capacity-probe' in modes and 'capacity-calibration' in modes
    steps=workflow['jobs']['ordered-source-gate']['steps']
    admit=next(index for index,step in enumerate(steps) if step.get('id')=='admit')
    toolcap=next(index for index,step in enumerate(steps) if step['name']=='Measure live compound capacity before reviewed Python tooling')
    assert 'python3 -I -B scripts/rc6_material_pr_admission.py' in steps[admit]['run']
    for index,step in enumerate(steps):
        if step.get('uses','').startswith('actions/setup-python@') or step.get('id')=='gate':
            assert admit<toolcap<index
            assert "env.RC6_GATE != 'capacity-probe'" in step['if']
            assert "env.RC6_GATE != 'capacity-calibration'" in step['if']
    diagnostic=next(step for step in steps if step.get('id')=='diagnostic')
    assert "github.event_name == 'workflow_dispatch'" in diagnostic['if']
    assert '--diagnostic-admission-json' in diagnostic['run']
    assert '--python311' not in diagnostic['run'] and 'setup-python' not in diagnostic['run']
    code=Path('scripts/rc6_material_carrier.py').read_text()
    mode=code[code.index('def diagnostic_main('):code.index('def need(')]
    assert 'static_admission(' not in mode and 'installed_env(' not in mode and 'receipt_base(' not in mode
    assert mode.index('native=run(')<mode.index("terminal=document(read(output/'calibration.json'))")
    assert mode.index('DIAGNOSTIC_INNER_LOOP_OR_NAMESPACE_UNKNOWN_OUTER_CLEANUP_VETO')<mode.index('capture_required_evidence(')<mode.index('cleanup_namespace(')
    assert "'G0_G8_claimed':False" in mode and "'qualification_claimed':False" in mode

def test_g7_prerequisite_and_capacity_checks_precede_any_python_tooling_download():
    steps=yaml.safe_load(WORKFLOW)['jobs']['artifact-gate']['steps']
    prior=next(index for index,step in enumerate(steps) if step.get('id')=='prerequisite_gates')
    capacity=next(index for index,step in enumerate(steps) if step['name']=='Measure capacity before reviewed Python tooling download')
    tooling=next(index for index,step in enumerate(steps) if step.get('uses','').startswith('actions/setup-python@'))
    assert prior<capacity<tooling and "stage='reviewed-tooling'" in steps[capacity]['run']
