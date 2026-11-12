"""R254: PR-gate consumes trusted immutable RELEASE receipts, never rebuilds.

These tests are source-contract checks; GH REST and artifact bytes are verified
by the separately owned, pinned R252 trusted-main checker.
"""

import os
from pathlib import Path
import subprocess

import yaml


YAML = Path('.github/workflows/porota-predeploy-v2.yml').read_text(encoding='utf-8')
WORKFLOW = yaml.safe_load(YAML)
JOBS = WORKFLOW['jobs']


def _required():
    return JOBS['release-evidence-required']


def _release_step():
    matches = [
        step for step in _required()['steps']
        if step.get('name') == 'Verify pinned R252 trusted RELEASE receipt'
    ]
    assert len(matches) == 1
    return matches[0]


def _run_release_step_offhost(tmp_path, *, draft, verifier_exit, run=None):
    """Exercise the real R254 bash step, replacing only Git/REST boundaries.

    The verifier fixture models the exit status of the independently pinned
    R252 D/E checker; it is deliberately NOT a native-image attestation.
    """
    pin = next(
        step['with']['ref'] for step in _required()['steps']
        if step.get('name') == 'Checkout immutable reviewed release evidence guard'
    )
    trusted = tmp_path / '.rc6-release-guard-trusted' / 'scripts'
    trusted.mkdir(parents=True)
    (tmp_path / '.rc6-release-head-inspect').mkdir()
    (trusted / 'rc6_ci_release_required_guard.py').write_text(
        'import os, sys\n'
        'from pathlib import Path\n'
        'if sys.argv[1:] != ["--trusted-release-mode", "PR_CANDIDATE"]:\n'
        '    sys.exit(91)\n'
        'Path("r252-verifier-invoked").write_text("yes")\n'
        'sys.exit(int(os.environ["RC6_TEST_VERIFIER_EXIT"]))\n',
        encoding='utf-8',
    )
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake_git = bin_dir / 'git'
    fake_git.write_text(
        '#!/usr/bin/env python3\n'
        'import os, sys\n'
        'case = tuple(sys.argv[1:])\n'
        'receipts = {\n'
        '    ("-C", ".rc6-release-guard-trusted", "rev-parse", "HEAD"):\n'
        '        "RC6_TEST_TRUSTED_SHA",\n'
        '    ("-C", ".rc6-release-head-inspect", "rev-parse", "HEAD"):\n'
        '        "RC6_TEST_CANDIDATE_SHA",\n'
        '    ("-C", ".rc6-release-head-inspect", "rev-parse", "HEAD^{tree}"):\n'
        '        "RC6_TEST_CANDIDATE_TREE",\n'
        '}\n'
        'if case not in receipts:\n'
        '    sys.exit(92)\n'
        'print(os.environ[receipts[case]])\n',
        encoding='utf-8',
    )
    fake_git.chmod(0o700)
    env = os.environ.copy()
    env.update({
        'PATH': str(bin_dir) + os.pathsep + env.get('PATH', ''),
        'RC6_RELEASE_PR_DRAFT': draft,
        'RC6_BRIDGE_CANDIDATE_SHA': 'a' * 40,
        'RC6_BRIDGE_CANDIDATE_TREE_SHA': '',
        'RC6_TEST_CANDIDATE_SHA': 'a' * 40,
        'RC6_TEST_CANDIDATE_TREE': 'b' * 40,
        'RC6_TEST_TRUSTED_SHA': pin,
        'RC6_TEST_VERIFIER_EXIT': str(verifier_exit),
    })
    script = run if run is not None else _release_step()['run']
    assert script.count('${{ github.event.pull_request.number }}') == 1
    script = script.replace('${{ github.event.pull_request.number }}', '1087')
    return subprocess.run(
        ['bash', '-c', script], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=10, check=False,
    )


def test_r254_keeps_untrusted_artifact_gate_disabled_for_1087():
    gate = JOBS['artifact-gate']
    assert '1087' in str(gate['if'])
    assert "github.event.pull_request.head.ref != 'integration/rc6-a1115-r87-unified-paper-candidate-20261110'" in str(gate['if'])
    # Both R144 holds remain in the original image/native stage; the PR gate
    # may accept a trusted D/E release without exercising them.
    assert YAML.count('          exit 79') >= 2


