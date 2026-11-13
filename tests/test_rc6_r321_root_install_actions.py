"""R321 finite ROOT-stage planning only; no SSH, sudo or host changes."""

from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
import yaml
from unittest.mock import patch

from scripts import rc6_r321_root_install_actions as r321
from scripts import rc6_host_rootowned_installer_contract as r280


NOW = datetime(2026, 11, 13, 14, 0, tzinfo=timezone.utc)
CANDIDATE = "a" * 40
TREE = "b" * 40
SHA256 = lambda raw: hashlib.sha256(raw).hexdigest()


def canonical(data):
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture(*, large=True):
    sources = {
        "helper": b"#!/usr/bin/python3 -I\n" + b"H" * (17000 if large else 10),
        "custody": b"C" * (18000 if large else 10),
        "profiles": b'{"paper_mode":"PRODUCTION_PAPER"}\n',
        "unit_template": b"[Service]\nExecStart=/usr/bin/false\n",
    }
    approval = {
        "schema_version": 1, "phase": "GENESIS",
        "candidate_sha": CANDIDATE, "candidate_tree": TREE,
        "artifact_digest": "sha256:" + "c" * 64,
        "image_id": "sha256:" + "d" * 64,
        "daemon_id": "engine-28.0.4", "generation": "e" * 32,
        "run_id": "43301355473", "run_attempt": "1",
        "paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False,
        "helper_sha256": SHA256(sources["helper"]),
        "custody_sha256": SHA256(sources["custody"]),
        "profiles_sha256": SHA256(sources["profiles"]),
        "unit_template_sha256": SHA256(sources["unit_template"]),
        "expires_at": "2026-11-13T16:00:00Z",
    }
    sources["approval"] = canonical(approval)
    manifest = {
        "schema": "RC6_ROOT_INSTALL_MANIFEST_V1", "candidate_sha": CANDIDATE,
        "candidate_tree": TREE, "paper_mode": "PRODUCTION_PAPER",
        "real_orders_allowed": False,
        "files": {key: SHA256(data) for key, data in sources.items()},
    }
    manifest_raw = canonical(manifest)
    return manifest_raw, sources


