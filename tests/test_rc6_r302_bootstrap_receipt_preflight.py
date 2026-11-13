"""R302 independent, off-host verification of R301 root files-only receipt."""

import hashlib
from pathlib import Path
import re
import sys
import time
import unittest

from scripts import rc6_r302_bootstrap_receipt_preflight as r302


EXPECTED_ID = "123456789"
EXPECTED_REGION = "nyc1"
ROOT_DIRS = (
    "/", "/usr", "/usr/local", "/usr/local/sbin", "/var", "/var/lib",
    "/usr/local/libexec", "/usr/local/libexec/porota-rc6-custody",
    "/var/lib/porota-rc6-oci-custody",
    "/var/lib/.porota-rc6-r297-bootstrap-reserved",
)
CLI = "/usr/local/sbin/porota-rc6-host-installer"
CONTRACT = ("/usr/local/libexec/porota-rc6-custody/"
            "rc6_host_rootowned_installer_contract.py")
PIN = "/var/lib/porota-rc6-oci-custody/installer-approved-manifest.sha256"
RECEIPT = "/var/lib/porota-rc6-oci-custody/bootstrap-r297-complete.json"
FILES = (CLI, CONTRACT, PIN, RECEIPT)
HASHES = (
    "0d46b0f99ce09fdf4d851255e79fba4fe8af98fd7ab62c98f3c1938c484f8c7a",
    "8ef4f5892560e496a85cae1df159e45b843d9f6047c3d87f68bc0b369f61adc9",
    "53c70ee0845b75a73369acfea32abbb06510d75ea272b9bde40c5572c3a9544e",
    "21b1a5f3ddabf3b77b9cb7053ae9c1c50ed57f2008917e5a3b2e9a3fd4e9ca5a",
)
STAGES = (
    "/usr/local/sbin/.porota-rc6-host-installer.r297-stage",
    "/usr/local/libexec/porota-rc6-custody/.root-installer-contract.r297-stage",
    "/var/lib/porota-rc6-oci-custody/.approved-manifest-pin.r297-stage",
    "/var/lib/porota-rc6-oci-custody/.bootstrap-r297-complete.stage",
)


def env(**updates):
    result = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "mbalbo2023",
        "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
        "GITHUB_SHA": "a" * 40,
        "RC6_EXPECTED_DO_ID": EXPECTED_ID,
        "RC6_EXPECTED_DO_REGION": EXPECTED_REGION,
        "DO_HOST": "rc6.example.net",
        "DO_USER": "porotaadmin",
        "DO_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----\nEXAMPLE\n",
        "DO_KNOWN_HOSTS": "rc6.example.net ssh-ed25519 EXAMPLE",
    }
    result.update(updates)
    return result


def witness_lines():
    """Hand-derived R301 physical fixture, independent of verifier builders."""
    result = []
    for i, path in enumerate(ROOT_DIRS):
        bits = "700" if path == ROOT_DIRS[-1] else "755"
        result.append(f"STAT|{path}|0|0|{bits}|2|directory|2049|{100+i}|4096")
    modes = ("555", "400", "400", "400")
    sizes = (3500, 8900, 65, 559)
    for i, path in enumerate(FILES):
        result.append(f"STAT|{path}|0|0|{modes[i]}|1|regular file|2049|{200+i}|{sizes[i]}")
    for path, digest in zip(FILES, HASHES):
        result.append(f"HASH|{path}|{digest}  {path}")
    for i, path in enumerate(FILES):
        result.append(f"STAT|{path}|0|0|{modes[i]}|1|regular file|2049|{200+i}|{sizes[i]}")
    result.append("STAGES|ABSENT")
    return result


def witness_bytes(lines=None):
    return ("\n".join(witness_lines() if lines is None else lines) + "\n").encode()


