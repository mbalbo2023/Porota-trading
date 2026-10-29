"""A176 can adopt only the exact running A175 PAPER/NO_GO failure."""

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
MODE = 'CLOSED_A175_PENDING_FIX_FORWARD'
SOURCE = '18ba517a846c6798f0d12f39072465fc02fa03b5'
IMAGE = 'sha256:9841cba8df3345fa22e39457645ba250431ad1f465ce4204d92f73f7816215b9'


def test_a175_exact_running_classifier_precedes_generic_closed():
    code = WORKFLOW.split('      - name: Classify current flat PAPER session before host writes', 1)[1]
    code = code.split('      - name: Capture exact pre-suspend host fingerprint', 1)[0]
    assert code.index(f"marker_candidate=='{SOURCE}'") < code.index("print('CLOSED')")
    assert IMAGE in code and 'candidate.a175_failed_running_proof(' in code
    assert 'operator_current_no_go_proof=operator.exact_a176_operator_no_go_continuation' in code
    assert 'require_installed_a175_source=True' in code
    assert "proof.get('origin_sha256')!=candidate.A175_FAILED_RECEIPT_SHA256" in code
    assert "proof.get('image_id')!=candidate.A175_LOADED_IMAGE_ID" in code
    assert 'RC6_A175_FAILED_VALIDATION_RECOVERY=ADMITTED|health=RED|PAPER=0' in code
    assert f"print('{MODE}')" in code


def test_a175_mode_has_exact_routing_and_cleanup_never_rearms():
    assert f'{MODE}) FLAG=\'--adopt-a175-running-pending\'' in WORKFLOW
    assert f'{MODE}) QUIESCENCE_FLAG=\'--adopt-a175-running-pending\'' in WORKFLOW
    assert f"env.RC6_QUIESCENCE_MODE == '{MODE}'" in WORKFLOW
    assert f'"$RC6_QUIESCENCE_MODE" = {MODE} ]]' in WORKFLOW
    assert 'RC6_PAPER_SESSION=A175_EXACT_NO_GO_ADMITTED|runtime=RUNNING|health=RED|PAPER=0' in WORKFLOW
    cleanup = WORKFLOW.split('      - name: Failure-safe host cleanup', 1)[1]
    assert f'[ "${{RC6_QUIESCENCE_MODE:-}}" = {MODE} ]' in cleanup
    assert 'SCHEDULER_ACTION=recover-a175-pending' in cleanup
    assert 'RC6_A125_CANDIDATE_SOURCE_SHA256=' in cleanup
    assert 'RC6_A145_QUIESCENCE_GZIP_B64=' in cleanup
    assert cleanup.index('SCHEDULER_ACTION=recover-a175-pending') < cleanup.index('--phase cleanup_restore')
    # A failed suspend must never select the generic scheduler restore.
    assert f'if [ "${{RC6_QUIESCENCE_MODE:-}}" = {MODE} ]; then' in cleanup


def test_low_disk_a175_can_run_only_bounded_reference_safe_preclean():
    disk = WORKFLOW.split('      - name: Dynamic pre-transfer disk guard', 1)[1]
    disk = disk.split('      - name: Suspend versioned RC6 schedulers', 1)[0]
    denied = disk.split("echo 'RC6_RESCUE_DISK_PREFLIGHT=RED", 1)[0]
    assert f'[ "$RC6_QUIESCENCE_MODE" = {MODE} ]' not in denied
    assert f'RC6_QUIESCENCE_MODE=\'$RC6_QUIESCENCE_MODE\'' in disk
    assert f'if [ "$RC6_QUIESCENCE_MODE" = {MODE} ]; then' in disk
    assert f"state.get('candidate_sha')=='{SOURCE}'" in disk
    assert f"test \"$CURRENT_IMAGE\" = '{IMAGE}'" in disk
    assert "state.get('validation_status')=='DEPLOYED_VALIDATION_PENDING'" in disk
    assert "STATE\" = \"PRODUCTION_PAPER|0\"" in disk
    assert "RC6_UNSCOPED_DOCKER_PRUNE=SKIPPED" in disk
    assert 'RC6_DISK_PRETRANSFER_GATE=GREEN' in disk


def test_workflow_yaml_and_all_shell_blocks_parse():
    document = yaml.safe_load(WORKFLOW)
    assert document['concurrency']['group'] == 'rc6-unified-paper-deploy'
    for step in document['jobs']['promote']['steps']:
        if 'run' in step:
            p = subprocess.run(['bash', '-n'], input=step['run'], text=True,
                               capture_output=True, check=False)
            assert p.returncode == 0, (step['name'], p.stderr)
