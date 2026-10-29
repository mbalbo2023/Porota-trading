"""The exact A174 failed runtime cannot fall through generic CLOSED admission."""
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/porota-deploy-v2-promote.yml').read_text()
MODE = 'CLOSED_A174_PENDING_FIX_FORWARD'
FLAG = '--adopt-a174-running-pending'
IMAGE = 'sha256:9ca6e7ca98f11bbb82353434f54ffab15b218ec54319c83ae1661aaf897a40e9'
SOURCE = 'b6e6ce75f29fe117aaa42b0bfcd9386ea93a0aa0'


def test_a174_exact_running_classifier_precedes_generic_closed():
    code = WORKFLOW.split('      - name: Classify current flat PAPER session before host writes', 1)[1]
    code = code.split('      - name: Capture exact pre-suspend host fingerprint', 1)[0]
    assert code.index(f"'{SOURCE}'") < code.index("print('CLOSED')")
    assert IMAGE in code
    assert "statuses=={'running'} and state and state[0]=='WAITING_MARKET'" in code
    assert 'candidate.a174_failed_running_proof(' in code
    assert 'require_installed_a174_source=True' in code
    assert "proof.get('origin_sha256')!=candidate.A174_FAILED_RECEIPT_SHA256" in code
    assert "proof.get('image_id')!=candidate.A174_LOADED_IMAGE_ID" in code
    assert "proof.get('flat_book',{}).get('real_orders_sent')!=0" in code
    assert "print('CLOSED_A174_PENDING_FIX_FORWARD')" in code
    assert 'RC6_A174_FAILED_VALIDATION_RECOVERY=ADMITTED|health=RED|PAPER=0' in code


def test_a174_mode_is_wired_through_all_exact_mutation_gates():
    assert f'{MODE}) FLAG=\'{FLAG}\'' in WORKFLOW
    assert f'{MODE}) QUIESCENCE_FLAG=\'{FLAG}\'' in WORKFLOW
    assert f"env.RC6_QUIESCENCE_MODE == '{MODE}'" in WORKFLOW
    assert '[ "$RC6_QUIESCENCE_MODE" = CLOSED_A174_PENDING_FIX_FORWARD ]' in WORKFLOW
    assert '"$RC6_QUIESCENCE_MODE" = CLOSED_A174_PENDING_FIX_FORWARD ]]' in WORKFLOW
    cleanup = WORKFLOW.split('      - name: Failure-safe host cleanup', 1)[1]
    assert '[ "${RC6_QUIESCENCE_MODE:-}" = CLOSED_A174_PENDING_FIX_FORWARD ]' in cleanup
    assert 'SCHEDULER_ACTION=recover-a174-pending' in cleanup
    assert 'RECOVERY_A125_ENV="RC6_A125_CANDIDATE_SOURCE_GZIP_B64=' in cleanup
    assert 'RC6_A125_CANDIDATE_SOURCE_SHA256=' in cleanup
    assert 'A143_ENV="RC6_A145_QUIESCENCE_GZIP_B64=' in cleanup
    assert cleanup.index('SCHEDULER_ACTION=recover-a174-pending') < cleanup.index('--phase cleanup_restore')


def test_workflow_syntax_and_serialized_host_phase():
    steps = yaml.safe_load(WORKFLOW)['jobs']['promote']['steps']
    assert yaml.safe_load(WORKFLOW)['concurrency']['group'] == 'rc6-unified-paper-deploy'
    for step in steps:
        if 'run' in step:
            result = subprocess.run(['bash', '-n'], input=step['run'], text=True,
                                    capture_output=True, check=False)
            assert result.returncode == 0, (step['name'], result.stderr)