def test_r254_ready_for_review_emits_fresh_non_draft_pr_event():
    # Old pull_request run reruns retain their original draft=true payload.
    # After immutable D/E evidence arrives, transitioning to READY must cause
    # a FRESH required check for the same candidate SHA.
    event = WORKFLOW.get('on', WORKFLOW.get(True))
    assert 'ready_for_review' in event['pull_request']['types']


def test_r254_required_job_runs_on_skipped_artifact_gate_for_product_pr():
    required = _required()
    assert required['needs'] == 'artifact-gate'
    assert 'always()' in str(required['if'])
    assert 'deploy/rc6-pr69-isolated-20260915' in str(required['if'])
    # R252 may authenticate/download up to a 2 GiB frozen image ZIP. Keep a
    # bounded job, but not the legacy five-minute tiny JSON-only timeout.
    assert 20 <= required['timeout-minutes'] <= 45
    assert required['permissions']['actions'] == 'read'
    assert required['permissions']['contents'] == 'read'
    assert required['permissions']['pull-requests'] == 'read'


def test_r254_uses_immutable_trusted_main_script_not_pr_head():
    required = _required()
    for checkout in (s for s in required['steps'] if s.get('name', '').startswith('Checkout ')):
        assert checkout['uses'] == (
            'actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683'
        )
    trusted = [step for step in required['steps'] if step.get('name') == 'Checkout immutable reviewed release evidence guard']
    assert len(trusted) == 1
    ref = trusted[0]['with']['ref']
    assert isinstance(ref, str) and len(ref) == 40
    assert all(char in '0123456789abcdef' for char in ref)
    assert ref != '0' * 40
    assert trusted[0]['with']['persist-credentials'] is False
    step = _release_step()
    assert 'python3 -I .rc6-release-guard-trusted/scripts/rc6_ci_release_required_guard.py' in step['run']
    assert '--trusted-release-mode PR_CANDIDATE' in step['run']
    assert 'scripts/rc6_ci_release_required_guard.py' not in step['run'].replace(
        '.rc6-release-guard-trusted/scripts/rc6_ci_release_required_guard.py', ''
    )
    assert 'docker build' not in step['run']


def test_r254_never_ships_old_main_as_r252_trusted_verifier_pin():
    # These two commits were valid historic bootstraps but cannot contain the
    # not-yet-merged R252 E/D verifier. A provisional pin MUST stay RED until
    # the reviewed R252 main-only PR lands and its real merged SHA is pinned.
    checkout = next(
        step for step in _required()['steps']
        if step.get('name') == 'Checkout immutable reviewed release evidence guard'
    )
    assert checkout['with']['ref'] not in {
        '7c1c60a7e050f2dc0bfa215f38e6e98dafac556e',
        '7a71d351725f7e54231a2b0deec6fb598973b32a',
    }


def test_r254_exact_candidate_tree_and_untrusted_selector_hints():
    step = _release_step()
    env = step['env']
    required_keys = {
        'RC6_BRIDGE_REPOSITORY', 'RC6_BRIDGE_MODE', 'RC6_BRIDGE_CANDIDATE_SHA',
        'RC6_BRIDGE_CANDIDATE_TREE_SHA', 'RC6_BRIDGE_PUBLISHER_MAIN_SHA',
        'RC6_BRIDGE_E_RUN_ID', 'RC6_BRIDGE_E_RUN_ATTEMPT',
        'RC6_BRIDGE_E_ARTIFACT_ID', 'RC6_BRIDGE_PRODUCT_SHA',
        'RC6_BRIDGE_PRODUCT_REF', 'GITHUB_TOKEN',
    }
    assert required_keys <= set(env)
    assert env['RC6_BRIDGE_REPOSITORY'] == '${{ github.repository }}'
    assert env['RC6_BRIDGE_MODE'] == 'PR_CANDIDATE'
    assert env['RC6_BRIDGE_CANDIDATE_SHA'] == '${{ github.event.pull_request.head.sha }}'
    # R259 removes unauthored Github Variables and manual credential setup:
    # all-three-empty instructs R252 to discover only the unique matching E
    # run by bounded read-only REST, then fully reauthenticate D/E ZIP bytes.
    assert env['RC6_BRIDGE_PUBLISHER_MAIN_SHA'] == ''
    assert env['RC6_BRIDGE_E_RUN_ID'] == ''
    assert env['RC6_BRIDGE_E_RUN_ATTEMPT'] == ''
    assert env['RC6_BRIDGE_E_ARTIFACT_ID'] == ''
    assert env['RC6_BRIDGE_PRODUCT_SHA'] == ''
    assert env['RC6_BRIDGE_PRODUCT_REF'] == ''
    assert env['GITHUB_TOKEN'] == '${{ github.token }}'
    assert 'git -C .rc6-release-head-inspect rev-parse HEAD' in step['run']
    assert 'git -C .rc6-release-head-inspect rev-parse HEAD^{tree}' in step['run']
    assert 'RC6_BRIDGE_CANDIDATE_TREE_SHA' in step['run']


