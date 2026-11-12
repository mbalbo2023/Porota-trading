"""OFFHOST source boundary for the shared-Docker RC6 promotion workflow.

This guard can reject unsafe workflow shapes, but cannot authorize Docker,
systemd, host cleanup or a deployment. A separately installed, root-owned
custody attestor must still prove each physical operation fail-closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml


class HostBoundaryViolation(ValueError):
    """The candidate workflow contains an unbounded host operation."""


_GLOBAL_DOCKER_INVENTORY = re.compile(
    r"\bdocker\s+(?:ps\s+(?:-aq\b|-qa\b|--all\s+--quiet\b|"
    r"--quiet\s+--all\b)|image\s+ls\s+-q\b)"
)
_DIRECT_CRITICAL_RESTART = re.compile(
    r"\bsystemctl\s+restart\s+porota-critical-approval-rc6\.service\b"
)
_PPI_WATCH_MUTATION = re.compile(
    r"\bsystemctl\s+(?:stop|start|restart|enable|disable|mask|unmask)\s+"
    r"[\w.:-]*ppi[\w.:-]*watch[\w.:-]*", re.IGNORECASE
)
_CANDIDATE_ROOT_SHELL = re.compile(
    r"\bsudo\s+-n\s+(?:env\s+(?:[A-Z][A-Z0-9_]*=\S+\s+){1,6})?"
    r"bash\s+[\"']?\$(?:STAGE|REMOTE_DIR)/"
)
_CHECKOUT_ROOT_INTERPRETER = re.compile(
    # sudo's env/timeout prefix is not a root-custody API. Neither an encoded
    # mutex nor a stdin program, shell or staged checkout path may run as root.
    r"\bsudo\s+-n\s+"
    r"(?:(?:env|timeout)\s+|[A-Za-z_][A-Za-z_0-9]*=\S+\s+|"
    r"--\S+\s+|[0-9]+s?\s+)*"
    r"(?:/(?:[A-Za-z0-9_.-]+/)+)?"
    r"(?:python(?:3(?:\.[0-9]+)?)?|bash|sh|dash|env|busybox|"
    r"perl|ruby|node|php|lua)\b"
)
# Deliberately narrow source-level allowance, not proof of host authority.
# Mutable shell variables such as "$CUSTODY_CLI" cannot be source-attested
# without a separate exact binding/no-reassignment contract.
_REVIEWED_SUDO_FIXED_EXEC = re.compile(
    r"\bsudo[ \t]+-n[ \t]+/usr/local/sbin/porota-rc6-oci-custody(?=\s|$)"
)
_SUDO_WORD = re.compile(r"\bsudo\b")
_MUTATION_BY_NAME = re.compile(
    # The candidate cannot supply any container-mutation argv directly:
    # even a harmless-looking variable may hide an unowned name or CID. The
    # fixed root helper owns stop/start/remove by sealed exact-CID journal.
    r"\bdocker(?:\s+(?:--host|--context|--config|-H)(?:=\S+|\s+\S+)){0,4}"
    r"\s+(?:container\s+)?(?:stop|start|rm|restart|kill|pause|unpause)\b"
)
_PREBIRTH_HELPER = (
    "sudo -n /usr/local/sbin/porota-rc6-oci-custody "
    "quiescence verify-prebirth"
)
_APPROVED_TAGS = {
    'docker tag "$FROZEN_IMAGE" "$CANDIDATE_IMAGE"',
    'docker tag "$FROZEN_IMAGE" "$STABLE_IMAGE"',
}
_PREBIRTH_ORDER = (
    "Checkout production promotion commit",
    "Resolve exact frozen candidate",
    "Locate successful Predeploy V2 artifact",
    "Download and verify frozen artifact",
    "Bound source-bearing SSH arguments before host contact",
    "Reject unsafe legacy shared-Docker source before host contact",
    "Require later exact owner authorization before host work",
    "Prepare strict SSH",
    "Pin exact DigitalOcean Droplet before host mutation",
    "Verify independent OCI prebirth before host mutation",
)
_EXACT_RELEASE_SELECTOR = "Use only exact R252-verified R251 D release artifact identity"
_PREBIRTH_RELEASE_ORDER = _PREBIRTH_ORDER[:2] + (
    _EXACT_RELEASE_SELECTOR,
) + _PREBIRTH_ORDER[3:]
_ALLOWED_READ_PERMISSIONS = frozenset({"actions", "contents", "issues", "pull-requests"})
_LEGACY_RELEASE_STEPS = ("Refuse host promotion without exact trusted product-release audit",)
_TRUSTED_RELEASE_STEPS = (
    "Reject unpinned postmerge verifier or wrong product event",
    "Checkout immutable R252 trusted-main verifier",
    "Authenticate exact R251 D/E receipts through R252",
    "Hold host control pending independent physical authority",
)
_TRUSTED_MAIN_R252_SHA = "1b397b2cc5cf7645e5dfcb9abc4566a89efe8d96"
_REVIEWED_RELEASE_STEP_HASHES = {
    # R164 historical hard HOLD; accepted ONLY with its exact original script.
    _LEGACY_RELEASE_STEPS[0]: "3407617b682e18ab64b85945f66984978d9a715b193f10edfd7acd6b1127a943",
    # R262 reviewed R253 REST auto-discovery adapter, host remains HOLD79.
    _TRUSTED_RELEASE_STEPS[0]: "42c1b953150f2cb38f60311f1e3be483a4dd019d4896ef075a4b9266811d1445",
    _TRUSTED_RELEASE_STEPS[2]: "18ae8cf3ca8bdb4a0b77e2fc5522e7c1b1ea5c5a1b2366c0baa687634de18cc8",
    _TRUSTED_RELEASE_STEPS[3]: "7b2a7c3aa3c804605468af8125d3d634dd03df66920ff01774b8d78cd54f20c9",
}
_REVIEWED_RELEASE_REF_ENV = {
    "RC6_EXPECTED_PRODUCT_REF": "refs/heads/deploy/rc6-pr69-isolated-20260915",
}
_REVIEWED_RELEASE_RECEIPT_ENV = {
    "GITHUB_TOKEN": "${{ github.token }}",
    "RC6_BRIDGE_MODE": "PRODUCT_POSTMERGE",
    "RC6_BRIDGE_REPOSITORY": "${{ github.repository }}",
    "RC6_BRIDGE_PRODUCT_SHA": "${{ github.sha }}",
    "RC6_BRIDGE_PRODUCT_REF": "${{ github.ref }}",
    "RC6_BRIDGE_CANDIDATE_SHA": "",
    "RC6_BRIDGE_CANDIDATE_TREE_SHA": "",
    "RC6_BRIDGE_PUBLISHER_MAIN_SHA": "",
    "RC6_BRIDGE_E_RUN_ID": "",
    "RC6_BRIDGE_E_RUN_ATTEMPT": "",
    "RC6_BRIDGE_E_ARTIFACT_ID": "",
}


def _audit_release_evidence_job(release: Mapping[str, Any]) -> None:
    """Permit only bounded, read-only provenance checks before host prebirth.

    This job must never handle Droplet credentials or host operations. Its
    evidence output cannot lift physical authorization; the host job has a
    separate unmet runtime gate. No code from the candidate is trusted here.
    """
    if (not isinstance(release, Mapping) or
            release.get("runs-on") != "ubuntu-24.04" or
            type(release.get("timeout-minutes")) is not int or
            not 1 <= release["timeout-minutes"] <= 45 or
            release.get("continue-on-error") not in (None, False) or
            set(release) - {"name", "runs-on", "timeout-minutes", "permissions",
                            "steps", "env", "outputs"} or
            not isinstance(release.get("outputs", {}), Mapping) or
            not isinstance(release.get("env", {}), Mapping)):
        raise HostBoundaryViolation("RELEASE_EVIDENCE_JOB_UNSAFE")
    permissions = release.get("permissions")
    if (not isinstance(permissions, Mapping) or
            not set(permissions).issubset(_ALLOWED_READ_PERMISSIONS) or
            any(value != "read" for value in permissions.values())):
        raise HostBoundaryViolation("RELEASE_EVIDENCE_PERMISSIONS")
    if re.search(r"\bsecrets\s*\.", json.dumps(release)):
        raise HostBoundaryViolation("RELEASE_EVIDENCE_SECRET_SCOPE")
    if "host_control_verified" in release.get("outputs", {}):
        # An offhost byte-evidence job cannot mint physical host authority.
        raise HostBoundaryViolation("RELEASE_EVIDENCE_HOST_AUTHORITY_FORGED")
    steps = release.get("steps")
    if not isinstance(steps, list) or not steps:
        raise HostBoundaryViolation("RELEASE_EVIDENCE_STEPS_INVALID")
    for step in steps:
        if not isinstance(step, Mapping):
            raise HostBoundaryViolation("RELEASE_EVIDENCE_STEPS_INVALID")
        if step.get("continue-on-error") not in (None, False):
            raise HostBoundaryViolation("RELEASE_EVIDENCE_FAILURE_SUPPRESSION")
        action = step.get("uses")
        if action is not None:
            if (not isinstance(action, str) or
                    re.fullmatch(r"actions/checkout@(?:v4|[0-9a-f]{40})", action) is None or
                    "run" in step):
                raise HostBoundaryViolation("RELEASE_EVIDENCE_ACTION_UNTRUSTED")
            opts = step.get("with")
            if (action != "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" or
                    not isinstance(opts, Mapping) or
                    opts.get("persist-credentials") is not False or
                    opts.get("path") != ".rc6-release-guard-trusted" or
                    set(opts) - {"repository", "ref", "path", "persist-credentials"} or
                    opts.get("repository", "mbalbo2023/Porota-trading") !=
                    "mbalbo2023/Porota-trading" or
                    opts.get("ref") != "${{ env.RC6_R252_TRUSTED_MAIN_SHA }}"):
                raise HostBoundaryViolation("RELEASE_EVIDENCE_CHECKOUT_CREDENTIALS")
            continue
        command = step.get("run")
        if not isinstance(command, str) or step.get("shell") != "bash":
            raise HostBoundaryViolation("RELEASE_EVIDENCE_STEPS_INVALID")
        active = "\n".join(line for line in command.splitlines()
                           if not re.match(r"^\s*#", line))
        if re.search(r"\b(?:sudo|ssh|docker|systemctl|systemd-run)\s+(?:-\S+\s+)*\S", active):
            raise HostBoundaryViolation("RELEASE_EVIDENCE_HOST_MUTATION")
        if re.search(r"\bcurl\b[^\n]*\s(?:-X|--request)\s*(?:POST|PUT|PATCH|DELETE)\b", active):
            raise HostBoundaryViolation("RELEASE_EVIDENCE_MUTATING_HTTP")
    last = steps[-1]
    last_lines = ([line.strip() for line in last["run"].splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
                  if isinstance(last.get("run"), str) else [])
    if last.get("if") or not last_lines or last_lines[-1] != "exit 79":
        raise HostBoundaryViolation("RELEASE_EVIDENCE_FINAL_HOLD_MISSING")
    names = tuple(step.get("name") for step in steps)
    if names not in (_LEGACY_RELEASE_STEPS, _TRUSTED_RELEASE_STEPS):
        # Without an exact reviewed step set a candidate could inject arbitrary
        # Python, HTTP calls, or token export and still leave a final exit79.
        raise HostBoundaryViolation("RELEASE_EVIDENCE_STEPS_INVALID")
    job_env = release.get("env", {})
    if names == _LEGACY_RELEASE_STEPS:
        if job_env:
            raise HostBoundaryViolation("RELEASE_EVIDENCE_ENV_DRIFT")
        expected_envs = (_REVIEWED_RELEASE_REF_ENV,)
        step_key_sets = ({"name", "shell", "env", "run"},)
    else:
        if (set(job_env) != {"RC6_R252_TRUSTED_MAIN_SHA"} or
                type(job_env["RC6_R252_TRUSTED_MAIN_SHA"]) is not str or
                re.fullmatch(r"[0-9a-f]{40}", job_env["RC6_R252_TRUSTED_MAIN_SHA"]) is None):
            raise HostBoundaryViolation("RELEASE_EVIDENCE_ENV_DRIFT")
        if job_env["RC6_R252_TRUSTED_MAIN_SHA"] != _TRUSTED_MAIN_R252_SHA:
            raise HostBoundaryViolation("RELEASE_EVIDENCE_TRUSTED_MAIN_PIN_DRIFT")
        expected_envs = (_REVIEWED_RELEASE_REF_ENV, {},
                         _REVIEWED_RELEASE_RECEIPT_ENV, {})
        step_key_sets = (
            {"name", "shell", "env", "run"},
            {"name", "uses", "with"},
            {"name", "id", "shell", "env", "run"},
            {"name", "shell", "run"},
        )
    for step, expected_env, keys in zip(steps, expected_envs, step_key_sets):
        if set(step) != keys or (step.get("id") is not None and
                                  step["id"] != "trusted_release"):
            raise HostBoundaryViolation("RELEASE_EVIDENCE_STEP_METADATA_DRIFT")
        # BASH_ENV/ENV, interpreter injection, command search path and Actions
        # secret shadowing would execute unseen code before a hashed script.
        if step.get("env", {}) != expected_env:
            raise HostBoundaryViolation("RELEASE_EVIDENCE_ENV_DRIFT")
    for step in steps:
        if step.get("uses"):
            continue
        digest = hashlib.sha256(step["run"].encode("utf-8")).hexdigest()
        if digest != _REVIEWED_RELEASE_STEP_HASHES.get(step.get("name")):
            code = ("RELEASE_EVIDENCE_FINAL_HOLD_MISSING" if step is last
                    else "RELEASE_EVIDENCE_SOURCE_DRIFT")
            raise HostBoundaryViolation(code)


def reject_untrusted_checkout_privilege(source: str) -> None:
    """Reject indirect sudo/root execution even through an unprivileged shell.

    The current PRODUCT still contains these historical idioms; the deploy
    Action MUST exit 79 before any SSH until independent installed fixed-verb
    replacements are integrated. A source hash from the same checkout is not
    independent host approval.
    """
    if "sudo -n python3 -c 'import base64;exec(base64.b64decode" in source:
        raise HostBoundaryViolation("ROOT_CHECKOUT_MUTEX_UNTRUSTED")
    if re.search(r"\$HOUSEKEEPING_B64[^\n]*\|\s*base64\s+-d\s*\|\s*sudo\s+-n\s+python3\s+-", source):
        raise HostBoundaryViolation("ROOT_CHECKOUT_HOUSEKEEPING_UNTRUSTED")
    if re.search(r'\bbash\s+["\']\$STAGE/ops/rc6_deploy_critical_control_plane\.sh["\']', source):
        raise HostBoundaryViolation("STAGED_CHECKOUT_SUDO_BYPASS")
    # A variation in whitespace, interpreter path or base64 spelling must not
    # turn legacy checkout-root execution into an accepted host operation.
    # This is a DENY, not an alternative installer or physical authority.
    active = re.sub(r"[ \t]*\\\n[ \t]*", " ", source)
    # The reviewed root helper is invoked as one fixed executable directly.
    # Allowing caller-selected sudo switches (--, -u, -s, -i, -E, etc.)
    # would also allow a shell or an unreviewed interpreter entrypoint.
    if re.search(r"\bsudo\s+-n\s+-", active):
        raise HostBoundaryViolation("ROOT_CHECKOUT_INTERPRETER_UNTRUSTED")
    if _CHECKOUT_ROOT_INTERPRETER.search(active):
        raise HostBoundaryViolation("ROOT_CHECKOUT_INTERPRETER_UNTRUSTED")
    # Enumerating known interpreters is incomplete: timeout, nice, awk,
    # find/xargs, tee/install and future binaries can all execute or replace
    # root-owned code. Deny every literal sudo command except the direct,
    # absolute installed fixed-verb entrypoint. This remains a NEGATIVE
    # source filter; it does not validate its physical installation or grant
    # host_control_verified.
    uncommented = "\n".join(line for line in active.splitlines()
                            if not line.lstrip().startswith("#"))
    for match in _SUDO_WORD.finditer(uncommented):
        if _REVIEWED_SUDO_FIXED_EXEC.match(uncommented, match.start()) is None:
            raise HostBoundaryViolation("ROOT_CHECKOUT_PRIVILEGE_UNTRUSTED")


def require_approved_critical_stage_cli(promotion_text: str) -> None:
    """Pin the workflow's fixed CLI shape; do not infer staging authority."""
    active = "\n".join(line for line in promotion_text.splitlines()
                       if not re.match(r"^\s*#", line))
    active = re.sub(r"[ \t]*\\\n[ \t]*", " ", active)
    approved = (
        'STAGE_RECEIPT="$(sudo -n "$CUSTODY_CLI" critical '
        'stage-approved-protector --paper-only --source-sha "$CANDIDATE_SHA")"'
    )
    verbs = re.findall(r"\bcritical\s+stage-[a-z-]+\b", active)
    if (active.count(approved) != 1 or
            verbs != ["critical stage-approved-protector"]):
        raise HostBoundaryViolation("CRITICAL_STAGE_CLI_UNTRUSTED")


