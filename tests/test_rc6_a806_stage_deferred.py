"""OFFHOST regression for the shared-Docker Deploy V2 boundary.

No test in this module invokes SSH, sudo, Docker, systemd or a host service.
The positive result is a source-level assessment, never runtime authority.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/porota-deploy-v2-promote.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


class DeployV2HostBoundaryTests(unittest.TestCase):
    def _audit(self, workflow=None):
        from scripts.rc6_deploy_legacy_host_guard import audit_deploy_workflow

        return audit_deploy_workflow(_workflow() if workflow is None else workflow)

    def test_candidate_source_holds_before_host_until_root_checkout_bypasses_removed(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        with self.assertRaisesRegex(HostBoundaryViolation, "ROOT_CHECKOUT_MUTEX_UNTRUSTED"):
            self._audit()

    def test_global_docker_inventory_is_rejected_even_in_late_cleanup(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = _workflow()
        source["jobs"]["promote"]["steps"][-1]["run"] += "\nsudo -n docker ps -aq\n"
        with self.assertRaisesRegex(HostBoundaryViolation, "GLOBAL_DOCKER_INVENTORY"):
            self._audit(source)

    def test_direct_critical_restart_is_rejected_even_in_precision_guard(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = _workflow()
        source["jobs"]["promote"]["steps"][-1]["run"] += (
            "\nsudo -n systemctl restart porota-critical-approval-rc6.service\n"
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "DIRECT_CRITICAL_RESTART"):
            self._audit(source)

    def test_name_based_docker_stop_is_rejected(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source["jobs"]["promote"]["steps"][-1]["run"] += (
            "\nsudo -n docker stop porota_production_observer\n"
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "DOCKER_MUTATION_BY_NAME"):
            self._audit(source)

    def test_indirect_variable_docker_mutation_is_also_rejected(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source["jobs"]["promote"]["steps"][-1]["run"] += (
            '\nsudo -n docker rm -f "$CRIT_CONTAINER"\n'
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "DOCKER_MUTATION_BY_NAME"):
            self._audit(source)

    def test_docker_global_flags_do_not_bypass_mutation_rejection(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source["jobs"]["promote"]["steps"][-1]["run"] += (
            '\nsudo -n docker --host unix:///var/run/docker.sock stop stranger\n'
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "DOCKER_MUTATION_BY_NAME"):
            self._audit(source)

    def test_manager_pending_cannot_be_accepted_as_paper_ready(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        promote = next(
            step for step in source["jobs"]["promote"]["steps"]
            if step.get("name") == "Promote exact frozen candidate and verify"
        )
        promote["run"] = promote["run"].replace(
            "grep -q 'POROTA_MODE=PENDING_CUSTODY_SEAL'",
            "grep -q 'POROTA_MODE=OK'",
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "PREMATURE_MODE_READY"):
            self._audit(source)

    def test_source_never_executes_candidate_stage_with_root_shell(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = _workflow()
        promote = next(step for step in source["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        promote["run"] += '\nsudo -n bash "$STAGE/ops/untrusted.sh"\n'
        with self.assertRaisesRegex(HostBoundaryViolation, "CANDIDATE_ROOT_SHELL"):
            self._audit(source)

    def test_existing_candidate_source_avoids_root_checkout_shell(self):
        source = _workflow()
        promote = next(step for step in source["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        self.assertNotIn('sudo -n env SOURCE_SHA=', promote["run"])
        self.assertNotIn('sudo -n bash "$STAGE/', promote["run"])
        self.assertNotIn('bash "$STAGE/ops/rc6_deploy_critical_control_plane.sh"', promote["run"])
        self.assertIn('critical stage-approved-protector', promote["run"])

    def test_existing_candidate_uses_sealed_quiescence_instead_of_names(self):
        source = _workflow()
        promote = next(step for step in source["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        self.assertIn('quiescence stop observer', promote["run"])
        self.assertIn('quiescence start observer', promote["run"])
        self.assertNotIn('docker stop porota_production_observer', promote["run"])
        self.assertNotIn('docker start porota_production_observer', promote["run"])
        self.assertNotIn('docker start porota_production_dashboard', promote["run"])

    def test_critical_telegram_generation_seals_before_contract_quiescence(self):
        promote = next(step for step in _workflow()["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        text = promote["run"]
        assert text.index('POROTA_MODE=PENDING_CUSTODY_SEAL') < text.index(
            'critical birth') < text.index('critical start-newborn') < text.index(
            'porota-rc6-oci-custody seal') < text.index('quiescence stop observer')

    def test_postseal_requires_new_generation_receipt_not_previous_role(self):
        source = _workflow()
        promote = next(step for step in source["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        text = promote["run"]
        self.assertNotIn('"$CUSTODY_CLI" verify "$role"', text)
        self.assertIn('"$CUSTODY_CLI" verify-current', text)
        self.assertIn('--candidate-sha "$CANDIDATE_SHA"', text)
        self.assertIn('--candidate-tree "$CANDIDATE_TREE"', text)
        self.assertIn('--generation "$CRIT_GENERATION"', text)
        self.assertIn('${#STAGE_INTENT} -le 320', text)
        self.assertIn('^sha256:[a-f0-9]{64}$', text)
        self.assertIn("value['schema']=='RC6_POSTSEAL_CUSTODY_V1'", text)
        self.assertIn("value['candidate_sha']==os.environ['CANDIDATE_SHA']", text)
        self.assertIn("value['generation']==os.environ['RC6_EXPECTED_GENERATION']", text)

    def test_later_host_steps_revalidate_current_cids_not_legacy_names(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        later = source[source.index('- name: Finalize runtime marker'):]
        self.assertNotIn("for name in ('porota_production_observer'", later)
        self.assertNotIn("['docker', 'inspect', name]", later)
        self.assertNotIn("['docker','inspect',name]", later)
        self.assertIn("state.get('oci_cids')", later)
        self.assertGreaterEqual(later.count("'verify-current'"), 3)

    def test_postseal_legacy_name_probe_fails_even_if_readonly(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        promote = next(step for step in source["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        promote["run"] += '\nsudo -n docker exec porota_production_dashboard id -u\n'
        with self.assertRaisesRegex(HostBoundaryViolation, "POSTSEAL_LEGACY_NAME"):
            self._audit(source)

    def test_postseal_oneshots_use_immutable_verified_image_not_mutable_tag(self):
        promote = next(step for step in _workflow()["jobs"]["promote"]["steps"]
                       if step.get("name") == "Promote exact frozen candidate and verify")
        text = promote["run"]
        self.assertIn('--entrypoint python "$RUNTIME_IMAGE_ID" ck_contract_evidence_runner_hf6.py', text)
        self.assertIn('--entrypoint python -i "$RUNTIME_IMAGE_ID" -', text)
        self.assertNotIn('--entrypoint python "$STABLE_IMAGE" ck_contract_evidence_runner_hf6.py', text)
        self.assertNotIn('--privileged', text)
        self.assertNotIn('--network host', text)
        self.assertNotIn('--pid host', text)

    def test_runner_never_uploads_or_executes_candidate_image_cleanup_code(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn('IMAGE_CLEANUP_B64', source)
        self.assertNotIn('base64 -w0 scripts/rc6_pretransfer_image_cleanup.py', source)
        self.assertIn('porota-rc6-oci-custody image preflight-cleanup', source)
        self.assertNotIn('sudo -n timeout 120 python3 -c', source)

    def test_exact_current_cids_are_required_in_all_runtime_audit_categories(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        promotion = text[text.index('POROTA_MODE=PENDING_CUSTODY_SEAL'):text.index(
            '- name: Finalize runtime marker')]
        self.assertGreaterEqual(promotion.count('"$DASHBOARD_CID"'), 17)
        self.assertGreaterEqual(promotion.count('"$OBSERVER_CID"'), 7)
        self.assertGreaterEqual(promotion.count('"$CRITICAL_CID"'), 2)
        self.assertNotIn('docker exec porota_production_', promotion)
        self.assertNotIn('docker logs --tail 250 porota_production_', promotion)

    def test_unreviewed_oneshot_cannot_acquire_global_or_privileged_docker_scope(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source["jobs"]["promote"]["steps"][-1]["run"] += (
            '\nsudo -n docker run --rm --privileged --network host intruder:latest\n'
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "UNREVIEWED_DOCKER_BIRTH"):
            self._audit(source)

    def test_tag_cannot_be_redirected_to_foreign_namespace(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source["jobs"]["promote"]["steps"][-1]["run"] += (
            '\nsudo -n docker tag "$FROZEN_IMAGE" boca:latest\n'
        )
        with self.assertRaisesRegex(HostBoundaryViolation, "UNREVIEWED_DOCKER_TAG"):
            self._audit(source)

    def test_unreviewed_prebirth_ssh_step_cannot_stop_ppi_watch(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source['jobs']['promote']['steps'].insert(1, {
            'name':'Inject host stop', 'shell':'bash',
            'run':'set -Eeuo pipefail\nssh droplet sudo -n systemctl stop ppi-watch.service\n'})
        with self.assertRaisesRegex(HostBoundaryViolation, "PPI_WATCH_MUTATION"):
            self._audit(source)

    def test_unreviewed_prebirth_python_can_not_mutate_foreign_docker(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source['jobs']['promote']['steps'].insert(1, {
            'name':'Inject docker subprocess', 'shell':'bash',
            'run':'python3 -c "import subprocess; subprocess.run([\'/usr/bin/docker\',\'rm\',\'-f\',\'someforeign\'])"'})
        with self.assertRaisesRegex(HostBoundaryViolation, "UNREVIEWED_PREBIRTH_STEP"):
            self._audit(source)

    def test_approved_prebirth_step_cannot_gain_indirect_python_docker_exec(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source['jobs']['promote']['steps'][2]['run'] += (
            '\npython3 -c "import subprocess; subprocess.run([\'/usr/bin/docker\',\'rm\',\'-f\',\'boca\'])"')
        with self.assertRaisesRegex(HostBoundaryViolation, "PREBIRTH_HOST_PRIVILEGE_BYPASS"):
            self._audit(source)

    def test_any_late_systemd_ppi_watch_mutation_is_rejected(self):
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        source = deepcopy(_workflow())
        source['jobs']['promote']['steps'][-1]['run'] += (
            '\nsudo -n systemctl stop ppi-watch.service\n')
        with self.assertRaisesRegex(HostBoundaryViolation, "PPI_WATCH_MUTATION"):
            self._audit(source)

    def test_guard_cli_exits_79_before_ssh_for_current_untrusted_source(self):
        from scripts.rc6_deploy_legacy_host_guard import main

        self.assertEqual(main(['--workflow',str(WORKFLOW)]),79)

    def test_candidate_stage_shell_is_rejected_regardless_of_outer_sudo(self):
        from scripts.rc6_deploy_legacy_host_guard import reject_untrusted_checkout_privilege
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        with self.assertRaisesRegex(HostBoundaryViolation,'STAGED_CHECKOUT_SUDO_BYPASS'):
            reject_untrusted_checkout_privilege(
                'SOURCE_SHA="$CANDIDATE_SHA" bash "$STAGE/ops/rc6_deploy_critical_control_plane.sh"')

    def test_housekeeping_streamed_into_root_python_is_rejected(self):
        from scripts.rc6_deploy_legacy_host_guard import reject_untrusted_checkout_privilege
        from scripts.rc6_deploy_legacy_host_guard import HostBoundaryViolation

        with self.assertRaisesRegex(HostBoundaryViolation,'ROOT_CHECKOUT_HOUSEKEEPING_UNTRUSTED'):
            reject_untrusted_checkout_privilege(
                'printf "%s" "$HOUSEKEEPING_B64" | base64 -d | sudo -n python3 -')


if __name__ == "__main__":
    unittest.main()
