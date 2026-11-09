"""Adversarial OFFHOST contracts for the existing RC6 Docker capacity receipt.

Run the runner-side Python embedded in the workflow with synthetic JSON only.
Never execute the remote SSH/Python portion or contact Docker/the droplet.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-r1-docker-capacity-readonly.yml"
EXPECTED = "a" * 40
OWNED_NAMES = (
    "porota_production_observer",
    "porota_production_dashboard",
    "porota_critical_approval_rc6",
)


def valid_receipt() -> dict:
    return {
        "schema": "rc6.r1.docker-capacity-readonly.v1",
        "state": "READ_ONLY_OBSERVED",
        "mutations": 0,
        "provider_gets": 0,
        "generated_at_utc": "2026-11-09T08:12:42.123456+00:00",
        "generated_at_art": "2026-11-09T05:12:42.123456-03:00",
        "expected_deployed_sha": EXPECTED,
        "observed_code_sha": EXPECTED,
        "code_identity_matches": True,
        "disk_total_bytes": 60000000000,
        "disk_free_bytes": 24000000000,
        "disk_used_bytes": 36000000000,
        "inode_free_percent": 95.123,
        "docker_root_same_filesystem": True,
        "docker_root_disk_total_bytes": 60000000000,
        "docker_root_disk_free_bytes": 24000000000,
        "docker_root_inode_free_percent": 95.123,
        "containers_total": 3,
        "images_total": 2,
        "referenced_images": 1,
        "owned_containers": [
            {"name": OWNED_NAMES[0], "running": False, "image_referenced": True},
            {"name": OWNED_NAMES[1], "state": "ABSENT"},
            {"name": OWNED_NAMES[2], "running": True, "image_referenced": True},
        ],
        "unreferenced_candidate_only_images": 0,
        "unreferenced_candidate_estimated_upper_bytes": 0,
        "referenced_candidate_only_images": 1,
        "other_or_ambiguous_unreferenced_images": 1,
        "docker_system_df": [
            {"Type": t, "TotalCount": "1", "Active": "0", "Size": "1.2GB", "Reclaimable": "0B (0%)"}
            for t in ("Images", "Containers", "Local Volumes", "Build Cache")
        ],
    }


def runner_verifier() -> str:
    source = WORKFLOW.read_text(encoding="utf-8")
    host = host_workflow_section(source)
    match = re.search(r"\n          python3 - <<'PY'\n(.*?)\n          PY", host, re.S)
    if match is None:
        raise AssertionError("existing host_readonly runner verifier not found")
    return textwrap.dedent(match.group(1))


def host_workflow_section(source: str) -> str:
    host = source.split("\n      - name: Read filesystem and Docker metadata only (max 35 seconds)\n", 1)[1]
    return host.split("\n      - name: Inspect RC6 timer contract READ_ONLY\n", 1)[0]


def run_verifier(payload: str, ssh_status: int = 0) -> tuple[subprocess.CompletedProcess, str]:
    with tempfile.TemporaryDirectory(prefix="rc6_capacity_offhost_") as tmp:
        root = Path(tmp)
        (root / "rc6_r1_capacity").mkdir()
        (root / "rc6_r1_capacity" / "raw.json").write_text(payload, encoding="utf-8")
        summary = root / "step-summary.md"
        env = os.environ.copy()
        env.update({
            "RUNNER_TEMP": str(root),
            "GITHUB_STEP_SUMMARY": str(summary),
            "EXPECTED_DEPLOY_SHA": EXPECTED,
            "SSH_STATUS": str(ssh_status),
        })
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", runner_verifier()],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=5, check=False,
        )
        return result, summary.read_text(encoding="utf-8") if summary.exists() else ""


def run_host_shell_with_fake_ssh(
    payload: str, return_code: int, unsuppress_stderr: bool = False,
) -> tuple[subprocess.CompletedProcess, str]:
    """Exercise the actual Bash + heredoc step, overriding SSH with a shell function."""
    host = host_workflow_section(WORKFLOW.read_text(encoding="utf-8"))
    shell = textwrap.dedent(host.split("        run: |\n", 1)[1])
    if unsuppress_stderr:
        # OFFHOST mutation proving the tests would expose an accidental
        # removal of SSH stderr redaction without changing production code.
        marker = " 2>/dev/null <<'PY'"
        if shell.count(marker) != 1:
            raise AssertionError("expected one legacy-host SSH stderr suppression")
        shell = shell.replace(marker, " <<'PY'", 1)
    fake_ssh = (
        'ssh() { printf "%s" "$FAKE_PAYLOAD"; '
        'printf "PRIVATE_SENTINEL\\n" >&2; return "$FAKE_RC"; };\n'
    )
    with tempfile.TemporaryDirectory(prefix="rc6_capacity_shell_offhost_") as tmp:
        root = Path(tmp)
        (root / "rc6_r1_capacity").mkdir()
        summary = root / "step-summary.md"
        env = os.environ.copy()
        env.update({
            "RUNNER_TEMP": str(root),
            "GITHUB_STEP_SUMMARY": str(summary),
            "EXPECTED_DEPLOY_SHA": EXPECTED,
            "DO_HOST": "fake-offhost",
            "DO_USER": "fake-user",
            "FAKE_PAYLOAD": payload,
            "FAKE_RC": str(return_code),
        })
        result = subprocess.run(
            ["bash", "-c", fake_ssh + shell], cwd=ROOT, env=env,
            text=True, capture_output=True, timeout=5, check=False,
        )
        return result, summary.read_text(encoding="utf-8") if summary.exists() else ""


class DockerCapacityReceiptSecurityContract(unittest.TestCase):
    def assert_rejected(self, payload: str, reason: str = "RECEIPT_UNVERIFIED", ssh_status: int = 0) -> None:
        result, summary = run_verifier(payload, ssh_status=ssh_status)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("RC6_R1_DOCKER_RO=RED|" + reason, result.stdout + result.stderr)
        self.assertNotIn("RC6_R1_DOCKER_RO=GREEN", result.stdout + result.stderr + summary)
        self.assertNotIn("PRIVATE_SENTINEL", result.stdout + result.stderr + summary)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(summary, "", "rejected receipts must not be published")

    def test_valid_bounded_read_only_receipt_is_green(self) -> None:
        result, summary = run_verifier(json.dumps(valid_receipt()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RC6_R1_DOCKER_RO=GREEN", result.stdout)
        self.assertIn("READ_ONLY_OBSERVED", summary)

    def test_unknown_private_field_is_rejected_and_never_leaked(self) -> None:
        data = valid_receipt()
        data["private"] = "PRIVATE_SENTINEL"
        self.assert_rejected(json.dumps(data))

    def test_mismatched_observed_sha_and_false_identity_cannot_be_green(self) -> None:
        data = valid_receipt()
        data["observed_code_sha"] = "b" * 40
        data["code_identity_matches"] = False
        self.assert_rejected(json.dumps(data), "CODE_IDENTITY_MISMATCH")

    def test_identity_true_cannot_overrule_observed_sha_mismatch(self) -> None:
        data = valid_receipt()
        data["observed_code_sha"] = "b" * 40
        self.assert_rejected(json.dumps(data), "RECEIPT_UNVERIFIED")

    def test_json_nonfinite_nan_is_rejected(self) -> None:
        data = valid_receipt()
        data["inode_free_percent"] = float("nan")
        self.assert_rejected(json.dumps(data, allow_nan=True))

    def test_json_duplicate_keys_are_rejected(self) -> None:
        raw = json.dumps(valid_receipt())
        raw = raw.replace('"state": ', '"state": "READ_ONLY_OBSERVED", "state": ', 1)
        self.assert_rejected(raw)

    def test_boolean_zero_is_not_an_integer_zero(self) -> None:
        data = valid_receipt()
        data["mutations"] = False
        self.assert_rejected(json.dumps(data))

    def test_private_nested_owned_name_is_not_published(self) -> None:
        data = valid_receipt()
        data["owned_containers"][0]["name"] = "PRIVATE_SENTINEL"
        self.assert_rejected(json.dumps(data))

    def test_private_nested_docker_stat_is_not_published(self) -> None:
        data = valid_receipt()
        data["docker_system_df"][0]["Size"] = "PRIVATE_SENTINEL"
        self.assert_rejected(json.dumps(data))

    def test_docker_size_rejects_newline_whitespace_smuggling(self) -> None:
        data = valid_receipt()
        data["docker_system_df"][0]["Size"] = "1.2\nGB"
        self.assert_rejected(json.dumps(data))

    def test_reclaimable_percent_above_100_is_rejected(self) -> None:
        data = valid_receipt()
        data["docker_system_df"][0]["Reclaimable"] = "1.2GB (999%)"
        self.assert_rejected(json.dumps(data))

    def test_docker_size_rejects_percentage_reserved_for_reclaimable(self) -> None:
        data = valid_receipt()
        data["docker_system_df"][0]["Size"] = "1.2GB (99%)"
        self.assert_rejected(json.dumps(data))

    def test_docker_system_df_numeric_string_above_limit_is_rejected(self) -> None:
        data = valid_receipt()
        data["docker_system_df"][0]["TotalCount"] = "9999999"
        self.assert_rejected(json.dumps(data))

    def test_docker_system_df_active_count_cannot_exceed_total(self) -> None:
        data = valid_receipt()
        data["docker_system_df"][0]["TotalCount"] = "1"
        data["docker_system_df"][0]["Active"] = "2"
        self.assert_rejected(json.dumps(data))

    def test_docker_size_with_literal_space_and_reclaimable_without_percent_is_valid(self) -> None:
        data = valid_receipt()
        data["docker_system_df"][0]["Size"] = "1.2 GB"
        data["docker_system_df"][0]["Reclaimable"] = "0B"
        result, summary = run_verifier(json.dumps(data))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RC6_R1_DOCKER_RO=GREEN", result.stdout)
        self.assertIn("READ_ONLY_OBSERVED", summary)

    def test_unbounded_nonnegative_quantity_is_rejected(self) -> None:
        data = valid_receipt()
        data["containers_total"] = 999999999
        self.assert_rejected(json.dumps(data))

    def test_unavailable_reserved_filesystem_blocks_are_not_false_red(self) -> None:
        # shutil.disk_usage("/") can report total > used + unprivileged free.
        data = valid_receipt()
        data["disk_used_bytes"] = 35000000000
        result, summary = run_verifier(json.dumps(data))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RC6_R1_DOCKER_RO=GREEN", result.stdout)
        self.assertIn("READ_ONLY_OBSERVED", summary)

    def test_remote_exit_two_is_categorized_and_never_green(self) -> None:
        negative = {"schema": "rc6.r1.docker-capacity-readonly.v1",
                    "state": "NO_VERIFICADO", "mutations": 0, "provider_gets": 0,
                    "reason": "CalledProcessError"}
        self.assert_rejected(json.dumps(negative), "HOST_COMMAND_UNVERIFIED", ssh_status=2)

    def test_ssh_failed_positive_payload_is_never_accepted(self) -> None:
        self.assert_rejected(json.dumps(valid_receipt()), "SSH_REMOTE_FAILED", ssh_status=255)

    def test_ssh_transport_failure_with_empty_receipt_is_categorized(self) -> None:
        self.assert_rejected("", "SSH_REMOTE_FAILED", ssh_status=255)

    def test_bash_hardening_handles_remote_exit_two_before_set_e(self) -> None:
        receipt = {"schema": "rc6.r1.docker-capacity-readonly.v1",
                   "state": "NO_VERIFICADO", "mutations": 0,
                   "provider_gets": 0, "reason": "CalledProcessError"}
        result, summary = run_host_shell_with_fake_ssh(json.dumps(receipt), 2)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("RC6_R1_DOCKER_RO=RED|HOST_COMMAND_UNVERIFIED", result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_SENTINEL", result.stdout + result.stderr + summary)
        self.assertEqual(summary, "")

    def test_bash_hardening_handles_ssh_255_without_host_call(self) -> None:
        result, summary = run_host_shell_with_fake_ssh("", 255)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("RC6_R1_DOCKER_RO=RED|SSH_REMOTE_FAILED", result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_SENTINEL", result.stdout + result.stderr + summary)
        self.assertEqual(summary, "")

    def test_bash_hardening_handles_ssh_exit_one_without_leak(self) -> None:
        result, summary = run_host_shell_with_fake_ssh("", 1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("RC6_R1_DOCKER_RO=RED|SSH_REMOTE_FAILED", result.stdout + result.stderr)
        self.assertNotIn("PRIVATE_SENTINEL", result.stdout + result.stderr + summary)
        self.assertEqual(summary, "")

    def test_bash_hardening_accepts_bounded_positive_fake_ssh_only(self) -> None:
        result, summary = run_host_shell_with_fake_ssh(json.dumps(valid_receipt()), 0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("RC6_R1_DOCKER_RO=GREEN", result.stdout)
        self.assertIn("READ_ONLY_OBSERVED", summary)
        self.assertNotIn("PRIVATE_SENTINEL", result.stdout + result.stderr + summary)

    def test_bash_mutant_without_ssh_stderr_redirect_would_leak(self) -> None:
        result, summary = run_host_shell_with_fake_ssh(
            json.dumps(valid_receipt()), 0, unsuppress_stderr=True,
        )
        self.assertIn("PRIVATE_SENTINEL", result.stdout + result.stderr + summary)

    def test_nonjson_or_private_failure_reason_never_leaks(self) -> None:
        self.assert_rejected('{"state":"NO_VERIFICADO","reason":"PRIVATE_SENTINEL"}')

    def test_legacy_receiver_only_and_current_login_modes_unchanged(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        current = "  current_login_readonly:" + source.split("\n  current_login_readonly:", 1)[1]
        self.assertEqual(
            hashlib.sha256(current.encode()).hexdigest(),
            "2c559268756b9476d8d5642fb87ff5f18d50ad98b3b4402ba0afe782452d6c99",
        )
        host = source.split("\n      - name: Read filesystem and Docker metadata only (max 35 seconds)\n", 1)[1]
        host = host.split("\n      - name: Inspect RC6 timer contract READ_ONLY\n", 1)[0]
        self.assertIn('python3 -I -B -', host)
        self.assertIn('SSH_STATUS=', host)
        self.assertIn('2>/dev/null', host)
        self.assertNotIn('docker prune', host)
        self.assertNotIn('docker image rm', host)
        self.assertNotIn('docker system prune', host)


if __name__ == "__main__":
    unittest.main()
