"""A621 offhost read-only copy inspection regression."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.rc6_fi_sell_ro_snapshot_audit import (
    ReadOnlySnapshotStore, audit_snapshot,
)
import test_rc6_fi_sell_persisted_evidence as fixtures


class OffhostAuditTests(unittest.TestCase):
    setUp = fixtures.PersistedEvidenceTests.setUp
    contract = fixtures.PersistedEvidenceTests.contract

    def test_true_readonly_copy_and_explicit_blocked_output(self):
        ro = ReadOnlySnapshotStore(self.store.path)
        with ro.connect() as db:
            self.assertEqual(db.execute("PRAGMA query_only").fetchone()[0],1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM paper_positions")
        with patch("rc6_fi_sell_persisted_evidence.verified_native_book_journal",
                   return_value=([],{"state":"NO_VERIFICADO","reason":"PPI_BOOK_JOURNAL_ABSENT"})):
            output = audit_snapshot(ro,at=fixtures.AT,journal_path=self.journal)
        self.assertEqual(output["status"],"BLOCKED")
        self.assertEqual(output["scanned_paper_positions"],1)
        self.assertFalse(output["observer_or_provider_contacted"])
        self.assertFalse(output["db_write_attempted"])
        self.assertEqual(output["real_routes_used"],[])

    def test_position_budget_rejects_truncation_without_partial_success(self):
        with self.store.connect() as db:
            db.execute("""INSERT INTO paper_positions
                (paper_id,source,status,symbol,asset_class,market,currency,settlement,
                 opened_at,closed_at,quantity,entry_price,target_price,stop_price,features_json)
                SELECT 'PAPER-FI-2',source,status,symbol,asset_class,market,currency,
                       settlement,opened_at,closed_at,quantity,entry_price,target_price,
                       stop_price,features_json FROM paper_positions WHERE paper_id='PAPER-FI-1'""")
        with self.assertRaisesRegex(ValueError,"FI_EXIT_AUDIT_POSITION_BUDGET_EXCEEDED"):
            audit_snapshot(ReadOnlySnapshotStore(self.store.path),at=fixtures.AT,
                           journal_path=self.journal,max_positions=1)

    def test_rejects_symlinked_sqlite_path(self):
        link=Path(self.tmp.name)/"paper-link.sqlite"
        link.symlink_to(self.store.path)
        with self.assertRaisesRegex(ValueError,"FI_EXIT_AUDIT_SQLITE_ABSOLUTE_REGULAR_COPY_REQUIRED"):
            ReadOnlySnapshotStore(link)


if __name__ == "__main__":
    unittest.main()
