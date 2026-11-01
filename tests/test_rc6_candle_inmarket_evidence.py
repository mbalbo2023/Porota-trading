import json
import os
import sqlite3
import shutil
import tempfile
import unittest
from unittest.mock import patch
import hashlib
import io
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.rc6_candle_inmarket_evidence import (
    atomic_write_json, build_summary, collect_inputs, is_fresh,
    main, previous_session_baseline, WORKER_IDENTITY,
)


def _scope_store(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
          CREATE TABLE candle_worker_state(id INTEGER PRIMARY KEY,cursor INTEGER,heartbeat_at TEXT,state TEXT);
          CREATE TABLE candle_dirty(series_id TEXT,bar_start TEXT,bar_end TEXT);
          CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY);
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
            result=build_summary(baseline_cursor=118740,baseline_reason='SAME_SESSION_PREVIOUS_SAMPLE',observed_at='2026-09-17T14:30:30+00:00',**collected)
            self.assertEqual(result['status'],'GREEN')
            self.assertEqual(result['integrity_scope'],'CANDLE_EVIDENCE_TABLES')
            self.assertEqual(result['schema_version'],4)
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
                result=build_summary(baseline_cursor=118740,baseline_reason='SAME_SESSION_PREVIOUS_SAMPLE',observed_at='2026-09-17T14:30:30+00:00',**value)
                self.assertEqual(result['status'],'RED')

    def test_unrelated_corruption_remains_the_full_integrity_owners_failure(self):
        from rc6_full_db_integrity import probe
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'unrelated.db';_scope_store(db);_corrupt_root(db,'unrelated_history')
            value=collect_inputs(db_path=str(db),observed_at='2026-09-17T14:30:30+00:00')
            result=build_summary(baseline_cursor=118740,baseline_reason='SAME_SESSION_PREVIOUS_SAMPLE',observed_at='2026-09-17T14:30:30+00:00',**value)
            self.assertEqual(result['status'],'GREEN')
            self.assertEqual(result['sqlite_quick_check'],'DELEGATED_TO_RC6_FULL_DB_INTEGRITY')
            self.assertEqual(probe(str(db))['status'],'RED')

    def test_bad_or_incomplete_partial_results_cannot_produce_green(self):
        for values in ({},{'candle_dirty':'ok'},{'candle_dirty':'bad page','candle_worker_state':'ok'}):
            with self.subTest(values=values):
                result=build_summary(baseline_cursor=1,current_cursor=2,worker_state='RUNNING',
                    heartbeat_at='2026-09-17T14:30:00+00:00',quick_check='DELEGATED_TO_RC6_FULL_DB_INTEGRITY',
                    dirty_due_bars=0,dirty_open_bars=0,invalid_dirty_timestamp_count=0,
                    observed_at='2026-09-17T14:30:30+00:00',table_quick_checks=values,
                    baseline_reason='SAME_SESSION_PREVIOUS_SAMPLE',latest_snapshot_cursor=2)
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
            baseline_reason="SAME_SESSION_PREVIOUS_SAMPLE", latest_snapshot_cursor=118741,
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
                CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY);
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


