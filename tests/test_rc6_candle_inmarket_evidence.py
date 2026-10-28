import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import hashlib
import io
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.rc6_candle_inmarket_evidence import (
    atomic_write_json, build_summary, collect_inputs, is_fresh,
)


def _scope_store(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
          CREATE TABLE candle_worker_state(id INTEGER PRIMARY KEY,cursor INTEGER,heartbeat_at TEXT,state TEXT);
          CREATE TABLE candle_dirty(series_id TEXT,bar_start TEXT,bar_end TEXT);
          CREATE INDEX dirty_end ON candle_dirty(bar_end);
          CREATE TABLE observer_state(id INTEGER PRIMARY KEY,real_orders_sent INTEGER,session_state TEXT);
          CREATE TABLE unrelated_history(id INTEGER PRIMARY KEY,payload TEXT NOT NULL);
          INSERT INTO candle_worker_state VALUES(1,118741,'2026-09-17T14:30:00+00:00','RUNNING');
          INSERT INTO candle_dirty VALUES('x','2026-09-17T14:30:00+00:00','2026-09-17T14:31:00+00:00');
          INSERT INTO observer_state VALUES(1,0,'MARKET_CLOSED');
        """)
        conn.executemany('INSERT INTO unrelated_history(payload) VALUES(?)', [('unrelated'*16,)]*50000)


def _corrupt_root(path, table):
    with sqlite3.connect(path) as conn:
        page=conn.execute('SELECT rootpage FROM sqlite_master WHERE name=?',(table,)).fetchone()[0]
        size=conn.execute('PRAGMA page_size').fetchone()[0]
    with Path(path).open('r+b') as stream:
        stream.seek((page-1)*size+(100 if page==1 else 0))
        stream.write(b'\xff')


class CandleIntegrityScopeRegressionTests(unittest.TestCase):
    def test_only_owned_table_btrees_are_checked_and_global_scope_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'scope.db';_scope_store(db)
            original=sqlite3.connect;statements=[]
            def traced(*args,**kwargs):
                conn=original(*args,**kwargs);conn.set_trace_callback(statements.append);return conn
            before=hashlib.sha256(db.read_bytes()).hexdigest()
            with patch('scripts.rc6_candle_inmarket_evidence.sqlite3.connect',traced):
                collected=collect_inputs(db_path=str(db),observed_at='2026-09-17T14:30:30+00:00')
            checks=[v.lower().strip() for v in statements if 'quick_check' in v.lower()]
            self.assertEqual(set(checks),{"pragma quick_check('candle_dirty')","pragma quick_check('candle_worker_state')"})
            self.assertEqual(collected['table_quick_checks'],{'candle_dirty':'ok','candle_worker_state':'ok'})
            self.assertEqual(collected['quick_check'],'DELEGATED_TO_RC6_FULL_DB_INTEGRITY')
            result=build_summary(baseline_cursor=118740,observed_at='2026-09-17T14:30:30+00:00',**collected)
            self.assertEqual(result['status'],'GREEN')
            self.assertEqual(result['integrity_scope'],'CANDLE_EVIDENCE_TABLES')
            self.assertEqual(result['schema_version'],3)
            self.assertNotEqual(result['sqlite_quick_check'],'ok')
            self.assertEqual(result['current_cursor'],118741)
            self.assertEqual(result['dirty_open_bars'],1)
            self.assertEqual(result['dirty_due_bars'],0)
            self.assertEqual(before,hashlib.sha256(db.read_bytes()).hexdigest())

    def test_real_corruption_of_each_owned_btree_cannot_produce_green(self):
        for table in ('candle_dirty','candle_worker_state'):
            with self.subTest(table=table),tempfile.TemporaryDirectory() as directory:
                db=Path(directory)/'corrupt.db';_scope_store(db);_corrupt_root(db,table)
                try:
                    value=collect_inputs(db_path=str(db),observed_at='2026-09-17T14:30:30+00:00')
                except sqlite3.DatabaseError:
                    continue
                result=build_summary(baseline_cursor=118740,observed_at='2026-09-17T14:30:30+00:00',**value)
                self.assertEqual(result['status'],'RED')

    def test_unrelated_corruption_remains_the_full_integrity_owners_failure(self):
        from rc6_full_db_integrity import probe
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'unrelated.db';_scope_store(db);_corrupt_root(db,'unrelated_history')
            value=collect_inputs(db_path=str(db),observed_at='2026-09-17T14:30:30+00:00')
            result=build_summary(baseline_cursor=118740,observed_at='2026-09-17T14:30:30+00:00',**value)
            self.assertEqual(result['status'],'GREEN')
            self.assertEqual(result['sqlite_quick_check'],'DELEGATED_TO_RC6_FULL_DB_INTEGRITY')
            self.assertEqual(probe(str(db))['status'],'RED')

    def test_bad_or_incomplete_partial_results_cannot_produce_green(self):
        for values in ({},{'candle_dirty':'ok'},{'candle_dirty':'bad page','candle_worker_state':'ok'}):
            with self.subTest(values=values):
                result=build_summary(baseline_cursor=1,current_cursor=2,worker_state='RUNNING',
                    heartbeat_at='2026-09-17T14:30:00+00:00',quick_check='DELEGATED_TO_RC6_FULL_DB_INTEGRITY',
                    dirty_due_bars=0,dirty_open_bars=0,invalid_dirty_timestamp_count=0,
                    observed_at='2026-09-17T14:30:30+00:00',table_quick_checks=values)
                self.assertEqual(result['status'],'RED')

    def test_real_sqlite_progress_budget_aborts_without_source_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'budget.db';_scope_store(db)
            with sqlite3.connect(db) as conn:
                conn.executemany('INSERT INTO candle_dirty VALUES(?,?,?)',[('x','a','2026-09-17T14:31:00+00:00')]*20000)
            before=hashlib.sha256(db.read_bytes()).hexdigest()
            with self.assertRaises(sqlite3.OperationalError):
                collect_inputs(db_path=str(db),observed_at='2026-09-17T14:30:30+00:00',query_budget_seconds=.000001)
            self.assertEqual(before,hashlib.sha256(db.read_bytes()).hexdigest())

    def test_failed_producer_publishes_explicit_partial_scope_without_global_claim(self):
        from scripts.rc6_candle_inmarket_evidence import main
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'failed.db';_scope_store(db);_corrupt_root(db,'candle_dirty')
            output=Path(directory)/'evidence.json'
            argv=['candle-evidence','--db-path',str(db),'--baseline-cursor','118740',
                  '--summary-path',str(output),'--observed-at','2026-09-17T14:30:30+00:00']
            with patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
                self.assertEqual(main(),1)
            result=json.loads(output.read_text())
            self.assertEqual(result['status'],'RED')
            self.assertEqual(result['integrity_scope'],'CANDLE_EVIDENCE_TABLES')
            self.assertEqual(result['global_database_integrity_status'],'NOT_ASSERTED_BY_THIS_EVIDENCE')


class CandleInmarketEvidenceTests(unittest.TestCase):
    def summary(self, **overrides):
        values = dict(
            baseline_cursor=118740, current_cursor=118741, worker_state="RUNNING",
            heartbeat_at="2026-09-17T14:30:01+00:00", quick_check="ok",
            dirty_due_bars=0, dirty_open_bars=1, invalid_dirty_timestamp_count=0,
            observed_at="2026-09-17T14:30:02+00:00",
        )
        values.update(overrides)
        return build_summary(**values)

    def test_green_requires_cursor_running_integrity_and_no_overdue_dirty_bars(self):
        summary = self.summary()
        self.assertEqual(summary["status"], "GREEN")
        self.assertTrue(summary["checks"]["dirty_due_bars_zero"])
        self.assertEqual(summary["dirty_open_bars"], 1)

    def test_overdue_dirty_bar_prevents_green(self):
        summary = self.summary(dirty_due_bars=1)
        self.assertEqual(summary["status"], "RED")
        self.assertFalse(summary["checks"]["dirty_due_bars_zero"])

    def test_freshness_is_not_latched(self):
        summary = self.summary()
        now = datetime.fromisoformat(summary["observed_at"]) + timedelta(seconds=901)
        self.assertFalse(is_fresh(summary, now=now))
        self.assertTrue(is_fresh(summary, now=datetime.fromisoformat(summary["observed_at"])))

    def test_collect_uses_real_candle_dirty_schema_and_excludes_open_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "observer.db"
            c = sqlite3.connect(db)
            c.executescript("""
                CREATE TABLE candle_worker_state(
                  id INTEGER PRIMARY KEY, cursor INTEGER, heartbeat_at TEXT, state TEXT, detail TEXT);
                CREATE TABLE candle_dirty(series_id TEXT, bar_start TEXT, bar_end TEXT);
                INSERT INTO candle_worker_state VALUES(1,118741,'2026-09-17T14:30:00+00:00','RUNNING','ok');
                INSERT INTO candle_dirty VALUES('x','2026-09-17T14:29:00+00:00','2026-09-17T14:30:00+00:00');
                INSERT INTO candle_dirty VALUES('x','2026-09-17T14:30:00+00:00','2026-09-17T14:31:00+00:00');
            """)
            c.commit(); c.close()
            collected = collect_inputs(db_path=str(db), observed_at="2026-09-17T14:30:30+00:00")
            self.assertEqual(collected["dirty_due_bars"], 1)
            self.assertEqual(collected["dirty_open_bars"], 1)

    def test_atomic_file_has_complete_non_secret_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "status.json"
            atomic_write_json(target, self.summary())
            stored = json.loads(target.read_text())
            self.assertEqual(stored["status"], "GREEN")
            self.assertNotIn("token", json.dumps(stored).lower())


if __name__ == "__main__":
    unittest.main()
