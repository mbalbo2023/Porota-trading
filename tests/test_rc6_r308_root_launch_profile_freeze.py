"""Behavioral regressions for the R308 off-host ROOT launch profile freezer."""

from copy import deepcopy
import hashlib
import json
import unittest

from scripts import rc6_oci_host_helper as host_contract
from scripts import rc6_r308_root_launch_profile_freeze as freeze


class RootLaunchProfileFreezeTest(unittest.TestCase):
    def setUp(self):
        self.arguments = {
            "candidate_sha": "a" * 40,
            "candidate_tree": "b" * 40,
            "artifact_digest": "sha256:" + "c" * 64,
            "image_id": "sha256:" + "d" * 64,
            "daemon_id": "docker-rootful-rc6",
            "generation": "e" * 32,
            "root_stage": "/opt/porota-trading",
            "dashboard_env_sha256": "f" * 64,
            "observer_env_sha256": "1" * 64,
            "observer_source_sha256": {
                "/app/cf_intraday_scalping.py": "2" * 64,
                "/app/co_market_sessions_hf6.py": "3" * 64,
            },
            "critical_source_sha": "4" * 40,
        }

    def make(self, **overrides):
        return freeze.freeze_root_launch_profiles(**(self.arguments | overrides))

    def test_produces_exact_helper_accepted_three_role_paper_profiles(self):
        result = self.make()
        doc = json.loads(result.profiles_bytes)
        authority = host_contract.ApprovedLaunchProfiles(
            doc, candidate_sha=self.arguments["candidate_sha"],
            artifact_digest=self.arguments["artifact_digest"],
            image_id=self.arguments["image_id"],
            generation=self.arguments["generation"])
        self.assertEqual(set(doc["roles"]), {"dashboard", "observer", "critical"})
        self.assertEqual(authority.observer_source_digests,
                         self.arguments["observer_source_sha256"])
        for role in doc["roles"].values():
            self.assertEqual(role["environment"]["POROTA_RUNTIME_MODE"],
                             "PRODUCTION_PAPER")
            self.assertEqual(role["environment"]["PPI_ORDERS"], "BLOCKED")
            self.assertEqual(role["environment"]["REAL_ORDERS_ALLOWED"], "false")
        self.assertFalse(result.host_go)
        self.assertFalse(result.deploy_authorized)

    def test_both_application_roles_keep_paper_data_mount_rw(self):
        roles = json.loads(self.make().profiles_bytes)["roles"]
        for role in ("dashboard", "observer"):
            with self.subTest(role=role):
                self.assertIn({"source": "/opt/porota-trading/data",
                               "target": "/app/data", "readonly": False},
                              roles[role]["mounts"])
        self.assertIn({"source": "/opt/porota-trading/.secrets/ppi_production.json",
                       "target": "/run/secrets/ppi_production.json", "readonly": True},
                      roles["observer"]["mounts"])
        self.assertEqual(roles["observer"]["environment"]["MARKET_OPEN_HOUR"], "10")
        self.assertEqual(roles["observer"]["environment"]["MARKET_OPEN_MINUTE"], "30")

    def test_critical_environment_and_mounts_are_exact_frozen_allowlist(self):
        critical = json.loads(self.make().profiles_bytes)["roles"]["critical"]
        self.assertIsNone(critical["env_file"])
        self.assertIsNone(critical["env_sha256"])
        self.assertEqual(len(critical["mounts"]), 6)
        self.assertEqual(len({m["target"] for m in critical["mounts"]}), 6)
        self.assertEqual(critical["environment"]["POROTA_CRITICAL_APPROVAL_ENABLED"], "true")
        self.assertEqual(critical["environment"]["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(critical["environment"]["POROTA_CRITICAL_TELEGRAM_CHAT_FILE"],
                         "/run/secrets/critical_telegram.chat")
        self.assertNotIn("PPI_PRODUCTION_SECRET_FILE", critical["environment"])

    def test_env_seals_are_external_and_pinned_to_generation(self):
        roles = json.loads(self.make().profiles_bytes)["roles"]
        root = "/var/lib/porota-rc6-oci-custody/generations/" + "e" * 32 + "/env/"
        self.assertEqual(roles["dashboard"]["env_file"], root + "dashboard.env")
        self.assertEqual(roles["observer"]["env_file"], root + "observer.env")
        self.assertEqual(roles["dashboard"]["env_sha256"], "f" * 64)
        self.assertEqual(roles["observer"]["env_sha256"], "1" * 64)

    def test_bytes_and_sha_are_stable_under_mapping_order_or_later_mutation(self):
        source = self.arguments["observer_source_sha256"]
        before = self.make()
        reversed_sources = dict(reversed(list(source.items())))
        later = self.make(observer_source_sha256=reversed_sources)
        self.assertEqual(before.profiles_bytes, later.profiles_bytes)
        self.assertEqual(before.unit_template_bytes, later.unit_template_bytes)
        self.assertEqual(before.profiles_sha256,
                         hashlib.sha256(before.profiles_bytes).hexdigest())
        self.assertEqual(before.unit_template_sha256,
                         hashlib.sha256(before.unit_template_bytes).hexdigest())
        self.assertLess(len(before.profiles_bytes), 128 * 1024)
        self.assertLess(len(before.unit_template_bytes), 128 * 1024)
        source["/app/cf_intraday_scalping.py"] = "9" * 64
        self.assertEqual(before.profiles_bytes, later.profiles_bytes)

    def test_systemd_template_reuses_existing_hold79_protector_semantics(self):
        unit = self.make().unit_template_bytes.decode("utf-8")
        self.assertIn("ExecStart=/usr/local/sbin/porota-rc6-oci-custody critical start-newborn", unit)
        self.assertIn("--paper-only --source-sha " + "4" * 40, unit)
        self.assertIn("--generation " + "e" * 32, unit)
        self.assertIn("Requires=docker.service porota-critical-github-proxy-rc6.service", unit)
        self.assertIn("After=docker.service porota-critical-github-proxy-rc6.service", unit)
        self.assertIn("ConditionPathExists=/var/lib/porota-rc6-oci-custody/host-helper-approval.json", unit)
        self.assertIn("ExecCondition=/bin/sh -c 'echo RC6_OCI_CUSTODY_HOLD79:CRITICAL_CUTOVER_NOT_AUTHORIZED >&2; exit 79'", unit)
        self.assertIn("i=$$((i+1)); [ $$i -lt 30 ]", unit)
        self.assertNotIn("i=$((i+1))", unit)
        self.assertNotIn("[ $i -lt 30 ]", unit)
        for forbidden in ("ExecStop", "[Install]", "RefuseManualStart=yes",
                          "systemctl", "docker rm", "docker stop", "WantedBy="):
            self.assertNotIn(forbidden, unit)
        for source in ("a" * 40, "b" * 40, "sha256:" + "c" * 64,
                       "sha256:" + "d" * 64, "docker-rootful-rc6"):
            self.assertIn(source, unit)

    def test_invalid_identity_and_external_hashes_are_denied(self):
        rejected = {
            "candidate_sha": "bad",
            "candidate_tree": "A" * 40,
            "artifact_digest": "sha256:" + "c" * 63,
            "image_id": "latest",
            "daemon_id": "daemon with spaces",
            "generation": "x" * 32,
            "root_stage": "/tmp/porota",
            "dashboard_env_sha256": "f" * 63,
            "observer_env_sha256": None,
            "critical_source_sha": "e" * 64,
        }
        for key, value in rejected.items():
            with self.subTest(input=key):
                with self.assertRaises(freeze.FreezeHold):
                    self.make(**{key: value})

    def test_source_closure_requires_exact_two_known_image_paths(self):
        for invalid in (
            {}, {"/app/cf_intraday_scalping.py": "2" * 64},
            {**self.arguments["observer_source_sha256"], "/app/other.py": "4" * 64},
            {**self.arguments["observer_source_sha256"],
             "/app/cf_intraday_scalping.py": ""},
            {"/app/cf_intraday_scalping.py": "2" * 64,
             "/app/co_market_sessions_hf6.py": "A" * 64},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(freeze.FreezeHold):
                    self.make(observer_source_sha256=invalid)

    def test_unsafe_stage_paths_fail_closed(self):
        for path in ("/opt/porota-trading/../boca", "/opt/porota-trading//current",
                     "/opt/porota-trading/./current", "/opt/porota-trading/secret:\\n",
                     "/opt/porota-trading-evil", "/", "", None):
            with self.subTest(path=path):
                with self.assertRaises(freeze.FreezeHold):
                    self.make(root_stage=path)


if __name__ == "__main__":
    unittest.main()
