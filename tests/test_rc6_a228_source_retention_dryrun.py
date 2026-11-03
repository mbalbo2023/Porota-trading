"""A228 source/retention planner: synthetic lineage, no provider or mutation."""

from copy import deepcopy
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from scripts import rc6_source_retention_dryrun as command
from rc6_source_retention_dryrun import (
    MAX_VERSIONS, REQUIRED_PIN_SCOPES, GAP_SCHEMA, SCHEMA,
    incremental_gap_dryrun, retention_dryrun,
)


NOW = datetime(2026, 11, 2, 16, 0, tzinfo=timezone.utc)
S = "a" * 64
T = "b" * 64
U = "c" * 64
V = "d" * 64


def version(version_id, *, economic_date="2025-08-01", family="ACCIONES",
            currency="ARS", source="PPI_API", raw_sha=U,
            event_at="2025-08-01T20:00:00Z",
            provider_at="2025-08-01T20:01:00Z",
            observed_at="2025-08-01T20:02:00Z",
            known_at="2025-08-01T20:02:00Z"):
    return {"id": version_id, "symbol": "AAA", "instrument_type": family,
            "market": "BYMA", "currency": currency, "settlement": "A-24HS",
            "date": economic_date, "price_basis": "PER_SHARE",
            "adjustment_basis": "UNADJUSTED", "payload_hash": T,
            "previous_version_id": 0, "source": source,
            "event_at": event_at, "provider_at": provider_at,
            "observed_at": observed_at, "version_known_at": known_at,
            "raw_sha256": raw_sha}


def manifest(versions=None, *, complete=True, scopes=None, refs=None, exports=None):
    return {"snapshot_sha256": S, "snapshot_known_at": "2026-11-02T15:00:00Z",
            "inventory_complete": complete,
            "pin_scopes_verified": sorted(REQUIRED_PIN_SCOPES) if scopes is None else scopes,
            "versions": [] if versions is None else versions,
            "references": [] if refs is None else refs,
            "exports": [] if exports is None else exports}


def gap(**overrides):
    base = {"symbol": "AAA", "instrument_type": "ON",
            "market": "BYMA", "currency": "USD_MEP", "settlement": "A-24HS",
            "source": "PPI_API", "evidence_sha256": S,
            "evidence_known_at": "2026-11-02T14:00:00Z",
            "gap_start": "2026-10-24", "gap_end": "2026-11-02"}
    return {**base, **overrides}


def synthetic_committed_receipt(proposal):
    """Synthetic fixture, NEVER evidence of an actual provider/sink commit."""
    return {**proposal["next_checkpoint_template"], "state": "COMMITTED",
            "sink_receipt_sha256": V, "committed_at": "2026-11-02T15:30:00Z"}


