"""A727 Docker-only GC: corrupt provenance, cross-app and stale-plan regressions."""

import importlib.util
import sqlite3
import tempfile
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


def details(tags=None, digests=None, iid=IMAGE):
    return {"id": iid, "tags": list(TAGS if tags is None else tags),
            "digests": list(DIGESTS if digests is None else digests),
            "size_upper": 1000}


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

    def test_referenced_candidate_never_selected(self):
        with patch.object(gc, "_admission", return_value=self.safety), \
             patch.object(gc, "_docker_inventory", return_value=({CRITICAL, IMAGE}, [IMAGE, CRITICAL])):
            self.assertFalse(gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)["targets"])

    def test_plan_fails_if_book_admission_unavailable(self):
        with patch.object(gc, "_admission", side_effect=gc.Hold("BOOK_NOT_VERIFIED")):
            with self.assertRaisesRegex(gc.Hold, "BOOK_NOT_VERIFIED"):
                gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)

    def test_refuses_ambiguous_inventory(self):
        with patch.object(gc, "_admission", return_value=self.safety), \
             patch.object(gc, "_docker_inventory", side_effect=gc.Hold("INVENTORY_UNKNOWN")):
            with self.assertRaises(gc.Hold):
                gc.make_plan(CANDIDATE, FAILED, "42260034260", PREVIOUS)


class BookAdmission(unittest.TestCase):
    def _admit(self, book_error, allow):
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
            if path == gc.BARRIER: return barrier, "a" * 64
            if path.name == "CURRENT_STATE_V2.json": return marker, "b" * 64
            if path.name.startswith("quiescence-"): return q, "c" * 64
            raise AssertionError("unexpected path")
        with patch.object(gc, "_file_json", side_effect=json_for), \
             patch.object(gc, "_docker", return_value=CRITICAL), \
             patch.object(gc, "_container", side_effect=lambda name: crit if name == gc.OWNED[2] else cont), \
             patch.object(gc, "_book", side_effect=book_error), \
             patch.object(gc, "_watch", return_value={}):
            return gc._admission(CANDIDATE, FAILED, "42260034260", PREVIOUS,
                                 allow_book_unverified=allow)

    def test_readonly_sqlite_unavailable_requires_explicit_fallback(self):
        with self.assertRaisesRegex(gc.Hold, "BOOK_READONLY_UNAVAILABLE"):
            self._admit(sqlite3.OperationalError("unavailable"), False)
        result = self._admit(sqlite3.OperationalError("unavailable"), True)
        self.assertEqual(result["book"]["state"], "NO_VERIFICADO_SQLITE")

    def test_readable_unsafe_book_never_falls_back(self):
        with self.assertRaisesRegex(gc.Hold, "BOOK_NOT_CLOSED_PAPER_ZERO"):
            self._admit(gc.Hold("BOOK_NOT_CLOSED_PAPER_ZERO"), True)


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
                     "safety": self.safety, "targets": [details()],
                     "discovered_candidate_shas": [CANDIDATE]}
        self.plan["plan_sha256"] = gc._sha(self.plan)

    def _perform(self, mode="apply", digest=None, fail_on_remove_n=None):
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
                 patch.object(gc, "_image_details", return_value=details()), \
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

    def test_explicit_unavailable_book_is_disclosed_not_claimed_verified(self):
        self.plan["safety"]["book"] = {"state": "NO_VERIFICADO_SQLITE"}
        self.plan["plan_sha256"] = gc._sha({k:v for k,v in self.plan.items()
                                             if k!="plan_sha256"})
        result, calls = self._perform(mode="plan")
        self.assertEqual(result["state"], "PLAN_READY")
        self.assertEqual(result["book_state"], "NO_VERIFICADO_SQLITE")
        self.assertFalse(result["book_closed_flat"])
        self.assertFalse(calls)


if __name__ == "__main__":
    unittest.main()
