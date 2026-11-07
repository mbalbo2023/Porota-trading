"""Bounded host census contract on disposable SQLite files, without network."""
from __future__ import annotations

import base64
from contextlib import redirect_stdout
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_history_identity_day_census as module
from scripts.rc6_history_identity_day_census import CensusError, census, target_from_base64


TARGET = ("D30N6", "LETRAS", "BYMA", "ARS", "A-24HS", "2026-10-30")


def encoded(target):
    return base64.b64encode(json.dumps(list(target)).encode()).decode()


def dbhash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


class CensusTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="rc6-census-test-")
        self.addCleanup(self.scratch.cleanup)
        self.observer = Path(self.scratch.name) / "observer.sqlite"
        self.history = Path(self.scratch.name) / "history.sqlite"
        with sqlite3.connect(self.observer) as db:
            db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent INTEGER)")
            db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        with sqlite3.connect(self.history) as db:
            db.executescript("""
                CREATE TABLE history_versions_v2(
                    id INTEGER PRIMARY KEY,symbol TEXT,instrument_type TEXT,market TEXT,
                    currency TEXT,settlement TEXT,date TEXT,source TEXT,
                    version_known_at TEXT,payload_hash TEXT,price_basis TEXT,adjustment_basis TEXT);
                CREATE INDEX idx_history_versions_v2_identity ON history_versions_v2(
                    symbol,instrument_type,market,currency,settlement,date,price_basis,adjustment_basis,id DESC);
                CREATE TABLE history_canonical_v2(
                    symbol TEXT,instrument_type TEXT,market TEXT,currency TEXT,
                    settlement TEXT,date TEXT,price_basis TEXT,adjustment_basis TEXT,
                    version_id INTEGER,source TEXT,version_known_at TEXT,close REAL,
                    PRIMARY KEY(symbol,instrument_type,market,currency,settlement,date,
                                price_basis,adjustment_basis));
                CREATE INDEX idx_history_canonical_v2_lookup ON history_canonical_v2(
                    instrument_type,market,currency,settlement,symbol,price_basis,date);
            """)

    def _version(self, n, *, family="LETRAS", day="2026-10-30"):
        with sqlite3.connect(self.history) as db:
            db.execute("""INSERT INTO history_versions_v2 VALUES(
                ?,'D30N6',?,'BYMA','ARS','A-24HS',?,'PPI_API',
                '2026-11-01T03:46:34Z',?,'RAW','RAW_NO_ADJUSTMENT')""",
                (n, family, day, f"{n:064x}"))

    def test_exact_indexed_rows_and_before_after_bytes(self):
        self._version(1)
        self._version(2, day="2026-10-29")
        self._version(3, family="BONOS")
        with sqlite3.connect(self.history) as db:
            db.execute("""INSERT INTO history_canonical_v2 VALUES(
                'D30N6','LETRAS','BYMA','ARS','A-24HS','2026-10-30',
                'RAW','RAW_NO_ADJUSTMENT',1,'PPI_API','2026-11-01T03:46:34Z',153900)""")
        before = dbhash(self.history), dbhash(self.observer)
        result = census(self.observer, self.history, TARGET)
        after = dbhash(self.history), dbhash(self.observer)
        self.assertEqual(before, after)
        self.assertEqual((result["versions"], result["canonical"]), (1, 1))
        self.assertEqual(result["state"], "EXACT_V2_READ_ONLY")
        self.assertEqual(result["history_v2_writes"], 0)
        self.assertEqual(result["ready_implication"], "NONE")
        self.assertEqual(result["provider_raw"], "NO_VERIFICADO")
        self.assertEqual(len(result["versions_sha256"]), 64)
        self.assertEqual(result["versions_sha256"], census(self.observer, self.history, TARGET)["versions_sha256"])

    def test_zero_v2_is_not_provider_or_legacy_absence(self):
        result = census(self.observer, self.history, TARGET)
        self.assertEqual(result["state"], "EMPTY_EXACT_V2_ONLY")
        self.assertEqual(result["provider_raw"], "NO_VERIFICADO")
        self.assertEqual(result["legacy_history"], "NO_VERIFICADO")

    def test_missing_exact_index_fails_before_large_read(self):
        with sqlite3.connect(self.history) as db:
            db.execute("DROP INDEX idx_history_versions_v2_identity")
        with self.assertRaisesRegex(CensusError, "CENSUS_EXACT_INDEX_MISSING"):
            census(self.observer, self.history, TARGET)

    def test_closed_paper_zero_is_required(self):
        with sqlite3.connect(self.observer) as db:
            db.execute("UPDATE observer_state SET real_orders_sent=1")
        with self.assertRaisesRegex(CensusError, "CENSUS_PAPER_ZERO_UNVERIFIED"):
            census(self.observer, self.history, TARGET)

    def test_row_budget_fails_without_claiming_complete(self):
        for n in range(1, 66):
            self._version(n)
        with self.assertRaisesRegex(CensusError, "CENSUS_ROW_BUDGET_EXCEEDED"):
            census(self.observer, self.history, TARGET)

    def test_invalid_input_rejected_before_database(self):
        for target in (
            ("D30N6", "LETRAS", "BYMA", "ARS", "A-24HS", "2026-10-31"),
            ("D30N6", "LETRAS", "BYMA", "ARS", "A-24HS", "2026-11-02"),
            ("D30N6", "OTHER", "BYMA", "ARS", "A-24HS", "2026-10-30"),
            ("D30N6;drop", "LETRAS", "BYMA", "ARS", "A-24HS", "2026-10-30"),
        ):
            with self.subTest(target=target), self.assertRaises(CensusError):
                target_from_base64(encoded(target))
        self.assertEqual(target_from_base64(encoded(TARGET)), TARGET)

    def test_cli_failure_exposes_only_sanitized_reason(self):
        output = io.StringIO()
        with (patch.dict(os.environ, {"RC6_CENSUS_TARGET_B64": encoded(TARGET)}),
              patch.object(module, "OBSERVER_DB", Path(self.scratch.name) / "absent.sqlite"),
              redirect_stdout(output)):
            self.assertEqual(module.main(), 2)
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt["state"], "NO_VERIFICADO")
        self.assertEqual(receipt["reason"], "CENSUS_DB_READ_UNAVAILABLE")
        self.assertNotIn(self.scratch.name, output.getvalue())

    def test_action_host_is_manual_single_target_and_serialized(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/"
                    "rc6-history-identity-day-readonly.yml").read_text()
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("  pull_request:", workflow)
        self.assertIn("if: github.event_name == 'pull_request'", workflow)
        self.assertIn("if: github.event_name == 'workflow_dispatch' && github.repository", workflow)
        self.assertIn("RC6_HISTORY_IDENTITY_DAY_OFFHOST_TESTS=GREEN", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertNotIn("  schedule:", workflow)
        self.assertIn("group: rc6-unified-paper-deploy", workflow)
        self.assertIn("StrictHostKeyChecking=yes", workflow)
        self.assertIn("RC6_EXPECTED_PRODUCT_SHA: 7e1e744d6ca9ab30c51b5747dd4467af5ece0145", workflow)
        self.assertIn("RC6_EXPECTED_IMAGE_ID: sha256:4a8f7306188979fe6976f400209151bd529bf0d9f46f068b50bbc54fc96fd70b", workflow)
        self.assertIn("tests -p test_rc6_history_identity_day_census.py", workflow)
        self.assertNotIn("docker logs", workflow)
        self.assertNotIn("GROUP BY", workflow)
        self.assertNotIn("docker prune", workflow)


if __name__ == "__main__":
    unittest.main()