class SourceRetentionDryRunTest(unittest.TestCase):
    def test_exact_365_day_art_cutoff_does_not_depend_on_utc_midnight(self):
        at = datetime(2026, 11, 2, 2, 30, tzinfo=timezone.utc)
        case = manifest()
        case["snapshot_known_at"] = "2026-11-01T23:00:00Z"
        report = retention_dryrun(case, as_of=at)
        self.assertEqual(report["today_art"], "2026-11-01")
        self.assertEqual(report["cutoff_inclusive_art"], "2025-11-02")
        self.assertEqual(report["schema"], SCHEMA)
        self.assertFalse(report["host_purge_allowed"])

    def test_source_retention_plan_is_stable_review_only_and_no_writes(self):
        old = version(1)
        original = manifest([old])
        before = deepcopy(original)
        report = retention_dryrun(original, as_of=NOW)
        again = retention_dryrun(original, as_of=NOW,
                                 checkpoint={"plan_id": report["plan_id"],
                                             "manifest_digest": report["manifest_digest"]})
        self.assertEqual(original, before)
        self.assertEqual(report["states"], {"UNPINNED_REVIEW_CANDIDATE_ONLY": 1})
        self.assertEqual(report["plan_id"], again["plan_id"])
        self.assertTrue(again["same_plan_checkpoint_seen"])
        self.assertFalse(report["source_mutated"])
        self.assertFalse(report["snapshot_identity_independently_verified"])
        self.assertFalse(report["host_migration_allowed"])
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["real_orders_sent"], 0)
        with self.assertRaisesRegex(ValueError, "RETENTION_CHECKPOINT_CONFLICT_REPLAN_REQUIRED"):
            retention_dryrun(original, as_of=NOW, checkpoint={
                "plan_id": "not the plan", "manifest_digest": report["manifest_digest"]})

    def test_canonical_pins_exported_raw_and_shared_retained_object_hold_expired(self):
        old_unshared = version(1, raw_sha=U)
        old_pinned = version(2, raw_sha=V)
        old_exported = version(3, raw_sha=T)
        recent = version(4, economic_date="2026-10-30", raw_sha=U,
                         event_at="2026-10-30T20:00:00Z",
                         provider_at="2026-10-30T20:01:00Z",
                         observed_at="2026-10-30T20:02:00Z",
                         known_at="2026-10-30T20:02:00Z")
        case = manifest(
            [old_unshared, old_pinned, old_exported, recent],
            refs=[{"scope": "history_canonical", "version_id": 2,
                   "reference": "canonical:v2"}],
            exports=[{"export_id": "export:1", "known_at": "2026-10-31T11:00:00Z",
                      "manifest_sha256": T, "version_ids": [3],
                      "object_sha256s": [T]}])
        report = retention_dryrun(case, as_of=NOW)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 3,
                                            "RETAIN_IN_WINDOW": 1})
        self.assertEqual(report["pinned_versions"], 2)
        self.assertEqual(report["exports_audited"], 1)
        self.assertEqual(report["exported_links"], 2)
        self.assertEqual(report["reasons"]["RETENTION_RAW_OBJECT_SHARED_WITH_RETAINED"], 1)
        self.assertEqual(report["reasons"]["RETENTION_RAW_OBJECT_PINNED_BY_EXPORT"], 1)
        self.assertEqual(report["reasons"]["RETENTION_PINNED_BY_CONSUMER"], 2)

    def test_legacy_missing_currency_pit_or_price_basis_never_fabricated(self):
        missing_currency = version(1, currency="")
        missing_provider = version(2, provider_at=None)
        wrong_clock = version(3, provider_at="2026-11-04T00:00:00Z")
        partial = version(4)
        partial["price_basis"] = ""
        report = retention_dryrun(manifest([
            missing_currency, missing_provider, wrong_clock, partial]), as_of=NOW)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 4})
        for reason in ("SOURCE_IDENTITY_EXACT_FIVE_KEY_REQUIRED",
                       "HISTORY_PROVIDER_NATIVE_CLOCK_UNVERIFIED",
                       "HISTORY_PIT_CLOCK_ORDER_INVALID",
                       "HISTORY_PRICE_BASIS_UNVERIFIED"):
            self.assertIn(reason, report["reasons"])

    def test_aliases_on_etf_fci_and_exact_currency_keep_separate_lineage(self):
        cases = [version(1, family="ON", currency="USD_MEP"),
                 version(2, family="OBLIGACIONES", currency="USD_CCL"),
                 version(3, family="ETF", currency="ARS"),
                 version(4, family="FCI_LOCAL", currency="USD")]
        report = retention_dryrun(manifest(cases), as_of=NOW)
        self.assertEqual(report["states"], {"UNPINNED_REVIEW_CANDIDATE_ONLY": 4})
        self.assertEqual(report["reasons"], {})
        # All four are distinct identity+currency combinations, not a ticker
        # join. The output intentionally exposes no raw identity/account data.
        self.assertNotIn("AAA", str(report))

    def test_incomplete_pin_inventory_and_unknown_reference_block(self):
        report = retention_dryrun(manifest(
            [version(1)], complete=False, scopes=["history_canonical"],
            refs=[{"scope": "cross_database_ledger", "version_id": 999,
                   "reference": "unknown cross-db pointer"}]), as_of=NOW)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 1})
        self.assertEqual(report["missing_reference_ids"], 1)
        self.assertIn("raw_objects_and_manifests", report["missing_pin_scopes"])
        self.assertFalse(report["host_purge_allowed"])

    def test_invalid_manifest_duplicate_version_export_clock_and_budgets_fail_closed(self):
        bad = [
            (manifest([version(1), version(1)]), "RETENTION_VERSION_ID_INVALID_OR_DUPLICATE"),
            (manifest([version(1)], exports=[{
                "export_id": "e", "known_at": "2026-11-03T00:00:00Z",
                "manifest_sha256": S, "version_ids": [], "object_sha256s": []}]),
                "RETENTION_EXPORT_IN_FUTURE"),
            (manifest([version(1)], scopes=["unknown_source_scope"]),
                "RETENTION_PIN_SCOPE_UNKNOWN"),
            (manifest([version(1)], exports=[{
                "export_id": "same", "known_at": "2026-10-31T11:00:00Z",
                "manifest_sha256": S, "version_ids": [], "object_sha256s": []
            }] * 2), "RETENTION_EXPORT_DUPLICATE_ID"),
            (manifest([version(1)] * (MAX_VERSIONS + 1)),
                "RETENTION_INVENTORY_BUDGET_OR_SHAPE_INVALID"),
        ]
        for case, code in bad:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                retention_dryrun(case, as_of=NOW)

    def test_future_economic_date_and_naive_snapshot_fail_closed(self):
        bad_date = version(1, economic_date="2026-11-04")
        bad_naive = version(2, known_at="2026-10-30T20:00:00")
        report = retention_dryrun(manifest([bad_date, bad_naive]), as_of=NOW)
        self.assertIn("HISTORY_ECONOMIC_DATE_IN_FUTURE", report["reasons"])
        self.assertIn("HISTORY_VERSION_PIT_UNVERIFIED", report["reasons"])
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 2})

    def test_provider_version_after_snapshot_capture_cannot_be_retention_candidate(self):
        impossible = version(1, known_at="2026-11-02T15:30:00Z",
                             observed_at="2026-11-02T15:30:00Z")
        report = retention_dryrun(manifest([impossible]), as_of=NOW)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 1})
        self.assertIn("HISTORY_VERSION_KNOWN_AFTER_CAPTURE", report["reasons"])

    def test_iol_only_history_is_not_ppi_authority(self):
        report = retention_dryrun(manifest([version(1, source="IOL")]), as_of=NOW)
        self.assertIn("HISTORY_NONPRIMARY_SOURCE_REQUIRES_REVIEW", report["reasons"])
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 1})

    def test_unknown_market_and_settlement_never_become_exact_identity(self):
        unknown_market = version(1)
        unknown_market["market"] = "NYSE"
        unknown_settlement = version(2)
        unknown_settlement["settlement"] = "T+99"
        report = retention_dryrun(manifest([unknown_market, unknown_settlement]), as_of=NOW)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 2})
        self.assertIn("SOURCE_MARKET_CONTRACT_UNVERIFIED", report["reasons"])
        self.assertIn("SOURCE_SETTLEMENT_CONTRACT_UNVERIFIED", report["reasons"])

    def test_revision_predecessor_missing_forward_or_cross_currency_is_blocked(self):
        missing = version(1)
        missing["previous_version_id"] = 999
        forward = version(2)
        forward["previous_version_id"] = 3
        other = version(3, currency="USD_CCL")
        drift = version(4, currency="USD_MEP")
        drift["previous_version_id"] = 3
        report = retention_dryrun(manifest([missing, forward, other, drift]), as_of=NOW)
        self.assertEqual(report["states"], {"HOLD_UNVERIFIED_OR_PINNED": 3,
                                            "UNPINNED_REVIEW_CANDIDATE_ONLY": 1})
        for code in ("HISTORY_REVISION_PREDECESSOR_NOT_IN_INVENTORY",
                     "HISTORY_REVISION_FORWARD_OR_CYCLIC_POINTER",
                     "HISTORY_REVISION_IDENTITY_OR_BASIS_DRIFT"):
            self.assertIn(code, report["reasons"])


