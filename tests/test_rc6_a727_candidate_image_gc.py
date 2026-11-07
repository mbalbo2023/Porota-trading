"""A727 Docker-only GC: corrupt provenance, cross-app and stale-plan regressions."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/rc6_a727_candidate_image_gc.py"
spec = importlib.util.spec_from_file_location("a727_candidate_gc", SCRIPT)
gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gc)

CANDIDATE = "a" * 40
FAILED = "b" * 40
PREVIOUS = "c" * 40
IMAGE = "sha256:" + "d" * 64
CRITICAL = "sha256:" + "e" * 64
TAGS = ["porota-predeploy-v2:" + CANDIDATE,
        "porota-trading-bot:17.0.0-rc6-candidate-" + CANDIDATE]
DIGESTS = ["porota-predeploy-v2@sha256:" + "f" * 64]
WORKFLOW = SCRIPT.parents[1] / ".github/workflows/rc6-a727-candidate-only-docker-gc.yml"


def details(tags=None, digests=None, iid=IMAGE, *,
            oci_commit=CANDIDATE, oci_predeploy="v2"):
    return {"id": iid, "tags": list(TAGS if tags is None else tags),
            "digests": list(DIGESTS if digests is None else digests),
            "size_upper": 1000,
            "_oci_commit": oci_commit, "_oci_predeploy": oci_predeploy}


def manifest_details(**kwargs):
    return gc._public_image(details(**kwargs))


class CandidateClassification(unittest.TestCase):
    def test_exact_candidate_tags_with_corresponding_digest(self):
        self.assertTrue(gc._target(details(), CANDIDATE))

    def test_digestless_exact_candidate_allowed(self):
        self.assertTrue(gc._target(details(digests=[]), CANDIDATE))

    def test_reject_other_application_alias(self):
        self.assertFalse(gc._target(details(tags=TAGS + ["boca-bot:latest"]), CANDIDATE))

    def test_reject_stable_porota_alias(self):
        self.assertFalse(gc._target(details(tags=TAGS + [gc.STABLE]), CANDIDATE))

    def test_reject_wrong_candidate_tag(self):
        self.assertFalse(gc._target(details(tags=[TAGS[0].replace(CANDIDATE, FAILED)]), CANDIDATE))

    def test_reject_cross_repository_digest(self):
        self.assertFalse(gc._target(details(digests=["boca@sha256:" + "f" * 64]), CANDIDATE))

    def test_reject_digest_repository_not_in_tags(self):
        self.assertFalse(gc._target(details(tags=[TAGS[0]], digests=[
            "porota-trading-bot@sha256:" + "f" * 64]), CANDIDATE))

    def test_reject_no_candidate_tag(self):
        self.assertFalse(gc._target(details(tags=[]), CANDIDATE))

    def test_exact_candidate_owner_can_be_discovered_without_foreign_identity(self):
        self.assertEqual(gc._candidate_owner(details()), CANDIDATE)
        self.assertIsNone(gc._candidate_owner(details(tags=["boca:current"])))

    def test_reject_tag_spoof_without_exact_oci_provenance(self):
        for value in (None, FAILED, "secret", ""):
            with self.subTest(commit=value):
                self.assertFalse(gc._target(details(oci_commit=value), CANDIDATE))
        for value in (None, "v1", "latest"):
            with self.subTest(predeploy=value):
                self.assertFalse(gc._target(details(oci_predeploy=value), CANDIDATE))
        self.assertEqual(gc._tag_claim(details(oci_commit=None)), CANDIDATE)
        self.assertIsNone(gc._candidate_owner(details(oci_commit=None)))

    def test_public_manifest_drops_oci_and_other_private_labels(self):
        public = gc._public_image(details())
        self.assertEqual(set(public), {"id", "tags", "digests", "size_upper"})
        self.assertNotIn("_oci_commit", public)


class ImageInspectProvenance(unittest.TestCase):
    def test_docker_queries_only_two_allowlisted_provenance_labels(self):
        raw = "|".join(map(json.dumps, (IMAGE, TAGS, DIGESTS, 1000,
                                        CANDIDATE, "v2")))
        with patch.object(gc, "_docker", return_value=raw) as docker:
            image = gc._image_details(IMAGE)
        self.assertEqual(gc._candidate_owner(image), CANDIDATE)
        self.assertEqual(set(image) - set(gc._public_image(image)),
                         {"_oci_commit", "_oci_predeploy"})
        formatter = docker.call_args.args[3]
        self.assertIn('index .Config.Labels "porota.commit"', formatter)
        self.assertIn('index .Config.Labels "porota.predeploy"', formatter)
        self.assertNotIn("{{json .Config.Labels}}", formatter)

    def test_missing_oci_labels_are_held_without_exposing_foreign_content(self):
        for pair in ((None, None), (FAILED, "v2"), ("boca-secret", "v2"),
                     (CANDIDATE, "untrusted")):
            with self.subTest(labels=pair):
                raw = "|".join(map(json.dumps, (IMAGE, TAGS, DIGESTS, 1000,
                                                *pair)))
                with patch.object(gc, "_docker", return_value=raw):
                    image = gc._image_details(IMAGE)
                self.assertIsNone(gc._candidate_owner(image))
                self.assertNotIn("boca-secret", json.dumps(gc._public_image(image)))


class CandidatePlan(unittest.TestCase):
    def setUp(self):
        self.safety = {"marker_hash": "a" * 64, "barrier_hash": "b" * 64,
                       "quiescence_hash": "c" * 64,
                       "book": {"state": "VERIFIED_CLOSED_FLAT",
                                "value": {"orders": 0, "session": "CLOSED"}},
                       "watch": {"ppi-watch.service": ("active", "enabled")},
                       "containers": {gc.OWNED[2]: {"image": CRITICAL}},
                       "candidate": CANDIDATE}

    def test_only_exact_unreferenced_candidate_selected(self):
        with patch.object(gc, "_admission", return_value=self.safety), \
             patch.object(gc, "_docker_inventory", return_value=(
                 {CRITICAL}, [IMAGE, CRITICAL, "sha256:" + "1" * 64])), \
             patch.object(gc, "_image_details", side_effect=lambda iid: (
                 details() if iid == IMAGE else details(tags=["boca:main"], iid=iid))):
            plan = gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)
        self.assertEqual([row["id"] for row in plan["targets"]], [IMAGE])
        self.assertEqual(plan["discovered_candidate_shas"], [CANDIDATE])
        digest = plan.pop("plan_sha256")
        self.assertEqual(gc._sha(plan), digest)

    def test_candidate_alias_without_oci_is_not_removable_and_reported(self):
        with patch.object(gc, "_admission", return_value=self.safety), \
             patch.object(gc, "_docker_inventory", return_value=({CRITICAL}, [IMAGE, CRITICAL])), \
             patch.object(gc, "_image_details", side_effect=lambda iid: (
                 details(oci_commit=None) if iid == IMAGE
                 else details(tags=["boca:main"], iid=iid))):
            plan = gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)
        self.assertEqual(plan["targets"], [])
        self.assertEqual(plan["discovered_candidate_shas"], [CANDIDATE])
        self.assertEqual(plan["oci_provenance_unverified_count"], 1)
        self.assertEqual(plan["apply_hold"], "OCI_LABEL_UNVERIFIED")
        self.assertIsNone(plan["safety"])

    def test_missing_postboot_barrier_remains_independent_of_oci_hold(self):
        with patch.object(gc, "_admission", side_effect=gc.Hold(
                "BARRIER_ABSENT_POSTBOOT_UNVERIFIED")), \
             patch.object(gc, "_docker_inventory", return_value=(set(), [IMAGE])), \
             patch.object(gc, "_image_details", return_value=details(oci_predeploy=None)):
            plan = gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)
        self.assertEqual(plan["targets"], [])
        self.assertEqual(plan["oci_provenance_unverified_count"], 1)
        self.assertEqual(plan["apply_hold"], "BARRIER_ABSENT_POSTBOOT_UNVERIFIED")
        self.assertIsNone(plan["safety"])


    def test_referenced_candidate_never_selected(self):
        with patch.object(gc, "_admission", return_value=self.safety), \
             patch.object(gc, "_docker_inventory", return_value=({CRITICAL, IMAGE}, [IMAGE, CRITICAL])):
            self.assertFalse(gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)["targets"])

    def test_plan_returns_exact_docker_inventory_when_book_unavailable(self):
        with patch.object(gc, "_admission", side_effect=gc.Hold("BOOK_READONLY_UNAVAILABLE")), \
             patch.object(gc, "_docker_inventory", return_value=(set(), [IMAGE])), \
             patch.object(gc, "_image_details", return_value=details()):
            plan = gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)
        self.assertEqual([x["id"] for x in plan["targets"]], [IMAGE])
        self.assertEqual(plan["discovered_candidate_shas"], [CANDIDATE])
        self.assertIsNone(plan["safety"])
        self.assertEqual(plan["apply_hold"], "BOOK_UNVERIFIED")

    def test_plan_keeps_inventory_but_denies_apply_on_owned_diagnostic(self):
        with patch.object(gc, "_admission", side_effect=gc.Hold(
                "OWNED_DASHBOARD_SEMANTICS_UNVERIFIED")), \
             patch.object(gc, "_docker_inventory", return_value=(set(), [IMAGE])), \
             patch.object(gc, "_image_details", return_value=details()):
            plan = gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)
        self.assertEqual([item["id"] for item in plan["targets"]], [IMAGE])
        self.assertIsNone(plan["safety"])
        self.assertEqual(plan["apply_hold"], "OWNED_DASHBOARD_SEMANTICS_UNVERIFIED")

    def test_plan_returns_inventory_when_marker_or_stop_unverified(self):
        for code, expected in (
                ("LAST_VALIDATED_RUNTIME_MARKER_INVALID", "MARKER_UNVERIFIED"),
                ("QUIESCENCE_STOP_IDENTITY_NOT_PROVEN", "STOP_UNVERIFIED"),
                ("FAILED_ATTEMPT_BARRIER_NOT_IDLE", "BARRIER_UNVERIFIED")):
            with self.subTest(code=code), \
                 patch.object(gc, "_admission", side_effect=gc.Hold(code)), \
                 patch.object(gc, "_docker_inventory", return_value=(set(), [IMAGE])), \
                 patch.object(gc, "_image_details", return_value=details()):
                plan = gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)
            self.assertEqual(plan["apply_hold"], expected)
            self.assertEqual(plan["targets"][0]["id"], IMAGE)

    def test_refuses_ambiguous_inventory(self):
        with patch.object(gc, "_admission", return_value=self.safety), \
             patch.object(gc, "_docker_inventory", side_effect=gc.Hold("INVENTORY_UNKNOWN")):
            with self.assertRaises(gc.Hold):
                gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)


class BookAdmission(unittest.TestCase):
    def _admit(self, book_error, fault_stage=None):
        marker = {"deploy_sha": PREVIOUS, "validation_status": "VALIDATED_RUNTIME",
                  "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                  "real_order_routes": "NOT_CALLED", "image_id": CRITICAL}
        barrier = {"candidate": FAILED, "run": "42260034260", "attempt": "1",
                   "phase": "cleanup_payload", "status": "IDLE"}
        cont = {"id": "1" * 64, "image": CRITICAL, "state": "exited",
                "exit_code": 0, "oom": False, "restarts": 0, "health": "ABSENT"}
        crit = {**cont, "id": "2" * 64, "state": "running", "health": "healthy"}
        q = {"candidate_sha": FAILED, "status": "FIX_FORWARD_VALIDATION_REQUIRED",
             "installation_started": True, "inflight": "ABSENT_OWNED_RUNTIME_STOPPED",
             "real_orders_sent": 0, "ppi_watch_accessed": False,
             "containers": {n: {"id": cont["id"], "image": CRITICAL}
                            for n in gc.OWNED[:2]},
             "stop_evidence": {n: {"after": {"Status": "exited", "ExitCode": 0,
                                                     "OOMKilled": False},
                                    "classification": "CLEAN_EXIT"}
                               for n in gc.OWNED[:2]}}
        def json_for(path, owner_root=False):
            if path == gc.BARRIER and fault_stage == "barrier_absent":
                raise FileNotFoundError("postboot volatile barrier missing")
            if path == gc.BARRIER and fault_stage == "barrier_file":
                raise gc.Hold("FILE_IDENTITY_UNPROVEN")
            if path.name == "CURRENT_STATE_V2.json" and fault_stage == "marker_file":
                raise OSError("do not publish host path")
            if path.name.startswith("quiescence-") and fault_stage == "quiescence_file":
                raise gc.Hold("FILE_CHANGED")
            if path == gc.BARRIER: return barrier, "a" * 64
            if path.name == "CURRENT_STATE_V2.json": return marker, "b" * 64
            if path.name.startswith("quiescence-"): return q, "c" * 64
            raise AssertionError("unexpected path")
        def docker_for(*args):
            if fault_stage == "stable_docker":
                raise gc.Hold("DOCKER_OPERATION_FAILED")
            return CRITICAL
        def container_for(name):
            if fault_stage == "owned_docker":
                raise subprocess.TimeoutExpired("docker", 1)
            return crit if name == gc.OWNED[2] else cont
        def watch_for():
            if fault_stage == "watch":
                raise gc.Hold("WATCH_STATUS_INVALID")
            return {}
        with patch.object(gc, "_file_json", side_effect=json_for), \
             patch.object(gc, "_docker", side_effect=docker_for), \
             patch.object(gc, "_container", side_effect=container_for), \
             patch.object(gc, "_book", side_effect=book_error), \
             patch.object(gc, "_watch", side_effect=watch_for):
            return gc._admission(CANDIDATE, FAILED, "42260034260", PREVIOUS)

    def test_readonly_sqlite_unavailable_always_blocks_apply(self):
        with self.assertRaisesRegex(gc.Hold, "BOOK_READONLY_UNAVAILABLE"):
            self._admit(sqlite3.OperationalError("unavailable"))

    def test_readable_unsafe_book_always_blocks_apply(self):
        with self.assertRaisesRegex(gc.Hold, "BOOK_NOT_CLOSED_PAPER_ZERO"):
            self._admit(gc.Hold("BOOK_NOT_CLOSED_PAPER_ZERO"))

    def test_each_read_error_has_exact_phase_not_other(self):
        expected = {
            "barrier_absent": "BARRIER_ABSENT_POSTBOOT_UNVERIFIED",
            "barrier_file": "BARRIER_FILE_UNVERIFIED",
            "marker_file": "MARKER_FILE_UNVERIFIED",
            "stable_docker": "STABLE_DOCKER_UNVERIFIED",
            "owned_docker": "OWNED_OBSERVER_TIMEOUT_UNVERIFIED",
            "quiescence_file": "QUIESCENCE_FILE_UNVERIFIED",
            "watch": "WATCH_READ_UNVERIFIED",
        }
        for stage, expected_code in expected.items():
            with self.subTest(stage=stage), self.assertRaises(gc.Hold) as caught:
                self._admit(None, fault_stage=stage)
            self.assertEqual(str(caught.exception), expected_code)
            self.assertEqual(gc._classify_apply_hold(caught.exception), expected_code)

    def test_fallback_classifier_never_leaks_dynamic_errors(self):
        self.assertEqual(gc._classify_apply_hold(gc.Hold("private token xyz")),
                         "OTHER_UNVERIFIED")
        self.assertEqual(gc._classify_apply_hold(OSError("private host path")),
                         "OTHER_UNVERIFIED")
        self.assertEqual(gc._classify_apply_hold(gc.Hold("FILE_CHANGED")),
                         "FILE_UNVERIFIED")
        self.assertEqual(gc._classify_apply_hold(gc.Hold("DOCKER_OPERATION_FAILED")),
                         "DOCKER_UNVERIFIED")

    def test_owned_failure_categories_for_each_allowlisted_role(self):
        cases = (
            (subprocess.TimeoutExpired(["docker", "secret"], 12), "TIMEOUT"),
            (gc.Hold("DOCKER_OPERATION_FAILED"), "CLI"),
            (gc.Hold("CONTAINER_FIELDS_INVALID"), "COUNT"),
            (json.JSONDecodeError("private token", "private host output", 0), "JSON"),
            (gc.Hold("CONTAINER_FIELDS_UNPROVEN"), "SEMANTICS"),
            (OSError("private host path"), "OTHER"),
            (gc.Hold("private unknown Docker error"), "OTHER"),
        )
        self.assertEqual(set(gc.OWNED_ROLE), set(gc.OWNED))
        for name in gc.OWNED:
            for error, kind in cases:
                expected = f"OWNED_{gc.OWNED_ROLE[name]}_{kind}_UNVERIFIED"
                with self.subTest(name=name, kind=kind), \
                     patch.object(gc, "_container", side_effect=error), \
                     self.assertRaises(gc.Hold) as caught:
                    gc._owned_container(name)
                self.assertEqual(str(caught.exception), expected)
                self.assertEqual(gc._classify_apply_hold(caught.exception), expected)
                self.assertIn(expected, gc.APPLY_HOLDS)
                self.assertNotIn("private", str(caught.exception))
                self.assertNotIn("secret", str(caught.exception))

    def test_owned_container_actual_split_json_and_semantics_are_distinct(self):
        valid = ('"' + '1' * 64 + '"|"' + CRITICAL + '"|"exited"|0|false|0|"ABSENT"')
        for raw, expected in (
                ("too|few", "COUNT"),
                (valid.replace('"exited"', '{broken}'), "JSON"),
                (valid.replace('"exited"', '"unknown"'), "SEMANTICS")):
            with self.subTest(expected=expected), \
                 patch.object(gc, "_docker", return_value=raw), \
                 self.assertRaises(gc.Hold) as caught:
                gc._owned_container(gc.OWNED[0])
            self.assertEqual(str(caught.exception),
                             f"OWNED_OBSERVER_{expected}_UNVERIFIED")


class ApplyGates(unittest.TestCase):
    def setUp(self):
        self.safety = {"marker_hash": "a" * 64, "barrier_hash": "b" * 64,
                       "quiescence_hash": "c" * 64,
                       "book": {"state": "VERIFIED_CLOSED_FLAT", "value": {"orders": 0}},
                       "watch": {},
                       "containers": {gc.OWNED[2]: {"image": CRITICAL}},
                       "candidate": CANDIDATE}
        self.plan = {"schema": gc.SCHEMA, "candidate_sha": CANDIDATE,
                     "failed_candidate_sha": FAILED, "failed_run_id": "42260034260",
                     "previous_deploy_sha": PREVIOUS,
                     "safety": self.safety, "apply_hold": "NONE", "targets": [manifest_details()],
                     "oci_provenance_unverified_count": 0,
                     "discovered_candidate_shas": [CANDIDATE]}
        self.plan["plan_sha256"] = gc._sha(self.plan)

    def _perform(self, mode="apply", digest=None, fail_on_remove_n=None,
                 image_details_override=None):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock"
            lock.touch()
            deleted = {"image": False}
            calls = {"rm": 0}
            def fake_inventory():
                return {CRITICAL}, [CRITICAL] if deleted["image"] else [IMAGE, CRITICAL]
            def fake_docker(*args):
                if args[:2] == ("image", "rm"):
                    calls["rm"] += 1
                    if calls["rm"] == fail_on_remove_n:
                        raise gc.Hold("DOCKER_OPERATION_FAILED")
                if args[:2] == ("image", "rm") and args[2] == IMAGE:
                    deleted["image"] = True
                return ""
            real_fstat = gc.os.fstat
            def fake_fstat(fd):
                st = real_fstat(fd)
                return types.SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=1)
            with patch.object(gc, "LOCK", lock), \
                 patch.object(gc.os, "fstat", side_effect=fake_fstat), \
                 patch.object(gc, "make_plan", return_value=self.plan), \
                 patch.object(gc, "_admission", return_value=self.safety), \
                 patch.object(gc, "_docker_inventory", side_effect=fake_inventory), \
                 patch.object(gc, "_image_details", return_value=(
                     image_details_override if image_details_override is not None
                     else details())), \
                 patch.object(gc, "_docker", side_effect=fake_docker) as docker:
                result = gc.perform(mode=mode, candidate=CANDIDATE, failed=FAILED,
                                    run_id="42260034260", previous=PREVIOUS,
                                    expected_digest=self.plan["plan_sha256"] if digest is None else digest)
                return result, docker.call_args_list

    def test_plan_is_pure_read_only(self):
        result, calls = self._perform(mode="plan")
        self.assertEqual(result["state"], "PLAN_READY")
        self.assertEqual(result["target_manifest"][0]["id"], IMAGE)
        self.assertEqual(result["discovered_candidate_shas"], [CANDIDATE])
        self.assertEqual(result["mutations"], 0)
        self.assertFalse(calls)

    def test_apply_requires_exact_sha256(self):
        with self.assertRaisesRegex(gc.Hold, "PLAN_DIGEST_MISMATCH"):
            self._perform(digest="0" * 64)

    def test_apply_non_force_only_candidate_aliases(self):
        result, calls = self._perform()
        self.assertEqual(result["state"], "GREEN")
        self.assertEqual(result["removed"], 4)
        self.assertEqual(result["disk_delta_observed_bytes"],
                         result["disk_free_after_bytes"]-result["disk_free_before_bytes"])
        self.assertEqual(len(calls), 4)
        for item in calls:
            self.assertEqual(item.args[:2], ("image", "rm"))
            self.assertIn(item.args[2], TAGS + DIGESTS + [IMAGE])
            self.assertNotIn("-f", item.args)

    def test_apply_rechecks_oci_before_any_image_remove(self):
        for bad in (details(oci_commit=FAILED), details(oci_predeploy=None)):
            with self.subTest(label=bad.get("_oci_predeploy")):
                result, calls = self._perform(image_details_override=bad)
                self.assertEqual(result["state"], "NO_GO_PARTIAL")
                self.assertEqual(result["removed"], 0)
                self.assertFalse(result["mutation_count_exact"])
                self.assertEqual(calls, [])


    def test_partial_docker_failure_preserves_bounded_lower_bound(self):
        result, calls = self._perform(fail_on_remove_n=2)
        self.assertEqual(result["state"], "NO_GO_PARTIAL")
        self.assertEqual(result["reason"], "APPLY_INCOMPLETE")
        self.assertEqual(result["removed"], 1)
        self.assertFalse(result["mutation_count_exact"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["disk_delta_observed_bytes"],
                         result["disk_free_after_bytes"]-result["disk_free_before_bytes"])

    def test_apply_refuses_new_container_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "lock"
            lock.touch()
            real_fstat = gc.os.fstat
            def fake_fstat(fd):
                return types.SimpleNamespace(st_mode=0o100600, st_uid=0, st_nlink=1)
            with patch.object(gc, "LOCK", lock), patch.object(gc.os, "fstat", side_effect=fake_fstat), \
                 patch.object(gc, "make_plan", return_value=self.plan), \
                 patch.object(gc, "_admission", return_value=self.safety), \
                 patch.object(gc, "_docker_inventory", return_value=({CRITICAL, IMAGE}, [IMAGE, CRITICAL])), \
                 patch.object(gc, "_docker") as docker:
                result = gc.perform(mode="apply", candidate=CANDIDATE, failed=FAILED,
                                    run_id="42260034260", previous=PREVIOUS,
                                    expected_digest=self.plan["plan_sha256"])
                self.assertEqual(result["state"], "NO_GO_PARTIAL")
                self.assertEqual(result["removed"], 0)
                self.assertFalse(result["mutation_count_exact"])
                docker.assert_not_called()

    def test_unverified_book_yields_plan_not_apply(self):
        self.plan["safety"] = None
        self.plan["apply_hold"] = "BOOK_UNVERIFIED"
        self.plan["plan_sha256"] = gc._sha({k:v for k,v in self.plan.items()
                                             if k!="plan_sha256"})
        result, calls = self._perform(mode="plan")
        self.assertEqual(result["state"], "PLAN_READY")
        self.assertEqual(result["book_state"], "NO_VERIFICADO")
        self.assertFalse(result["book_closed_flat"])
        self.assertFalse(result["paper_zero_receipts"])
        self.assertEqual(result["apply_hold"], "BOOK_UNVERIFIED")
        self.assertFalse(calls)
        with self.assertRaisesRegex(gc.Hold, "APPLY_ADMISSION_NOT_PROVEN"):
            self._perform(mode="apply")


class SanitizerRegressions(unittest.TestCase):
    """Execute the actual workflow's public-receipt sanitizer without Actions."""

    def _sanitize(self, receipt):
        workflow = WORKFLOW.read_text()
        source = workflow.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        script = textwrap.dedent(source)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "rc6_a727_gc"
            folder.mkdir()
            (folder / "host.private.json").write_text(json.dumps(receipt))
            env = dict(os.environ, RUNNER_TEMP=directory, A727_MODE="plan",
                       A727_CANDIDATE_SHA=CANDIDATE, A727_FAILED_SHA=FAILED,
                       A727_FAILED_RUN="42260034260", A727_EXPECTED_PLAN_SHA256="",
                       A727_REMOTE_EXIT_CODE="0")
            completed = subprocess.run([sys.executable, "-B", "-c", script],
                                       env=env, capture_output=True, text=True, timeout=5)
            sanitized = json.loads((folder / "receipt.json").read_text())
            return completed.returncode, sanitized, completed.stdout + completed.stderr

    def _receipt(self):
        return {"schema": gc.SCHEMA, "mode": "plan", "state": "PLAN_READY",
                "candidate_sha": CANDIDATE, "failed_candidate_sha": FAILED,
                "failed_run_id": "42260034260", "plan_sha256": "a" * 64,
                "targets": 1, "target_manifest": [manifest_details()],
                "oci_provenance_unverified_count": 0,
                "discovered_candidate_shas": [CANDIDATE],
                "size_upper_bytes": 1000, "paper_zero_receipts": False,
                "book_closed_flat": False, "book_state": "NO_VERIFICADO",
                "apply_hold": "BOOK_UNVERIFIED", "other_apps_touched": False,
                "ppi_watch_touched": False, "mutations": 0, "removed": 0,
                "mutation_count_exact": True, "disk_free_before_bytes": 3_300_000_000,
                "disk_free_after_bytes": 3_300_000_000,
                "disk_delta_observed_bytes": 0, "inodes_free_before": 1000,
                "inodes_free_after": 1000}

    def test_sanitizer_accepts_readonly_inventory_with_book_apply_hold(self):
        code, data, output = self._sanitize(self._receipt())
        self.assertEqual(code, 0, output)
        self.assertEqual(data["apply_hold"], "BOOK_UNVERIFIED")
        self.assertEqual(data["target_manifest"][0]["id"], IMAGE)

    def test_sanitizer_accepts_all_new_bounded_stage_codes(self):
        for value in ("BARRIER_FILE_UNVERIFIED", "BARRIER_ABSENT_POSTBOOT_UNVERIFIED",
                      "OCI_LABEL_UNVERIFIED", "MARKER_FILE_UNVERIFIED",
                      "STABLE_DOCKER_UNVERIFIED", "OWNED_DOCKER_UNVERIFIED",
                      "QUIESCENCE_FILE_UNVERIFIED", "WATCH_READ_UNVERIFIED",
                      "FILE_UNVERIFIED", "DOCKER_UNVERIFIED"):
            receipt = self._receipt()
            receipt["apply_hold"] = value
            code, data, output = self._sanitize(receipt)
            self.assertEqual(code, 0, (value, output))
            self.assertEqual(data["apply_hold"], value)

    def test_sanitizer_accepts_owned_role_and_error_kind_only(self):
        for value in sorted(gc.OWNED_HOLDS):
            with self.subTest(value=value):
                receipt = self._receipt()
                receipt["apply_hold"] = value
                code, data, output = self._sanitize(receipt)
                self.assertEqual(code, 0, (value, output))
                self.assertEqual(data["apply_hold"], value)

    def test_sanitizer_preserves_only_bounded_oci_count_and_barrier_hold(self):
        for hold in ("OCI_LABEL_UNVERIFIED",
                     "BARRIER_ABSENT_POSTBOOT_UNVERIFIED"):
            receipt = self._receipt()
            receipt["apply_hold"] = hold
            receipt["oci_provenance_unverified_count"] = 1
            code, data, output = self._sanitize(receipt)
            self.assertEqual(code, 0, output)
            self.assertEqual(data["oci_provenance_unverified_count"], 1)
            self.assertIn("oci_unverified=1", output)
            self.assertNotIn("_oci_commit", output + json.dumps(data))

    def test_sanitizer_rejects_forged_oci_count(self):
        for count, hold in ((9, "OCI_LABEL_UNVERIFIED"),
                            (-1, "OCI_LABEL_UNVERIFIED"),
                            (True, "OCI_LABEL_UNVERIFIED"),
                            (1, "NONE")):
            with self.subTest(count=count, hold=hold):
                receipt = self._receipt()
                receipt["oci_provenance_unverified_count"] = count
                receipt["apply_hold"] = hold
                if hold == "NONE":
                    # Isolate the new OCI consistency guard: all unrelated
                    # book/PAPER evidence is otherwise valid.
                    receipt["book_state"] = "VERIFIED_CLOSED_FLAT"
                    receipt["book_closed_flat"] = True
                    receipt["paper_zero_receipts"] = True
                code, data, output = self._sanitize(receipt)
                self.assertEqual(code, 2)
                self.assertEqual(data["state"], "NO_VERIFICADO")


    def test_sanitizer_rejects_unowned_role_and_secret_injected_reason(self):
        for value in ("OWNED_BOCA_CLI_UNVERIFIED",
                      "OWNED_OBSERVER_CLI_UNVERIFIED|private secret",
                      "OWNED_OBSERVER_UNKNOWN_UNVERIFIED"):
            with self.subTest(value=value):
                receipt = self._receipt()
                receipt["apply_hold"] = value
                code, data, output = self._sanitize(receipt)
                self.assertEqual(code, 2)
                self.assertEqual(data["state"], "NO_VERIFICADO")
                self.assertNotIn(value, output + json.dumps(data))

    def test_sanitizer_rejects_foreign_alias_without_leaking_it(self):
        receipt = self._receipt()
        receipt["target_manifest"][0]["tags"] = ["boca-private:my-secret"]
        code, data, output = self._sanitize(receipt)
        self.assertEqual(code, 2)
        self.assertEqual(data["state"], "NO_VERIFICADO")
        self.assertNotIn("boca-private", output + json.dumps(data))

    def test_sanitizer_rejects_unexpected_apply_hold_without_leaking(self):
        receipt = self._receipt()
        receipt["apply_hold"] = "SECRET_ABC"
        code, data, output = self._sanitize(receipt)
        self.assertEqual(code, 2)
        self.assertEqual(data["state"], "NO_VERIFICADO")
        self.assertNotIn("SECRET_ABC", output + json.dumps(data))


if __name__ == "__main__":
    unittest.main()
