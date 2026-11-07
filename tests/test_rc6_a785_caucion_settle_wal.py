"""A785: real SQLite T+1/no-due WAL regression for the CaucionBook method.

The exact production method is loaded from its AST so the test needs no broker,
PPI, network, or Python imports from the application. The synthetic positions()
adapter mirrors the production invariant: validate every ledger row before the
status filter. Existing test_caucion_ledger_v17.py verifies the actual six
append-only receipt implementations separately.
"""

import ast
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
import unittest
from tempfile import TemporaryDirectory


SOURCE = Path(__file__).resolve().parents[1] / "bt_caucion_paper.py"


def aware_datetime(value):
    at = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return at


def production_settle_due():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "CaucionBook")
    method = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == "settle_due")
    fragment = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"aware_datetime": aware_datetime}
    exec(compile(fragment, str(SOURCE), "exec"), namespace)
    return namespace["settle_due"]


class SqliteStore:
    def __init__(self, path):
        self.path = str(path)
        self.traces = []
        with sqlite3.connect(self.path, isolation_level=None) as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE paper_cauciones(
              paper_id TEXT PRIMARY KEY, status TEXT NOT NULL,
              maturity_at TEXT NOT NULL, settled_at TEXT, valid INTEGER NOT NULL);
            CREATE TABLE paper_events(
              id INTEGER PRIMARY KEY, at TEXT, scope TEXT, event_type TEXT,
              paper_id TEXT, message TEXT);
            """)

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=0.75,
                                     isolation_level=None, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.set_trace_callback(self.traces.append)
        return connection

    def insert(self, key, *, maturity, status="OPEN", valid=True):
        with self.connect() as c:
            c.execute("INSERT INTO paper_cauciones VALUES(?,?,?,NULL,?)",
                      (key, status, maturity, int(valid)))


class ValidatingBook:
    def __init__(self, store, barrier=None):
        self.store = store
        self.barrier = barrier
        self.lock = threading.Lock()
        self.seen = set()

    def positions(self, *, status=None, connection=None):
        rows = [dict(row) for row in connection.execute(
            "SELECT paper_id,status,maturity_at,valid FROM paper_cauciones")]
        if any(not row["valid"] for row in rows):
            raise ValueError("CAUCION_LEDGER_INVALID: historical fee receipt")
        # Two contenders both observe due in the initial RO snapshot.
        if self.barrier is not None:
            identifier = threading.get_ident()
            with self.lock:
                first = identifier not in self.seen
                self.seen.add(identifier)
            if first:
                self.barrier.wait(timeout=5)
        return [row for row in rows if status is None or row["status"] == status]


class TestA785CaucionSettlementNoDueWAL(unittest.TestCase):
    AT = "2026-11-06T17:15:00-03:00"
    LATER = "2026-11-06T17:20:00-03:00"
    DUE = "2026-11-06T17:10:00-03:00"
    TOMORROW = "2026-11-07T17:10:00-03:00"

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SqliteStore(Path(self.temp.name) / "caucion.db")
        self.settle = production_settle_due()
        self.book = ValidatingBook(self.store)

    def test_no_due_never_enters_write_transaction(self):
        self.store.insert("FUTURE", maturity=self.TOMORROW)
        self.store.traces.clear()
        self.assertEqual(self.settle(self.book, self.AT), [])
        self.assertFalse(any("BEGIN IMMEDIATE" in command.upper()
                             for command in self.store.traces), self.store.traces)

    def test_no_due_during_other_writer_does_not_raise_sqlite_busy(self):
        self.store.insert("FUTURE", maturity=self.TOMORROW)
        blocker = self.store.connect()
        try:
            blocker.execute("BEGIN IMMEDIATE")
            self.assertEqual(self.settle(self.book, self.AT), [])
        finally:
            blocker.rollback()
            blocker.close()

    def test_due_matures_immediately_and_only_once(self):
        self.store.insert("DUE", maturity=self.DUE)
        self.assertEqual(self.settle(self.book, self.AT), ["DUE"])
        self.assertEqual(self.settle(self.book, self.AT), [])
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM paper_cauciones")
                             .fetchone()["status"], "MATURED")
            self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM paper_events")
                             .fetchone()["n"], 1)

    def test_aware_offset_equivalence_is_due(self):
        self.store.insert("OFFSET", maturity="2026-11-06T20:20:00+00:00")
        self.assertEqual(self.settle(self.book, self.LATER), ["OFFSET"])

    def test_invalid_historical_ledger_fails_even_without_open_due(self):
        self.store.insert("CLOSED_CORRUPT", maturity=self.DUE,
                          status="MATURED", valid=False)
        self.store.insert("FUTURE", maturity=self.TOMORROW)
        with self.assertRaisesRegex(ValueError, "CAUCION_LEDGER_INVALID"):
            self.settle(self.book, self.AT)

    def test_two_contenders_recheck_due_atomically(self):
        self.store.insert("RACE", maturity=self.DUE)
        race_book = ValidatingBook(self.store, barrier=threading.Barrier(2))
        outcomes, failures = [], []

        def worker():
            try:
                outcomes.append(self.settle(race_book, self.AT))
            except Exception as exc:  # testify exact failure in caller
                failures.append(exc)

        first = threading.Thread(target=worker)
        second = threading.Thread(target=worker)
        first.start()
        second.start()
        first.join(timeout=6)
        second.join(timeout=6)
        self.assertFalse(first.is_alive() or second.is_alive(), "deadlocked writer")
        self.assertFalse(failures, failures)
        self.assertCountEqual(outcomes, [["RACE"], []])
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM paper_events")
                             .fetchone()["n"], 1)


if __name__ == "__main__":
    unittest.main()
