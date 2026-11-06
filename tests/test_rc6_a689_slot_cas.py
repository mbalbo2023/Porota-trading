"""OFFHOST regressions for A689's independent durable day/slot claim."""

import base64
from contextlib import redirect_stdout
from hashlib import sha256
import inspect
import io
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts import rc6_a630_host_ppi_current_book as engine
from scripts import rc6_a689_slot_cas as durable
from scripts.rc6_a689_slot_cas import (
    CLAIM_FILE, CLAIM_SUBDIR, REPOSITORY, CONTROL_REF,
    SlotClaimHold, claim_once,
)


DATE = "2026-11-05"
SHA = "0d19172068e343360ccb593de955934e37400a2a"
CLOCK = datetime(2026, 11, 5, 13, 45, tzinfo=timezone.utc)


def args(root, **overrides):
    payload = {
        "art_date": DATE, "slot": "1045", "run_id": "42012345678",
        "run_attempt": "1", "control_sha": SHA,
        "repository": REPOSITORY, "control_ref": CONTROL_REF,
        "event_name": "schedule", "clock": CLOCK,
        "data_root": Path(root), "require_mount": False,
    }
    payload.update(overrides)
    return payload


def race_worker(root, run_id, gate, results):
    gate.wait(timeout=4)
    try:
        receipt = claim_once(**args(root, run_id=str(run_id)))
        results.put(receipt["state"])
    except SlotClaimHold as exc:
        results.put(str(exc))


def crash_worker(root):
    claim_once(**args(root))
    os._exit(42)


