"""A603: the RC4 compatibility due-check must never authorize work on DB errors.

All tests use an isolated SQLite fixture and a frozen weekday market clock.
No host process, broker, timer or observer database is accessed.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import rc4_contract_due_job as due_job


class MarketClock(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)
        return instant if tz is None else instant.astimezone(tz)


def run_due(path: Path):
    output = io.StringIO()
    with patch.object(due_job, "DB", str(path)), patch.object(due_job, "datetime", MarketClock):
        with contextlib.redirect_stdout(output):
            code = due_job.main()
    return code, json.loads(output.getvalue())


class ContractDueFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rc6-a603-contract-due-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "isolated.db"

    def test_missing_sqlite_database_never_returns_ok_or_due(self):
        code, receipt = run_due(self.db)
        self.assertNotEqual(code, 0)
        self.assertEqual(receipt["state"], "ERROR")
        self.assertEqual(receipt["due_jobs"], [])
        self.assertEqual(receipt["reason"], "CONTRACT_DUE_SQLITE_READ_FAILED")

    def test_missing_runs_table_never_returns_ok_or_due(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE unrelated(k TEXT)")
        code, receipt = run_due(self.db)
        self.assertNotEqual(code, 0)
        self.assertEqual(receipt["state"], "ERROR")
        self.assertEqual(receipt["reason"], "CONTRACT_DUE_RUNS_TABLE_MISSING")
        self.assertEqual(receipt["due_jobs"], [])

    def test_missing_required_runs_column_never_returns_ok_or_due(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE contract_evidence_v2_runs(job_key TEXT)")
        code, receipt = run_due(self.db)
        self.assertNotEqual(code, 0)
        self.assertEqual(receipt["state"], "ERROR")
        self.assertEqual(receipt["reason"], "CONTRACT_DUE_SQLITE_READ_FAILED")
        self.assertEqual(receipt["due_jobs"], [])

    def test_corrupt_sqlite_database_never_returns_ok_or_due(self):
        self.db.write_bytes(b"not-a-sqlite-database")
        code, receipt = run_due(self.db)
        self.assertNotEqual(code, 0)
        self.assertEqual(receipt["state"], "ERROR")
        self.assertEqual(receipt["reason"], "CONTRACT_DUE_SQLITE_READ_FAILED")
        self.assertEqual(receipt["due_jobs"], [])

    def test_valid_runs_table_preserves_market_cadences(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE contract_evidence_v2_runs(job_key TEXT, started_at TEXT)")
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        code, receipt = run_due(self.db)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["state"], "OK")
        self.assertEqual(set(receipt["due_jobs"]), {
            "CONTRACT_EVIDENCE_DYNAMIC",
            "CONTRACT_EVIDENCE_CAUCIONES",
            "CONTRACT_EVIDENCE_AUCTIONS",
            "CONTRACT_EVIDENCE_DERIVATIVES",
        })

    def test_recent_start_preserves_ttl_without_triggering_that_job(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE contract_evidence_v2_runs(job_key TEXT, started_at TEXT)")
            conn.execute("INSERT INTO contract_evidence_v2_runs VALUES (?,?)", (
                "CONTRACT_EVIDENCE_CAUCIONES", "2026-11-03T14:58:00+00:00",
            ))
        code, receipt = run_due(self.db)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["state"], "OK")
        self.assertNotIn("CONTRACT_EVIDENCE_CAUCIONES", receipt["due_jobs"])
        self.assertIn("CONTRACT_EVIDENCE_DYNAMIC", receipt["due_jobs"])

    def test_existing_rc5_wrapper_denies_error_receipts(self):
        wrapper = (Path(__file__).resolve().parents[1] /
                   "scripts/porota_contract_evidence_rc5_runtime.sh").read_text()
        self.assertIn("if d.get('state')!='OK': sys.exit(3)", wrapper)
        self.assertIn("DUE_POLICY_INVALID_OUTPUT", wrapper)


if __name__ == "__main__":
    unittest.main()
