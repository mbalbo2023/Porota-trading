"""A806 fail-first safety contract for deploy staging on a shared host.

Staging has no independent root-issued provenance yet.  A matching name,
same UID or DO_USER-written receipt must not authorize deletion.  A safe
deferred result is preferable to ever touching Boca or other applications.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts import rc6_deploy_failure_cleanup as cleanup


CANDIDATE = "7" * 40
RUN = "44556677"
ATTEMPT = "2"


class SharedHostStagingDeferredTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rc6-a806-deferred-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.stage = self.base / f"porota-deploy-v2-{CANDIDATE}-{RUN}-{ATTEMPT}"

    def run_cleanup(self):
        # Offhost fixtures simulate *only* candidate-specific inspection;
        # a Docker mutation would fail this test, not quietly be mocked away.
        with (patch.object(cleanup, "REMOTE_ROOT", self.base),
              patch.object(cleanup, "image_id", return_value=None),
              patch.object(cleanup, "container_inventory", return_value=(set(), [])),
              patch.object(cleanup, "host_mountpoints", return_value=[Path("/")]),
              patch.object(cleanup, "docker", side_effect=AssertionError("MUTATION_FORBIDDEN"))):
            return cleanup.cleanup(CANDIDATE, RUN, ATTEMPT)

    def test_foreign_exact_name_retained_without_root_attestation(self):
        self.stage.mkdir(mode=0o700)
        foreign = self.stage / "foreign-app-data"
        foreign.write_text("MUST_SURVIVE")
        result = self.run_cleanup()
        self.assertEqual(result["status"], "RED")
        self.assertIn("STAGING_CLEANUP_DEFERRED_UNVERIFIED", result["errors"])
        self.assertFalse(result["staging_removed"])
        self.assertEqual(foreign.read_text(), "MUST_SURVIVE")

    def test_do_user_self_receipt_does_not_allow_stage_delete(self):
        self.stage.mkdir(mode=0o700)
        nested = self.stage / "stage"
        nested.mkdir(mode=0o700)
        foreign = nested / "foreign-data"
        foreign.write_text("KEEP")
        # This receipt is *not* root attestation.  It can be forged by any
        # other process that shares the SSH user's UID.
        (self.stage / ".rc6-exact-stage-owner.json").write_text(json.dumps({
            "candidate": CANDIDATE, "run": RUN, "attempt": ATTEMPT,
            "root": {"uid": os.geteuid(), "ino": os.lstat(self.stage).st_ino},
        }))
        result = self.run_cleanup()
        self.assertEqual(result["status"], "RED")
        self.assertIn("STAGING_CLEANUP_DEFERRED_UNVERIFIED", result["errors"])
        self.assertEqual(foreign.read_text(), "KEEP")

    def test_missing_mutex_must_not_yield_write_access(self):
        self.stage.mkdir(mode=0o700)
        with patch.object(cleanup, "REMOTE_ROOT", self.base):
            with self.assertRaises(cleanup.CleanupRejected):
                with cleanup.promotion_lock():
                    self.fail("MISSING_MUTEX_MUST_NOT_YIELD")
        self.assertTrue(self.stage.is_dir())

    def test_no_stage_at_start_does_not_claim_this_cleanup_removed_it(self):
        result = self.run_cleanup()
        self.assertEqual(result['status'], 'RED')
        self.assertFalse(result['staging_removed'], 'No deletion took place')
        self.assertEqual(result['space_recovered'], 0)

    def test_external_disk_delta_does_not_claim_host_bytes_reclaimed(self):
        # The disk might change while a failed cleanup exits. It is not
        # legitimate to attribute that free-space delta to this no-op.
        with (patch.object(cleanup.shutil, 'disk_usage', side_effect=[
                    SimpleNamespace(free=1000), SimpleNamespace(free=1200)]),
              patch('builtins.print') as output):
            self.assertEqual(cleanup.main(['--candidate-sha', CANDIDATE]), 2)
        result = json.loads(output.call_args.args[0])
        self.assertEqual(result['host_space_recovered'], 0)

    def test_inline_preopen_has_no_recursive_or_file_delete(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" /
                    "porota-deploy-v2-promote.yml").read_text()
        block = workflow.split('RC6_ZERO_KNOWN_ERROR_IMMEDIATE=GREEN', 1)[1]
        block = block.split("DISK_PREOPEN_AFTER_SAFE_RECLAIM=", 1)[0]
        self.assertNotIn("shutil.rmtree", block)
        self.assertNotIn(".unlink(", block)
        self.assertIn("RC6_PREOPEN_EXACT_STAGE_RECLAIM=DEFERRED_UNVERIFIED", block)

    def test_inline_never_claims_green_reclaim_of_retained_stage(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" /
                    "porota-deploy-v2-promote.yml").read_text()
        block = workflow.split('RC6_ZERO_KNOWN_ERROR_IMMEDIATE=GREEN', 1)[1]
        block = block.split("# External source sampling", 1)[0]
        self.assertNotIn("RC6_PREOPEN_SAFE_RECLAIM=GREEN", block)
        self.assertIn("RC6_PREOPEN_SAFE_RECLAIM=DEFERRED_UNVERIFIED", block)
        self.assertIn('test "$DISK_PREOPEN_AFTER_SAFE_RECLAIM" -ge "$POST_CLEANUP_MIN_FREE"', block)

    def test_provenance_no_go_precedes_every_remote_host_contact(self):
        """A source-level RED must stop before reconcile/suspend/transfer/promote."""
        workflow_path = (Path(__file__).parents[1] / '.github' / 'workflows' /
                         'porota-deploy-v2-promote.yml')
        steps = yaml.safe_load(workflow_path.read_text())['jobs']['promote']['steps']
        names = [step.get('name', '') for step in steps]
        gate_matches = [(idx, step) for idx, step in enumerate(steps)
                        if step.get('id') == 'a806_staging_preadmission']
        self.assertEqual(len(gate_matches), 1, 'Exactly one pre-host staging safety gate')
        gate_idx, gate = gate_matches[0]
        self.assertEqual(gate_idx, 1, 'Gate must immediately follow runner checkout')
        self.assertIn('Checkout production promotion commit', names[0])
        self.assertNotIn('ssh ', gate['run'])
        self.assertNotIn('sudo ', gate['run'])
        self.assertNotIn('docker ', gate['run'])
        first_host = next(i for i, name in enumerate(names)
                          if 'DigitalOcean Droplet' in name or 'host barrier' in name)
        suspend = next(i for i, name in enumerate(names) if 'Suspend versioned' in name)
        transfer = next(i for i, name in enumerate(names) if 'Transfer frozen artifact' in name)
        promotion = next(i for i, name in enumerate(names) if 'Promote exact frozen' in name)
        self.assertLess(gate_idx, min(first_host, suspend, transfer, promotion))

        # Run only the early runner-local step, never downstream host writers.
        marker = subprocess.run(['bash', '-Eeuo', 'pipefail', '-c', gate['run']],
                                cwd=workflow_path.parents[2], capture_output=True,
                                text=True, timeout=5, check=False)
        self.assertEqual(marker.returncode, 79, marker.stdout + marker.stderr)
        self.assertIn('RC6_DEPLOY_V2=NO_GO_PREMUTATION_STAGING_UNVERIFIED',
                      marker.stdout)
        self.assertIn('STAGING_CLEANUP=DEFERRED_UNVERIFIED', marker.stdout)

    def test_early_provenance_failure_cannot_trigger_host_recovery(self):
        workflow_path = (Path(__file__).parents[1] / '.github' / 'workflows' /
                         'porota-deploy-v2-promote.yml')
        steps = yaml.safe_load(workflow_path.read_text())['jobs']['promote']['steps']
        gates = [idx for idx, step in enumerate(steps)
                 if step.get('id') == 'a806_staging_preadmission']
        self.assertEqual(len(gates), 1)
        gate_idx = gates[0]
        for step in steps[gate_idx + 1:]:
            if step.get('id') not in ('failure_cleanup', 'failure_orphan_cleanup'):
                continue
            expression = step.get('if', '')
            self.assertNotIn('steps.a806_staging_preadmission.outcome', expression)
            if step['id'] == 'failure_cleanup':
                self.assertIn('steps.suspend.outcome', expression)
            if step['id'] == 'failure_orphan_cleanup':
                self.assertIn("steps.failure_cleanup.outcome == 'success'", expression)
            self.assertNotIn('steps.a806_staging_preadmission.conclusion', expression)


if __name__ == "__main__":
    unittest.main()