class DurableCAS(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "data"
        self.root.mkdir(mode=0o700)

    def tearDown(self):
        self.temp.cleanup()

    @property
    def claim_path(self):
        return self.root / CLAIM_SUBDIR / CLAIM_FILE

    def expect_hold(self, why, **overrides):
        with self.assertRaises(SlotClaimHold) as exc:
            claim_once(**args(self.root, **overrides))
        self.assertEqual(str(exc.exception), why)

    def test_first_and_repeated_attempt_irreversible(self):
        result = claim_once(**args(self.root))
        self.assertEqual(result["state"], "CLAIMED_DURABLE")
        self.assertTrue(self.claim_path.is_file())
        self.expect_hold("ALREADY_CLAIMED")
        self.expect_hold("ALREADY_CLAIMED", run_id="42012345679")
        with sqlite3.connect(self.claim_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slot_claims").fetchone()[0], 1)

    def test_second_slot_may_claim_same_day(self):
        claim_once(**args(self.root))
        clock = datetime(2026, 11, 5, 14, 20, tzinfo=timezone.utc)
        result = claim_once(**args(self.root, slot="1120", clock=clock))
        self.assertEqual(result["state"], "CLAIMED_DURABLE")
        self.expect_hold("ALREADY_CLAIMED", slot="1120", clock=clock)
        with sqlite3.connect(self.claim_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slot_claims").fetchone()[0], 2)

    def test_multi_process_only_one_claims(self):
        ctx = multiprocessing.get_context("fork")
        gate = ctx.Event()
        outcomes = ctx.Queue()
        workers = [ctx.Process(target=race_worker,
                               args=(str(self.root), 42012345678 + i, gate, outcomes))
                   for i in range(6)]
        for worker in workers:
            worker.start()
        gate.set()
        for worker in workers:
            worker.join(6)
            self.assertEqual(worker.exitcode, 0)
        received = [outcomes.get(timeout=2) for _ in workers]
        self.assertEqual(received.count("CLAIMED_DURABLE"), 1, received)
        self.assertEqual(received.count("ALREADY_CLAIMED") +
                         received.count("CLAIM_STORAGE_OR_LOCK_HOLD"), 5, received)

    def test_crash_after_commit_holds_restart(self):
        ctx = multiprocessing.get_context("fork")
        crashed = ctx.Process(target=crash_worker, args=(str(self.root),))
        crashed.start()
        crashed.join(5)
        self.assertEqual(crashed.exitcode, 42)
        self.expect_hold("ALREADY_CLAIMED", run_id="42012345690")

    def test_storage_mount_must_be_explicitly_proven(self):
        self.expect_hold("NONCANONICAL_DATA_ROOT", require_mount=True)

    def test_refuse_retry_even_for_new_slot(self):
        self.expect_hold("RETRY_ATTEMPT_HOLD", run_attempt="2")
        self.assertFalse(self.claim_path.exists())

    def test_refuse_ambiguous_slot_day_or_clock(self):
        self.expect_hold("SLOT_WINDOW_HOLD", clock=datetime(2026, 11, 5, 13, 44,
                                                           tzinfo=timezone.utc))
        self.expect_hold("SLOT_WINDOW_HOLD", clock=datetime(2026, 11, 5, 14, 46,
                                                           tzinfo=timezone.utc))
        self.expect_hold("ART_DATE_CLOCK_MISMATCH", art_date="2026-11-04")
        self.expect_hold("ART_DATE_INVALID", art_date="2026-11-45")
        self.expect_hold("SLOT_INVALID", slot="9999")
        self.expect_hold("CLOCK_INVALID", clock=datetime(2026, 11, 5, 13, 45))

    def test_refuse_untrusted_execution_metadata(self):
        for name, value, why in (
            ("run_id", "-1", "RUN_ID_INVALID"),
            ("control_sha", "0" * 40 + "x", "CONTROL_SHA_INVALID"),
            ("repository", "someoneelse/fork", "REPOSITORY_NOT_ATTESTED"),
            ("control_ref", "refs/heads/feature", "REF_NOT_ATTESTED"),
            ("event_name", "pull_request", "EVENT_NOT_ALLOWED"),
        ):
            with self.subTest(field=name):
                self.expect_hold(why, **{name: value})
        self.assertFalse(self.claim_path.exists())

    def test_refuse_symlink_root_or_claim_directory(self):
        alias = self.root.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.expect_hold("MOUNT_SYMLINK", data_root=alias)
        destination = self.root.parent / "outside"
        destination.mkdir(mode=0o700)
        (self.root / CLAIM_SUBDIR).symlink_to(destination, target_is_directory=True)
        self.expect_hold("SYMLINK_DIRECTORY")
        self.assertFalse((destination / CLAIM_FILE).exists())

    def test_refuse_symlink_db_and_file_hardlink(self):
        folder = self.root / CLAIM_SUBDIR
        folder.mkdir(mode=0o700)
        outside = self.root.parent / "outside"
        outside.touch(mode=0o600)
        self.claim_path.symlink_to(outside)
        self.expect_hold("CLAIM_STORAGE_OR_LOCK_HOLD")
        self.claim_path.unlink()
        os.link(outside, self.claim_path)
        self.expect_hold("CLAIM_HARDLINK_HOLD")

    def test_refuse_nonprivate_existing_directory_or_file(self):
        folder = self.root / CLAIM_SUBDIR
        folder.mkdir(mode=0o777)
        folder.chmod(0o755)
        self.expect_hold("DIRECTORY_NOT_PRIVATE")
        folder.chmod(0o700)
        self.claim_path.touch(mode=0o644)
        self.claim_path.chmod(0o644)
        self.expect_hold("CLAIM_NOT_PRIVATE")

    def test_refuse_world_writable_data_root(self):
        self.root.chmod(0o777)
        self.expect_hold("DATA_ROOT_GROUP_OR_WORLD_WRITABLE")

    def test_refuse_group_writable_data_root(self):
        self.root.chmod(0o775)
        self.expect_hold("DATA_ROOT_GROUP_OR_WORLD_WRITABLE")

    def test_refuse_corrupt_db_without_reissuing_claim(self):
        folder = self.root / CLAIM_SUBDIR
        folder.mkdir(mode=0o700)
        self.claim_path.write_bytes(b"not a sqlite database")
        self.claim_path.chmod(0o600)
        self.expect_hold("CLAIM_STORAGE_OR_LOCK_HOLD")

    def test_new_directory_parent_is_fsynced_before_cas_returns(self):
        parent_ino = os.stat(self.root).st_ino
        observed = []
        real_fsync = os.fsync

        def logged_fsync(fd):
            observed.append("parent" if os.fstat(fd).st_ino == parent_ino else "other")
            return real_fsync(fd)

        with patch.object(durable.os, "fsync", side_effect=logged_fsync):
            claim_once(**args(self.root))
        self.assertIn("parent", observed, observed)
        self.assertEqual(observed[0], "parent", observed)

    def test_parent_fsync_failure_denies_before_any_slot_claim(self):
        parent_ino = os.stat(self.root).st_ino
        real_fsync = os.fsync

        def failed_parent(fd):
            if os.fstat(fd).st_ino == parent_ino:
                raise OSError("disk error while persisting newly created directory")
            return real_fsync(fd)

        with patch.object(durable.os, "fsync", side_effect=failed_parent):
            self.expect_hold("CLAIM_STORAGE_OR_LOCK_HOLD")
        self.assertFalse(self.claim_path.exists())

    def test_refuse_preexisting_db_without_unique_key(self):
        folder = self.root / CLAIM_SUBDIR
        folder.mkdir(mode=0o700)
        with sqlite3.connect(self.claim_path) as db:
            db.execute("CREATE TABLE slot_claims (art_date TEXT, slot TEXT, "
                       "run_id TEXT, control_sha TEXT, claimed_at_utc TEXT)")
        self.claim_path.chmod(0o600)
        self.expect_hold("CLAIM_SCHEMA_INVALID")
        with sqlite3.connect(self.claim_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slot_claims").fetchone()[0], 0)

    def test_refuse_trigger_that_silently_discards_insert(self):
        folder = self.root / CLAIM_SUBDIR
        folder.mkdir(mode=0o700)
        with sqlite3.connect(self.claim_path) as db:
            db.execute(durable.SCHEMA_DDL)
            db.execute("CREATE TRIGGER skip_claim BEFORE INSERT ON slot_claims "
                       "BEGIN SELECT RAISE(IGNORE); END")
        self.claim_path.chmod(0o600)
        self.expect_hold("CLAIM_SCHEMA_INVALID")
        with sqlite3.connect(self.claim_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM slot_claims").fetchone()[0], 0)

    def test_refuse_unexpected_schema_object_even_if_claim_table_valid(self):
        folder = self.root / CLAIM_SUBDIR
        folder.mkdir(mode=0o700)
        with sqlite3.connect(self.claim_path) as db:
            db.execute(durable.SCHEMA_DDL)
            db.execute("CREATE VIEW extra AS SELECT 1")
        self.claim_path.chmod(0o600)
        self.expect_hold("CLAIM_SCHEMA_INVALID")


class ProviderIntegrationNoGet(unittest.TestCase):
    def test_helper_bytes_from_checkout_match_literal_runtime_pin(self):
        helper = Path(__file__).resolve().parents[1] / "scripts/rc6_a689_slot_cas.py"
        self.assertEqual(sha256(helper.read_bytes()).hexdigest(),
                         engine.A689_HELPER_SHA256)

    def test_cas_is_inline_before_reader_import_and_capture(self):
        source = inspect.getsource(engine.container_main)
        self.assertLess(source.index("_claim_a689_before_provider(target)"),
                        source.index("from bd_ppi_readonly_guard import ProductionMarketReader"))
        self.assertLess(source.index("_claim_a689_before_provider(target)"),
                        source.index("receipt = capture_two(target, credentials, budget"))

    def test_missing_cas_proof_holds_without_provider_sdk_or_capture(self):
        target = engine.target_for_slot("1045")
        encoded = base64.b64encode(json.dumps(list(target)).encode()).decode()
        module = SimpleNamespace(MODE="PRODUCTION_PAPER", REAL_ORDER_CAPABILITY="BLOCKED")
        with patch.dict(sys.modules, {"_version": module}), patch.dict(os.environ, {
            "RC6_A630_EXPECTED_PRODUCT_SHA": engine.PRODUCT_SHA,
            "RC6_A630_TARGET_B64": encoded,
            "RC6_A689_SOURCE_B64": "",  # fail before import, GET or budget lease
        }, clear=True), patch.object(engine, "existing_container_keypair", return_value=("a", "b")), \
             patch.object(engine, "_existing_shared_budget", return_value=(object(), None)), \
             patch.object(engine, "capture_two", side_effect=AssertionError("PROVIDER_GET_CALLED")):
            output = io.StringIO()
            with redirect_stdout(output):
                code = engine.container_main()
        self.assertEqual(code, 2)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt["state"], "NO_VERIFICADO")
        self.assertEqual(receipt["reason"], "A689_DURABLE_SLOT_CAS_HOLD")
        self.assertEqual(receipt["current_gets"], 0)
        self.assertEqual(receipt["book_gets"], 0)
        self.assertEqual(receipt["orders_sent"], 0)

    def test_substituted_helper_rejected_before_provider(self):
        helper = Path(__file__).resolve().parents[1] / "scripts/rc6_a689_slot_cas.py"
        encoded = base64.b64encode(helper.read_bytes() + b"# unreviewed mutation\n").decode()
        with patch.dict(os.environ, {"RC6_A689_SLOT": "1045",
                                  "RC6_A689_SOURCE_B64": encoded}, clear=True):
            with self.assertRaisesRegex(ValueError, "A689_DURABLE_SLOT_CAS_HOLD"):
                engine._claim_a689_before_provider(engine.target_for_slot("1045"))

    def test_workflow_authenticates_specific_porota_mount_and_exact_source(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/rc6-a669-ppi-current-book-20261105.yml").read_text()
        self.assertIn("bind|/opt/porota-trading/data|true", workflow)
        self.assertIn("test \\\"\\$runtime_user\\\" = botuser", workflow)
        self.assertIn("from scripts.rc6_a630_host_ppi_current_book import A689_HELPER_SHA256",
                      workflow)
        self.assertIn("-e RC6_A689_SOURCE_B64=$RC6_A689_SOURCE_B64", workflow)
        self.assertIn("-e RC6_A689_RUN_ID=$GITHUB_RUN_ID", workflow)
        self.assertIn("-e RC6_A689_RUN_ATTEMPT=$GITHUB_RUN_ATTEMPT", workflow)
        self.assertIn("-e RC6_A689_CONTROL_REF=$GITHUB_REF", workflow)
        self.assertIn("-e RC6_A689_ART_DATE=$RC6_A670_ART_DATE", workflow)



if __name__ == "__main__":
    unittest.main()
