"""OFFHOST regression: COM19031 T+1/EOD must never race SQLite/report outbox.

Extracts production functions by AST to test exact R4 logic without importing
the PPI client, app runtime, secrets, Docker, or broker. No network/DB writes.
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ART = ZoneInfo("America/Argentina/Buenos_Aires")
ROOT = Path(__file__).resolve().parents[1]


def stamp(day, clock):
    return datetime.fromisoformat(f"{day}T{clock}:00").replace(tzinfo=ART)


def production_fn(filename, name, ns):
    source = ROOT.joinpath(filename).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=filename)
    node = next(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)
    code = compile(ast.Module(body=[node], type_ignores=[]), filename, "exec")
    exec(code, ns)
    return ns[name]


def clock_module():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import co_market_sessions_hf6 as sessions
    return sessions


def test_authoritative_heavy_write_clock_dates():
    sessions = clock_module()
    gate = sessions.byma_heavy_writes_safe_now
    # Source-only 06-Nov, official 09/10-Nov holidays, regular 11-Nov.
    for d in ("2026-11-06", "2026-11-11"):
        assert gate(stamp(d, "10:29"))
        for h in ("10:30", "17:00", "17:30", "18:00", "18:14"):
            assert not gate(stamp(d, h)), (d, h)
        assert gate(stamp(d, "18:15"))
    for d in ("2026-11-09", "2026-11-10"):
        assert gate(stamp(d, "17:30"))
    assert not gate(datetime(2026, 11, 11, 17, 30))  # no timezone proof


class NoSqlStore:
    def __init__(self):
        self.events = []
        self.states = []
    def event(self, *a, **kw):
        self.events.append((a, kw))
    def state(self, **kw):
        self.states.append(kw)
    def connect(self):
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, *_): return self
            def fetchone(self): return None
        return Connection()


def test_observer_catalog_history_manual_are_deferred_in_t1():
    calls, completions = [], []
    now = stamp("2026-11-11", "17:30")
    ns = {
        "TZ": ART, "datetime": datetime, "time": time,
        "OFFHOURS_RECONCILE_SECONDS": 1, "COMPLEMENTARY_RECONCILE_SECONDS": 1,
        "_business_day": lambda _: True,
        "_source_sync_due": lambda *_: True, "_history_daily_sync_due": lambda *_: True,
        "_download_catalog": lambda *_: calls.append("catalog") or 3,
        "_download_histories": lambda *_: calls.append("history") or 5,
        "_reconcile_complementary_catalog": lambda *_: calls.append("reconcile"),
        "_release_idle_catalog_heap": lambda: {},
        "_health": lambda *_a, **_kw: None,
        "_sync_state": lambda *_a, **_kw: None,
        "now_iso": lambda: now.isoformat(),
        "_finish_command": lambda *_a: completions.append(_a),
        "byma_heavy_writes_safe_now": lambda _: False,
        "json": json,
    }
    reconcile = production_fn("bf_production_paper_observer.py",
                              "_periodic_complementary_reconcile", ns)
    daily = production_fn("bf_production_paper_observer.py", "_daily_sync", ns)
    background_due = production_fn("bf_production_paper_observer.py",
                                   "_background_ingest_due", ns)
    manual = production_fn("bf_production_paper_observer.py", "_run_command", ns)
    store = NoSqlStore()
    assert reconcile(store, "CLOSED", 0.0) == 0.0
    assert daily(object(), store, now=now) is None
    assert background_due(store, now=now) is False
    reader = object()
    assert manual(store, reader, (17, "LOGIN_AND_SYNC")) is reader
    assert calls == []
    assert completions and completions[-1][1] == 17
    assert completions[-1][2] == "DEFERRED"


def test_services_defer_heavy_work_and_eod_closing_until_proven():
    calls = []
    class FakeClock:
        current = stamp("2026-11-11", "17:30")
        @classmethod
        def now(cls, tz): return cls.current
    final_proof = {"ready": False}
    ns = {
        "datetime": FakeClock, "TZ": ART, "os": __import__("os"), "sqlite3": sqlite3,
        "init_schema": lambda _: calls.append("schema"),
        "_job_due": lambda *a: True, "collect_sre": lambda _: calls.append("sre"),
        "backups_suspended": lambda: False,
        "create_backup": lambda *_a, **_kw: calls.append("backup"),
        "create_history_backup": lambda *_a, **_kw: calls.append("history_backup"),
        "refresh_financial": lambda _: calls.append("finance"),
        "refresh_news": lambda _: calls.append("news"),
        "ensure_reports": lambda *_a, **_kw: calls.append("reports"),
        "send_closing_summary": lambda _: calls.append("closing") or "EN_COLA",
        "_reports_due": lambda *_: True, "_postclose_daily_due": lambda *_: True,
        "_job": lambda *a, **k: calls.append(("job", a, k)),
        "byma_heavy_writes_safe_now": lambda at: at >= stamp("2026-11-11", "18:15"),
        "_verified_final_source_receipt": lambda *_: final_proof["ready"],
        "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD": "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD",
        "byma_eod_publication_floor": lambda _at: stamp("2026-11-11", "18:15"),
    }
    fn = production_fn("bi_operational_services.py", "service_tick", ns)
    cal = types.ModuleType("ak_byma_calendar")
    cal.es_dia_habil_operativo = lambda day: day.isoformat() == "2026-11-11"
    with patch.dict(sys.modules, {"ak_byma_calendar": cal}):
        fn(NoSqlStore(), "CLOSED")
        assert calls == ["schema", "sre"], calls
        calls.clear()
        FakeClock.current = stamp("2026-11-11", "18:14")
        fn(NoSqlStore(), "CLOSED")
        assert "reports" not in calls and "closing" not in calls, calls
        calls.clear()
        FakeClock.current = stamp("2026-11-11", "18:15")
        fn(NoSqlStore(), "CLOSED")
        assert "reports" not in calls and "closing" not in calls, calls
        assert any(isinstance(c, tuple) and
                   "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD" in str(c) for c in calls)
        calls.clear()
        final_proof["ready"] = True
        fn(NoSqlStore(), "CLOSED")
        assert "reports" in calls and "closing" in calls, calls


def test_postclose_hourly_dedupe_does_not_freeze_before_1815():
    class FakeClock(datetime):
        @classmethod
        def now(cls, _tz): return stamp("2026-11-11", "18:16")
    class Store:
        def connect(self):
            class C:
                def __enter__(self): return self
                def __exit__(self, *_): return False
                def execute(self, *_): return self
                def fetchone(self):
                    return ("VERDE", "Informe publicado", stamp("2026-11-11", "18:00").isoformat())
            return C()
    ns = {
        "datetime": FakeClock, "TZ": ART,
        "_job_due": lambda *_: True,
        "byma_heavy_writes_safe_now": lambda _: True,
        "byma_eod_publication_floor": lambda _at: stamp("2026-11-11", "18:15"),
        "date": datetime.date,
    }
    due = production_fn("bi_operational_services.py", "_postclose_daily_due", ns)
    assert due(Store(), "REPORTS") is True


def test_eod_hold_rechecks_immediately_once_real_receipt_arrives():
    proof = {"ready": False}
    class Clock(datetime):
        @classmethod
        def now(cls, _tz): return stamp("2026-11-11", "18:16")
    class Store:
        def connect(self):
            class C:
                def __enter__(self): return self
                def __exit__(self, *_): return False
                def execute(self, *_): return self
                def fetchone(self):
                    return ("HOLD", "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD",
                            stamp("2026-11-11", "18:15").isoformat())
            return C()
    ns = {
        "datetime": Clock, "TZ": ART,
        "byma_heavy_writes_safe_now": lambda _: True,
        "byma_eod_publication_floor": lambda _: stamp("2026-11-11", "18:15"),
        "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD": "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD",
        "EOD_FINAL_SOURCE_HOLD_RECHECK_SECONDS": 300,
        "_job_due": lambda *_: False,
        "_verified_final_source_receipt": lambda *_: proof["ready"],
    }
    due = production_fn("bi_operational_services.py", "_postclose_daily_due", ns)
    assert due(Store(), "CLOSING_SUMMARY_RETRY") is False
    proof["ready"] = True
    assert due(Store(), "CLOSING_SUMMARY_RETRY") is True


def test_closing_outbox_never_consumes_unique_key_before_final_receipt():
    """The first 17:xx summary would permanently freeze today's unique key."""
    current = {"at": stamp("2026-11-11", "18:14"), "final": False}
    class FakeClock(datetime):
        @classmethod
        def now(cls, _tz): return current["at"]
    class Store:
        def __init__(self):
            self.db = sqlite3.connect(":memory:")
            self.db.execute("CREATE TABLE paper_notification_outbox("
                            "event_key TEXT PRIMARY KEY,state TEXT NOT NULL)")
            self.db.execute("CREATE TABLE operational_jobs("
                            "job_key TEXT PRIMARY KEY,last_run_at TEXT,"
                            "last_success_at TEXT,state TEXT,detail TEXT)")
        def connect(self): return self.db
    store = Store()
    message_count = []
    telegram = types.ModuleType("bn_telegram_bus")
    def enqueue(c, key, category, text, at):
        message_count.append((key, category, text, at))
        c.execute("INSERT INTO paper_notification_outbox VALUES(?,?)", (key, "EN_COLA"))
        return True
    telegram.enqueue = enqueue
    byma = types.ModuleType("ak_byma_calendar")
    byma.es_dia_habil_operativo = lambda day: day.isoformat() == "2026-11-11"
    ns = {
        "datetime": FakeClock, "TZ": ART, "json": json,
        "timedelta": timedelta,
        "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD": "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD",
        "byma_heavy_writes_safe_now": lambda at: at >= stamp("2026-11-11", "18:15"),
        "byma_eod_publication_floor": lambda _at: stamp("2026-11-11", "18:15"),
        "_verified_final_source_receipt": lambda *_: current["final"],
        "init_schema": lambda *_: None,
        "_period_data": lambda *_: {"win_rate": None, "closed": [], "pnl": 0,
                                    "pnl_by_currency": {}, "cauciones_matured": [],
                                    "decisions": []},
        "now_iso": lambda: current["at"].isoformat(),
    }
    # The caller gained a real formatter and an explicitly unverified orders
    # status in A877. Extract both from production; do not stub the message.
    filename = "bi_operational_services.py"
    module = ast.parse(ROOT.joinpath(filename).read_text(encoding="utf-8"), filename)
    orders_status = next(
        node for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "REAL_ORDERS_STATUS_UNVERIFIED"
                for target in node.targets)
    )
    ns["REAL_ORDERS_STATUS_UNVERIFIED"] = ast.literal_eval(orders_status.value)
    production_fn(filename, "_closing_summary_text", ns)
    fn = production_fn(filename, "send_closing_summary", ns)
    with patch.dict(sys.modules, {"bn_telegram_bus": telegram, "ak_byma_calendar": byma}):
        assert fn(store) == "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD"  # 18:14
        current["at"] = stamp("2026-11-11", "18:15")
        assert fn(store) == "EOD_FINAL_SOURCE_NO_VERIFICADO_HOLD"  # 18:15 no receipt
        assert message_count == []
        current["final"] = True
        assert fn(store) == "EN_COLA"
        assert len(message_count) == 1
        assert message_count[0][0] == "TELEGRAM_CLOSE_2026-11-11"
        assert "Órdenes reales: NO_VERIFICADO" in message_count[0][2]
        current["at"] = stamp("2026-11-11", "18:16")
        assert fn(store) == "EN_COLA"  # existing key, no duplicate enqueue
        assert len(message_count) == 1



