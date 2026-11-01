"""A325: no false final EOD at 18:08; bounded read-only re-publication."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from scripts import rc6_source_cycle_dispatch as dispatch
import rc6_snapshot as snapshot
import rc6_postclose_review as review
import rc6_decision_evidence_report as decisions
from rc6_postclose_source_contract import (PENDING,VERIFIED,postclose_eod_receipt)


ART = ZoneInfo("America/Argentina/Buenos_Aires")
ROOT = Path(__file__).resolve().parents[1]


def clock(text):
    return datetime.fromisoformat(text).astimezone(ART)


def paper_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE source_sync(source TEXT PRIMARY KEY,status TEXT,
            last_attempt_at TEXT,last_success_at TEXT,items INT,detail TEXT)""")


def source_row(db_path, *, status="VERDE", received="2026-11-02T18:45:00-03:00",
               detail="session=2026-11-02; source_session_verified=2026-11-02:1/1; readiness=NONE"):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""INSERT OR REPLACE INTO source_sync VALUES(
            'PPI_PRODUCTION_HISTORY',?,?,?,?,?)""",
            (status,received,received,1,detail))


@pytest.mark.parametrize("instant,expected", [
    ("2026-10-30T17:08:00-03:00", True),
    ("2026-10-30T18:20:00-03:00", False),
    ("2026-11-02T17:08:00-03:00", False),
    ("2026-11-02T18:07:59-03:00", False),
    ("2026-11-02T18:08:00-03:00", True),
    ("2026-11-02T18:14:59-03:00", True),
    ("2026-11-02T18:20:00-03:00", True),
    ("2026-11-02T18:50:00-03:00", True),
    ("2026-11-02T22:20:00-03:00", True),
    ("2026-11-02T22:30:00-03:00", False),
    ("2026-11-07T18:20:00-03:00", False),
])
def test_snapshot_phases_preserve_legacy_and_bound_new_retries(instant,expected):
    assert dispatch.phase_status(clock(instant),"postclose_snapshot")[0] is expected


def test_existing_timer_only_adds_bounded_retried_projections():
    timer = (ROOT / "systemd/porota-snapshot-postclose-rc6.timer").read_text()
    service = (ROOT / "systemd/porota-snapshot-postclose-rc6.service").read_text()
    assert "17:08:00 America/Argentina/Buenos_Aires" in timer
    assert "18:08:00 America/Argentina/Buenos_Aires" in timer
    assert "18..21:20,50:00 America/Argentina/Buenos_Aires" in timer
    assert "22:20:00 America/Argentina/Buenos_Aires" in timer
    assert "postclose_snapshot" in service
    for name in ("rc6_snapshot.py --phase postclose", "rc6_postclose_review.py",
                 "rc6_decision_evidence_report.py"):
        assert name in service
    assert "porota_production_dashboard" in service
    assert "porota_production_observer" not in service


def test_broker_requested_day_and_zero_verified_rows_are_never_eod(tmp_path):
    database = tmp_path / "paper.db"
    paper_db(database)
    before = clock("2026-11-02T18:08:00-03:00")
    assert postclose_eod_receipt(before,db_path=database)["reason"] == "BYMA_EOD_PUBLICATION_FLOOR_NOT_REACHED"
    at_1845 = clock("2026-11-02T18:45:00-03:00")
    assert postclose_eod_receipt(at_1845,db_path=database)["state"] == PENDING
    source_row(database,detail="session=2026-11-02; readiness=NONE")
    assert postclose_eod_receipt(at_1845,db_path=database)["reason"] == "PPI_EOD_EXACT_SESSION_NOT_ATTESTED"
    source_row(database,detail="session=2026-11-02; source_session_verified=2026-11-02:0/1")
    assert postclose_eod_receipt(at_1845,db_path=database)["state"] == PENDING
    source_row(database,detail="session=2026-11-02; source_session_verified=2026-11-02:1/2")
    assert postclose_eod_receipt(at_1845,db_path=database)["state"] == PENDING
    source_row(database,received="2026-11-02T18:14:59-03:00")
    assert postclose_eod_receipt(at_1845,db_path=database)["state"] == PENDING
    source_row(database,received="2026-11-02T18:46:00-03:00")
    assert postclose_eod_receipt(at_1845,db_path=database)["state"] == PENDING
    source_row(database,status="AMARILLO")
    assert postclose_eod_receipt(at_1845,db_path=database)["state"] == PENDING
    source_row(database)
    found = postclose_eod_receipt(at_1845,db_path=database)
    assert (found["state"],found["verified_exact_keys"],found["required_exact_keys"]) == (VERIFIED,1,1)
    assert postclose_eod_receipt(clock("2026-11-07T18:45:00-03:00"),
                                 db_path=database)["reason"] == "NON_TRADING_DAY"


def api_payload(orders=0):
    return {"state":{"mode":"PRODUCTION_PAPER","real_orders_sent":orders},
            "closed":[],"decisions":[]}


def test_snapshot_review_paper_safety_is_separate_from_eod_source(tmp_path):
    db_path = tmp_path / "paper.db"
    paper_db(db_path)
    at_1808 = clock("2026-11-02T18:08:00-03:00")
    preliminary = snapshot.build_snapshot("postclose",at_1808,api_payload(),
                      eod_source=postclose_eod_receipt(at_1808,db_path=db_path))
    assert preliminary["status"] == PENDING
    assert preliminary["paper_safety_status"] == "VERIFIED"
    assert preliminary["real_orders_sent"] == 0
    assert preliminary["urgent_alerts"] == []
    initial_review = review.build_review(preliminary,at_1808)
    assert initial_review["status"] == PENDING
    assert "PPI_EOD_CURRENT_SESSION_SOURCE_PENDING" in initial_review["findings"]
    at_1845 = clock("2026-11-02T18:45:00-03:00")
    source_row(db_path)
    certified = snapshot.build_snapshot("postclose",at_1845,api_payload(),
                   eod_source=postclose_eod_receipt(at_1845,db_path=db_path))
    assert certified["status"] == "VERIFIED"
    assert certified["postclose_eod_source"]["state"] == VERIFIED
    assert review.build_review(certified,at_1845)["status"] == "VERIFIED"
    unsafe = snapshot.build_snapshot("postclose",at_1845,api_payload(orders=1),
                                    eod_source=certified["postclose_eod_source"])
    assert unsafe["status"] == "INSUFFICIENT_EVIDENCE"
    assert unsafe["paper_safety_status"] == "INSUFFICIENT_EVIDENCE"
    assert review.build_review(unsafe,at_1845)["status"] == "INSUFFICIENT_EVIDENCE"


def test_decision_report_cannot_outvote_earlier_provisional_snapshot(tmp_path):
    db_path = tmp_path / "paper.db"
    paper_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE decision_evidence_snapshots(
           decision_key TEXT,captured_at TEXT,payload_sha256 TEXT,payload_json TEXT)""")
        conn.execute("""CREATE TABLE paper_positions(
           paper_id TEXT,status TEXT,net_pnl TEXT,closed_at TEXT)""")
    snap_file = tmp_path / "snapshot.json"
    at_1808 = clock("2026-11-02T18:08:00-03:00")
    provisional = snapshot.build_snapshot("postclose",at_1808,api_payload(),
                     eod_source=postclose_eod_receipt(at_1808,db_path=db_path))
    snap_file.write_text(json.dumps(provisional),encoding="utf-8")
    at_1845 = clock("2026-11-02T18:45:00-03:00")
    source_row(db_path)
    output = decisions.publish(db_path=db_path,postclose=True,source_at=at_1845,
                              root=tmp_path,snapshot_path=snap_file)
    assert output["postclose_report_status"] == PENDING
    assert output["postclose_eod_source"]["state"] == VERIFIED
    certified = snapshot.build_snapshot("postclose",at_1845,api_payload(),
                  eod_source=postclose_eod_receipt(at_1845,db_path=db_path))
    snap_file.write_text(json.dumps(certified),encoding="utf-8")
    updated = decisions.publish(db_path=db_path,postclose=True,source_at=at_1845,
                                root=tmp_path,snapshot_path=snap_file)
    assert updated["postclose_report_status"] == "VERIFIED"
    compact = json.loads((tmp_path / decisions.COUNTERFACTUAL_NAME).read_text())
    assert compact["postclose_report_status"] == "VERIFIED"
    assert compact["real_orders_authorized"] is False
