"""R254: PR-gate consumes trusted immutable RELEASE receipts, never rebuilds.

These tests are source-contract checks; GH REST and artifact bytes are verified
by the separately owned, pinned R252 trusted-main checker.
"""

from pathlib import Path

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