def test_r254_fail_closed_draft_and_stale_same_run_evidence():
    step = _release_step()
    assert step['env']['RC6_RELEASE_PR_DRAFT'] == '${{ github.event.pull_request.draft }}'
    assert 'RC6_RELEASE_PR_DRAFT' in step['run']
    assert 'exit 79' in step['run']
    assert 'RC6_RELEASE_GATE_RESULT' not in step['env']
    assert 'RC6_RELEASE_ARTIFACT_ID' not in step['env']
    assert 'RC6_RELEASE_ARTIFACT_DIGEST' not in step['env']
    assert 'max(created_at)' not in step['run']
    assert 'RC6_BRIDGE_E_RUN_ID' in step['env']


def test_r254_draft_cannot_invoke_trusted_release_even_if_checker_would_accept(tmp_path):
    result = _run_release_step_offhost(tmp_path, draft='true', verifier_exit=0)
    assert result.returncode == 79, (result.stdout, result.stderr)
    assert not (tmp_path / 'r252-verifier-invoked').exists()
    assert 'RC6_R254=TRUSTED_RELEASE_BYTES_BOUND' not in result.stdout


def test_r254_ready_does_not_publish_green_if_de_receipt_verifier_fails(tmp_path):
    # This represents absent, stale, ambiguous or tampered E/D evidence, and
    # the future native same-image verifier rejecting an invalid receipt.
    result = _run_release_step_offhost(tmp_path, draft='false', verifier_exit=79)
    assert result.returncode == 79, (result.stdout, result.stderr)
    assert (tmp_path / 'r252-verifier-invoked').read_text() == 'yes'
    assert 'RC6_R254=TRUSTED_RELEASE_BYTES_BOUND' not in result.stdout
    assert 'POROTA_PREDEPLOY_V2=GREEN' not in result.stdout


def test_r254_ready_with_successful_verifier_reports_only_offhost_receipt(tmp_path):
    # Synthetic checker success tests only bridge control flow; actual D/E
    # bytes and future native same-image proof remain the trusted R252 scope.
    result = _run_release_step_offhost(tmp_path, draft='false', verifier_exit=0)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (tmp_path / 'r252-verifier-invoked').read_text() == 'yes'
    assert 'RC6_R254=TRUSTED_RELEASE_BYTES_BOUND|automatic_go=NO|host=NO' in result.stdout
    assert 'POROTA_PREDEPLOY_V2=GREEN' not in result.stdout


def test_r254_wrong_verifier_mode_cannot_count_as_invocation(tmp_path):
    source = _release_step()['run']
    wrong_mode = source.replace(
        '--trusted-release-mode PR_CANDIDATE || exit 79',
        '--trusted-release-mode PRODUCT_POSTMERGE || exit 79',
    )
    assert wrong_mode != source
    result = _run_release_step_offhost(
        tmp_path, draft='false', verifier_exit=0, run=wrong_mode,
    )
    assert result.returncode == 79, (result.stdout, result.stderr)
    assert not (tmp_path / 'r252-verifier-invoked').exists()
    assert 'RC6_R254=TRUSTED_RELEASE_BYTES_BOUND' not in result.stdout
