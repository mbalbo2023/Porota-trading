"""A927: health green must mean PAPER mode and exact SQLite integer zero."""

from datetime import datetime, timezone
import sqlite3
import unittest
from unittest.mock import patch

import rc6_fast_functional_health as fast
import rc6_full_db_integrity as full


REAL_CONNECT = sqlite3.connect


class SreHealthPaperZeroTests(unittest.TestCase):
    """Real in-memory SQLite reads; no host, provider, or file mutation."""

    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def _database(self, counter, *, mode="PRODUCTION_PAPER", session="MARKET_CLOSED", row=True):
        db = REAL_CONNECT(":memory:")
        # BLOB affinity preserves malformed TEXT "0" and REAL 0.0 for tests.
        # SQLite INTEGER affinity normally coerces those values at insertion.
        db.execute("""CREATE TABLE observer_state (
            id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT, session_state TEXT,
            ppi_auth TEXT, real_orders_sent BLOB, heartbeat_at TEXT,
            last_market_data_at TEXT)""")
        if row:
            db.execute("INSERT INTO observer_state VALUES (1,?,?,?,?,?,?,?)", (
                mode, "RUNNING", session, "OK", counter, self.now.isoformat(),
                self.now.isoformat()))
        return db

    def _probe(self, module, counter, *, mode="PRODUCTION_PAPER",
               session="MARKET_CLOSED", row=True, defer=False):
        def db_factory(*_args, **_kwargs):
            return self._database(counter, mode=mode, session=session, row=row)
        with patch.object(module.sqlite3, "connect", side_effect=db_factory):
            if module is fast:
                return module.probe(db_path="/no-real-sqlite.db", now=self.now)
            return module.probe(db_path="/no-real-sqlite.db", defer_when_market_open=defer)

    def test_fast_green_only_for_exact_integer_zero(self):
        # Catches int(raw)==0 on SQLite TEXT "0" and REAL 0.0.
        for counter, expected in (
            (0, "GREEN"), (None, "RED"), ("0", "RED"),
            (0.0, "RED"), (1, "RED"), (-1, "RED"),
        ):
            with self.subTest(counter=counter):
                result = self._probe(fast, counter)
                self.assertEqual(expected, result["status"])
                self.assertEqual(expected == "GREEN", result["checks"]["real_orders_sent"])

    def test_fast_wrong_mode_is_red_despite_integer_zero(self):
        # Counter=0 does not establish PRODUCTION_PAPER mode.
        result = self._probe(fast, 0, mode="REAL")
        self.assertEqual("RED", result["status"])
        self.assertFalse(result["checks"]["mode"])

    def test_full_integrity_never_coerces_invalid_counter_to_zero(self):
        # Catches `int(state[0])`: the report must not rewrite TEXT/REAL to 0.
        for counter, expected in (
            (0, "GREEN"), (None, "RED"), ("0", "RED"),
            (0.0, "RED"), (1, "RED"), (-1, "RED"),
        ):
            with self.subTest(counter=counter):
                result = self._probe(full, counter)
                self.assertEqual(expected, result["status"])
                self.assertEqual(counter, result.get("real_orders_sent"))
                if expected == "GREEN":
                    self.assertEqual("ok", result["quick_check"])
                else:
                    self.assertEqual("NOT_RUN_SAFETY", result["quick_check"])

    def test_full_integrity_wrong_mode_is_red_despite_integer_zero(self):
        # Catches omission of observer mode from the full-integrity read.
        result = self._probe(full, 0, mode="REAL")
        self.assertEqual("RED", result["status"])
        self.assertEqual("REAL", result["observer_mode"])
        self.assertEqual("NOT_RUN_SAFETY", result["quick_check"])

    def test_full_integrity_missing_observer_state_is_unverified(self):
        result = self._probe(full, 0, row=False)
        self.assertEqual("RED", result["status"])
        self.assertIsNone(result.get("real_orders_sent"))

    def test_full_integrity_preserves_explicit_market_open_deferral(self):
        # Existing job contract: safety first; full quick_check can be deferred.
        accepted = self._probe(full, 0, session="MARKET_OPEN", defer=True)
        self.assertEqual("GREEN", accepted["status"])
        self.assertEqual("DEFERRED_MARKET_OPEN", accepted["quick_check"])
        unverified = self._probe(full, "0", session="MARKET_OPEN", defer=True)
        self.assertEqual("RED", unverified["status"])
        self.assertEqual("NOT_RUN_SAFETY", unverified["quick_check"])

    def test_python_boolean_false_from_adaptor_is_not_an_integer_zero(self):
        # SQLite itself stores Python False as integer 0. A synthetic row
        # adapter is required to exercise a real Python bool at this boundary.
        current_time = self.now

        class Cursor:
            def __init__(self, value):
                self.value = value

            def fetchone(self):
                return self.value

        class BoolRowConnection:
            def execute(self, sql):
                if "PRAGMA schema_version" in sql:
                    return Cursor((1,))
                if "PRAGMA quick_check" in sql:
                    return Cursor(("ok",))
                if "FROM observer_state" in sql:
                    if "mode,real_orders_sent,session_state" in sql.replace(" ", ""):
                        return Cursor(("PRODUCTION_PAPER", False, "MARKET_CLOSED"))
                    if "SELECT real_orders_sent" in sql:
                        return Cursor((False, "MARKET_CLOSED"))
                    return Cursor({
                        "mode": "PRODUCTION_PAPER", "real_orders_sent": False,
                        "heartbeat_at": current_time.isoformat(),
                    })
                return Cursor(None)

            def close(self):
                return None

        for module in (fast, full):
            with self.subTest(probe=module.__name__), patch.object(
                module.sqlite3, "connect", side_effect=lambda *_a, **_k: BoolRowConnection()
            ):
                result = (module.probe(db_path="/no-real-sqlite.db", now=self.now)
                          if module is fast else module.probe(db_path="/no-real-sqlite.db"))
                self.assertEqual("RED", result["status"])


if __name__ == "__main__":
    unittest.main()
