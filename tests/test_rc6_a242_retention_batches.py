"""A242 negative-first offline retention batch / cursor evidence tests."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from rc6_retention_batch_dryrun import (
    SCHEMA, MAX_BATCHES_PER_REVIEW, MAX_TOTAL_VERSIONS, review_batches,
)
from scripts.rc6_retention_batch_dryrun import main as command


AS_OF = "2026-10-31T20:00:00Z"
SNAPSHOT_AT = "2026-10-31T19:00:00Z"
A = "a" * 64
B = "b" * 64
C = "c" * 64


def version(identity, *, economic_date="2025-09-30", raw=C,
            currency="ARS", prev=0):
    return {"id": identity, "symbol": "PRIVATE_TICKER_DO_NOT_PRINT",
            "instrument_type": "ACCIONES", "market": "BYMA",
            "currency": currency, "settlement": "A-24HS",
            "date": economic_date, "price_basis": "PER_SHARE",
            "adjustment_basis": "UNADJUSTED", "payload_hash": B,
            "previous_version_id": prev, "source": "PPI_API",
            "event_at": "2025-09-30T20:00:00Z",
            "provider_at": "2025-09-30T20:01:00Z",
            "observed_at": "2025-09-30T20:02:00Z",
            "version_known_at": "2025-09-30T20:02:00Z",
            "raw_sha256": raw}


def batch(rows, *, refs=None):
    return {"snapshot_sha256": A, "snapshot_known_at": SNAPSHOT_AT,
            "inventory_complete": True,
            "pin_scopes_verified": ["history_canonical"],
            "versions": rows, "references": refs or [], "exports": []}


def make_case(groups):
    manifests = {}
    descriptors = []
    for i, manifest in enumerate(groups):
        name = f"batch-{i:06d}.json"
        payload = json.dumps(manifest, sort_keys=True).encode("utf-8")
        manifests[name] = (hashlib.sha256(payload).hexdigest(), manifest)
        rows = manifest["versions"]
        descriptors.append({"name": name,
                            "sha256": manifests[name][0],
                            "versions_count": len(rows),
                            "first_version_id": rows[0]["id"],
                            "last_version_id": rows[-1]["id"]})
    index = {"schema": SCHEMA, "snapshot_sha256": A,
             "snapshot_known_at": SNAPSHOT_AT,
             "total_versions": sum(len(x["versions"]) for x in groups),
             "batches": descriptors}

    def reader(name, digest):
        expected, content = manifests[name]
        if digest != expected:
            raise ValueError("SYNTHETIC_READER_SHA_MISMATCH")
        return deepcopy(content)

    return index, reader


class RetentionBatchPlanTest(unittest.TestCase):
    def test_more_than_10000_versions_complete_in_bounded_replay(self):
        index, reader = make_case([batch([version(x) for x in range(1, 10001)]),
                                   batch([version(x) for x in range(10001, 10009)])])
        one = review_batches(index, as_of=AS_OF, read_batch=reader, batch_limit=1)
        self.assertEqual(one["versions_checked"], 10000)
        self.assertEqual(one["status"], "OFFHOST_REVIEW_CONTINUE")
        self.assertEqual(one["cutoff_inclusive_art"], "2025-11-01")
        self.assertEqual(one["states"], {"HOLD_UNVERIFIED_OR_PINNED": 10000})
        second = review_batches(index, as_of=AS_OF, read_batch=reader,
                                checkpoint=one["next_checkpoint_template"],
                                batch_limit=1)
        self.assertEqual(second["versions_checked"], 10008)
        self.assertEqual(second["new_batches_checked"], 1)
        self.assertEqual(second["status"], "OFFHOST_REVIEW_COMPLETE_HOLD")
        self.assertFalse(second["host_purge_allowed"])
        self.assertFalse(second["host_migration_allowed"])
        self.assertFalse(second["inventory_complete_proven"])
        self.assertEqual(second["cross_store_pin_and_fk_verification"], "NO_VERIFICADO")
        self.assertIsNone(second["physically_reclaimable_bytes"])
        self.assertEqual(second["real_orders_sent"], 0)
        final_repeat = review_batches(index, as_of=AS_OF, read_batch=reader,
            checkpoint=second["next_checkpoint_template"], batch_limit=1)
        self.assertEqual(final_repeat["new_batches_checked"], 0)
        self.assertEqual(final_repeat["next_checkpoint_template"],
                         second["next_checkpoint_template"])

    def test_checkpoint_is_replayed_not_trusted_after_crash(self):
        index, reader = make_case([batch([version(1)]), batch([version(2)])])
        first = review_batches(index, as_of=AS_OF, read_batch=reader,
                               batch_limit=1)
        ck = first["next_checkpoint_template"]
        self.assertEqual(review_batches(index, as_of=AS_OF, read_batch=reader,
                                        batch_limit=1), first)
        for key, value in (("states", {}), ("prefix_digest", A),
                           ("last_version_id", 2), ("versions_classified", 99),
                           ("pinned_versions", 9),
                           ("unresolved_reference_ids", 1)):
            forged = {**ck, key: value}
            with self.subTest(key=key), self.assertRaisesRegex(
                    ValueError, "RETENTION_BATCH_CHECKPOINT_REPLAY_CONFLICT"):
                review_batches(index, as_of=AS_OF, read_batch=reader,
                               checkpoint=forged, batch_limit=1)

    def test_interruption_on_next_partition_preserves_replay_cursor(self):
        index, reader = make_case([batch([version(1)]), batch([version(2)])])
        first = review_batches(index, as_of=AS_OF, read_batch=reader,
                               batch_limit=1)
        saved = deepcopy(first["next_checkpoint_template"])
        def interrupted(name, digest):
            if name == "batch-000001.json":
                raise ValueError("RETENTION_BATCH_SIMULATED_INTERRUPTION")
            return reader(name, digest)
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_SIMULATED_INTERRUPTION"):
            review_batches(index, as_of=AS_OF, read_batch=interrupted,
                           checkpoint=saved, batch_limit=1)
        self.assertEqual(saved, first["next_checkpoint_template"])
        resumed = review_batches(index, as_of=AS_OF, read_batch=reader,
                                 checkpoint=saved, batch_limit=1)
        self.assertEqual(resumed["versions_checked"], 2)
        self.assertEqual(resumed["states"], {"HOLD_UNVERIFIED_OR_PINNED": 2})

    def test_synthetic_sqlite_fk_and_paper_pin_are_never_deleted(self):
        # In-memory fixture only, not a claim about real cross-store FKs.
        with sqlite3.connect(":memory:") as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE versions (id INTEGER PRIMARY KEY)")
            db.execute("CREATE TABLE paper_pin (version_id INTEGER NOT NULL REFERENCES versions(id))")
            db.executemany("INSERT INTO versions VALUES (?)", [(1,), (2,)])
            db.execute("INSERT INTO paper_pin VALUES (1)")
            original = db.execute("SELECT count(*) FROM versions").fetchone()[0]
            refs = [{"scope": "active_paper_positions", "version_id": 1,
                     "reference": "in-memory-paper-pin"}]
            index, reader = make_case([batch([version(1), version(2)], refs=refs)])
            result = review_batches(index, as_of=AS_OF, read_batch=reader)
            self.assertEqual(result["states"], {"HOLD_UNVERIFIED_OR_PINNED": 2})
            self.assertEqual(result["pinned_versions"], 1)
            self.assertFalse(result["host_purge_allowed"])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT count(*) FROM versions").fetchone()[0], original)

    def test_changed_index_or_capture_clock_fails_resume(self):
        index, reader = make_case([batch([version(1)]), batch([version(2)])])
        ck = review_batches(index, as_of=AS_OF, read_batch=reader,
                            batch_limit=1)["next_checkpoint_template"]
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_CHECKPOINT_CONFLICT"):
            review_batches(index, as_of="2026-11-01T20:00:00Z",
                           read_batch=reader, checkpoint=ck)
        changed = deepcopy(index)
        changed["snapshot_known_at"] = "2026-10-31T18:59:00Z"
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_CHECKPOINT_CONFLICT"):
            review_batches(changed, as_of=AS_OF, read_batch=reader, checkpoint=ck)

    def test_cross_batch_raw_and_outside_pin_never_grant_delete(self):
        ref = {"scope": "active_paper_positions", "version_id": 2,
               "reference": "PAPER_PIN_SANITIZED"}
        index, reader = make_case([batch([version(1)], refs=[ref]),
                                   batch([version(2)])])
        report = review_batches(index, as_of=AS_OF, read_batch=reader)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 2})
        self.assertEqual(report["unresolved_reference_ids"], 1)
        self.assertEqual(report["cross_batch_raw_sharing_verification"], "NO_VERIFICADO")
        self.assertEqual(report["reasons"]["RETENTION_INVENTORY_OR_PIN_COVERAGE_UNVERIFIED"], 2)

    def test_old_missing_pit_currency_basis_and_recent_date_safe(self):
        missing = version(1, currency="")
        missing.pop("version_known_at")
        missing.pop("adjustment_basis")
        recent = version(2, economic_date="2025-11-01")
        index, reader = make_case([batch([missing, recent])])
        report = review_batches(index, as_of=AS_OF, read_batch=reader)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 1,
                                            "RETAIN_IN_WINDOW": 1})
        for reason in ("SOURCE_IDENTITY_EXACT_FIVE_KEY_REQUIRED",
                       "HISTORY_VERSION_PIT_UNVERIFIED",
                       "HISTORY_PRICE_BASIS_UNVERIFIED"):
            self.assertIn(reason, report["reasons"])

    def test_overlap_duplicate_unsorted_and_range_tamper_fail_closed(self):
        index, reader = make_case([batch([version(1)]), batch([version(2)])])
        overlap = deepcopy(index)
        overlap["batches"][1]["first_version_id"] = 1
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_RANGE_OVERLAP_OR_BUDGET"):
            review_batches(overlap, as_of=AS_OF, read_batch=reader)
        index, reader = make_case([batch([version(1), version(3), version(2), version(4)])])
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_VERSION_ORDER_OR_RANGE_INVALID"):
            review_batches(index, as_of=AS_OF, read_batch=reader)
        index, reader = make_case([batch([version(1), version(1)])])
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_IMPOSSIBLE_CARDINALITY"):
            review_batches(index, as_of=AS_OF, read_batch=reader)

    def test_index_caps_counts_names_hashes_and_future_clock(self):
        index, reader = make_case([batch([version(1)])])
        cases = [("total", {**index, "total_versions": MAX_TOTAL_VERSIONS + 1},
                  "RETENTION_BATCH_INDEX_BUDGET_INVALID"),
                 ("count", {**index, "total_versions": 2},
                  "RETENTION_BATCH_TOTAL_COUNT_MISMATCH"),
                 ("filename", {**index, "batches": [{**index["batches"][0],
                                "name": "../secret.json"}]},
                  "RETENTION_BATCH_FILE_NAME_OR_ORDER_INVALID"),
                 ("digest", {**index, "batches": [{**index["batches"][0],
                              "sha256": "not-a-sha"}]},
                  "RETENTION_BATCH_DIGEST_INVALID"),
                 ("future", {**index,
                             "snapshot_known_at": "2026-11-02T00:00:00Z"},
                  "RETENTION_BATCH_CAPTURE_IN_FUTURE")]
        for label, candidate, reason in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, reason):
                review_batches(candidate, as_of=AS_OF, read_batch=reader)
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_LIMIT_EXCEEDED"):
            review_batches(index, as_of=AS_OF, read_batch=reader,
                           batch_limit=MAX_BATCHES_PER_REVIEW + 1)

    def test_snapshot_mismatch_is_fatal_even_when_local_claim_is_complete(self):
        index, reader = make_case([batch([version(1)])])
        def swapped(name, digest):
            altered = reader(name, digest)
            altered["snapshot_sha256"] = B
            return altered
        with self.assertRaisesRegex(ValueError, "RETENTION_BATCH_CAPTURE_MISMATCH"):
            review_batches(index, as_of=AS_OF, read_batch=swapped)

    def test_cli_end_to_end_checked_regular_files_no_writes(self):
        with tempfile.TemporaryDirectory(prefix="rc6-retention-a242-") as folder:
            directory = Path(folder)
            index, reader = make_case([batch([version(1)])])
            one = directory / "batch-000000.json"
            one.write_text(json.dumps(reader(one.name,
                index["batches"][0]["sha256"]), sort_keys=True), encoding="utf-8")
            source = directory / "index.json"
            source.write_text(json.dumps(index, sort_keys=True), encoding="utf-8")
            sha = hashlib.sha256(source.read_bytes()).hexdigest()
            original = (one.read_bytes(), source.read_bytes())
            args = ["--index", str(source), "--expected-index-sha256", sha,
                    "--batch-directory", str(directory), "--as-of", AS_OF]
            with redirect_stdout(output := StringIO()):
                rc = command(args)
            self.assertEqual(rc, 0)
            receipt = json.loads(output.getvalue())
            self.assertEqual(receipt["status"], "OFFHOST_REVIEW_COMPLETE_HOLD")
            self.assertNotIn("PRIVATE_TICKER_DO_NOT_PRINT", output.getvalue())
            self.assertEqual(original, (one.read_bytes(), source.read_bytes()))
            alias = directory / "another.json"
            alias.symlink_to(one)
            one.unlink()
            one.symlink_to(alias)
            with redirect_stdout(output := StringIO()):
                self.assertEqual(command(args), 2)
            self.assertFalse(json.loads(output.getvalue())["host_purge_allowed"])


if __name__ == "__main__":
    unittest.main()