class CandleMondaySessionRegressionTests(unittest.TestCase):
    """Virtual Monday ticks; no provider, timer, host, order or network call."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="rc6-candle-monday-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = self.root / "observer.db"
        self.summary = self.root / "candle_inmarket_latest.json"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
              CREATE TABLE candle_worker_state(
                id INTEGER PRIMARY KEY, cursor INTEGER, heartbeat_at TEXT,
                state TEXT, detail TEXT);
              CREATE TABLE candle_dirty(series_id TEXT,bar_start TEXT,bar_end TEXT);
              CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY);
              INSERT INTO candle_worker_state
                VALUES(1,118741,'2026-11-02T13:39:40+00:00','RUNNING','ok');
              INSERT INTO market_snapshots VALUES(118741);
            """)

    def update(self, *, cursor=118741, heartbeat="2026-11-02T13:39:40+00:00",
               snapshot=None, state="RUNNING"):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE candle_worker_state SET cursor=?,heartbeat_at=?,state=? WHERE id=1",
                         (cursor, heartbeat, state))
            if snapshot is not None:
                conn.execute("INSERT INTO market_snapshots(id) VALUES(?)", (snapshot,))

    def sample(self, at, *, legacy=None):
        argv = ["candle-evidence", "--db-path", str(self.db),
                "--summary-path", str(self.summary), "--observed-at", at]
        if legacy is not None:
            argv += ["--baseline-cursor", str(legacy)]
        with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
            code = main()
        record = json.loads(self.summary.read_text())
        self.assertEqual(record["worker_identity"], WORKER_IDENTITY)
        self.assertEqual(record["orders"], "NOT_CALLED")
        self.assertEqual(record["data_mutation"], "EVIDENCE_JSON_ONLY")
        return code, record

    def test_monday_first_sample_cannot_reuse_september_static_baseline(self):
        code, first = self.sample("2026-11-02T13:40:00+00:00", legacy=0)
        self.assertEqual(code, 0)
        self.assertEqual(first["status"], "NO_VERIFICADO")
        self.assertEqual(first["baseline_reason"], "FIRST_SESSION_SAMPLE")
        self.assertIsNone(first["baseline_cursor"])
        self.assertEqual(first["session_date"], "2026-11-02")

    def test_idle_source_is_waiting_not_green_or_false_red_then_advance_green(self):
        self.sample("2026-11-02T13:40:00+00:00")
        self.update(heartbeat="2026-11-02T13:49:45+00:00")
        code, idle = self.sample("2026-11-02T13:50:00+00:00")
        self.assertEqual(code, 0)
        self.assertEqual(idle["status"], "WAITING_SOURCE")
        self.assertFalse(idle["checks"]["cursor_strictly_advanced"])
        self.assertTrue(idle["checks"]["market_snapshots_caught_up"])
        self.update(cursor=118742, snapshot=118742,
                    heartbeat="2026-11-02T13:59:40+00:00")
        code, advanced = self.sample("2026-11-02T14:00:00+00:00")
        self.assertEqual(code, 0)
        self.assertEqual(advanced["status"], "GREEN")
        self.assertEqual(advanced["baseline_cursor"], 118741)
        self.assertTrue(advanced["checks"]["cursor_strictly_advanced"])

    def test_stale_running_heartbeat_cannot_fake_green(self):
        self.sample("2026-11-02T13:40:00+00:00")
        self.update(cursor=118742, snapshot=118742,
                    heartbeat="2026-11-02T13:39:40+00:00")
        code, stale = self.sample("2026-11-02T13:50:00+00:00")
        self.assertEqual(code, 1)
        self.assertEqual(stale["status"], "RED")
        self.assertFalse(stale["checks"]["worker_heartbeat_fresh"])
        self.assertTrue(stale["checks"]["cursor_strictly_advanced"])

    def test_pending_source_snapshot_is_red_even_if_cursor_idles(self):
        self.sample("2026-11-02T13:40:00+00:00")
        self.update(snapshot=118742, heartbeat="2026-11-02T13:49:50+00:00")
        code, backlog = self.sample("2026-11-02T13:50:00+00:00")
        self.assertEqual(code, 1)
        self.assertEqual(backlog["status"], "RED")
        self.assertFalse(backlog["checks"]["market_snapshots_caught_up"])

    def test_worker_regression_future_clock_and_nonrunning_fail_closed(self):
        self.sample("2026-11-02T13:40:00+00:00")
        self.update(cursor=118740, heartbeat="2026-11-02T13:49:50+00:00")
        _, regressed = self.sample("2026-11-02T13:50:00+00:00")
        self.assertEqual(regressed["status"], "RED")
        self.assertFalse(regressed["checks"]["cursor_not_regressed"])
        self.update(cursor=118742, heartbeat="2026-11-02T14:00:01+00:00",
                    state="STOPPED")
        _, bad_state = self.sample("2026-11-02T14:00:00+00:00")
        self.assertEqual(bad_state["status"], "RED")
        self.assertFalse(bad_state["checks"]["worker_heartbeat_fresh"])
        self.assertFalse(bad_state["checks"]["candle_worker_running"])

    def test_db_inode_change_invalidates_previous_session_baseline(self):
        self.sample("2026-11-02T13:40:00+00:00")
        new_db = self.root / "replacement.db"
        shutil.copy2(self.db, new_db)
        os.replace(new_db, self.db)
        self.update(cursor=118742, snapshot=118742,
                    heartbeat="2026-11-02T13:49:45+00:00")
        _, replaced = self.sample("2026-11-02T13:50:00+00:00")
        self.assertEqual(replaced["status"], "NO_VERIFICADO")
        self.assertEqual(replaced["baseline_reason"], "PREVIOUS_EVIDENCE_NOT_CONTIGUOUS")

    def test_friday_or_local_sunday_baseline_cannot_admit_monday(self):
        for prior_at in ("2026-10-30T19:50:00+00:00",
                         "2026-11-02T02:50:00+00:00"):
            _, prior = self.sample("2026-11-02T13:40:00+00:00")
            prior["observed_at"] = prior_at
            prior["session_date"] = (datetime.fromisoformat(prior_at)
                                      .astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
                                      .date().isoformat())
            atomic_write_json(self.summary, prior)
            baseline, reason = previous_session_baseline(
                path=self.summary, observed_at="2026-11-02T13:50:00+00:00",
                db_identity=prior["db_identity"])
            self.assertIsNone(baseline)
            self.assertEqual(reason, "PREVIOUS_EVIDENCE_NOT_CONTIGUOUS")

    def test_invalid_prior_identity_and_legacy_schema_never_go_green(self):
        _, first = self.sample("2026-11-02T13:40:00+00:00")
        for change in ({"worker_identity": "unexpected-worker"},
                       {"schema_version": 3}, {"db_identity": "99:1"}):
            atomic_write_json(self.summary, {**first, **change})
            self.update(cursor=118742, heartbeat="2026-11-02T13:49:50+00:00")
            _, value = self.sample("2026-11-02T13:50:00+00:00")
            self.assertEqual(value["status"], "NO_VERIFICADO")

    def test_timer_and_holiday_execcondition_are_source_gated(self):
        from scripts import rc6_source_cycle_dispatch as dispatch
        art = ZoneInfo("America/Argentina/Buenos_Aires")
        monday = datetime(2026, 11, 2, 10, 40, tzinfo=art)
        self.assertTrue(dispatch.phase_status(monday, "open")[0])
        self.assertFalse(dispatch.phase_status(monday.replace(hour=17, minute=10), "open")[0])
        self.assertTrue(dispatch.phase_status(monday.replace(hour=17, minute=10), "late_source")[0])
        self.assertFalse(dispatch.phase_status(datetime(2026, 11, 9, 10, 40, tzinfo=art), "open")[0])
        self.assertFalse(dispatch.phase_status(datetime(2026, 11, 10, 10, 40, tzinfo=art), "open")[0])
        base = Path(__file__).resolve().parents[1]
        timer = (base / "ops/systemd/porota-rc6-candle-inmarket-verify.timer").read_text()
        service = (base / "ops/systemd/porota-rc6-candle-inmarket-verify.service").read_text()
        self.assertIn("10:40,50:00", timer)
        self.assertIn("11..16:00/10:00", timer)
        self.assertIn("--check-phase open", service)

    def test_timer_wrapper_has_no_historical_static_cursor(self):
        wrapper = (Path(__file__).resolve().parents[1] /
                   "ops/porota-rc6-candle-inmarket-verify").read_text()
        self.assertNotIn("BASELINE_FILE", wrapper)
        self.assertNotIn("--baseline-cursor", wrapper)
        self.assertIn("--summary-path", wrapper)


if __name__ == "__main__":
    unittest.main()
