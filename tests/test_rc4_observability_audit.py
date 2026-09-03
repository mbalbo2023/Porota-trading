"""RC4 behavioral regressions for audit-confirmed observability defects."""
from __future__ import annotations

import sqlite3

import dd_history_metrics_hf6 as metrics
import de_scheduler_catalog_hf6 as scheduler


def test_v2_file_without_v2_schema_does_not_suppress_legacy_coverage(tmp_path):
    db=tmp_path/"market_history.db"
    sqlite3.connect(db).close()
    result=metrics.v2_store_metrics(db)
    assert result["available"] is False
    assert result["reason"] == "V2_SCHEMA_NOT_PRESENT"
    assert result["identities"] == 0


def test_v2_schema_is_available_and_counts_full_identity(tmp_path):
    db=tmp_path/"market_history.db"
    c=sqlite3.connect(db)
    c.execute("""CREATE TABLE history_canonical_v2(
        symbol TEXT,instrument_type TEXT,market TEXT,settlement TEXT,date TEXT)""")
    c.executemany("INSERT INTO history_canonical_v2 VALUES(?,?,?,?,?)",[
        ("GGAL","ACCIONES","BYMA","A-24HS","2026-09-01"),
        ("GGAL","ACCIONES","BYMA","A-24HS","2026-09-02"),
        ("GGAL","ACCIONES","BYMA","INMEDIATA","2026-09-02"),
    ])
    c.commit(); c.close()
    result=metrics.v2_store_metrics(db)
    assert result["available"] is True
    assert result["canonical_rows"] == 3
    assert result["identities"] == 2


def test_scheduler_merges_operational_source_sync_and_api_health():
    rows=scheduler.internal_rows(
        [{"job_key":"SRE_SNAPSHOT","last_run_at":"2026-09-03T10:00:00-03:00",
          "last_success_at":"2026-09-03T10:00:00-03:00","state":"VERDE","detail":"ok"}],
        source_sync_rows=[
            {"source":"PPI_PRODUCTION_HISTORY","last_attempt_at":"2026-09-03T11:03:14-03:00",
             "last_success_at":"2026-09-03T11:03:14-03:00","status":"VERDE","detail":"67"}],
        api_health_rows=[
            {"component":"PAPER_SIGNAL_ROTATION","checked_at":"2026-09-03T10:55:00-03:00",
             "last_success_at":"2026-09-03T10:55:00-03:00","state":"VERDE","detail":"rotating"}],
    )
    by={row["key"]:row for row in rows}
    assert by["SRE_SNAPSHOT"]["source"] == "operational_jobs"
    assert by["PPI_PRODUCTION_HISTORY"]["source"] == "source_sync"
    assert by["PPI_PRODUCTION_HISTORY"]["state"] == "VERDE"
    assert by["PAPER_SIGNAL_ROTATION"]["source"] == "api_health"
    assert by["PAPER_SIGNAL_ROTATION"]["state"] == "VERDE"
    assert by["PAPER_SIGNAL_ROTATION"]["next_run_at"] != "DUE_NOW"


def test_operational_jobs_wins_when_same_job_exists_in_auxiliary_store():
    rows=scheduler.internal_rows(
        [{"job_key":"PPI_PRODUCTION_HISTORY","last_run_at":"2026-09-03T09:00:00-03:00",
          "last_success_at":"2026-09-03T09:00:00-03:00","state":"VERDE","detail":"canonical"}],
        source_sync_rows=[
            {"source":"PPI_PRODUCTION_HISTORY","last_attempt_at":"2026-09-03T11:03:14-03:00",
             "last_success_at":"2026-09-03T11:03:14-03:00","status":"AMARILLO","detail":"aux"}],
    )
    row={item["key"]:item for item in rows}["PPI_PRODUCTION_HISTORY"]
    assert row["detail"] == "canonical"
    assert row["source"] == "operational_jobs"


def test_news_cadence_reflects_policy(monkeypatch):
    monkeypatch.setenv("PAPER_NEWS_INGEST_ENABLED","false")
    assert scheduler._news_cadence_seconds() == 12*3600
    monkeypatch.setenv("PAPER_NEWS_INGEST_ENABLED","true")
    assert scheduler._news_cadence_seconds() == 45*60