class ValidateWitnessTests(unittest.TestCase):
    def test_exact_physical_files_only_witness_is_accepted_without_host_go(self):
        self.assertEqual(r302.verify_witness(witness_bytes()),
                         "FILES_WITNESSED_ONLY_NOT_HOST_GO")
        self.assertEqual(r302.verify_host_identity(
            b"123456789\nnyc1\n1000\n", env()), None)

    def test_pinned_manifest_mismatch_holds_even_if_other_files_match(self):
        lines = witness_lines()
        index = next(i for i, line in enumerate(lines) if line.startswith(f"HASH|{PIN}|"))
        lines[index] = lines[index].replace(HASHES[2], "a" * 64)
        with self.assertRaisesRegex(r302.R302Hold, "MANIFEST_PIN_HASH_MISMATCH"):
            r302.verify_witness(witness_bytes(lines))

    def test_receipt_mismatch_holds_even_if_files_are_installed(self):
        lines = witness_lines()
        index = next(i for i, line in enumerate(lines) if line.startswith(f"HASH|{RECEIPT}|"))
        lines[index] = lines[index].replace(HASHES[3], "b" * 64)
        with self.assertRaisesRegex(r302.R302Hold, "BOOTSTRAP_RECEIPT_HASH_MISMATCH"):
            r302.verify_witness(witness_bytes(lines))

    def test_different_owner_and_modes_and_symlinks_and_hardlinks_hold(self):
        original = witness_lines()
        i = next(i for i, line in enumerate(original) if line.startswith(f"STAT|{CONTRACT}|"))
        for old, new in (("|0|0|", "|1000|0|"), ("|400|1|", "|644|1|"),
                         ("|1|regular file|", "|2|regular file|"),
                         ("|regular file|", "|symbolic link|")):
            with self.subTest(old=old, new=new):
                lines = original.copy()
                lines[i] = lines[i].replace(old, new, 1)
                with self.assertRaises(r302.R302Hold):
                    r302.verify_witness(witness_bytes(lines))

    def test_lock_mode_and_identity_and_inode_mutation_hold(self):
        original = witness_lines()
        i = next(i for i, line in enumerate(original) if line.startswith(f"STAT|{ROOT_DIRS[-1]}|"))
        lines = original.copy()
        lines[i] = lines[i].replace("|700|", "|755|")
        with self.assertRaises(r302.R302Hold):
            r302.verify_witness(witness_bytes(lines))
        lines = original.copy()
        second = next(i for i in range(len(lines)-1, -1, -1)
                      if lines[i].startswith(f"STAT|{CLI}|"))
        lines[second] = lines[second].replace("|2049|200|", "|2049|999|")
        with self.assertRaisesRegex(r302.R302Hold, "INODE_CHANGED"):
            r302.verify_witness(witness_bytes(lines))

    def test_absent_stage_marker_or_extra_output_holds(self):
        for lines in (witness_lines()[:-1], witness_lines() + ["BANNER"],
                      [witness_lines()[0]] + witness_lines()):
            with self.subTest(lines=len(lines)):
                with self.assertRaises(r302.R302Hold):
                    r302.verify_witness(witness_bytes(lines))

    def test_unbounded_output_and_non_ascii_rejected(self):
        for data in (b"Z" * 16385, b"\xff\n", witness_bytes() + b"\r"):
            with self.subTest(size=len(data)):
                with self.assertRaises(r302.R302Hold):
                    r302.verify_witness(data)

    def test_do_id_or_region_mismatch_holds_without_exposing_values(self):
        for raw in (b"123456780\nnyc1\n1000\n", b"123456789\nams3\n1000\n",
                    b"123456789\nnyc1\n0\n", b"123456789\nnyc1\nEXTRA\n"):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(r302.R302Hold, "DO_IDENTITY_UNVERIFIED"):
                    r302.verify_host_identity(raw, env())


