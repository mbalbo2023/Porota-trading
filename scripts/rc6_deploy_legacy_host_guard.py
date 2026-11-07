"""OFFHOST source-level hold for RC6's still-rootful Deploy V2 lifecycle.

The current promotion workflow has a shared-Docker/rootful implementation.
Until independent physical tenant custody, a private rootless daemon and a
least-privilege broker are proven, it MUST stop before any host contact. This
guard verifies that containment and detects accidental GitHub Actions control-
flow bypasses. A successful check means BLOQUEADO, never permission to deploy.

This deliberately contains NO path to authorize a rootless deployment. The
future replacement needs its own positive attestation and integration contract.
"""
from __future__ import annotations

import ast
import hashlib
import re
from typing import Any, Mapping


class LegacyHostBoundaryViolation(RuntimeError):
    """Legacy promotion may reach the host or lacks a trustworthy hold."""


_EXACT_GATE_RUN = (
    "set -Eeuo pipefail\n"
    "echo 'STAGING_CLEANUP=DEFERRED_UNVERIFIED|no_independent_root_attestation|no_host_contact'\n"
    "echo '::error::RC6_A806_PREADMISSION_STAGING_UNVERIFIED: no root-issued stage ownership proof; forbidden to enter host promotion'\n"
    "echo 'RC6_DEPLOY_V2=NO_GO_PREMUTATION_STAGING_UNVERIFIED|remote_mutations=0'\n"
    "exit 79\n"
)
_EXACT_RUNNER_CLEANUP = (
    'set -Eeuo pipefail\n'
    '# A806 holds before artifacts/SSH credentials are staged. Never\n'
    '# remove historical fixed /tmp names (or HOME paths) without an\n'
    '# independent, exact runner-artifact ownership proof. Those names\n'
    '# may belong to another attempt or tenant on a shared host.\n'
    "echo 'POROTA_DEPLOY_V2_RUNNER_CLEANUP=DEFERRED_UNVERIFIED|"
    "no_independent_runner_artifact_ownership|delete_attempts=0'\n"
)

_HOST_CONTACT = re.compile(
    r"\b(?:ssh|scp|rsync)\s+(?:-|\S)|\$DO_USER@\$DO_HOST|\bsudo\s+-n\b"
)
_ROOTFUL_DOCKER = re.compile(r"\bsudo\s+-n\s+docker\b")
_SHELL_STARTUP_ENV = frozenset({'BASH_ENV', 'ENV', 'SHELLOPTS', 'BASHOPTS'})
_EXPECTED_WORKFLOW_FIELDS = {
    'name': 'RC6 Deploy V2 promote frozen candidate',
    'permissions': {'contents': 'read', 'actions': 'read', 'issues': 'read'},
    'concurrency': {'group': 'rc6-unified-paper-deploy', 'cancel-in-progress': False},
}
_EXPECTED_TRIGGERS = {
    'push': {'branches': ['deploy/rc6-pr69-isolated-20260915'],
             'paths-ignore': ['docs/**']},
}
_EXPECTED_JOB_FIELDS = {
    'runs-on': 'ubuntu-24.04',
    'timeout-minutes': 150,
    'env': {
        'GH_TOKEN': '${{ github.token }}',
        'DO_HOST': '${{ secrets.DO_HOST }}',
        'DO_USER': '${{ secrets.DO_USER }}',
        'SSH_KEY': '${{ secrets.DO_SSH_KEY }}',
        'KNOWN_HOSTS': '${{ secrets.DO_KNOWN_HOSTS }}',
    },
}
_EXACT_CHECKOUT = {
    'name': 'Checkout production promotion commit',
    'uses': 'actions/checkout@v4',
    'with': {'ref': '${{ github.sha }}', 'fetch-depth': 0},
}
# Exact pre-host-failure control flow at the audited 33-step legacy workflow.
# This is intentionally a *hold-only* source seal. A changed/added condition
# requires a new human-readable review and regression, not a guess about how
# arbitrary GitHub Actions expressions (including unknown env values) behave.
_SUCCESS_IF_HASH = '0c2a21f8355513ea53fe0d2fd21bf578f07878ac43b5a09f5cca7e01afe1d3d9'
_EXPECTED_STEP_IF_HASHES = {
    12: 'f7b7222003869812086572c25122b6af6761b2648e6fe9026e27a7a04ef6efe5',
    13: 'bb4bc7743863800af03c4e8afe7585ba69c92c3431de72dc8c24e60d246c0b96',
    18: '6ffce47c57409e46745d0ddbdb454f529f9ae5663d9529e1ebd65ce644cc6914',
    19: 'beb53306cfd2733693a72f633e64fbc2de44e239c26b05e59976091c69eb5443',
    **{i: _SUCCESS_IF_HASH for i in range(22, 28)},
    28: '1cd29b390165e9e6446be3bdffe7729d56793bf8d4f10e727e66b5180fcfb82f',
    29: '093d3ce4908f6066c883a6b418a217833916a1872cae7f38ada844dbf1e89e21',
    30: 'd474d6a6236e103acecc0b594f5957744b7555f0f0750b7e477884c7439dcf26',
    31: _SUCCESS_IF_HASH,
    32: 'b2daf9a143924dc722c6703acb6c59f40053a84d610cd76163f239b269845f86',
}
_OUTCOME = re.compile(r"\bsteps\.([A-Za-z_][A-Za-z0-9_]*)\.outcome\b")
_OUTPUT = re.compile(r"\bsteps\.([A-Za-z_][A-Za-z0-9_]*)\.outputs\.[A-Za-z_][A-Za-z0-9_]*\b")
_ENV = re.compile(r"\benv\.[A-Za-z_][A-Za-z0-9_]*\b")


