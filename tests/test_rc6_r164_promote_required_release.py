"""R164 permanent fail-closed regression for Deploy V2 product promotion.

Run with Python 3.11 stdlib:
    python3 -I -m unittest discover -s tests -p test_rc6_r164_promote_required_release.py -v

Targeted YAML-shape/semantic static guard, not a substitute for GitHub Actions
parsing, R252 trusted RELEASE byte verification, or host preflight. R253 has a
separately pinned source and an independent post-audit host HOLD79.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "porota-deploy-v2-promote.yml"
)
PRODUCT_BRANCH = "deploy/rc6-pr69-isolated-20260915"
REVIEWED_R252_MAIN_SHA = "f831e2929d1a622f409211010fadaece6b15e953"
EXPECTED_IF = (
    "$" + "{{ needs.release-evidence-required.result == 'success' && "
    "needs.release-evidence-required.outputs.release_bytes_verified == 'true' && "
    "needs.release-evidence-required.outputs.release_product_sha == github.sha && "
    "needs.release-evidence-required.outputs.release_candidate_sha != '' && "
    "needs.release-evidence-required.outputs.release_candidate_tree != '' && "
    "needs.release-evidence-required.outputs.release_artifact_id != '' && "
    "needs.release-evidence-required.outputs.release_artifact_digest != '' && "
    "needs.release-evidence-required.outputs.release_run_id != '' && "
    "needs.release-evidence-required.outputs.release_run_attempt != '' && "
    "needs.release-evidence-required.outputs.trusted_main_audit_run_id != '' && "
    "needs.release-evidence-required.outputs.trusted_main_audit_run_attempt != '' && "
    "needs.release-evidence-required.outputs.host_control_verified == 'true' }}"
)


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise AssertionError("RC6_R164_PROMOTE_GUARD_RED:" + reason)


def _job_blocks(source: str) -> tuple[str, dict[str, str]]:
    """Separate top-level jobs by exact indentation; fail on ambiguous YAML."""
    _require(source.endswith("\n"), "WORKFLOW_NO_FINAL_NEWLINE")
    split = re.split(r"(?m)^jobs:[ \t]*\n", source)
    _require(len(split) == 2, "JOBS_KEY_AMBIGUOUS")
    header, body = split
    _require(
        re.search(
            r"(?m)^on:\n  push:\n    branches:\n"
            rf"      - {re.escape(PRODUCT_BRANCH)}\n",
            header,
        )
        is not None,
        "PRODUCT_PUSH_TRIGGER_CHANGED",
    )
    _require(
        re.search(
            r"(?m)^concurrency:\n"
            r"  group: rc6-unified-paper-deploy\n"
            r"  cancel-in-progress: false\n",
            header,
        )
        is not None,
        "GLOBAL_MUTEX_CHANGED",
    )
    _require(
        re.search(r"(?m)^  workflow_dispatch:|^  workflow_run:|^  repository_dispatch:", header)
        is None,
        "UNREVIEWED_ALTERNATE_EVENT",
    )
    _require(
        all(not line or line.startswith((" ", "#")) for line in body.splitlines()),
        "TOP_LEVEL_KEY_AFTER_JOBS",
    )
    markers = list(re.finditer(r"(?m)^  ([a-z][a-z0-9-]*):[ \t]*$", body))
    _require(bool(markers), "NO_JOB_BLOCKS")
    _require(
        all(not line.strip() or line.lstrip().startswith("#")
            for line in body[: markers[0].start()].splitlines()),
        "UNRECOGNIZED_JOB_PREAMBLE",
    )
    jobs: dict[str, str] = {}
    for index, marker in enumerate(markers):
        name = marker.group(1)
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        _require(name not in jobs, "DUPLICATE_JOB_" + name)
        jobs[name] = body[marker.end() : end]
    _require(
        set(jobs) == {"release-evidence-required", "promote"},
        "UNREVIEWED_JOB_OR_MISSING_REQUIRED_JOB",
    )
    return header, jobs


def assert_product_promotion_fail_closed(source: str) -> None:
    """Reject any missing, bypassable, or weak pre-host RELEASE trust gate."""
    _header, jobs = _job_blocks(source)
    gate = jobs["release-evidence-required"]
    promote = jobs["promote"]

    _require(
        re.search(r"(?m)^    runs-on: ubuntu-24\.04$", gate) is not None,
        "GATE_RUNNER_CHANGED",
    )
    _require(
        re.search(
            r"(?m)^    permissions:\n      contents: read\n      actions: read\n"
            r"      pull-requests: read\n",
            gate,
        )
        is not None,
        "GATE_PERMISSIONS_NOT_READONLY",
    )
    _require(
        f"RC6_R252_TRUSTED_MAIN_SHA: '{REVIEWED_R252_MAIN_SHA}'" in gate,
        "R252_MAIN_SHA_NOT_EXACT_REVIEWED_PIN",
    )
    _require(
        len(re.findall(r"(?m)^    outputs:\n", gate)) == 1 and
        "host_control_verified:" not in gate,
        "UNAUTHORIZED_HOST_RECEIPT_OUTPUT",
    )
    _require(
        re.search(
            rf"(?m)^          RC6_EXPECTED_PRODUCT_REF: refs/heads/{re.escape(PRODUCT_BRANCH)}$",
            gate,
        )
        is not None,
        "PRODUCT_REF_NOT_PINNED",
    )
    _require(
        len(re.findall(r"(?m)^      - name: ", gate)) == 4,
        "R253_GUARD_STEP_COUNT_DRIFT",
    )
    steps = re.split(r"(?m)^      - name: ", gate)
    _require(len(steps) == 5, "MISSING_R253_STEP")
    preflight, checkout, audit, host_hold = steps[1:]
    _require(
        preflight.startswith("Reject unpinned postmerge verifier or wrong product event\n")
        and "        shell: bash\n" in preflight
        and ('        run: |\n          set -Eeuo pipefail\n'
             '          if [[ "$GITHUB_EVENT_NAME" != push ||') in preflight,
        "R253_PREFLIGHT_BASH_CHANGED",
    )
    _require(
        re.search(
            r"(?m)^          if \[\[ "
            r'"\$GITHUB_EVENT_NAME" != push \|\| '
            r'"\$GITHUB_REF" != "\$RC6_EXPECTED_PRODUCT_REF" \]\]; then$',
            preflight,
        )
        is not None,
        "SOURCE_EVENT_OR_REF_GUARD_REMOVED",
    )
    _require(
        re.search(r'(?m)^          if \[\[ ! "\$GITHUB_SHA" =~ \^\[0-9a-f\]\{40\}\$ \]\]; then$', preflight)
        is not None,
        "PRODUCT_SHA_FORMAT_GUARD_REMOVED",
    )
    _require(
        "RC6_R164_RELEASE_REQUIRED=HOLD79|reason=TRUSTED_MAIN_SOURCE_UNPINNED"
        in preflight and "$RC6_R252_TRUSTED_MAIN_SHA" in preflight,
        "TRUSTED_SOURCE_HOLD_MISSING",
    )
    _require(
        len(re.findall(r"(?m)^ {10,}exit 79[ \t]*$", preflight)) == 3,
        "MUST_EXIT79_ON_EACH_PREFLIGHT_BRANCH",
    )
    _require(
        "Checkout immutable R252 trusted-main verifier\n" in checkout
        and "        uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683\n" in checkout
        and "          ref: ${{ env.RC6_R252_TRUSTED_MAIN_SHA }}\n" in checkout
        and "          persist-credentials: false\n" in checkout,
        "TRUSTED_MAIN_PINNED_CHECKOUT_CHANGED",
    )
    _require(
        "Authenticate exact R251 D/E receipts through R252\n" in audit
        and "          GITHUB_TOKEN: ${{ github.token }}\n" in audit
        and ("PYTHONDONTWRITEBYTECODE=1 python3 -I "
             ".rc6-release-guard-trusted/scripts/rc6_ci_release_required_guard.py") in audit
        and "--trusted-release-mode PRODUCT_POSTMERGE" in audit
        and "RC6_R164_RELEASE_BYTES=VERIFIED|host_authority=HOLD79" in audit,
        "UNTRUSTED_R252_REPLACED_OR_BYPASSED",
    )
    _require(
        all(field in audit for field in (
            "RC6_BRIDGE_CANDIDATE_SHA", "RC6_BRIDGE_CANDIDATE_TREE_SHA",
            "RC6_BRIDGE_PUBLISHER_MAIN_SHA", "RC6_BRIDGE_E_RUN_ID",
            "RC6_BRIDGE_E_RUN_ATTEMPT", "RC6_BRIDGE_E_ARTIFACT_ID",
            "RC6_BRIDGE_PRODUCT_SHA", "RC6_BRIDGE_PRODUCT_REF",
        )),
        "R252_D_E_POSTMERGE_BINDING_MISSING",
    )
    _require(
        all(f"          {field}: ''\n" in audit for field in (
            "RC6_BRIDGE_CANDIDATE_SHA", "RC6_BRIDGE_CANDIDATE_TREE_SHA",
            "RC6_BRIDGE_PUBLISHER_MAIN_SHA", "RC6_BRIDGE_E_RUN_ID",
            "RC6_BRIDGE_E_RUN_ATTEMPT", "RC6_BRIDGE_E_ARTIFACT_ID",
        )) and "${{ vars.RC6_R251_" not in audit,
        "R252_UNTRUSTED_SELECTOR_VARIABLES_REINTRODUCED",
    )
    _require(
        "Hold host control pending independent physical authority\n" in host_hold
        and "RC6_R164_HOST_CONTROL_HOLD79" in host_hold
        and host_hold.rstrip().endswith("          exit 79"),
        "HOST_HOLD79_REMOVED",
    )
    _require(
        re.search(r"(?m)^\s+continue-on-error:", gate) is None,
        "GATE_CONTINUE_ON_ERROR",
    )
    _require(
        re.search(r"(?m)^\s+if:", gate) is None,
        "CONDITIONAL_GATE_BYPASS",
    )
    _require(
        re.search(r"(?m)^\s*(?:sudo|ssh|docker)\b|secrets\.",
                  re.sub(r"(?m)^\s*#.*$", "", gate), flags=re.IGNORECASE)
        is None,
        "PREHOST_HOST_COMMAND_FORBIDDEN",
    )
    _require(
        len(re.findall(r"(?m)^    needs: release-evidence-required[ \t]*$", promote))
        == 1,
        "PROMOTE_NOT_DEPENDENT_ON_REQUIRED_GATE",
    )
    _require(
        len(re.findall(r"(?m)^    needs:", promote)) == 1,
        "PROMOTE_JOB_HAS_ALTERNATE_NEEDS",
    )
    statements = list(re.finditer(r"(?m)^    if: >-\n((?:^ {6}.*\n)+)", promote))
    _require(len(statements) == 1, "PROMOTE_REQUIRED_IF_MISSING")
    actual_if = re.sub(r"\s+", "", statements[0].group(1))
    expected_if = re.sub(r"\s+", "", EXPECTED_IF)
    _require(actual_if == expected_if, "PROMOTE_RELEASE_IDENTITY_OR_AUDIT_BYPASS")
    _require(
        len(re.findall(r"(?m)^    if:", promote)) == 1,
        "PROMOTE_ALTERNATE_JOB_IF",
    )
    _require(
        re.search(
            r"(?m)^ {4}if:[ \t]*\$\{\{\s*(?:always|failure)\(", promote
        )
        is None,
        "PROMOTE_ALWAYS_OR_FAILURE_BYPASS",
    )
    _require(
        re.search(r"(?m)^    continue-on-error:", promote) is None,
        "PROMOTE_JOB_CAN_IGNORE_GUARD",
    )
    _require(
        re.search(r"(?m)^    steps:", promote) is not None
        and "      - name: Prepare strict SSH\n" in promote,
        "PROMOTE_HOST_STEPS_REMOVED_OR_MOVED",
    )


class R164PromoteRequiredReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_current_workflow_must_require_trusted_release_before_host(self) -> None:
        assert_product_promotion_fail_closed(self.source)

    def test_security_mutants_are_rejected(self) -> None:
        cases = [
            ("no_prerequisite", "    needs: release-evidence-required\n", ""),
            ("wrong_dependency", "    needs: release-evidence-required\n", "    needs: artifact-gate\n"),
            ("conditional_bypass", "    if: >-\n      ${{ needs.release-evidence-required.result", "    if: ${{ always() }}\n      ${{ needs.release-evidence-required.result"),
            ("job_level_failure_bypass", "    if: >-\n      ${{ needs.release-evidence-required.result", "    if: ${{ failure() }}\n      ${{ needs.release-evidence-required.result"),
            ("drop_SHA_binding", "needs.release-evidence-required.outputs.release_product_sha == github.sha", "true"),
            ("drop_byte_proof", "needs.release-evidence-required.outputs.release_bytes_verified == 'true'", "true"),
            ("drop_artifact_id", "needs.release-evidence-required.outputs.release_artifact_id != ''", "true"),
            ("drop_artifact_digest", "needs.release-evidence-required.outputs.release_artifact_digest != ''", "true"),
            ("drop_trusted_audit_run", "needs.release-evidence-required.outputs.trusted_main_audit_run_id != ''", "true"),
            ("drop_attempt", "needs.release-evidence-required.outputs.release_run_attempt != ''", "true"),
            ("drop_host_control", "needs.release-evidence-required.outputs.host_control_verified == 'true'", "true"),
            ("host_final_stop", "          echo 'RC6_R164_HOST_CONTROL_HOLD79|host_ownership=NO_VERIFICADO|SSH=BLOCKED|sudo=BLOCKED|automatic_go=NO'\n          exit 79", "          echo 'RC6_R164_HOST_CONTROL_HOLD79|host_ownership=NO_VERIFICADO|SSH=BLOCKED|sudo=BLOCKED|automatic_go=NO'\n          exit 0"),
            ("early_exit_before_source_checks", "        run: |\n          set -Eeuo pipefail\n          if [[ \"$GITHUB_EVENT_NAME\"", "        run: |\n          set -Eeuo pipefail\n          exit 0\n          if [[ \"$GITHUB_EVENT_NAME\""),
            ("disable_strict_bash", "        run: |\n          set -Eeuo pipefail\n          if [[ \"$GITHUB_EVENT_NAME\"", "        run: |\n          set +e\n          if [[ \"$GITHUB_EVENT_NAME\""),
            ("pin_mutable_main", f"RC6_R252_TRUSTED_MAIN_SHA: '{REVIEWED_R252_MAIN_SHA}'", "RC6_R252_TRUSTED_MAIN_SHA: main"),
            ("pin_other_valid_sha", f"RC6_R252_TRUSTED_MAIN_SHA: '{REVIEWED_R252_MAIN_SHA}'", "RC6_R252_TRUSTED_MAIN_SHA: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"),
            ("reintroduce_untrusted_E_var", "          RC6_BRIDGE_E_RUN_ID: ''", "          RC6_BRIDGE_E_RUN_ID: ${{ vars.RC6_R251_E_RUN_ID }}"),
            ("checkout_mutable_main", "          ref: ${{ env.RC6_R252_TRUSTED_MAIN_SHA }}", "          ref: main"),
            ("fake_candidate_audit", "PYTHONDONTWRITEBYTECODE=1 python3 -I .rc6-release-guard-trusted/scripts/rc6_ci_release_required_guard.py", "PYTHONDONTWRITEBYTECODE=1 python3 -I scripts/rc6_ci_release_required_guard.py"),
            ("fake_github_event", 'if [[ "$GITHUB_EVENT_NAME" != push', 'if [[ "$GITHUB_EVENT_NAME" == push'),
            ("allow_secrets", "    permissions:\n      contents: read\n      actions: read\n", "    permissions:\n      contents: write\n      actions: read\n"),
            ("ignore_failures", "    timeout-minutes: 30\n    permissions:\n      contents: read\n", "    continue-on-error: true\n    timeout-minutes: 30\n    permissions:\n      contents: read\n"),
            ("unreviewed_host_step", "    steps:\n      - name: Reject unpinned", "    steps:\n      - name: Unexpected host action\n        run: sudo -n true\n      - name: Reject unpinned"),
            ("change_mutex", "  group: rc6-unified-paper-deploy", "  group: rc6-split-promote-mutex"),
            ("new_host_job", "jobs:\n  # R262: R252/R259 trusted verifier", "jobs:\n  bypass-host:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: ssh invalid-host\n  # R262: R252/R259 trusted verifier"),
        ]
        for name, old, new in cases:
            with self.subTest(mutant=name):
                self.assertEqual(
                    self.source.count(old),
                    1,
                    "Mutation anchor drifted; security test must not silently skip",
                )
                mutated = self.source.replace(old, new, 1)
                with self.assertRaises(AssertionError):
                    assert_product_promotion_fail_closed(mutated)


if __name__ == "__main__":
    unittest.main()