class R321RootInstallActionsTest(unittest.TestCase):
    def test_trusted_main_action_is_only_offhost_and_never_mutates_droplet(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.load(
            (root / ".github/workflows/rc6-r321-root-install-approved.yml").read_text(),
            Loader=yaml.BaseLoader)
        self.assertIn("workflow_dispatch", workflow["on"])
        self.assertEqual(workflow["concurrency"]["group"],
                         "rc6-unified-paper-deploy")
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(workflow["permissions"],
                         {"contents": "read", "issues": "read"})
        self.assertEqual(set(workflow["jobs"]), {"root_install_offhost_preflight"})
        job = workflow["jobs"]["root_install_offhost_preflight"]
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertIn("github.actor == 'mbalbo2023'", job["if"])
        self.assertIn("github.triggering_actor == 'mbalbo2023'", job["if"])
        checkout = job["steps"][0]
        self.assertEqual(checkout["with"]["persist-credentials"], "false")
        self.assertEqual(checkout["with"]["ref"], "${{ github.sha }}")
        commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
        self.assertIn("--check-offhost", commands)
        for forbidden in ("sudo ", "ssh ", "docker ", "install-approved", "--install-approved"):
            self.assertNotIn(forbidden, commands)
        self.assertFalse(job.get("secrets"))

    def test_valid_root_bundle_plan_chunks_all_five_roles_and_manifest_below_8192(self):
        manifest, sources = fixture()
        planned = r321.build_staging_plan(
            manifest, sources, expected_manifest_sha256=SHA256(manifest), now=NOW)
        self.assertEqual(planned.state, "PROPUESTO")
        self.assertFalse(planned.host_control_verified)
        self.assertFalse(planned.executable)
        self.assertEqual(planned.candidate_sha, CANDIDATE)
        self.assertEqual(planned.candidate_tree, TREE)
        self.assertEqual(planned.manifest_sha256, SHA256(manifest))
        self.assertEqual(planned.total_bytes, len(manifest) + sum(map(len, sources.values())))
        self.assertEqual(
            tuple(dict.fromkeys(chunk.role for chunk in planned.chunks)),
            ("helper", "custody", "profiles", "approval", "unit_template", "manifest"))
        self.assertTrue(all(0 < len(chunk.data) <= 8192 for chunk in planned.chunks))
        self.assertEqual(sum(len(chunk.data) for chunk in planned.chunks), planned.total_bytes)
        self.assertEqual(
            b"".join(chunk.data for chunk in planned.chunks if chunk.role == "helper"),
            sources["helper"])
        self.assertEqual(
            b"".join(chunk.data for chunk in planned.chunks if chunk.role == "custody"),
            sources["custody"])
        self.assertEqual(planned.chunks[-1].role, "manifest")

    def test_order_source_bytes_do_not_authorize_their_own_digest(self):
        manifest, sources = fixture(large=False)
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256="f" * 64, now=NOW)
        self.assertEqual(caught.exception.reason, "INDEPENDENT_ROOT_PIN_MISMATCH")

    def test_modified_role_is_rejected_before_staging_plan(self):
        manifest, sources = fixture(large=False)
        sources["custody"] += b"changed"
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256=SHA256(manifest), now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_SOURCE_HASH_MISMATCH")

    def test_source_mutated_after_approval_does_not_change_frozen_staged_bytes(self):
        manifest, sources = fixture(large=False)
        original = r280._validate_approval

        def concurrent_source_change(*args, **kwargs):
            result = original(*args, **kwargs)
            sources["custody"] = b"ATTACKER_CHANGED_SOURCE_AFTER_VALIDATION"
            return result

        with patch.object(r280, "_validate_approval",
                          side_effect=concurrent_source_change):
            plan = r321.build_staging_plan(
                manifest, sources, expected_manifest_sha256=SHA256(manifest), now=NOW)
        staged = b"".join(chunk.data for chunk in plan.chunks
                          if chunk.role == "custody")
        self.assertEqual(staged, b"C" * 10)
        self.assertNotEqual(staged, sources["custody"])
        self.assertEqual(SHA256(staged), json.loads(manifest)["files"]["custody"])

    def test_roles_are_exact_and_no_unknown_paths_are_planned(self):
        manifest, sources = fixture(large=False)
        for bad in (dict(sources, new_file=b"bad"),
                    {key: value for key, value in sources.items() if key != "unit_template"}):
            with self.subTest(roles=set(bad)), self.assertRaises(r321.R321Hold):
                r321.build_staging_plan(manifest, bad,
                                        expected_manifest_sha256=SHA256(manifest), now=NOW)

    def test_release_d_schema_is_never_a_root_install_manifest(self):
        _manifest, sources = fixture(large=False)
        legacy = canonical({"schema": "RC6_RELEASE_MANIFEST_D_V1",
                            "files": {key: SHA256(data) for key, data in sources.items()}})
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(legacy, sources,
                                    expected_manifest_sha256=SHA256(legacy), now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_MANIFEST_SCHEMA")

    def test_paper_false_or_approval_expired_is_rejected(self):
        manifest, sources = fixture(large=False)
        bad_manifest = json.loads(manifest)
        bad_manifest["real_orders_allowed"] = True
        altered = canonical(bad_manifest)
        with self.assertRaises(r321.R321Hold):
            r321.build_staging_plan(altered, sources,
                                    expected_manifest_sha256=SHA256(altered), now=NOW)
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256=SHA256(manifest),
                                    now=datetime(2026, 11, 13, 16, 0, tzinfo=timezone.utc))
        self.assertEqual(caught.exception.reason, "ROOT_APPROVAL_R280_REJECTED")

    def test_overflow_from_r280_approval_expiry_is_normalized_to_hold79(self):
        _manifest, sources = fixture(large=False)
        approval = json.loads(sources["approval"])
        approval["expires_at"] = "9999-12-31T23:30:00Z"
        sources["approval"] = canonical(approval)
        manifest = canonical({
            "schema": "RC6_ROOT_INSTALL_MANIFEST_V1", "candidate_sha": CANDIDATE,
            "candidate_tree": TREE, "paper_mode": "PRODUCTION_PAPER",
            "real_orders_allowed": False,
            "files": {role: SHA256(raw) for role, raw in sources.items()},
        })
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(
                manifest, sources,
                expected_manifest_sha256=SHA256(manifest),
                now=datetime(9999, 12, 31, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(caught.exception.reason, "ROOT_APPROVAL_R280_REJECTED")

    def test_stage_size_budget_blocks_large_total_without_host(self):
        manifest, sources = fixture(large=False)
        sources["custody"] = b"C" * (512 * 1024)
        with self.assertRaises(r321.R321Hold) as caught:
            r321.build_staging_plan(manifest, sources,
                                    expected_manifest_sha256=SHA256(manifest), now=NOW)
        self.assertEqual(caught.exception.reason, "ROOT_STAGE_TOTAL_BUDGET")

    def test_only_exact_trusted_main_owner_dispatch_is_eligible(self):
        context = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_ACTOR": "mbalbo2023",
            "GITHUB_TRIGGERING_ACTOR": "mbalbo2023",
            "GITHUB_SHA": "a" * 40,
        }
        self.assertEqual(r321.validate_dispatch_context(context), "a" * 40)
        for name, value in (("GITHUB_EVENT_NAME", "pull_request"),
                            ("GITHUB_REF", "refs/heads/deploy/rc6-pr69-isolated-20260915"),
                            ("GITHUB_ACTOR", "fork-user"),
                            ("GITHUB_TRIGGERING_ACTOR", "fork-user"),
                            ("GITHUB_REPOSITORY", "some-other/repo"),
                            ("GITHUB_SHA", "a" * 39)):
            bad = dict(context, **{name: value})
            with self.subTest(name=name), self.assertRaises(r321.R321Hold):
                r321.validate_dispatch_context(bad)

    def test_literal_installer_is_accepted_only_from_reviewed_r305_allowlist(self):
        with self.assertRaises(r321.R321Hold):
            r321.require_reviewed_installer_command()
        reviewed = SimpleNamespace(
            reviewed_root_commands=lambda: frozenset({r321.INSTALLER_COMMAND}))
        with patch.object(r321, "import_module", return_value=reviewed):
            self.assertEqual(r321.require_reviewed_installer_command(),
                             r321.INSTALLER_COMMAND)
        unreviewed = SimpleNamespace(reviewed_root_commands=lambda: frozenset())
        with patch.object(r321, "import_module", return_value=unreviewed):
            with self.assertRaises(r321.R321Hold):
                r321.require_reviewed_installer_command()
        malformed = SimpleNamespace(reviewed_root_commands=lambda: {r321.INSTALLER_COMMAND})
        with patch.object(r321, "import_module", return_value=malformed):
            with self.assertRaises(r321.R321Hold):
                r321.require_reviewed_installer_command()
        with self.assertRaises(TypeError):
            r321.require_reviewed_installer_command(frozenset({r321.INSTALLER_COMMAND}))

    def test_physical_entry_is_fail_closed_even_with_a_valid_offhost_plan(self):
        result = io.StringIO()
        with redirect_stdout(result):
            status = r321.main(["--install-approved"])
        self.assertEqual(status, 79)
        self.assertIn("PHYSICAL_TRANSPORT_NOT_CERTIFIED", result.getvalue())
        self.assertIn("host_control_verified=NO_VERIFICADO", result.getvalue())


if __name__ == "__main__":
    unittest.main()
