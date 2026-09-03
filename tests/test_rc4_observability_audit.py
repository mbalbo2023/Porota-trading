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
    assert set(result["missing_tables"]) == {"history_versions_v2","history_canonical_v2"}


def test_v2_schema_is_available_and_counts_full_identity(tmp_path):
    db=tmp_path/"market_history.db"
    c=sqlite3.connect(db)
    c.execute("CREATE TABLE history_versions_v2(id INTEGER PRIMARY KEY)")
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
    assert result["by_family"]["ACCIONES"]["identities"] == 2


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
        now="2026-09-03T11:04:00-03:00",
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
        now="2026-09-03T11:04:00-03:00",
    )
    row={item["key"]:item for item in rows}["PPI_PRODUCTION_HISTORY"]
    assert row["detail"] == "canonical"
    assert row["source"] == "operational_jobs"


def test_contract_run_is_visible_with_counts_auth_duration_and_next_run():
    rows=scheduler.internal_rows([],contract_run_rows=[{
        "run_id":"r1","job_key":"PPI_CONTRACT_XHR_DYNAMIC",
        "source_class":"PPI_AUTHENTICATED_XHR",
        "started_at":"2026-09-03T11:00:00-03:00",
        "finished_at":"2026-09-03T11:00:12-03:00","state":"OK",
        "auth_state":"AUTHENTICATED","observed":100,"recorded":95,
        "changed":2,"conflicts":1,"blocked":3,"errors":0,"detail":"ok",
    }],now="2026-09-03T11:05:00-03:00")
    row={item["key"]:item for item in rows}["PPI_CONTRACT_XHR_DYNAMIC"]
    assert row["source"] == "contract_evidence_v2_runs"
    assert row["duration_seconds"] == 12
    assert row["auth_state"] == "AUTHENTICATED"
    assert row["counts"]["changed"] == 2
    assert row["counts"]["conflicts"] == 1
    assert row["ui_state"] == "GREEN"
    assert row["next_run_at"] != "DUE_NOW"


def test_missing_scheduled_job_is_gray_with_explicit_reason():
    row={item["key"]:item for item in scheduler.internal_rows([],now="2026-09-03T11:00:00-03:00")}["PPI_CONTRACT_CAUCIONES_OPEN_AUCTIONS"]
    assert row["ui_state"] == "GRAY"
    assert row["ui_reason"] == "NUNCA_EJECUTADO"
    assert row["source"] == "contract_evidence_v2_runs"


def test_overdue_evidence_is_yellow_stale_not_unexplained_gray():
    rows=scheduler.internal_rows([],contract_run_rows=[{
        "job_key":"PPI_CONTRACT_CAUCIONES_OPEN_AUCTIONS",
        "started_at":"2026-09-03T10:00:00-03:00",
        "finished_at":"2026-09-03T10:00:01-03:00","state":"OK","auth_state":"AUTHENTICATED",
    }],now="2026-09-03T10:10:00-03:00")
    row={item["key"]:item for item in rows}["PPI_CONTRACT_CAUCIONES_OPEN_AUCTIONS"]
    assert row["ui_state"] == "YELLOW"
    assert row["ui_reason"] == "STALE_EVIDENCE"


def test_news_cadence_reflects_policy(monkeypatch):
    monkeypatch.setenv("PAPER_NEWS_INGEST_ENABLED","false")
    assert scheduler._news_cadence_seconds() == 12*3600
    monkeypatch.setenv("PAPER_NEWS_INGEST_ENABLED","true")
    assert scheduler._news_cadence_seconds() == 45*60


def test_scheduler_invariants_include_rc4_contract_and_health_jobs():
    scheduler.assert_scheduler_invariants()
