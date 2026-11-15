"""A614: fund terms/tariff are visible cadences but not executable sources.

Pure offhost checks: no provider GET, browser, host, credentials, runtime DB,
Predeploy artifact or trading route is involved.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from co_contract_ingestion_policy_hf6 import CADENCES
import rc4_contract_schedule as schedule
import rc4_contract_due_job as due_job


class AfterCloseClock(datetime):
    @classmethod
    def now(cls, tz=None):
        now = datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc)
        return now if tz is None else now.astimezone(tz)


def _receipt(path: Path):
    output = io.StringIO()
    with patch.object(due_job, "DB", str(path)), patch.object(due_job, "datetime", AfterCloseClock):
        with contextlib.redirect_stdout(output):
            code = due_job.main()
    return code, json.loads(output.getvalue())


def test_all_eight_policy_cadences_are_registered_but_two_sources_unbound():
    assert set(schedule.JOB_TO_CADENCE.values()) == set(CADENCES)
    assert len(schedule.JOB_TO_CADENCE) == 8
    assert schedule.SOURCE_UNBOUND_REASONS == {
        "CONTRACT_EVIDENCE_FUND_TERMS": "FUND_TERMS_EXACT_CLASS_AUTHORITY_UNVERIFIED",
        "CONTRACT_EVIDENCE_TARIFF": "TARIFF_OFFICIAL_ACCOUNT_AUTHORITY_UNVERIFIED",
    }


def test_unbound_ttl_never_opens_session_even_in_allowed_static_window():
    for clock in (
        datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc),  # 12:00 ART
        datetime(2026, 11, 3, 21, 0, tzinfo=timezone.utc),  # 18:00 ART
        datetime(2026, 11, 7, 21, 0, tzinfo=timezone.utc),  # Saturday
    ):
        for job in schedule.SOURCE_UNBOUND_REASONS:
            assert schedule.window_allows(job, clock) is False
            assert schedule.due(None, job, clock) is False
            assert schedule.due("2026-10-01T00:00:00Z", job, clock) is False


def test_due_receipt_exposes_missing_authorities_without_dispatch(tmp_path):
    database = tmp_path / "standalone_fixture.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE contract_evidence_v2_runs(job_key TEXT, started_at TEXT)")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    code, receipt = _receipt(database)
    assert code == 0
    assert receipt["state"] == "OK"  # Successful calculation, not provider authority.
    assert receipt["source_authority_complete"] is False
    assert receipt["registered_cadences"] == 8
    assert receipt["executable_cadences"] == 6
    assert set(receipt["source_unbound_jobs"]) == set(schedule.SOURCE_UNBOUND_REASONS)
    assert set(receipt["due_jobs"]) == {
        "CONTRACT_EVIDENCE_STATIC", "CONTRACT_EVIDENCE_FULL_BROWSER"
    }
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_existing_or_stale_unbound_run_cannot_forge_provider_authority(tmp_path):
    database = tmp_path / "standalone_fixture.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE contract_evidence_v2_runs(job_key TEXT, started_at TEXT)")
        conn.executemany("INSERT INTO contract_evidence_v2_runs VALUES (?,?)", [
            (job, "2026-11-02T21:00:00Z") for job in schedule.SOURCE_UNBOUND_REASONS
        ])
    code, receipt = _receipt(database)
    assert code == 0
    assert receipt["source_authority_complete"] is False
    assert all(job not in receipt["due_jobs"] for job in schedule.SOURCE_UNBOUND_REASONS)
    assert all(receipt["last_runs"][job] is not None for job in schedule.SOURCE_UNBOUND_REASONS)