class IncrementalGapDryRunTest(unittest.TestCase):
    def test_bounded_five_date_windows_and_durable_checkpoint_continuation(self):
        proposed = incremental_gap_dryrun(gap(), as_of=NOW)
        self.assertEqual(proposed["schema"], GAP_SCHEMA)
        self.assertEqual((proposed["window_start_art"], proposed["window_end_art"]),
                         ("2026-10-24", "2026-10-28"))
        self.assertEqual(proposed["window_date_count"], 5)
        self.assertEqual(proposed["status"], "PROPOSED_OFFLINE_ONLY")
        second = incremental_gap_dryrun(gap(), as_of=NOW,
                                         checkpoint=synthetic_committed_receipt(proposed))
        self.assertEqual((second["window_start_art"], second["window_end_art"]),
                         ("2026-10-29", "2026-11-02"))
        self.assertEqual(second["operation_id"], proposed["operation_id"])
        self.assertNotEqual(second["window_id"], proposed["window_id"])
        complete = incremental_gap_dryrun(gap(), as_of=NOW,
                                           checkpoint=synthetic_committed_receipt(second))
        self.assertEqual(complete["status"], "ALREADY_COVERED_CHECKPOINT")
        self.assertEqual(complete["window_date_count"], 0)
        self.assertIsNone(complete["window_id"])
        for receipt in (proposed, second, complete):
            self.assertEqual(receipt["provider_calls"], 0)
            self.assertEqual(receipt["real_orders_sent"], 0)
            self.assertFalse(receipt["execution_authority"])
            self.assertTrue(receipt["requires_exclusive_lock"])
            self.assertTrue(receipt["requires_durable_checkpoint"])

    def test_rerun_same_uncommitted_window_is_bytewise_idempotent(self):
        first = incremental_gap_dryrun(gap(), as_of=NOW)
        again = incremental_gap_dryrun(gap(), as_of=NOW)
        self.assertEqual(first, again)
        self.assertEqual(first["window_id"], again["window_id"])
        with self.assertRaisesRegex(ValueError, "GAP_CHECKPOINT_NOT_COMMITTED"):
            incremental_gap_dryrun(gap(), as_of=NOW,
                                   checkpoint=first["next_checkpoint_template"])
        forged = synthetic_committed_receipt(first)
        forged["window_id"] = S
        with self.assertRaisesRegex(ValueError, "GAP_CHECKPOINT_WINDOW_CONFLICT"):
            incremental_gap_dryrun(gap(), as_of=NOW, checkpoint=forged)

    def test_secondary_source_missing_identity_and_conflicting_checkpoint_rejected(self):
        cases = [
            (gap(source="IOL"), "GAP_PPI_PRIMARY_SOURCE_EVIDENCE_REQUIRED"),
            (gap(source="BYMA"), "GAP_PPI_PRIMARY_SOURCE_EVIDENCE_REQUIRED"),
            (gap(currency=""), "SOURCE_IDENTITY_EXACT_FIVE_KEY_REQUIRED"),
            (gap(gap_start="2025-01-01"), "GAP_OUTSIDE_RETENTION_OR_FUTURE"),
            (gap(gap_end="2026-11-03"), "GAP_OUTSIDE_RETENTION_OR_FUTURE"),
            (gap(evidence_known_at="2026-11-03T00:00:00Z"), "GAP_EVIDENCE_FUTURE_AT_AS_OF"),
        ]
        for candidate, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                incremental_gap_dryrun(candidate, as_of=NOW)
        with self.assertRaisesRegex(ValueError, "GAP_CHECKPOINT_IDENTITY_OR_PROVENANCE_CONFLICT"):
            incremental_gap_dryrun(gap(), as_of=NOW,
                                   checkpoint={"operation_id": S,
                                               "completed_through": "2026-10-28"})

    def test_alias_change_mutation_cannot_resume_other_identity_checkpoint(self):
        previous = incremental_gap_dryrun(gap(), as_of=NOW)
        with self.assertRaisesRegex(ValueError, "GAP_CHECKPOINT_IDENTITY_OR_PROVENANCE_CONFLICT"):
            incremental_gap_dryrun(gap(currency="USD_CCL"), as_of=NOW,
                                   checkpoint=synthetic_committed_receipt(previous))
        with self.assertRaisesRegex(ValueError, "GAP_CHECKPOINT_IDENTITY_OR_PROVENANCE_CONFLICT"):
            incremental_gap_dryrun(gap(evidence_sha256=T), as_of=NOW,
                                   checkpoint=synthetic_committed_receipt(previous))


