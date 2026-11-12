"""R256 source-only host boundary regressions; no host process is started."""

from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest

import yaml

from scripts.rc6_deploy_legacy_host_guard import (
    HostBoundaryViolation,
    audit_deploy_workflow,
    main as guard_main,
    reject_untrusted_checkout_privilege,
)


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-deploy-v2-promote.yml"


def live_source():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


class R256TrustedReleaseJobBoundary(unittest.TestCase):
    def test_existing_release_job_does_not_mask_the_root_checkout_guard(self):
        """An added safe evidence job cannot silence downstream source checks."""
        with self.assertRaisesRegex(HostBoundaryViolation, "ROOT_CHECKOUT_MUTEX_UNTRUSTED"):
            audit_deploy_workflow(live_source())

    def test_guard_cli_reports_exact_source_hold_reason_without_host_authority(self):
        output = io.StringIO()
        with redirect_stdout(output):
            result = guard_main(["--workflow", str(WORKFLOW)])
        self.assertEqual(result, 79)
        self.assertEqual(json.loads(output.getvalue()), {
            "status": "BLOQUEADO",
            "source_validated": False,
            "host_mutation_authorized": False,
            "reason": "ROOT_CHECKOUT_MUTEX_UNTRUSTED",
        })

    def test_root_checkout_python_and_shell_entrypoints_are_untrusted(self):
        # These are direct source boundaries, not mocked host executions.
        # A renamed/encoded checkout interpreter remains arbitrary root code.
        unsafe = (
            "sudo -n python3 -c 'import os;os.system(\"true\")'",
            "sudo -n /usr/bin/python3 -c 'print(1)'",
            "sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 -c 'print(1)'",
            "sudo -n python3 - <<'PY'\nprint(1)\nPY",
            "sudo -n timeout 10 python3 - <<'PY'\nprint(1)\nPY",
            'sudo -n env PYTHONPATH="$STAGE" python3 "$STAGE/scripts/a.py"',
            "sudo -n bash -s <<'REMOTE'\necho executed\nREMOTE",
            "sudo -n /bin/sh -c 'echo executed'",
        )
        for command in unsafe:
            with self.subTest(command=command):
                with self.assertRaisesRegex(
                    HostBoundaryViolation, "ROOT_CHECKOUT_INTERPRETER_UNTRUSTED"
                ):
                    reject_untrusted_checkout_privilege(command)

    def test_altered_mutex_decoder_and_unbounded_subprocess_stay_denied(self):
        unsafe = (
            "sudo -n python3 -c 'import base64 ; exec(base64.b64decode(\"$RC6_MUTEX_B64\"))' --phase reconcile",
            "sudo -n /usr/bin/python3 scripts/rc6_deploy_host_mutex.py --phase classify sh -c 'echo arbitrary'",
        )
        for command in unsafe:
            with self.subTest(command=command):
                with self.assertRaisesRegex(
                    HostBoundaryViolation, "ROOT_CHECKOUT_INTERPRETER_UNTRUSTED"
                ):
                    reject_untrusted_checkout_privilege(command)

    def test_promotion_rejects_legacy_critical_stage_cli_before_mutex_guard(self):
        workflow = live_source()
        promote = next(step for step in workflow["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        self.assertIn("critical stage-approved-protector", promote["run"])
        promote["run"] = promote["run"].replace(
            "critical stage-approved-protector", "critical stage-protector", 1)
        with self.assertRaisesRegex(HostBoundaryViolation, "CRITICAL_STAGE_CLI_UNTRUSTED"):
            audit_deploy_workflow(workflow)

    def test_promotion_rejects_mismatched_stage_source_sha(self):
        workflow = live_source()
        promote = next(step for step in workflow["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        self.assertIn('--paper-only --source-sha "$CANDIDATE_SHA"', promote["run"])
        promote["run"] = promote["run"].replace(
            '--paper-only --source-sha "$CANDIDATE_SHA"',
            '--paper-only --source-sha "$DEPLOY_SHA"', 1)
        with self.assertRaisesRegex(HostBoundaryViolation, "CRITICAL_STAGE_CLI_UNTRUSTED"):
            audit_deploy_workflow(workflow)

    def test_promotion_rejects_stage_cli_extra_checkout_argument(self):
        workflow = live_source()
        promote = next(step for step in workflow["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        self.assertIn('stage-approved-protector', promote["run"])
        promote["run"] = promote["run"].replace(
            '--paper-only --source-sha "$CANDIDATE_SHA")"',
            '--paper-only --source-sha "$CANDIDATE_SHA" --unit-template "$STAGE/critical.service")"',
            1,
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "CRITICAL_STAGE_CLI_UNTRUSTED"):
            audit_deploy_workflow(workflow)

    def test_unknown_extra_job_remains_denied(self):
        workflow = live_source()
        workflow["jobs"]["unreviewed-extra"] = {"runs-on": "ubuntu-latest", "steps": []}
        with self.assertRaisesRegex(HostBoundaryViolation, "UNREVIEWED_HOST_JOB"):
            audit_deploy_workflow(workflow)

    def test_evidence_job_cannot_invoke_docker_through_sudo(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"][0]["run"] += (
            "\nsudo -n docker rm -f boca\n"
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_HOST_MUTATION"):
            audit_deploy_workflow(workflow)

    def test_evidence_job_cannot_gain_droplet_secrets(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["env"] = {
            "DO_HOST": "${{ secrets.DO_HOST }}"
        }
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_SECRET_SCOPE"):
            audit_deploy_workflow(workflow)

    def test_evidence_job_cannot_gain_repository_write_token(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["permissions"] = {
            "contents": "write", "actions": "read"
        }
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_PERMISSIONS"):
            audit_deploy_workflow(workflow)

    def test_trusted_main_verifier_pin_cannot_switch_to_other_valid_sha(self):
        # A 40-hex checkout ref is immutable but not necessarily reviewed.
        # The candidate must not replace R252 with an attacker-controlled SHA.
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["env"]["RC6_R252_TRUSTED_MAIN_SHA"] = "a" * 40
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_TRUSTED_MAIN_PIN_DRIFT"):
            audit_deploy_workflow(workflow)

    def test_evidence_job_cannot_gain_implicit_environment_secrets(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["environment"] = "production"
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_JOB_UNSAFE"):
            audit_deploy_workflow(workflow)

    def test_job_bash_env_cannot_execute_candidate_code_before_reviewed_script(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["env"] = {
            "BASH_ENV": "./candidate.sh",
        }
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_ENV_DRIFT"):
            audit_deploy_workflow(workflow)

    def test_step_bash_env_cannot_execute_candidate_code_before_reviewed_script(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"][0]["env"] = {
            "RC6_EXPECTED_PRODUCT_REF": "refs/heads/deploy/rc6-pr69-isolated-20260915",
            "BASH_ENV": "./candidate.sh",
        }
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_ENV_DRIFT"):
            audit_deploy_workflow(workflow)

    def test_environment_injection_variants_are_denied(self):
        for field in ("ENV", "PYTHONPATH", "LD_PRELOAD", "GIT_CONFIG_COUNT",
                      "SHELLOPTS", "CDPATH", "PATH", "GH_TOKEN"):
            with self.subTest(field=field):
                workflow = live_source()
                workflow["jobs"]["release-evidence-required"]["steps"][0]["env"][field] = "attacker"
                with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_ENV_DRIFT"):
                    audit_deploy_workflow(workflow)

    def test_release_step_cannot_set_unreviewed_working_directory(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"][0]["working-directory"] = "./candidate"
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_STEP_METADATA_DRIFT"):
            audit_deploy_workflow(workflow)

    def test_release_step_cannot_add_alternate_timeout_or_id(self):
        for field, value in (("timeout-minutes", 0), ("id", "override")):
            with self.subTest(field=field):
                workflow = live_source()
                workflow["jobs"]["release-evidence-required"]["steps"][0][field] = value
                with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_STEP_METADATA_DRIFT"):
                    audit_deploy_workflow(workflow)

    def test_evidence_job_outputs_must_be_a_mapping(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["outputs"] = ["unexpected"]
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_JOB_UNSAFE"):
            audit_deploy_workflow(workflow)

    def test_evidence_job_cannot_suppress_failure(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"][0]["continue-on-error"] = True
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_FAILURE_SUPPRESSION"):
            audit_deploy_workflow(workflow)

    def test_untrusted_checkout_action_must_not_persist_credentials(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"].insert(0, {
            "name": "Checkout trusted verifier",
            "uses": "actions/checkout@v4",
            "with": {"ref": "main", "persist-credentials": True},
        })
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_CHECKOUT_CREDENTIALS"):
            audit_deploy_workflow(workflow)

    def test_evidence_job_must_not_checkout_mutable_main_even_with_ephemeral_credentials(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"].insert(0, {
            "name": "Checkout mutable branch",
            "uses": "actions/checkout@v4",
            "with": {"ref": "main", "path": ".rc6-release-guard-trusted",
                     "persist-credentials": False},
        })
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_CHECKOUT_CREDENTIALS"):
            audit_deploy_workflow(workflow)

    def test_unknown_action_in_release_job_is_not_trusted(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"].insert(0, {
            "name": "External executable",
            "uses": "attacker/host-deployer@v1",
        })
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_ACTION_UNTRUSTED"):
            audit_deploy_workflow(workflow)

    def test_new_arbitrary_python_step_cannot_run_before_final_hold(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"].insert(0, {
            "name": "Unreviewed executable",
            "shell": "bash",
            "run": "set -Eeuo pipefail\npython3 -c 'print(123)'\n",
        })
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_STEPS_INVALID"):
            audit_deploy_workflow(workflow)

    def test_product_host_job_cannot_drop_release_dependency(self):
        workflow = live_source()
        workflow["jobs"]["promote"].pop("needs")
        with self.assertRaisesRegex(HostBoundaryViolation, "HOST_RELEASE_DEPENDENCY_MISSING"):
            audit_deploy_workflow(workflow)

    def test_evidence_job_cannot_forge_host_authority_output(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["outputs"] = {
            "host_control_verified": "true",
        }
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_HOST_AUTHORITY_FORGED"):
            audit_deploy_workflow(workflow)

    def test_evidence_final_hold_must_be_unconditional(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["steps"][-1]["run"] = (
            "set -Eeuo pipefail\nif false; then\n  exit 79\nfi\necho bypass\n"
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_FINAL_HOLD_MISSING"):
            audit_deploy_workflow(workflow)

    def test_last_line_exit79_cannot_hide_an_earlier_successful_exit(self):
        workflow = live_source()
        release = workflow["jobs"]["release-evidence-required"]["steps"][-1]
        release["run"] = release["run"].replace("\nexit 79\n", "\nexit 0\nexit 79\n")
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_FINAL_HOLD_MISSING"):
            audit_deploy_workflow(workflow)

    def test_forged_legacy_release_output_cannot_bypass_root_checkout_hold(self):
        # R253 has replaced the historic latest-by-date selector with exact
        # R252 D/E identity. The old LEGACY_ARTIFACT_HOLD_REQUIRED branch is
        # only reached for legacy workflow topology, not this new topology.
        # Forging an older receipt output must still leave host mutation HOLD.
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["outputs"] = {
            "release_bytes_verified": "true",
        }
        with self.assertRaisesRegex(HostBoundaryViolation, "ROOT_CHECKOUT_MUTEX_UNTRUSTED"):
            audit_deploy_workflow(workflow)

    def test_release_receipt_needs_bounded_thirty_minute_window_for_large_zip(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["timeout-minutes"] = 30
        with self.assertRaisesRegex(HostBoundaryViolation, "ROOT_CHECKOUT_MUTEX_UNTRUSTED"):
            audit_deploy_workflow(workflow)

    def test_release_job_cannot_run_unbounded_verification(self):
        workflow = live_source()
        workflow["jobs"]["release-evidence-required"]["timeout-minutes"] = 120
        with self.assertRaisesRegex(HostBoundaryViolation, "RELEASE_EVIDENCE_JOB_UNSAFE"):
            audit_deploy_workflow(workflow)

    def test_promote_prebirth_step_cannot_acquire_third_party_action(self):
        workflow = live_source()
        workflow["jobs"]["promote"]["steps"][1]["uses"] = "attacker/deployer@v1"
        with self.assertRaisesRegex(HostBoundaryViolation, "UNREVIEWED_PROMOTE_ACTION"):
            audit_deploy_workflow(workflow)

    def test_new_exact_artifact_selector_still_checks_root_shell(self):
        workflow = live_source()
        selector = workflow["jobs"]["promote"]["steps"][2]
        selector["name"] = "Use only exact R252-verified R251 D release artifact identity"
        selector["run"] = (
            "set -Eeuo pipefail\n"
            ': "${RC6_RELEASE_ARTIFACT_ID:?}"\n'
            ': "${RC6_RELEASE_ARTIFACT_DIGEST:?}"\n'
            ': "${RC6_RELEASE_RUN_ID:?}"\n'
            "echo 'selection=R252_D_E_ONLY'\n"
        )
        workflow["jobs"]["promote"]["if"] += (
            " && needs.release-evidence-required.outputs.host_control_verified == 'true'"
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "ROOT_CHECKOUT_MUTEX_UNTRUSTED"):
            audit_deploy_workflow(workflow)

    def test_new_selector_cannot_hide_old_latest_by_timestamp_fallback(self):
        workflow = deepcopy(live_source())
        selector = workflow["jobs"]["promote"]["steps"][2]
        selector["name"] = "Use only exact R252-verified R251 D release artifact identity"
        selector["run"] += "\n_,artifact,run=max(candidates,key=lambda x:x[0])\n"
        with self.assertRaisesRegex(HostBoundaryViolation, "UNTRUSTED_RELEASE_ARTIFACT_SELECTOR"):
            audit_deploy_workflow(workflow)


if __name__ == "__main__":
    unittest.main()
