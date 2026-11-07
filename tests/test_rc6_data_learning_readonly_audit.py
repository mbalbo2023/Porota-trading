"""A923: SQLite PAPER/0 evidence must not be inferred from missing values."""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import rc6_data_learning_readonly_audit as audit


class LearningReadOnlyPaperZeroAuditTests(unittest.TestCase):
    """Exercise real, synthetic SQLite reads; never contact a provider or host."""

    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "synthetic-observer.db"

    def _create_observer(self, counter=0, *, mode="PRODUCTION_PAPER", counter_column=True,
                         insert_row=True, observer_table=True):
        with sqlite3.connect(self.path) as db:
            if not observer_table:
                db.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
                return
            # BLOB affinity retains malformed 0.0 and text "0" as distinct values.
            # An INTEGER column coerces text "0" into integer 0 during insertion.
            suffix = ", real_orders_sent BLOB" if counter_column else ""
            db.execute("CREATE TABLE observer_state (id INTEGER PRIMARY KEY, mode TEXT" + suffix + ")")
            if insert_row:
                columns = "id, mode, real_orders_sent" if counter_column else "id, mode"
                parameters = (1, mode, counter) if counter_column else (1, mode)
                marks = ", ".join("?" for _ in parameters)
                db.execute(f"INSERT INTO observer_state ({columns}) VALUES ({marks})", parameters)

    def _audit(self):
        with patch.object(audit, "OBSERVER_DB", self.path):
            return audit.audit_observer()

    def test_exact_sqlite_integer_zero_is_only_green_counter_evidence(self):
        # Detects a regression that accepts the DB's integer 0 but labels it unknown.
        self._create_observer(counter=0)
        before = self.path.read_bytes()
        result = self._audit()
        self.assertEqual("ok", result["quick_check"])
        self.assertEqual("GREEN", result["state"])
        self.assertEqual("SQLITE_INTEGER_ZERO", result["real_orders_evidence"])
        self.assertEqual(0, result["observer_state"]["real_orders_sent"])
        self.assertEqual(before, self.path.read_bytes(), "audit changed the SQLite file")

    def test_null_text_float_and_nonzero_values_cannot_be_green(self):
        # Detects `int(counter or 0)` and accidental acceptance of text/float 0.
        cases = (
            (None, "NO_VERIFICADO"),
            ("0", "NO_VERIFICADO"),
            (0.0, "NO_VERIFICADO"),
            (1, "NONZERO"),
            (-1, "NONZERO"),
        )
        for counter, expected_evidence in cases:
            with self.subTest(counter=counter):
                self.path.unlink(missing_ok=True)
                self._create_observer(counter)
                result = self._audit()
                self.assertEqual("RED", result["state"])
                self.assertEqual(expected_evidence, result["real_orders_evidence"])
                self.assertEqual(counter, result["observer_state"]["real_orders_sent"])

    def test_false_is_not_integer_zero_even_before_sqlite_binding(self):
        # SQLite itself serializes Python False as integer 0, so check the value
        # boundary directly (no fake SQLite bool storage type exists).
        self.assertEqual("NO_VERIFICADO", audit.counter_evidence(False))
        self.assertEqual("NO_VERIFICADO", audit.counter_evidence(None))
        self.assertEqual("SQLITE_INTEGER_ZERO", audit.counter_evidence(0))

    def test_missing_counter_row_table_and_database_are_unverified(self):
        # Detects a regression that translates a missing row/column/table to 0.
        for case in ("no_counter_column", "no_row", "no_table", "no_database"):
            with self.subTest(case=case):
                self.path.unlink(missing_ok=True)
                if case == "no_counter_column":
                    self._create_observer(counter_column=False)
                elif case == "no_row":
                    self._create_observer(insert_row=False)
                elif case == "no_table":
                    self._create_observer(observer_table=False)
                result = self._audit()
                self.assertEqual("RED", result["state"])
                self.assertEqual("NO_VERIFICADO", result["real_orders_evidence"])

    def test_live_mode_cannot_be_green_despite_exact_zero(self):
        # Detects counter-only GREEN for a real-order operating mode.
        self._create_observer(mode="REAL", counter=0)
        result = self._audit()
        self.assertEqual("RED", result["state"])
        self.assertEqual("SQLITE_INTEGER_ZERO", result["real_orders_evidence"])

    def test_unverified_observer_sets_main_status_red_and_nonzero_exit(self):
        # Detects a diagnostic that returns success even if observer evidence is NULL.
        self._create_observer(counter=None)
        output = StringIO()
        with patch.object(audit, "OBSERVER_DB", self.path), patch.object(
            audit, "audit_history", return_value={"state": "GREEN"}
        ), patch.object(audit, "shadow_learning", return_value={"state": "GREEN"}), redirect_stdout(output):
            exit_code = audit.main()
        report = json.loads(output.getvalue())
        self.assertEqual(2, exit_code)
        self.assertEqual("RED", report["status"])
        self.assertEqual("NO_VERIFICADO", report["observer"]["real_orders_evidence"])
        self.assertFalse(report["orders_performed"])
        self.assertFalse(report["network_calls_performed"])


if __name__ == "__main__":
    unittest.main()