def _condition_truth_if_early_gate_fails(expression: str, step_ids: set[str]) -> bool:
    """Evaluate the small known status-expression subset with all later steps skipped.

    Explicit expressions without a status-check function retain GitHub Actions'
    implicit success() gate, therefore they cannot run after A806 failure.
    Unsupported expressions with a status function fail closed.
    """
    text = expression.strip()
    if text.startswith('${{') and text.endswith('}}'):
        text = text[3:-2].strip()
    if not any(name in text for name in ('always()', 'failure()', 'cancelled()', 'success()')):
        return False

    def output(_match: re.Match[str]) -> str:
        return "''"

    def outcome(match: re.Match[str]) -> str:
        identifier = match.group(1)
        if identifier not in step_ids:
            raise LegacyHostBoundaryViolation('UNKNOWN_STEP_IN_HOST_CONDITION')
        return repr('failure' if identifier == 'a806_staging_preadmission' else 'skipped')

    text = _OUTPUT.sub(output, text)
    text = _OUTCOME.sub(outcome, text)
    text = _ENV.sub("'NONEMPTY'", text)  # worst case for an environment flag
    text = (text.replace('always()', 'True')
                .replace('failure()', 'True')
                .replace('cancelled()', 'False')
                .replace('success()', 'False')
                .replace('&&', ' and ').replace('||', ' or '))
    try:
        tree = ast.parse(text, mode='eval')
    except SyntaxError as exc:
        raise LegacyHostBoundaryViolation('UNSUPPORTED_HOST_CONDITION') from exc

    def evaluate(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (str, bool):
            return node.value
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            return all(bool(evaluate(v)) for v in node.values)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return any(bool(evaluate(v)) for v in node.values)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not bool(evaluate(node.operand))
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1:
            lhs, rhs = evaluate(node.left), evaluate(node.comparators[0])
            if isinstance(node.ops[0], ast.Eq):
                return lhs == rhs
            if isinstance(node.ops[0], ast.NotEq):
                return lhs != rhs
        raise LegacyHostBoundaryViolation('UNSUPPORTED_HOST_CONDITION')

    return bool(evaluate(tree))


def classify_promotion_host_boundary(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Accept only an unconditional, fail-closed A806 veto around legacy code.

    Returns a HOLD receipt; raises on any attempted host reachability. Does not
    make a physical rootless/SQLite/tenant trust decision.
    """
    # A blocked `promote` job says nothing about a new independent job. Until
    # each job has a separately reviewed prehost barrier, the only admissible
    # workflow graph is the exact, currently protected single-job topology.
    jobs = workflow.get('jobs')
    if not isinstance(jobs, Mapping) or set(jobs) != {'promote'}:
        raise LegacyHostBoundaryViolation('UNREVIEWED_PROMOTION_JOB')
    try:
        job = jobs['promote']
        steps = job['steps']
    except (KeyError, TypeError) as exc:
        raise LegacyHostBoundaryViolation('A806_GATE_JOB_MISSING') from exc
    if job.get('continue-on-error') not in (None, False):
        raise LegacyHostBoundaryViolation('JOB_FAILURE_SUPPRESSION')
    gate_positions = [i for i, step in enumerate(steps)
                      if step.get('id') == 'a806_staging_preadmission']
    if gate_positions != [1]:
        raise LegacyHostBoundaryViolation('A806_GATE_CARDINALITY_OR_POSITION')
    gate = steps[1]
    # GitHub merges workflow, job and step environment. BASH_ENV is sourced by
    # a noninteractive bash *before* `set -Eeuo pipefail` or `exit 79` runs.
    # A merely textual check of gate['run'] would therefore be insufficient.
    for scope in (workflow.get('env'), job.get('env'), steps[0].get('env')):
        if scope is None:
            continue
        if not isinstance(scope, Mapping) or _SHELL_STARTUP_ENV.intersection(scope):
            raise LegacyHostBoundaryViolation('UNTRUSTED_SHELL_ENV_BEFORE_A806')

    # Source checks performed *inside* the gate cannot constrain what a
    # different runner, job service/container, workflow default, or altered
    # checkout action already ran before that gate. Keep the whole pre-gate
    # execution envelope fixed until the physical rootless protocol is reviewed.
    event_keys = [key for key in workflow if key is True or key == 'on']
    if (len(event_keys) != 1 or set(workflow) != set(_EXPECTED_WORKFLOW_FIELDS) | {'jobs', event_keys[0]}
            or workflow[event_keys[0]] != _EXPECTED_TRIGGERS
            or any(workflow.get(key) != value for key, value in _EXPECTED_WORKFLOW_FIELDS.items())):
        raise LegacyHostBoundaryViolation('UNSAFE_PROMOTION_WORKFLOW')
    if (set(job) != set(_EXPECTED_JOB_FIELDS) | {'steps'}
            or any(job.get(key) != value for key, value in _EXPECTED_JOB_FIELDS.items())):
        raise LegacyHostBoundaryViolation('UNSAFE_PROMOTION_JOB')
    if steps[0] != _EXACT_CHECKOUT:
        raise LegacyHostBoundaryViolation('UNSAFE_PROMOTION_CHECKOUT')
    if (gate.get('run') != _EXACT_GATE_RUN or gate.get('shell') != 'bash'
            or 'if' in gate or gate.get('continue-on-error') not in (None, False)
            or gate.get('env')
            or steps[0].get('uses') != 'actions/checkout@v4'):
        raise LegacyHostBoundaryViolation('A806_GATE_NOT_EXECUTABLE_UNCONDITIONAL')
    if len(steps) != 33:
        raise LegacyHostBoundaryViolation('POST_A806_NONTERMINAL_EXECUTION')

    # No novel conditional expression is trusted. This closes the otherwise
    # unbounded string/env/status-language problem: even an expression that a
    # local interpreter mistakenly calls false is rejected if it changed.
    # The already audited original expressions remain checked again below.
    step_ids = {step['id'] for step in steps if step.get('id')}
    for index, step in enumerate(steps):
        actual = (hashlib.sha256(str(step['if']).encode('utf-8')).hexdigest()
                  if 'if' in step else None)
        if actual == _EXPECTED_STEP_IF_HASHES.get(index):
            continue
        if index >= 2:
            if _condition_truth_if_early_gate_fails(str(step.get('if', '')), step_ids):
                if _HOST_CONTACT.search(str(step.get('run', ''))):
                    raise LegacyHostBoundaryViolation('HOST_CONTACT_AFTER_A806')
                if index != len(steps) - 1:
                    raise LegacyHostBoundaryViolation('POST_A806_NONTERMINAL_EXECUTION')
        raise LegacyHostBoundaryViolation('UNREVIEWED_POST_A806_STATUS_CONDITION')

    source = '\n'.join(str(step.get('run', '')) for step in steps)
    rootful_references = len(_ROOTFUL_DOCKER.findall(source))
    if rootful_references == 0:
        # A source rewrite to bare `docker` proves neither the configured
        # endpoint nor exclusive daemon/data-root/UID/capabilities.
        raise LegacyHostBoundaryViolation('NO_INDEPENDENT_ROOTLESS_ATTESTATION')

    for index, step in enumerate(steps[2:], start=2):
        reachable = _condition_truth_if_early_gate_fails(
            str(step.get('if', '')), step_ids)
        if step.get('uses') and reachable:
            raise LegacyHostBoundaryViolation(
                f'UNREVIEWED_ALWAYS_ACTION_AFTER_A806|step={index}')
        if reachable and _HOST_CONTACT.search(str(step.get('run', ''))):
            raise LegacyHostBoundaryViolation(
                f'HOST_CONTACT_AFTER_A806|step={index}|id={step.get("id", "no-id")}')
        if not reachable:
            continue
        # All other later steps must be SKIPPED; otherwise their outcomes could
        # make an even later host-step condition true, invalidating a static
        # model that assumes every post-A806 outcome is SKIPPED. The ONE known
        # exception is a sealed runner-only cleanup as the final job step.
        if index != len(steps) - 1:
            raise LegacyHostBoundaryViolation('POST_A806_NONTERMINAL_EXECUTION')
        if (step.get('name') != 'Runner cleanup' or step.get('run') != _EXACT_RUNNER_CLEANUP
                or step.get('shell') != 'bash' or step.get('if') != 'always()'
                or step.get('env') or step.get('continue-on-error') not in (None, False)):
            raise LegacyHostBoundaryViolation('UNREVIEWED_ALWAYS_TERMINAL')

    return {
        'status': 'BLOQUEADO',
        'prehost_exit': 79,
        'rootful_docker_references': rootful_references,
        'rootless_certified': False,
        'host_contact_authorized': False,
    }