def audit_deploy_workflow(workflow: Mapping[str, Any]) -> dict[str, Any]:
    """Reject known unsafe host operations and return a non-authorizing HOLD.

    Source inspection deliberately cannot turn into physical authority. The
    canonical Action must independently verify an installed trusted helper.
    """
    if not isinstance(workflow, Mapping):
        raise HostBoundaryViolation("INVALID_WORKFLOW")
    concurrency = workflow.get("concurrency")
    if concurrency != {"group": "rc6-unified-paper-deploy", "cancel-in-progress": False}:
        raise HostBoundaryViolation("UNSAFE_DEPLOY_MUTEX")
    jobs = workflow.get("jobs")
    if not isinstance(jobs, Mapping) or set(jobs) != {"release-evidence-required", "promote"}:
        raise HostBoundaryViolation("UNREVIEWED_HOST_JOB")
    _audit_release_evidence_job(jobs["release-evidence-required"])
    promote = jobs["promote"]
    if not isinstance(promote, Mapping) or promote.get("continue-on-error") not in (None, False):
        raise HostBoundaryViolation("PROMOTION_FAILURE_SUPPRESSION")
    if promote.get("needs") != "release-evidence-required":
        raise HostBoundaryViolation("HOST_RELEASE_DEPENDENCY_MISSING")
    condition = promote.get("if")
    if (not isinstance(condition, str) or
            "needs.release-evidence-required.result == 'success'" not in condition or
            "needs.release-evidence-required.outputs.release_bytes_verified == 'true'" not in condition):
        raise HostBoundaryViolation("HOST_RELEASE_DEPENDENCY_MISSING")
    steps = promote.get("steps")
    if not isinstance(steps, list) or not all(isinstance(s, Mapping) for s in steps):
        raise HostBoundaryViolation("INVALID_HOST_STEPS")
    for index, step in enumerate(steps):
        action = step.get("uses")
        if action is not None and (index != 0 or
                                   not isinstance(action, str) or
                                   re.fullmatch(r"actions/checkout@(?:v4|[0-9a-f]{40})", action) is None):
            raise HostBoundaryViolation("UNREVIEWED_PROMOTE_ACTION")

    prebirth_index = None
    first_mutator_index = None
    promotion_text = None
    for index, step in enumerate(steps):
        source = step.get("run", "")
        if not isinstance(source, str):
            raise HostBoundaryViolation("UNSUPPORTED_ACTION_RUN")
        # Ignore complete shell comment lines, including escaped literals in
        # explanatory comments, but never ignore active shell expressions.
        active = "\n".join(line for line in source.splitlines()
                           if not re.match(r"^\s*#", line))
        active = re.sub(r"\\\s*\n\s*", " ", active)
        if _GLOBAL_DOCKER_INVENTORY.search(active):
            raise HostBoundaryViolation("GLOBAL_DOCKER_INVENTORY")
        if _DIRECT_CRITICAL_RESTART.search(active):
            raise HostBoundaryViolation("DIRECT_CRITICAL_RESTART")
        if _PPI_WATCH_MUTATION.search(active):
            raise HostBoundaryViolation("PPI_WATCH_MUTATION")
        if _MUTATION_BY_NAME.search(active):
            raise HostBoundaryViolation("DOCKER_MUTATION_BY_NAME")
        if _CANDIDATE_ROOT_SHELL.search(active):
            raise HostBoundaryViolation("CANDIDATE_ROOT_SHELL")
        # Docker CREATE and arbitrary one-shots can join unrelated host
        # namespaces even without an explicit stop. The two existing RC6
        # audit one-shots are read-only, unprivileged and image-ID-bound.
        runs = list(re.finditer(r"\bdocker\s+run\b", active))
        if re.search(r"\bdocker\s+(?:create|compose)\b", active):
            raise HostBoundaryViolation("UNREVIEWED_DOCKER_BIRTH")
        if runs:
            allowed = (step.get("name") == "Promote exact frozen candidate and verify" and
                       len(runs) == 2 and
                       '--entrypoint python "$RUNTIME_IMAGE_ID" ck_contract_evidence_runner_hf6.py' in active and
                       '--entrypoint python -i "$RUNTIME_IMAGE_ID"' in active and
                       active.count("--cap-drop ALL") >= 2 and
                       active.count("--read-only") >= 2 and
                       '--security-opt no-new-privileges:true' in active and
                       '--pull never' in active and
                       '--name "$CONTRACT_NAME"' in active)
            if not allowed or re.search(
                    r"--privileged\b|--network(?:=|\s+)host\b|--pid(?:=|\s+)host\b|"
                    r"--device\b|--cap-add\b|--volumes-from\b|/var/run/docker\.sock", active):
                raise HostBoundaryViolation("UNREVIEWED_DOCKER_BIRTH")
        tag_commands = re.findall(r"\bdocker\s+tag[^\n]*", active)
        if tag_commands:
            if (step.get("name") != "Promote exact frozen candidate and verify" or
                    len(tag_commands) != 2 or
                    set(map(str.strip, tag_commands)) != _APPROVED_TAGS):
                raise HostBoundaryViolation("UNREVIEWED_DOCKER_TAG")

        if step.get("name") == "Verify independent OCI prebirth before host mutation":
            if prebirth_index is not None or step.get("if") or step.get("continue-on-error"):
                raise HostBoundaryViolation("UNTRUSTED_PREBIRTH_GATE")
            if (step.get("shell") != "bash" or
                    "set -Eeuo pipefail" not in source or
                    _PREBIRTH_HELPER not in source or
                    "RC6_PREBIRTH_ATTESTOR=GREEN" not in source):
                raise HostBoundaryViolation("UNTRUSTED_PREBIRTH_GATE")
            prebirth_index = index

        if step.get("name") == "Promote exact frozen candidate and verify":
            promotion_text = source
        if first_mutator_index is None and step.get("name") == (
                "Reconcile only exact A138 cleanly exited host barrier"):
            first_mutator_index = index

    if (prebirth_index is None or first_mutator_index is None or
            prebirth_index >= first_mutator_index):
        raise HostBoundaryViolation("PREBIRTH_NOT_BEFORE_MUTATION")
    inspected = tuple(step.get("name") for step in steps[:prebirth_index + 1])
    if inspected not in (_PREBIRTH_ORDER, _PREBIRTH_RELEASE_ORDER):
        raise HostBoundaryViolation("UNREVIEWED_PREBIRTH_STEP")
    if inspected == _PREBIRTH_ORDER:
        release = jobs["release-evidence-required"]
        # Old newest-by-date artifact selection is tolerated solely while
        # R164's one-step, exact-source, no-outputs unconditional HOLD79 is in
        # force. It can never accompany the R253 receipt-enabled release job.
        if (tuple(s.get("name") for s in release["steps"]) != _LEGACY_RELEASE_STEPS or
                release.get("outputs")):
            raise HostBoundaryViolation("LEGACY_ARTIFACT_HOLD_REQUIRED")
    if inspected == _PREBIRTH_RELEASE_ORDER:
        # The new path cannot fall back to the old newest-by-date artifact;
        # trusted R252 must supply the exact authenticated D identity.
        selector = steps[2].get("run", "")
        if (not isinstance(selector, str) or
                "max(candidates" in selector or "created_at" in selector or
                any(field not in selector for field in (
                    "RC6_RELEASE_ARTIFACT_ID", "RC6_RELEASE_ARTIFACT_DIGEST",
                    "RC6_RELEASE_RUN_ID", "selection=R252_D_E_ONLY"))):
            raise HostBoundaryViolation("UNTRUSTED_RELEASE_ARTIFACT_SELECTOR")
        if "needs.release-evidence-required.outputs.host_control_verified == 'true'" not in condition:
            raise HostBoundaryViolation("HOST_CONTROL_AUTHORITY_UNBOUND")
    # The single identity pin may use read-only SSH. Before that, no step may
    # invoke ssh/sudo or an unreviewed host process. The prebirth step may
    # call only the exact fixed root-owned attestor once, never broad sudo.
    for index, step in enumerate(steps[:prebirth_index + 1]):
        source = step.get("run", "")
        active = "\n".join(line for line in source.splitlines()
                           if not re.match(r"^\s*#", line))
        sudo_count = len(re.findall(r"\bsudo\s+-n\b", active))
        # A renamed but otherwise allowlisted step must not acquire a hidden
        # second interpreter, nested Python subprocess, Docker or systemd
        # route before the independently verified root-owned prebirth gate.
        if re.search(r"\b(?:subprocess|Popen|docker|systemctl)\b|"
                     r"\bos\.(?:system|popen|exec\w*)\s*\(|"
                     r"\b(?:eval|exec)\s*\(", active):
            raise HostBoundaryViolation("PREBIRTH_HOST_PRIVILEGE_BYPASS")
        if index == prebirth_index:
            if (sudo_count != 1 or _PREBIRTH_HELPER not in active or
                    len(re.findall(r"\bsudo\b", active)) != 1):
                raise HostBoundaryViolation("UNREVIEWED_PREBIRTH_PRIVILEGE")
        elif re.search(r"\bsudo\b", active) or (index < _PREBIRTH_ORDER.index(
                "Pin exact DigitalOcean Droplet before host mutation") and
                re.search(r"(?<![.\w])ssh\s+-", active)):
            raise HostBoundaryViolation("PREBIRTH_HOST_PRIVILEGE_BYPASS")
    if promotion_text is None:
        raise HostBoundaryViolation("PROMOTION_STEP_MISSING")
    require_approved_critical_stage_cli(promotion_text)
    mode_line = "grep -q 'POROTA_MODE=PENDING_CUSTODY_SEAL'"
    if mode_line not in promotion_text or "grep -q 'POROTA_MODE=OK'" in promotion_text:
        raise HostBoundaryViolation("PREMATURE_MODE_READY")
    if "sudo -n /usr/local/sbin/porota-rc6-oci-custody seal" not in promotion_text:
        raise HostBoundaryViolation("CUSTODY_SEAL_NOT_ENFORCED")
    # Any postseal lookup/exec on the old human-readable names can resolve
    # stale legacy CIDs, not the newly sealed generation. Reject outright;
    # all postseal Docker calls must consume immutable CIDs from current proof.
    after_manager_pending = promotion_text.split(mode_line, 1)[1]
    for forbidden in ("porota_production_observer",
                      "porota_production_dashboard", "porota_critical_approval_rc6"):
        if forbidden in after_manager_pending:
            raise HostBoundaryViolation("POSTSEAL_LEGACY_NAME")
    # Later finalizer/classifier jobs must re-open the independently root-owned
    # current receipt; local SSH variables never cross Actions steps.
    promote_index = next(i for i, step in enumerate(steps)
                         if step.get("name") == "Promote exact frozen candidate and verify")
    for step in steps[promote_index + 1:]:
        source = step.get("run", "")
        if any(name in source for name in ("porota_production_observer",
                                           "porota_production_dashboard",
                                           "porota_critical_approval_rc6")):
            raise HostBoundaryViolation("POSTSEAL_LEGACY_NAME")
    # This deliberately denies the current legacy candidate. Only a fixed,
    # root-owned installed mutex/housekeeping/protector can lift the HOLD79;
    # there is NO "legacy permitted" branch or GitHub source override.
    for step in steps:
        reject_untrusted_checkout_privilege(step.get("run", ""))
    return {
        "status": "BLOQUEADO",
        "host_mutation_authorized": False,
        "prebirth_before_mutation": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        workflow = yaml.safe_load(args.workflow.read_text(encoding="utf-8"))
        receipt = audit_deploy_workflow(workflow)
    except HostBoundaryViolation as exc:
        reason = str(exc)
    except (OSError, UnicodeError, yaml.YAMLError, TypeError):
        reason = "SOURCE_GUARD_INPUT_UNAVAILABLE"
    else:
        print(json.dumps(receipt, sort_keys=True))
        return 0
    # Only bounded enum-like reasons may be printed in public Actions logs;
    # never interpolate raw workflow source or host/private paths.
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", reason) is None:
        reason = "UNSAFE_HOST_SOURCE"
    print(json.dumps({"status": "BLOQUEADO", "source_validated": False,
                      "host_mutation_authorized": False, "reason": reason},
                     sort_keys=True))
    return 79


if __name__ == "__main__":
    raise SystemExit(main())
