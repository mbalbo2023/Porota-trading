"""A574 synthetic-only OFFHOST retention/PIT census; never a purge test."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from be_paper_engine import Quote
import rc6_ppi_scanner_provenance as ppi
import rc6_ppi_book_retention_inventory as retention


NOW = datetime(2026, 11, 3, 17, 0, 0, tzinfo=timezone.utc)
KEY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")


def book_fixture(source_at: datetime, *, known_at: datetime | None = None):
    observed = (known_at or (source_at + timedelta(seconds=1))).isoformat()
    stamp = source_at.isoformat()
    q = Quote("GGAL", "ACCIONES", "A-24HS", Decimal("99.9"),
              Decimal("99.8"), Decimal("100.2"), Decimal("5"), Decimal("8"),
              observed, currency="ARS", market="BYMA", trade_at=stamp,
              book_at=stamp, last_kind="TRADE")
    book = {"date": stamp,
            "bids": [{"position": 0, "price": "99.8", "quantity": "5"}],
            "offers": [{"position": 0, "price": "100.2", "quantity": "8"}]}
    return q, book


class TestPpiBookOffhostRetention(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.writer = ppi.ScannerPrimaryRecorder(self.root / "market" / ppi.FILENAME)
        self.path = self.root / "market" / ppi.PIT_JOURNAL_FILENAME

    def record(self, at: datetime, *, known_at: datetime | None = None):
        q, book = book_fixture(at, known_at=known_at)
        result = self.writer.capture_raw_book(q, raw_book=book,
                                              request=("GGAL", "ACCIONES", "A-24HS"),
                                              identity=KEY, observed_at=q.observed_at)
        self.assertEqual(result["status"], "CAPTURED", msg=result)

    def inventory(self, **kwargs):
        return retention.inventory_native_book_retention(
            self.path, runner_root=self.root, as_of_utc=NOW.isoformat(), **kwargs)

    def test_native_utc_365_day_boundary_is_inclusive_not_rejuvenated_by_known_at(self):
        edge = NOW - timedelta(days=365)
        older = edge - timedelta(microseconds=1)
        self.record(older, known_at=older + timedelta(seconds=1))
        self.record(edge, known_at=edge + timedelta(seconds=1))
        self.record(NOW - timedelta(minutes=10))
        report = self.inventory()
        self.assertEqual(report["state"], "OFFHOST_CHAIN_VERIFIED_RETENTION_AND_PIN_HOLD")
        self.assertEqual(report["cohorts"], {
            "WITHIN_365D_NATIVE_BOOK": 2,
            "OLDER_THAN_365D_PROTECTED_UNKNOWN_PINS": 1,
            "FUTURE_AT_AS_OF_HOLD": 0})
        self.assertEqual(report["cutoff_utc_inclusive"], edge.isoformat())
        self.assertEqual(report["daily_date_only_cutoff_art_inclusive"], "2025-11-04")
        self.assertEqual(report["known_at_role"], "PIT_AVAILABILITY_NOT_RETENTION_REJUVENATION")
        self.assertEqual(report["pin_scope"], "PIN_SCOPE_UNKNOWN")
        self.assertEqual(report["host_purge_allowed"], False)
        self.assertEqual(report["physical_host_delete_count"], 0)

    def test_recent_observation_does_not_make_expired_native_book_young(self):
        old = NOW - timedelta(days=366)
        self.record(old, known_at=NOW - timedelta(seconds=1))
        report = self.inventory()
        self.assertEqual(report["cohorts"]["OLDER_THAN_365D_PROTECTED_UNKNOWN_PINS"], 1)
        self.assertEqual(report["cohorts"]["WITHIN_365D_NATIVE_BOOK"], 0)

    def test_art_calendar_date_cutoff_changes_at_midnight_not_utc_midnight(self):
        # The source-native rolling 365*24h cutoff is separate from the ART
        # 365-calendar-day inclusive bound for date-only histories. In the
        # 30-minute window spanning local midnight, their boundaries differ.
        self.record(datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc))
        before = retention.inventory_native_book_retention(
            self.path, runner_root=self.root,
            as_of_utc="2026-11-03T02:30:00+00:00")
        after = retention.inventory_native_book_retention(
            self.path, runner_root=self.root,
            as_of_utc="2026-11-03T03:01:00+00:00")
        self.assertEqual(before["daily_date_only_cutoff_art_inclusive"], "2025-11-03")
        self.assertEqual(after["daily_date_only_cutoff_art_inclusive"], "2025-11-04")
        self.assertEqual(before["cutoff_utc_inclusive"], "2025-11-03T02:30:00+00:00")
        self.assertEqual(after["cutoff_utc_inclusive"], "2025-11-03T03:01:00+00:00")
        self.assertEqual(before["pin_scope"], "PIN_SCOPE_UNKNOWN")
        self.assertEqual(after["pin_scope"], "PIN_SCOPE_UNKNOWN")

    def test_forged_pin_claims_cannot_authorize_purge_or_legacy_backfill(self):
        self.record(NOW - timedelta(minutes=3))
        fake = {"pin_scopes_verified": ["sqlite", "json", "artifact", "replay"],
                "complete": True, "provider_authentic": True,
                "history_version_ids": [1, 596196], "host_purge_allowed": True}
        report = self.inventory(pin_claims=fake)
        self.assertTrue(report["pin_claims_supplied"])
        self.assertFalse(report["pin_claims_accepted_as_proof"])
        self.assertEqual(report["pin_scope"], "PIN_SCOPE_UNKNOWN")
        self.assertFalse(report["external_consumer_universe_verified"])
        self.assertFalse(report["legacy_596196_pit_backfill_authorized"])
        self.assertFalse(report["host_purge_allowed"])
        self.assertNotIn("history_version_ids", report)

    def test_tampered_hash_chain_yields_hold_without_counts(self):
        self.record(NOW - timedelta(minutes=3))
        original = self.path.read_text()
        corrupted = original.replace("100.2", "199.2", 1)
        self.assertNotEqual(corrupted, original)
        self.path.write_text(corrupted)
        self.path.chmod(0o600)
        report = self.inventory()
        self.assertEqual(report["state"], "HOLD")
        self.assertIn("JOURNAL", report["reason"])
        self.assertNotIn("cohorts", report)
        self.assertFalse(report["host_purge_allowed"])

    def test_capacity_is_observation_not_automatic_rotation_or_deletion(self):
        self.record(NOW - timedelta(minutes=3))
        size = self.path.stat().st_size
        with (patch.object(retention, "PIT_JOURNAL_MAX_RECORDS", 1),
              patch.object(retention, "PIT_SESSION_MAX_RECORDS", 1)):
            report = self.inventory()
        self.assertEqual(report["budgets"]["capture_capacity_state"], "CAPTURE_CAPACITY_HOLD")
        self.assertEqual(report["budgets"]["global_remaining_records"], 0)
        self.assertEqual(report["budgets"]["session_remaining_records"], 0)
        self.assertEqual(report["journal_copy_bytes"], size)
        self.assertEqual(report["journal_record_count"], 1)
        self.assertEqual(report["physical_bytes_reclaimed"], 0)
        self.assertEqual(len(ppi.verified_native_book_journal(self.path)[0]), 1)

    def test_offhost_path_and_clock_guards_fail_before_data_use(self):
        self.record(NOW - timedelta(minutes=3))
        wrong_clock = retention.inventory_native_book_retention(
            self.path, runner_root=self.root, as_of_utc="2026-11-03T17:00:00")
        self.assertEqual(wrong_clock["reason"], "RETENTION_CLOCK_UNVERIFIED")
        outside = retention.inventory_native_book_retention(
            self.path, runner_root=self.root / "unrelated", as_of_utc=NOW.isoformat())
        self.assertEqual(outside["reason"], "PRIVATE_OFFHOST_COPY_REQUIRED")
        link = self.root / "market" / "external-pit.json"
        link.symlink_to(self.path)
        rejected = retention.inventory_native_book_retention(
            link, runner_root=self.root, as_of_utc=NOW.isoformat())
        self.assertEqual(rejected["reason"], "PRIVATE_OFFHOST_COPY_REQUIRED")
        self.root.chmod(0o755)
        try:
            unsafe_root = self.inventory()
            self.assertEqual(unsafe_root["reason"], "PRIVATE_OFFHOST_COPY_REQUIRED")
        finally:
            self.root.chmod(0o700)

    def test_runner_root_symlink_ancestor_cannot_escape_to_external_copy(self):
        """An alias above runner_root must not authorize reading outside data."""
        self.record(NOW - timedelta(minutes=3))
        with tempfile.TemporaryDirectory() as external_dir:
            outside_root = Path(external_dir) / "private"
            outside_root.mkdir(mode=0o700)
            outside_market = outside_root / "market"
            outside_market.mkdir(mode=0o700)
            outside_path = outside_market / ppi.PIT_JOURNAL_FILENAME
            outside_path.write_bytes(self.path.read_bytes())
            outside_path.chmod(0o600)
            outside_digest = hashlib.sha256(outside_path.read_bytes()).hexdigest()

            tunnel = self.root / "escape-ancestor"
            tunnel.symlink_to(Path(external_dir), target_is_directory=True)
            aliased_root = tunnel / "private"  # final component is NOT a symlink
            aliased_path = aliased_root / "market" / ppi.PIT_JOURNAL_FILENAME
            self.assertFalse(aliased_root.is_symlink())
            self.assertFalse(aliased_path.is_symlink())
            report = retention.inventory_native_book_retention(
                aliased_path, runner_root=aliased_root, as_of_utc=NOW.isoformat())
            self.assertEqual(report["state"], "HOLD")
            self.assertEqual(report["reason"], "PRIVATE_OFFHOST_COPY_REQUIRED")
            self.assertFalse(report["host_purge_allowed"])
            self.assertEqual(report["physical_host_delete_count"], 0)
            self.assertEqual(hashlib.sha256(outside_path.read_bytes()).hexdigest(),
                             outside_digest)

    def test_parent_swap_after_precheck_cannot_hash_external_journal(self):
        """A symlink installed after validation must not change the read target."""
        self.record(NOW - timedelta(minutes=3))
        with tempfile.TemporaryDirectory() as external_dir:
            outside_market = Path(external_dir) / "market"
            outside_market.mkdir(mode=0o700)
            outside_path = outside_market / ppi.PIT_JOURNAL_FILENAME
            outside_path.write_bytes(self.path.read_bytes())
            outside_path.chmod(0o600)
            outside_digest = hashlib.sha256(outside_path.read_bytes()).hexdigest()
            original_precheck = retention._private_copy_under_root
            original_market = self.root / "market"
            relocated_market = self.root / "market-before-swap"
            swaps = []

            def swap_parent_after_approval(path, root):
                self.assertTrue(original_precheck(path, root))
                original_market.rename(relocated_market)
                original_market.symlink_to(outside_market, target_is_directory=True)
                swaps.append(True)
                return True

            with patch.object(retention, "_private_copy_under_root",
                              swap_parent_after_approval):
                report = self.inventory()
            self.assertEqual(swaps, [True])
            self.assertEqual(report["state"], "HOLD")
            self.assertNotIn("journal_copy_sha256", report)
            self.assertNotIn("cohorts", report)
            self.assertEqual(hashlib.sha256(outside_path.read_bytes()).hexdigest(),
                             outside_digest)
            self.assertFalse(report["host_purge_allowed"])

    def test_parent_swap_after_directory_fd_pin_reads_original_inode(self):
        """The next component must be opened below an already pinned dirfd."""
        self.record(NOW - timedelta(minutes=3))
        expected_digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as external_dir:
            outside_market = Path(external_dir) / "market"
            outside_market.mkdir(mode=0o700)
            outside_path = outside_market / ppi.PIT_JOURNAL_FILENAME
            outside_path.write_bytes(b"external-data-must-never-be-inspected")
            outside_path.chmod(0o600)
            original_open = retention.os.open
            original_market = self.root / "market"
            swapped = []

            def swap_after_dirfd_open(path, flags, *args, **kwargs):
                fd = original_open(path, flags, *args, **kwargs)
                if path == "market" and kwargs.get("dir_fd") is not None and not swapped:
                    original_market.rename(self.root / "original-market-pinned")
                    original_market.symlink_to(outside_market, target_is_directory=True)
                    swapped.append(True)
                return fd

            with patch.object(retention.os, "open", swap_after_dirfd_open):
                report = self.inventory()
            self.assertEqual(swapped, [True])
            self.assertEqual(report["state"],
                             "OFFHOST_CHAIN_VERIFIED_RETENTION_AND_PIN_HOLD")
            self.assertEqual(report["journal_copy_sha256"], expected_digest)
            self.assertEqual(outside_path.read_bytes(),
                             b"external-data-must-never-be-inspected")

    def test_fifo_swap_after_precheck_opens_nonblocking_and_fails_closed(self):
        """An attacker-replaced FIFO must not indefinitely block open()."""
        self.record(NOW - timedelta(minutes=3))
        original_precheck = retention._private_copy_under_root
        original_open = retention.os.open
        checked_final_flags = []

        def swap_final_to_fifo(path, root):
            self.assertTrue(original_precheck(path, root))
            self.path.rename(self.path.with_suffix(".before-fifo-swap"))
            os.mkfifo(self.path, mode=0o600)
            return True

        def guard_final_open(path, flags, *args, **kwargs):
            if path == self.path.name and kwargs.get("dir_fd") is not None:
                checked_final_flags.append(flags)
                # A missing nonblock flag would hang forever on the FIFO;
                # fail the test immediately instead of actually blocking.
                if not flags & os.O_NONBLOCK:
                    raise AssertionError("FIFO_OPEN_CAN_BLOCK_FOREVER")
            return original_open(path, flags, *args, **kwargs)

        with (patch.object(retention, "_private_copy_under_root", swap_final_to_fifo),
              patch.object(retention.os, "open", guard_final_open)):
            report = self.inventory()
        self.assertEqual(len(checked_final_flags), 1)
        self.assertEqual(report["state"], "HOLD")
        self.assertNotIn("journal_copy_sha256", report)
        self.assertFalse(report["host_purge_allowed"])

    def test_swapped_journal_between_chain_and_digest_is_rejected(self):
        self.record(NOW - timedelta(minutes=3))
        original_validator = retention._pit_verified_rows

        def swap_after_first_verify(doc):
            rows = original_validator(doc)
            # A concurrent writer within the same private root changed the
            # path after the first fingerprint; never report that digest as
            # the verified chain snapshot.
            with self.path.open("ab") as handle:
                handle.write(b" ")
            return rows

        with patch.object(retention, "_pit_verified_rows", swap_after_first_verify):
            report = self.inventory()
        self.assertEqual(report["reason"], "PRIVATE_JOURNAL_CHANGED_DURING_CENSUS_HOLD")
        self.assertFalse(report["host_purge_allowed"])

    def test_final_path_swap_after_open_uses_original_pinned_inode(self):
        """A late final-file substitution cannot change the verified bytes."""
        self.record(NOW - timedelta(minutes=3))
        expected_digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as external_dir:
            outside_path = Path(external_dir) / ppi.PIT_JOURNAL_FILENAME
            outside_path.write_bytes(b"external-private-payload-not-a-book-journal")
            outside_path.chmod(0o600)
            original_validator = retention._pit_verified_rows
            swaps = []

            def swap_final_after_validation(doc):
                rows = original_validator(doc)
                self.path.rename(self.path.with_suffix(".pinned-original"))
                self.path.symlink_to(outside_path)
                swaps.append(True)
                return rows

            with patch.object(retention, "_pit_verified_rows",
                              swap_final_after_validation):
                report = self.inventory()
            self.assertEqual(swaps, [True])
            self.assertEqual(report["state"],
                             "OFFHOST_CHAIN_VERIFIED_RETENTION_AND_PIN_HOLD")
            self.assertEqual(report["journal_copy_sha256"], expected_digest)
            self.assertEqual(outside_path.read_bytes(),
                             b"external-private-payload-not-a-book-journal")

    def test_private_hash_and_public_aggregate_do_not_leak_prices_or_symbols(self):
        self.record(NOW - timedelta(minutes=3))
        report = self.inventory()
        self.assertEqual(len(report["journal_copy_sha256"]), 64)
        self.assertEqual(report["journal_record_count"], 1)
        self.assertGreater(report["budgets"]["global_remaining_bytes"], 0)
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["real_orders_sent"], 0)
        text = str(report)
        self.assertNotIn("GGAL", text)
        self.assertNotIn("100.2", text)
        self.assertNotIn("99.8", text)


if __name__ == "__main__":
    unittest.main()
