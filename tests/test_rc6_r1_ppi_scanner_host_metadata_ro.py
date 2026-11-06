"""Offline adversarial contracts for R1 PPI scanner metadata-only Action."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_r1_ppi_scanner_host_metadata_ro as m


PRODUCT = "eefcf6d8c93f3eddfc23bd471d9d5b41d9a31a9f"
IMAGE = "sha256:" + "f" * 64


class R1ScannerMetadataTest(unittest.TestCase):
    def test_env_launch_is_not_effective_policy(self):
        raw = json.dumps(["POROTA_DYNAMIC_CAPACITY_MODE=SHADOW",
                          "POROTA_CAPACITY_POLICY_PATH=/some/private/path",
                          "PPI_PRODUCTION_SECRET_FILE=/private/key",
                          "ACCESS_TOKEN=sensitive-do-not-log"])
        result = m.safe_env_policy(raw)
        self.assertEqual(result, {
            "launch_capacity_mode": "SHADOW", "policy_path_selector": "OVERRIDE_PRESENT",
            "ppi_secret_path_selector": "OVERRIDE_PRESENT",
            "effective_runtime_policy": "NO_VERIFICADO",
        })
        self.assertNotIn("sensitive", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))

    def test_env_duplicate_and_unknown_values_fail_closed(self):
        with self.assertRaises(ValueError):
            m.safe_env_policy(json.dumps(["POROTA_DYNAMIC_CAPACITY_MODE=OFF",
                                           "POROTA_DYNAMIC_CAPACITY_MODE=APPROVED"]))
        self.assertEqual(m.safe_env_policy(json.dumps([
            "POROTA_DYNAMIC_CAPACITY_MODE=secrets-do-not-print"]))["launch_capacity_mode"], "INVALID")

    def test_exact_mounts_without_host_paths(self):
        raw = json.dumps([
            {"Destination": "/app/data", "Type": "bind", "Source": "/opt/porota-trading/data", "RW": True},
            {"Destination": "/run/secrets/ppi_production.json", "Type": "bind", "Source": "/private/mount", "RW": False},
        ])
        result = m.safe_mounts(raw)
        self.assertEqual(result, {"runtime_data_mount": "EXPECTED_BIND", "ppi_secret_mount": "MOUNT_PRESENT"})
        self.assertNotIn("/private", json.dumps(result))
        self.assertEqual(m.safe_mounts(json.dumps([{
            "Destination": "/app/data", "Type": "bind", "Source": "/opt/porota-trading/databackup",
        }]))["runtime_data_mount"], "OTHER")

    def test_log_counts_only_exact_three_markers_without_payload(self):
        raw = (b"2026-11-06T14:01:00Z PPI_SCANNER_PRIMARY_RECEIPT={secret:do-not-publish}\n"
               b"2026-11-06T14:02:00Z PPI_SCANNER_PRIMARY_CAPTURE_ERROR=API_KEY_LEAK?\n"
               b"2026-11-06T14:03:00Z PPI_SCANNER_PRIMARY_PUBLISH_ERROR=private/path\n"
               b"2026-11-06T14:04:00Z OTHER_MARKER=old\n")
        result = m.marker_counts(raw)
        self.assertEqual([result[x] for x in m.MARKERS], [1, 1, 1])
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("API_KEY", json.dumps(result))
        with self.assertRaises(ValueError):
            m.marker_counts(b"\x00PPI_SCANNER_PRIMARY_RECEIPT=1")
        with self.assertRaises(ValueError):
            m.marker_counts(b"x" * (m.MAX_DOCKER_LOGS + 1))

    def test_lstat_never_opens_sqlite_or_follows_alias(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sidecar.sqlite"
            self.assertEqual(m.file_kind(path), "ABSENT")
            path.write_bytes(b"not-opened")
            self.assertEqual(m.file_kind(path), "REGULAR_SINGLE_LINK")
            self.assertIn(m.age_band(path), {"LT_5M", "LT_1H"})
            alias = Path(td) / "alias.sqlite"
            alias.symlink_to(path)
            self.assertEqual(m.file_kind(alias), "UNSAFE_OR_ALIAS")
            self.assertEqual(m.age_band(alias), "UNKNOWN")

    def test_runner_rejects_malicious_remote_payload_and_false_go(self):
        base = m.default_receipt(PRODUCT, IMAGE)
        base.update(status="OBSERVED_ONLY", reason="OBSERVED_ONLY_NOT_PAPER_GO",
                    image_pin_verified=True, container_running=True, log_marker_state="OBSERVED")
        self.assertEqual(m.validate_receipt(base, PRODUCT, IMAGE)["status"], "OBSERVED_ONLY")
        for key, value in (("paper_ready", True), ("probe_provider_gets", 1),
                           ("product_runtime_sha_attested", True),
                           ("effective_runtime_policy", "APPROVED"),
                           ("reason", "secret error message"),
                           ("expected_image_id", "sha256:"+"0"*64)):
            changed = dict(base)
            changed[key] = value
            with self.assertRaises(ValueError, msg=key):
                m.validate_receipt(changed, PRODUCT, IMAGE)
        changed = dict(base, shell_command="curl provider")
        with self.assertRaises(ValueError):
            m.validate_receipt(changed, PRODUCT, IMAGE)

    def test_host_probe_does_not_continue_after_wrong_image(self):
        with patch.object(m, "_docker", side_effect=[b"sha256:" + b"0" * 64 + b"\n", b"true\n"]) as docker:
            result = m.host_probe(PRODUCT, IMAGE)
        self.assertEqual(result["reason"], "IMAGE_PIN_MISMATCH")
        self.assertEqual(result["probe_provider_gets"], 0)
        self.assertFalse(result["product_runtime_sha_attested"])
        self.assertEqual(docker.call_count, 2)
        self.assertFalse(result["image_pin_verified"])

    def test_host_probe_classifies_three_markers_without_provider(self):
        calls = [
            IMAGE.encode() + b"\n", b"true\n",
            json.dumps(["POROTA_DYNAMIC_CAPACITY_MODE=OFF"]).encode(),
            json.dumps([{"Destination": "/app/data", "Type": "bind",
                         "Source": "/opt/porota-trading/data"}]).encode(),
            b"2026-11-06T14:10:00Z PPI_SCANNER_PRIMARY_RECEIPT={secret}\n",
        ]
        with patch.object(m, "_docker", side_effect=calls) as docker, patch.object(
                m, "file_kind", return_value="ABSENT"), patch.object(
                m, "age_band", return_value="UNKNOWN"):
            result = m.host_probe(PRODUCT, IMAGE)
        self.assertEqual(result["status"], "OBSERVED_ONLY")
        self.assertEqual(result["effective_runtime_policy"], "NO_VERIFICADO")
        self.assertEqual(result["marker_counts"][m.MARKERS[0]], 1)
        self.assertEqual(docker.call_count, 5)
        self.assertEqual(result["probe_ppi_watch_mutations"], 0)

    def test_scope_is_metadata_only_and_guards_forbidden_calls(self):
        source = Path(m.__file__).read_text()
        for forbidden in ("sqlite3.connect", "requests.get", "urllib.request", "docker exec",
                          "docker prune", "docker rm", "docker restart", "systemctl start",
                          "PPI_PRODUCTION_SECRET_FILE=/run", "PPI_WATCH_RESTART"):
            self.assertNotIn(forbidden, source)

    def test_nonzero_ssh_never_promotes_forged_remote_observed(self):
        base = m.default_receipt(PRODUCT, IMAGE)
        base.update(status="OBSERVED_ONLY", reason="OBSERVED_ONLY_NOT_PAPER_GO",
                    image_pin_verified=True, container_running=True, log_marker_state="OBSERVED")
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "remote.txt"
            clean = Path(td) / "receipt.json"
            raw.write_text(json.dumps(base))
            env = dict(os.environ, RC6_DIAG_SSH_RC="2", RC6_DIAG_EXPECTED_PRODUCT_SHA=PRODUCT,
                       RC6_DIAG_EXPECTED_IMAGE_ID=IMAGE, RC6_DIAG_RECEIPT_PATH=str(raw),
                       RC6_DIAG_SANITIZED_PATH=str(clean))
            reply = subprocess.run([sys.executable, "-B", str(Path(m.__file__)), "--validate-runner"],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(reply.returncode, 2)
            self.assertEqual(json.loads(clean.read_text())["status"], "NO_VERIFICADO")


if __name__ == "__main__":
    unittest.main()
