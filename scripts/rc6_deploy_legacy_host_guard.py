"""OFFHOST source boundary for the shared-Docker RC6 promotion workflow.

This guard can reject unsafe workflow shapes, but cannot authorize Docker,
systemd, host cleanup or a deployment. A separately installed, root-owned
custody attestor must still prove each physical operation fail-closed.
"""

from __future__ import annotations

import argparse
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
    if not isinstance(jobs, Mapping) or set(jobs) != {"promote"}:
        raise HostBoundaryViolation("UNREVIEWED_HOST_JOB")
    promote = jobs["promote"]
    if not isinstance(promote, Mapping) or promote.get("continue-on-error") not in (None, False):
        raise HostBoundaryViolation("PROMOTION_FAILURE_SUPPRESSION")
    steps = promote.get("steps")
    if not isinstance(steps, list) or not all(isinstance(s, Mapping) for s in steps):
        raise HostBoundaryViolation("INVALID_HOST_STEPS")

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
    if tuple(step.get("name") for step in steps[:prebirth_index + 1]) != _PREBIRTH_ORDER:
        raise HostBoundaryViolation("UNREVIEWED_PREBIRTH_STEP")
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
    except (OSError, UnicodeError, yaml.YAMLError, HostBoundaryViolation, TypeError):
        print(json.dumps({"status": "BLOQUEADO", "source_validated": False,
                          "host_mutation_authorized": False}, sort_keys=True))
        return 79
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
