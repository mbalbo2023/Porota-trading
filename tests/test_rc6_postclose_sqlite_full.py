"""A report-side SQLITE_FULL must not restart the PAPER scanner each tick."""

import sqlite3
import sys
import types
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import bi_operational_services as services


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def sqlite_error(code):
    exc = sqlite3.OperationalError("injected SQLite error")
    exc.sqlite_errorcode = code
    return exc


@pytest.fixture
def postclose(monkeypatch, tmp_path):
    store = Store(tmp_path / "paper.db")
    services.init_schema(store)
    original_due = services._job_due
    monkeypatch.setattr(services, "_job_due", lambda s, key, seconds:
                        False if key in {"SRE_SNAPSHOT", "FINANCIAL_REFRESH", "NEWS_REFRESH"}
                        else original_due(s, key, seconds))
    monkeypatch.setattr(services, "backups_suspended", lambda: True)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = datetime(2026, 10, 26, 19, 30, tzinfo=services.TZ)
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(services, "datetime", FixedDateTime)
    calendar = types.ModuleType("ak_byma_calendar")
    calendar.es_dia_habil_operativo = lambda _day: True
    with patch.dict(sys.modules, {"ak_byma_calendar": calendar}):
        yield store


def status(store, key):
    with store.connect() as conn:
        return conn.execute("SELECT state,last_run_at,last_success_at,detail "
                            "FROM operational_jobs WHERE job_key=?", (key,)).fetchone()


def allow_retry(store, key):
    older = (datetime(2026, 10, 26, 19, 30, tzinfo=services.TZ) -
             timedelta(seconds=services.REPORT_SQLITE_RETRY_SECONDS + 1)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE operational_jobs SET last_run_at=? WHERE job_key=?", (older, key))


def test_report_full_is_red_and_bounded_without_faking_summary(postclose, monkeypatch):
    store = postclose
    services._job(store, "REPORTS", "VERDE", "prior completed report", success=True)
    prior_success = status(store, "REPORTS")[2]
    with store.connect() as conn:
        conn.execute("UPDATE operational_jobs SET last_run_at=? WHERE job_key='REPORTS'",
                     (datetime(2026, 10, 26, 12, 0, tzinfo=services.TZ).isoformat(),))
    calls = {"reports": 0, "summary": 0}

    def report(s, include_today=False):
        calls["reports"] += 1
        if calls["reports"] == 1:
            raise sqlite_error(sqlite3.SQLITE_FULL)
        services._job(s, "REPORTS", "VERDE", "actual report completed", success=True)

    def summary(_store):
        calls["summary"] += 1
        return "EN_COLA"

    monkeypatch.setattr(services, "ensure_reports", report)
    monkeypatch.setattr(services, "send_closing_summary", summary)
    services.service_tick(store, "CLOSED")
    assert calls == {"reports": 1, "summary": 0}
    assert (status(store, "REPORTS")[0], status(store, "REPORTS")[3]) == (
        "ROJO", "POSTCLOSE_SQLITE_FULL")
    assert status(store, "REPORTS")[2] == prior_success
    services.service_tick(store, "CLOSED")
    assert calls == {"reports": 1, "summary": 0}
    assert status(store, "REPORTS")[0] == "ROJO"
    allow_retry(store, "REPORTS")
    services.service_tick(store, "CLOSED")
    assert calls == {"reports": 2, "summary": 1}
    assert status(store, "REPORTS")[0] == "VERDE"


def test_summary_full_has_separate_retry_without_delivery_claim(postclose, monkeypatch):
    store = postclose
    services._job(store, "REPORTS", "VERDE", "prior report", success=True)
    attempts = []

    def summary(_store):
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite_error(sqlite3.SQLITE_FULL)
        return "EN_COLA"

    monkeypatch.setattr(services, "send_closing_summary", summary)
    services.service_tick(store, "CLOSED")
    assert len(attempts) == 1
    failure = status(store, "CLOSING_SUMMARY_RETRY")
    assert failure[0] == "ROJO" and failure[2] is None
    services.service_tick(store, "CLOSED")
    assert len(attempts) == 1
    allow_retry(store, "CLOSING_SUMMARY_RETRY")
    services.service_tick(store, "CLOSED")
    assert len(attempts) == 2
    assert status(store, "CLOSING_SUMMARY_RETRY")[3] == "SUMMARY_CHECK_COMPLETED"


@pytest.mark.parametrize("code", [sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_IOERR])
def test_other_sqlite_errors_still_propagate(postclose, monkeypatch, code):
    monkeypatch.setattr(services, "ensure_reports", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(sqlite_error(code)))
    with pytest.raises(sqlite3.OperationalError):
        services.service_tick(postclose, "CLOSED")
    assert status(postclose, "REPORTS") is None


def test_full_main_db_cannot_be_silenced_by_failed_receipt(postclose, monkeypatch):
    monkeypatch.setattr(services, "ensure_reports", lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(sqlite_error(sqlite3.SQLITE_FULL)))
    original_job = services._job

    def reject_receipt(store, key, *args, **kwargs):
        if key == "REPORTS":
            raise sqlite_error(sqlite3.SQLITE_FULL)
        return original_job(store, key, *args, **kwargs)

    monkeypatch.setattr(services, "_job", reject_receipt)
    with pytest.raises(sqlite3.OperationalError):
        services.service_tick(postclose, "CLOSED")


def test_report_decision_order_keeps_legacy_offsets_without_sql_temp_sort(tmp_path):
    with sqlite3.connect(tmp_path / "decisions.db") as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE paper_decisions(id INTEGER PRIMARY KEY, "
                     "decision_key TEXT UNIQUE, decided_at TEXT NOT NULL)")
        for key, stamp in (("later", "2026-10-26T10:01:00-03:00"),
                           ("utc", "2026-10-26T13:00:00+00:00"),
                           ("first", "2026-10-26T10:00:00-03:00"),
                           ("tie", "2026-10-26T10:00:00-03:00"),
                           ("outside", "2026-10-27T00:01:00-03:00")):
            conn.execute("INSERT INTO paper_decisions(decision_key,decided_at) VALUES(?,?)", (key, stamp))
        start, end = "2026-10-26T00:00:00-03:00", "2026-10-27T00:00:00-03:00"
        old = [row["decision_key"] for row in conn.execute(
            services.DECISION_REPORT_SQL + " ORDER BY decided_at,id", (start, end))]
        new = [row["decision_key"] for row in services._period_decisions(conn, start, end)]
        assert new == old == ["first", "tie", "later", "utc"]
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " +
                                           services.DECISION_REPORT_SQL, (start, end))]
        assert not any("TEMP B-TREE" in detail for detail in plan)
