"""Synthetic R164 release-byte contract tests: no GitHub, Docker, or host calls."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile

from scripts import rc6_r163_native_volume_proof as native


SHA = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64
SOURCE = "d" * 64
COLLECTOR = "e" * 64
VOLUME = "f" * 64
RUN_ID = 99123
ATTEMPT = 2
ARTIFACT_ID = 88123
PR = 1087
HEAD = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
NAME = "porota-predeploy-v2-" + SHA
BYTE_MANIFEST = "porota-artifact-byte-manifest-v1.json"
REPO = "mbalbo2023/Porota-trading"
WORKFLOW = ".github/workflows/porota-predeploy-v2.yml"


def _raw_json(data: object) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inspect(collector: bool) -> dict:
    role = "bundle-collector" if collector else "bundle"
    return {
        "Id": COLLECTOR if collector else SOURCE,
        "Image": IMAGE,
        "Config": {
            "User": "1000:1000",
            "Labels": {
                "porota.commit": SHA, "porota.candidate_tree": TREE,
                "porota.predeploy": "v2", "porota.native-proof": "v3",
                "porota.native-role": role,
                "porota.run_id": str(RUN_ID),
                "porota.run_attempt": str(ATTEMPT),
            },
        },
        "HostConfig": {
            "NetworkMode": "none", "ReadonlyRootfs": True,
            "CapDrop": ["ALL"], "Privileged": False,
            "SecurityOpt": ["no-new-privileges"],
            "IpcMode": "private", "UTSMode": "", "PidMode": "",
            "UsernsMode": "", "CgroupnsMode": "private",
            "GroupAdd": None, "DeviceRequests": None,
            "DeviceCgroupRules": None, "Runtime": "runc",
            "Sysctls": None, "CgroupParent": "",
            "PublishAllPorts": False, "PortBindings": {},
            "Links": None, "ExtraHosts": None, "Binds": None,
            "Mounts": [] if collector else [{
                "Type": "volume", "Source": "", "Target": "/app/data",
                "ReadOnly": False,
            }],
            "VolumesFrom": [SOURCE + ":ro"] if collector else None,
        },
        "Mounts": [{
            "Type": "volume", "Name": VOLUME,
            "Source": f"/var/lib/docker/volumes/{VOLUME}/_data",
            "Destination": "/app/data", "Driver": "local",
            "Mode": "ro" if collector else "", "RW": not collector,
        }],
    }


def _native_tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path in sorted(native.NATIVE_EVIDENCE_PATHS):
            if path.endswith(".xml"):
                payload = b'<testsuite tests="1" errors="0" failures="0" skipped="0"><testcase name="offline"/></testsuite>'
            else:
                payload = b'{"offline_fixture":true}\n'
            info = tarfile.TarInfo("./" + path)
            info.uid = info.gid = 1000
            info.mode = 0o644
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def _custody() -> dict:
    return {
        "schema_version": 1, "status": "OFFHOST_CONTRACT_ONLY",
        "candidate_sha": SHA, "candidate_tree_sha": TREE,
        "image_id": IMAGE, "run_id": str(RUN_ID),
        "run_attempt": str(ATTEMPT), "role": "bundle",
        "source_cid": SOURCE, "collector_cid": COLLECTOR,
        "volume_name": VOLUME, "mount_target": "/app/data",
        "uid": 1000, "gid": 1000, "host_or_runtime_verified": False,
    }


class ReleaseBytes(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="rc6-r164-offline-")
        self.addCleanup(self.temp.cleanup)
        archive = Path(self.temp.name) / "github-rest-downloaded.zip"
        image = gzip.compress(b"synthetic docker-save tar bytes", mtime=0)
        bundle = gzip.compress(b"synthetic exact source bundle", mtime=0)
        frozen = {
            "schema_version": 1, "candidate_sha": SHA,
            "candidate_tree_sha": TREE, "image_id": IMAGE,
            "image_tar_sha256": _sha(image),
            "image_config_sha256": "1" * 64,
            "image_size_bytes": 4096,
            "paper_mode_required": "PRODUCTION_PAPER",
            "real_orders_sent_required": 0,
            "real_order_capability_required": "BLOCKED",
            "build_once": True,
        }
        bundle_manifest = {
            "schema_version": 1, "status": "GREEN", "bundle_sha256": _sha(bundle),
            "file_count": 1,
            "files": [{"path": "app.py", "sha256": "1" * 64, "bytes": 2}],
        }
        native_tar = _native_tar()
        native_manifest = native.attest_collector_tar(native_tar, custody_receipt=_custody())
        self.files = {
            "porota-predeploy-image.tar.gz": image,
            "porota-deploy-bundle-v2.tgz": bundle,
            "porota-deploy-bundle-v2-manifest.json": _raw_json(bundle_manifest),
            "porota-frozen-candidate.json": _raw_json(frozen),
            "native.tar": native_tar,
            "native-manifest.json": _raw_json(native_manifest),
            "source-inspect.json": _raw_json([_inspect(False)]),
            "collector-inspect.json": _raw_json([_inspect(True)]),
            "custody.json": _raw_json(_custody()),
            "porota-governed-tests.xml": b'<testsuite tests="1" errors="0" failures="0" skipped="0"><testcase name="offline"/></testsuite>',
        }
        self.payload = {
            "archive": archive,
            "expected": {
                "schema_version": 1, "repository": REPO,
                "pr_number": PR, "base_ref": PRODUCT, "head_ref": HEAD,
                "candidate_sha": SHA, "candidate_tree_sha": TREE,
                "run_id": RUN_ID, "run_attempt": ATTEMPT,
                "artifact_id": ARTIFACT_ID, "artifact_name": NAME,
                "artifact_digest": "", "image_id": IMAGE,
                "image_tar_sha256": _sha(image),
                "file_paths": sorted(self.files),
            },
            "artifact_receipt": {
                "id": ARTIFACT_ID, "name": NAME, "size_in_bytes": 0,
                "expired": False, "digest": "",
                "workflow_run": {"id": RUN_ID, "head_sha": SHA},
            },
            "run_receipt": {
                "id": RUN_ID, "run_attempt": ATTEMPT,
                "head_sha": SHA, "head_branch": HEAD,
                "status": "completed", "conclusion": "success",
                "event": "pull_request", "path": WORKFLOW,
                "repository": {"full_name": REPO},
                "pull_requests": [{"number": PR, "head": {"sha": SHA}}],
            },
            "pr_receipt": {
                "number": PR, "draft": False, "state": "open",
                "head": {"sha": SHA, "ref": HEAD, "repo": {"full_name": REPO}},
                "base": {"ref": PRODUCT, "repo": {"full_name": REPO}},
            },
            "expected_receipt_sha256": "",
        }
        self._reseal()

    def _reseal(self) -> None:
        rows = [
            {"path": path, "type": "file", "size_bytes": len(data),
             "sha256": _sha(data)}
            for path, data in sorted(self.files.items())
        ]
        byte_manifest = {
            "schema_version": 1, "candidate_sha": SHA,
            "candidate_tree_sha": TREE, "run_id": RUN_ID,
            "run_attempt": ATTEMPT, "artifact_name": NAME,
            "file_count": len(rows),
            "total_size_bytes": sum(x["size_bytes"] for x in rows),
            "files": rows,
        }
        with zipfile.ZipFile(self.payload["archive"], "w", zipfile.ZIP_DEFLATED) as zf:
            for path, data in self.files.items():
                zf.writestr(path, data)
            zf.writestr(BYTE_MANIFEST, _raw_json(byte_manifest))
        archive = self.payload["archive"]
        digest = _sha(archive.read_bytes())
        self.payload["expected"]["artifact_digest"] = "sha256:" + digest
        self.payload["artifact_receipt"]["digest"] = "sha256:" + digest
        self.payload["artifact_receipt"]["size_in_bytes"] = archive.stat().st_size
        self._pin()

    def _pin(self) -> None:
        self.payload["expected_receipt_sha256"] = _sha(_raw_json(self.payload["expected"]))

    def _resign_zip_only(self) -> None:
        raw = self.payload["archive"].read_bytes()
        self.payload["expected"]["artifact_digest"] = "sha256:" + _sha(raw)
        self.payload["artifact_receipt"]["digest"] = "sha256:" + _sha(raw)
        self.payload["artifact_receipt"]["size_in_bytes"] = len(raw)
        self._pin()

    def _change_native_xml(self, xml: bytes) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=io.BytesIO(self.files["native.tar"]), mode="r:") as src:
            with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as dst:
                for item in src:
                    contents = src.extractfile(item).read()
                    if item.name.endswith("session/dashboard_auth_52.xml"):
                        contents = xml
                    item.size = len(contents)
                    dst.addfile(item, io.BytesIO(contents))
        self.files["native.tar"] = buf.getvalue()
        manifest = native.attest_collector_tar(self.files["native.tar"],
                                               custody_receipt=_custody())
        self.files["native-manifest.json"] = _raw_json(manifest)
        self._reseal()

    def _verify(self):
        from scripts.rc6_r164_release_artifact_byte_verifier import verify_release_artifact_bytes
        return verify_release_artifact_bytes(**self.payload)

    def _reject(self, reason: str) -> None:
        from scripts.rc6_r164_release_artifact_byte_verifier import ReleaseArtifactEvidenceRed
        with self.assertRaisesRegex(ReleaseArtifactEvidenceRed, "^" + reason + "$"):
            self._verify()

    def test_valid_same_run_release_bytes_stay_offline_not_release_go(self) -> None:
        evidence = self._verify()
        self.assertEqual(evidence["status"], "RELEASE_BYTES_MATCH_OFFLINE_ONLY")
        self.assertEqual(evidence["artifact_id"], ARTIFACT_ID)
        self.assertEqual(evidence["candidate_tree_sha"], TREE)
        self.assertEqual(evidence["image_tar_sha256"], self.payload["expected"]["image_tar_sha256"])
        self.assertEqual(evidence["native_file_count"], 13)
        self.assertFalse(evidence["trusted_bootstrap_verified"])
        self.assertFalse(evidence["release_go"])
        self.assertFalse(evidence["automatic_go"])
        self.assertFalse(evidence["runtime_verified"])
        self.assertFalse(evidence["host_gate_verified"])
        self.assertFalse(evidence["bundle_inner_tar_verified"])
        self.assertFalse(evidence["docker_image_load_verified"])
        self.assertFalse(evidence["fresh_rest_race_check_verified"])

    def test_another_run_artifact_id_is_rejected(self) -> None:
        self.payload["artifact_receipt"]["id"] += 1
        self._reject("ARTIFACT_ID_MISMATCH")

    def test_in_progress_run_cannot_self_attest(self) -> None:
        self.payload["run_receipt"]["status"] = "in_progress"
        self._reject("RUN_NOT_SUCCESS")

    def test_missing_external_receipt_pin_is_rejected(self) -> None:
        self.payload["expected_receipt_sha256"] = ""
        self._reject("EXTERNAL_PIN_INVALID")

    def test_draft_pr_is_not_release_evidence(self) -> None:
        self.payload["pr_receipt"]["draft"] = True
        self._reject("PR_NOT_RELEASE_ELIGIBLE")

    def test_signed_offline_receipt_detects_forgery_but_never_authenticates_github(self) -> None:
        key = b"an offline-only 32-byte test secret"
        signature = "hmac-sha256:" + hmac.new(
            key, _raw_json(self.payload["expected"]), hashlib.sha256).hexdigest()
        from scripts.rc6_r164_release_artifact_byte_verifier import verify_release_artifact_bytes
        result = verify_release_artifact_bytes(
            **self.payload, offline_test_hmac=signature, offline_test_key=key)
        self.assertTrue(result["offline_test_hmac_match"])
        self.assertFalse(result["trusted_bootstrap_verified"])
        self.assertFalse(result["release_go"])

    def test_resealed_bad_junit_inside_native_tar_must_fail_release_bytes(self) -> None:
        self._change_native_xml(
            b'<testsuite tests="1" failures="1" errors="0" skipped="0">'
            b'<testcase name="failed"><failure>red</failure></testcase></testsuite>')
        self._reject("NATIVE_JUNIT_RED")

    def test_resealed_skipped_junit_inside_native_tar_must_fail_release_bytes(self) -> None:
        self._change_native_xml(
            b'<testsuite tests="1" failures="0" errors="0" skipped="1">'
            b'<testcase name="skipped"><skipped/></testcase></testsuite>')
        self._reject("NATIVE_JUNIT_RED")

    def test_governed_junit_with_resealed_failure_is_not_release_evidence(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuite tests="1" failures="1" errors="0" skipped="0">'
            b'<testcase name="failed"><failure/></testcase></testsuite>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_governed_root_testsuites_failure_cannot_contradict_green_child(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuites tests="1" failures="1" errors="0" skipped="0">'
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase name="pretend"/></testsuite></testsuites>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_native_testcase_status_skipped_cannot_override_zero_suite_counter(self) -> None:
        self._change_native_xml(
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase name="fake" status="skipped"/></testsuite>')
        self._reject("NATIVE_JUNIT_RED")

    def test_governed_testcase_status_xfail_or_failure_is_not_green(self) -> None:
        for status in ("xfail", "failure", "error"):
            with self.subTest(status=status):
                self.files["porota-governed-tests.xml"] = (
                    b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
                    + f'<testcase name="fake" status="{status}"/></testsuite>'.encode())
                self._reseal()
                self._reject("GOVERNED_JUNIT_RED")

    def test_namespace_wrapped_failure_cannot_hide_inside_successful_case(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuite xmlns:x="urn:fake" tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase name="hidden"><x:failure/></testcase></testsuite>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_namespaced_testcase_status_attribute_is_never_ignored(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase xmlns:n="urn:fake" name="hidden" n:status="skipped"/>'
            b'</testsuite>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_namespaced_suite_attribute_is_not_silent_metadata(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuites xmlns:n="urn:fake" n:status="failed">'
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase name="fake"/></testsuite></testsuites>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_unknown_testcase_status_aliases_rejected_fail_closed(self) -> None:
        for alias, value in (("test_state", "skipped"),
                             ("resultStatus", "failed"),
                             ("pytest_outcome", "skipped")):
            with self.subTest(alias=alias):
                self.files["porota-governed-tests.xml"] = (
                    b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
                    + f'<testcase name="fake" {alias}="{value}"/></testsuite>'.encode())
                self._reseal()
                self._reject("GOVERNED_JUNIT_RED")

    def test_unknown_suite_status_alias_and_disabled_counter_are_red(self) -> None:
        for extra in ('resultStatus="failed"', 'disabled="1"'):
            with self.subTest(extra=extra):
                self.files["porota-governed-tests.xml"] = (
                    f'<testsuite tests="1" failures="0" errors="0" skipped="0" {extra}>'.encode()
                    + b'<testcase name="fake"/></testsuite>')
                self._reseal()
                self._reject("GOVERNED_JUNIT_RED")

    def test_unknown_nested_result_tag_is_not_silent_junit_metadata(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase name="fake"><rerunFailure/></testcase></testsuite>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_property_named_outcome_cannot_hide_skipped_result(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase name="fake"><properties>'
            b'<property name="outcome" value="skipped"/>'
            b'</properties></testcase></testsuite>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_known_success_status_and_junit_metadata_stay_valid(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<testsuite name="smoke" tests="1" failures="0" errors="0" skipped="0" '
            b'timestamp="2026-11-11T23:00:00Z" hostname="runner" time="0.1">'
            b'<testcase name="safe" classname="test_offline" time="0.1" '
            b'file="tests/test_offline.py" line="10" '
            b'status="passed" outcome="run" result="success"/>'
            b'</testsuite>')
        self._reseal()
        self.assertEqual(self._verify()["status"], "RELEASE_BYTES_MATCH_OFFLINE_ONLY")

    def test_utf16_encoded_dtd_entity_is_never_expanded_in_junit(self) -> None:
        xml = ('<?xml version="1.0" encoding="UTF-16"?>'
               '<!DOCTYPE testsuite [<!ENTITY xyz "forged">]>'
               '<testsuite tests="1" failures="0" errors="0" skipped="0">'
               '<testcase name="&xyz;"/></testsuite>')
        self.files["porota-governed-tests.xml"] = xml.encode("utf-16")
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_utf8_dtd_entity_is_rejected_too(self) -> None:
        self.files["porota-governed-tests.xml"] = (
            b'<!DOCTYPE testsuite [<!ENTITY xyz "forged">]>'
            b'<testsuite tests="1" failures="0" errors="0" skipped="0">'
            b'<testcase name="&xyz;"/></testsuite>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_million_digit_style_junit_counter_never_escapes_as_exception(self) -> None:
        huge = b"9" * 5000
        self.files["porota-governed-tests.xml"] = (
            b'<testsuite tests="' + huge +
            b'" failures="0" errors="0" skipped="0"><testcase name="offline"/></testsuite>')
        self._reseal()
        self._reject("GOVERNED_JUNIT_RED")

    def test_new_zip_digest_cannot_validate_old_external_pin(self) -> None:
        self.payload["archive"].write_bytes(
            self.payload["archive"].read_bytes() + b"tampered outside zip")
        self._reject("ZIP_DIGEST_MISMATCH")

    def test_foreign_pull_request_head_and_branch_are_rejected(self) -> None:
        self.payload["pr_receipt"]["head"]["sha"] = "1" * 40
        self._reject("PR_REF_SHA_MISMATCH")

    def test_wrong_workflow_path_does_not_certify_probe_artifact(self) -> None:
        self.payload["run_receipt"]["path"] = (
            ".github/workflows/rc6-r163-offhost-guard79-proof.yml")
        self._reject("RUN_WORKFLOW_MISMATCH")

    def test_wrong_run_attempt_fails_even_if_artifact_name_matches(self) -> None:
        self.payload["run_receipt"]["run_attempt"] = ATTEMPT + 1
        self._reject("RUN_ATTEMPT_MISMATCH")

    def test_missing_rest_artifact_digest_is_red(self) -> None:
        del self.payload["artifact_receipt"]["digest"]
        self._reject("ARTIFACT_DIGEST_MISMATCH")

    def test_unpinned_file_allowlist_modification_is_red(self) -> None:
        self.payload["expected"]["file_paths"].append("extra-script.sh")
        self._reject("EXTERNAL_PIN_MISMATCH")

    def test_resigned_allowlist_with_unexpected_extra_path_is_red(self) -> None:
        self.payload["expected"]["file_paths"].append("extra-script.sh")
        self.payload["expected"]["file_paths"].sort()
        self._pin()
        self._reject("RELEASE_FILE_SET_MISMATCH")

    def test_resealed_image_mutation_disagrees_with_frozen_digest(self) -> None:
        self.files["porota-predeploy-image.tar.gz"] += b"modified exported image"
        self._reseal()
        self._reject("IMAGE_BYTES_DIGEST_MISMATCH")

    def test_resealed_frozen_build_once_false_is_red(self) -> None:
        frozen = json.loads(self.files["porota-frozen-candidate.json"])
        frozen["build_once"] = False
        self.files["porota-frozen-candidate.json"] = _raw_json(frozen)
        self._reseal()
        self._reject("FROZEN_IMAGE_CONTRACT_MISMATCH")

    def test_resealed_native_foreign_source_cid_is_red(self) -> None:
        source = json.loads(self.files["source-inspect.json"])
        source[0]["Id"] = "0" * 64
        self.files["source-inspect.json"] = _raw_json(source)
        self._reseal()
        self._reject("NATIVE_PROOF_RED")

    def test_duplicate_zip_member_fails_even_after_digest_is_rebound(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.payload["archive"], "a") as zf:
                zf.writestr("native.tar", b"duplicate")
        self._resign_zip_only()
        self._reject("ZIP_MEMBER_DUPLICATE")

    def test_zip_traversal_path_is_never_extracted(self) -> None:
        with zipfile.ZipFile(self.payload["archive"], "a") as zf:
            zf.writestr("../other-run.txt", b"evil")
        self._resign_zip_only()
        self._reject("ZIP_MEMBER_PATH_UNSAFE")

    def test_zip_symlink_member_is_rejected(self) -> None:
        link = zipfile.ZipInfo("foreign-link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.payload["archive"], "a") as zf:
            zf.writestr(link, b"/etc/passwd")
        self._resign_zip_only()
        self._reject("ZIP_MEMBER_TYPE_UNSAFE")

    def test_zip_expansion_bomb_is_rejected_before_decompression(self) -> None:
        with zipfile.ZipFile(self.payload["archive"], "a", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("zero-bomb", bytes(8 * 1024 * 1024))
        self._resign_zip_only()
        self._reject("ZIP_EXPANSION_RATIO_LIMIT")

    def test_symlink_to_archive_is_rejected_before_open(self) -> None:
        real = Path(self.temp.name) / "archive-original.zip"
        self.payload["archive"].rename(real)
        self.payload["archive"].symlink_to(real)
        self._reject("ARCHIVE_NOT_REGULAR")

    def test_offline_hmac_with_wrong_key_does_not_sign_github_receipts(self) -> None:
        key = b"an offline-only 32-byte test secret"
        signature = "hmac-sha256:" + hmac.new(
            key, _raw_json(self.payload["expected"]), hashlib.sha256).hexdigest()
        from scripts.rc6_r164_release_artifact_byte_verifier import verify_release_artifact_bytes
        from scripts.rc6_r164_release_artifact_byte_verifier import ReleaseArtifactEvidenceRed
        with self.assertRaisesRegex(ReleaseArtifactEvidenceRed, "^OFFLINE_HMAC_MISMATCH$"):
            verify_release_artifact_bytes(
                **self.payload, offline_test_hmac=signature,
                offline_test_key=b"a different offline-only test secret")

    def test_cli_accepts_offline_fixture_without_claiming_trusted_release(self) -> None:
        root = Path(self.temp.name)
        paths = {}
        for field, basename in (
            ("expected", "expected.json"),
            ("artifact_receipt", "artifact-rest.json"),
            ("run_receipt", "run-rest.json"),
            ("pr_receipt", "pr-rest.json"),
        ):
            dest = root / basename
            dest.write_bytes(_raw_json(self.payload[field]))
            paths[field] = str(dest)
        script = Path(__file__).resolve().parents[1] / "scripts" / (
            "rc6_r164_release_artifact_byte_verifier.py")
        command = [
            sys.executable, "-I", str(script),
            "--archive", str(self.payload["archive"]),
            "--expected-receipt", paths["expected"],
            "--expected-receipt-sha256", self.payload["expected_receipt_sha256"],
            "--artifact-rest", paths["artifact_receipt"],
            "--run-rest", paths["run_receipt"],
            "--pr-rest", paths["pr_receipt"],
        ]
        accepted = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        data = json.loads(accepted.stdout)
        self.assertEqual(data["status"], "RELEASE_BYTES_MATCH_OFFLINE_ONLY")
        self.assertFalse(data["release_go"])
        self.assertFalse(data["trusted_bootstrap_verified"])
        self.payload["run_receipt"]["status"] = "queued"
        Path(paths["run_receipt"]).write_bytes(_raw_json(self.payload["run_receipt"]))
        rejected = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(rejected.returncode, 79)
        self.assertIn("RC6_R164_RELEASE_BYTES=RED|reason=RUN_NOT_SUCCESS", rejected.stderr)
        self.assertNotIn(SOURCE, rejected.stderr)

    def test_pinned_source_loader_refuses_different_dependency_bytes(self) -> None:
        from scripts.rc6_r164_release_artifact_byte_verifier import _load_pinned_dependency
        root = Path(self.temp.name)
        (root / "rc6_r163_artifact_byte_verifier.py").write_bytes(b"print('untrusted')\n")
        with self.assertRaisesRegex(ImportError, "^R164_DEPENDENCY_PIN_MISMATCH$"):
            _load_pinned_dependency("rc6_r163_artifact_byte_verifier.py", directory=root)


if __name__ == "__main__":
    unittest.main()