class OfflineManifestCommandTest(unittest.TestCase):
    def test_read_only_cli_requires_external_input_hash_and_never_echoes_ticker(self):
        with tempfile.TemporaryDirectory(prefix="rc6-a228-manifest-") as folder:
            source = Path(folder) / "synthetic.json"
            source.write_text(json.dumps(manifest([version(1)])), encoding="utf-8")
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            before = source.read_bytes()
            output = StringIO()
            with redirect_stdout(output):
                rc = command.main(["--retention-manifest", str(source),
                                   "--expected-input-sha256", expected,
                                   "--as-of", NOW.isoformat()])
            self.assertEqual(rc, 0)
            receipt = json.loads(output.getvalue())
            self.assertFalse(receipt["host_purge_allowed"])
            self.assertEqual(receipt["versions_classified"], 1)
            self.assertNotIn("AAA", output.getvalue())
            self.assertEqual(source.read_bytes(), before)
            with redirect_stdout(output := StringIO()):
                rc = command.main(["--retention-manifest", str(source),
                                   "--expected-input-sha256", "0" * 64,
                                   "--as-of", NOW.isoformat()])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(output.getvalue())["reason"],
                             "SOURCE_DRYRUN_INPUT_SHA_MISMATCH")

    def test_symlink_hardlink_and_unpaired_checkpoint_are_blocked(self):
        with tempfile.TemporaryDirectory(prefix="rc6-a228-manifest-") as folder:
            original = Path(folder) / "source.json"
            original.write_text(json.dumps(manifest()), encoding="utf-8")
            expected = hashlib.sha256(original.read_bytes()).hexdigest()
            alias = Path(folder) / "alias.json"
            alias.symlink_to(original)
            with self.assertRaisesRegex(ValueError, "SOURCE_DRYRUN_SYMLINK_NOT_ALLOWED"):
                command.read_verified_json(alias, expected)
            alias.unlink()
            alias.hardlink_to(original)
            with self.assertRaisesRegex(ValueError, "SOURCE_DRYRUN_REGULAR_BOUNDED_COPY_REQUIRED"):
                command.read_verified_json(original, expected)
            with redirect_stdout(output := StringIO()):
                rc = command.main(["--retention-manifest", str(original),
                                   "--expected-input-sha256", expected,
                                   "--as-of", NOW.isoformat(),
                                   "--checkpoint", str(original)])
            self.assertEqual(rc, 2)
            self.assertFalse(json.loads(output.getvalue())["host_purge_allowed"])


if __name__ == "__main__":
    unittest.main()