class PhysicalTransportTests(unittest.TestCase):
    def test_missing_pins_and_untrusted_dispatch_are_rejected_pre_ssh(self):
        for patch in ({"RC6_EXPECTED_DO_ID": ""},
                      {"RC6_EXPECTED_DO_REGION": ""},
                      {"GITHUB_REF": "refs/heads/feature"},
                      {"GITHUB_ACTOR": "attacker"},
                      {"GITHUB_REPOSITORY": "attacker/Porota-trading"},
                      {"DO_USER": "root"},
                      {"DO_KNOWN_HOSTS": ""},
                      {"DO_HOST": "bad host;command"}):
            with self.subTest(patch=patch):
                with self.assertRaises(r302.R302Hold):
                    r302.validate_environment(env(**patch))

    def test_two_ssh_calls_identity_before_privilege_and_no_host_changes(self):
        calls = []

        def remote(argv, payload, *, timeout_s, output_cap):
            calls.append((argv, payload, timeout_s, output_cap))
            return b"123456789\nnyc1\n1000\n" if len(calls) == 1 else witness_bytes()

        report = r302.physical_preflight(env(), ssh_call=remote)
        self.assertEqual(len(calls), 2)
        self.assertEqual(report, "FILES_WITNESSED_ONLY_NOT_HOST_GO")
        self.assertNotIn(b"/usr/bin/sudo", calls[0][1])
        self.assertIn(b"/usr/bin/sudo -n /usr/bin/sha256sum", calls[1][1])
        self.assertIn(b"/usr/bin/sudo -n /usr/bin/stat", calls[1][1])
        self.assertIn(b"/usr/bin/test ! -e", calls[1][1])
        self.assertIn(b"/usr/bin/test ! -L", calls[1][1])
        self.assertEqual(len(re.findall(rb"/usr/bin/ssh\b", str(calls).encode())), 2)
        self.assertNotIn(b"docker ", calls[1][1].lower())
        self.assertNotIn(b"systemctl", calls[1][1].lower())
        self.assertNotIn(b"/usr/bin/install ", calls[1][1])
        self.assertNotIn(b"/usr/bin/rm ", calls[1][1])
        self.assertNotIn(b"/usr/bin/sync ", calls[1][1])
        self.assertNotIn(b"/usr/bin/ln ", calls[1][1])
        self.assertNotIn(b"/usr/bin/mkdir ", calls[1][1])
        self.assertNotIn(b"/usr/local/sbin/porota-rc6-oci-custody", calls[1][1])

    def test_remote_script_rejects_symlink_for_every_parent_and_target(self):
        script = r302._witness_script().decode("ascii")
        for path in (*ROOT_DIRS, *FILES):
            with self.subTest(path=path):
                # Positive absence check must be repeated to catch a swap
                # between the first stat and final digest/re-stat.
                self.assertGreaterEqual(
                    script.count(f"/usr/bin/sudo -n /usr/bin/test ! -L {path}\n"), 2)

    def test_mismatched_host_identity_prevents_second_connection(self):
        calls = []

        def remote(argv, payload, *, timeout_s, output_cap):
            calls.append(payload)
            return b"987654321\nnyc1\n1000\n"

        with self.assertRaisesRegex(r302.R302Hold, "DO_IDENTITY_UNVERIFIED"):
            r302.physical_preflight(env(), ssh_call=remote)
        self.assertEqual(len(calls), 1)

    def test_cli_holds_without_host_or_network_on_invalid_args(self):
        self.assertEqual(r302.main(["--unsupported"], env=env()), 79)

    def test_bounded_local_process_rejects_overflow(self):
        with self.assertRaisesRegex(r302.R302Hold, "SSH_OUTPUT_UNBOUNDED"):
            r302.run_bounded_command(
                [sys.executable, "-c", "import sys;sys.stdout.write('a'*1000)"],
                b"", timeout_s=2, output_cap=32)

    def test_stalled_ssh_stdin_is_killed_by_deadline_not_blocking_write(self):
        started = time.monotonic()
        with self.assertRaisesRegex(r302.R302Hold, "SSH_TIMEOUT"):
            r302.run_bounded_command(
                [sys.executable, "-c", "import time;time.sleep(1.5)"],
                b"x" * 16384, timeout_s=0.15, output_cap=32)
        self.assertLess(time.monotonic() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
