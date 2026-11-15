"""Functional acceptance reads SQLite values, never substitutes an expected zero."""
import importlib
import importlib.util
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


class FunctionalDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "paper.db"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE observer_state(id INTEGER PRIMARY KEY,mode TEXT,real_orders_sent)")
        self.db.execute("INSERT INTO observer_state VALUES(1,'PRODUCTION_PAPER',0)")
        self.db.commit()

    def inspect(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.porota_ci_functional"), "Functional acceptance is missing")
        return importlib.import_module("scripts.porota_ci_functional").inspect_paper_database(self.path)

    def test_reads_real_sqlite_paper_zero_and_integrity(self):
        report = self.inspect()
        self.assertEqual(report, {"paper_mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
                                  "sqlite_integrity": "ok", "sqlite_journal_mode": "wal"})

    def test_positive_or_text_counter_is_rejected(self):
        for count in (1, "0", -1, None):
            with self.subTest(count=count):
                self.db.execute("UPDATE observer_state SET real_orders_sent=?", (count,))
                self.db.commit()
                with self.assertRaisesRegex(ValueError, "PAPER_COUNTER"):
                    self.inspect()

    def test_nonpaper_mode_is_rejected(self):
        self.db.execute("UPDATE observer_state SET mode='PRODUCTION_REAL'")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "PAPER_MODE"):
            self.inspect()

    def test_synthetic_dashboard_credentials_satisfy_actual_startup_guard(self):
        module = importlib.import_module("scripts.porota_ci_functional")
        configure = getattr(module, "synthetic_environment", None)
        self.assertTrue(callable(configure), "Synthetic runtime configuration is missing")
        environment = configure(Path(self.temp.name), "c" * 43)
        result = subprocess.run([sys.executable, "-B", "-c",
            "import ay_dashboard_auth as auth; assert auth.validar_configuracion() == []; "
            "assert auth.token_valido('c' * 43); assert not auth.token_valido('invalid')"],
            env=os.environ | environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
