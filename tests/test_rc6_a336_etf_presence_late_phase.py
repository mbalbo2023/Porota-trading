"""A336/A337: exact ETF presence receipt and BYMA source-only audit phase.

All identities are synthetic SQLite rows. No broker credentials, orders, host
mutations, trade admission or deployment are involved.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from scripts import rc6_zero_known_error_runtime_audit as audit


def _report(*, catalog=(), candidates=(), incomplete_catalog=False):
    """Build the same raw grouped source, with optional unverified catalogue."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    try:
        db.execute("""CREATE TABLE candidate_identity_v2(
            ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
            settlement TEXT, can_simulate INTEGER, status TEXT,
            PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
        db.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?)",
                       candidates)
        if catalog is not None:
            if incomplete_catalog:
                db.execute("""CREATE TABLE financial_instrument_catalog(
                    ticker TEXT, instrument_type TEXT, market TEXT,
                    currency TEXT, status TEXT, capability TEXT)""")
            else:
                db.execute("""CREATE TABLE financial_instrument_catalog(
                    ticker TEXT, instrument_type TEXT, market TEXT,
                    currency TEXT, settlement TEXT, status TEXT, capability TEXT,
                    PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
                db.executemany(
                    "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                    catalog)
        grouped = [dict(row) for row in db.execute("""
            SELECT upper(instrument_type) family,
              SUM(CASE WHEN can_simulate=1 AND upper(status)='AVAILABLE'
                       THEN 1 ELSE 0 END) ready,
              COUNT(*) total
            FROM candidate_identity_v2 GROUP BY upper(instrument_type)
            ORDER BY upper(instrument_type)""")]
        original = [dict(row) for row in grouped]
        original_candidate_rows = db.execute(
            "SELECT COUNT(*), SUM(can_simulate) FROM candidate_identity_v2"
        ).fetchone()
        receipt = audit._etf_raw_presence_projection(db, grouped)
        assert grouped == original, "Report-only guard must not mutate by_family"
        assert tuple(original_candidate_rows) == tuple(db.execute(
            "SELECT COUNT(*), SUM(can_simulate) FROM candidate_identity_v2").fetchone())
        assert receipt["ready_or_admission_modified"] is False
        assert receipt["report_only"] is True
        assert receipt["aliases_inferred"] is False
        return receipt, {row["family"]: row["ready"] for row in grouped}
    finally:
        db.close()


def _candidate(ticker, family="ETFS", *, ready=0, currency="ARS",
               settlement="A-24HS", market="BYMA"):
    return (ticker, family, market, currency, settlement, int(ready),
            "AVAILABLE" if ready else "PAUSED_EXPLICIT")


def _catalog(ticker, family="ETFS", *, ready=True, currency="ARS",
             settlement="A-24HS", market="BYMA"):
    return (ticker, family, market, currency, settlement, "AVAILABLE",
            "READY_PAPER_SPOT" if ready else "NEEDS_CONTRACT")


def test_absent_candidate_group_is_unknown_not_zero_even_if_catalog_empty():
    receipt, by_family = _report(catalog=(), candidates=())
    assert by_family == {}
    assert receipt["catalog_schema_verified"] is True
    for family in ("ETF", "ETFS"):
        row = receipt["families"][family]
        assert row["presence_state"] == "BOTH_RAW_FAMILY_GROUPS_ABSENT"
        assert row["candidate_group_present"] is False
        assert row["candidate_ready"] is None
        assert row["catalog_group_present"] is False
        assert row["catalog_total"] is None
    assert receipt["identity_key"] == "ticker+raw_family+market+currency+settlement"


def test_catalog_has_etfs_but_candidate_group_missing_is_not_ready_zero():
    receipt, by_family = _report(catalog=[_catalog("SYNTHETF")])
    assert "ETFS" not in by_family
    row = receipt["families"]["ETFS"]
    assert row["presence_state"] == "CATALOG_PRESENT_CANDIDATE_GROUP_ABSENT"
    assert row["catalog_total"] == 1 and row["catalog_annotated_ready"] == 1
    assert row["candidate_total"] is None and row["candidate_ready"] is None
    assert row["catalog_without_exact_candidate"] == 1


def test_catalog_and_candidate_identity_matching_paused_is_verified_zero():
    receipt, by_family = _report(
        catalog=[_catalog("SYNTHETF")], candidates=[_candidate("SYNTHETF")])
    assert by_family == {"ETFS": 0}
    row = receipt["families"]["ETFS"]
    assert row["presence_state"] == "CANDIDATE_GROUP_PRESENT_ZERO_READY"
    assert row["candidate_group_present"] is True
    assert row["candidate_ready"] == 0
    assert row["exact_matched_catalog_rows"] == 1
    assert row["exact_matched_candidate_rows"] == 1
    assert row["catalog_without_exact_candidate"] == 0
    assert row["candidate_without_exact_catalog"] == 0


def test_etf_alias_is_not_inferred_from_etfs_even_for_same_ticker():
    receipt, by_family = _report(
        catalog=[_catalog("SYNTHETF", "ETFS")],
        candidates=[_candidate("SYNTHETF", "ETF", ready=1)])
    assert by_family == {"ETF": 1}
    assert receipt["families"]["ETFS"]["candidate_ready"] is None
    assert receipt["families"]["ETFS"]["catalog_without_exact_candidate"] == 1
    assert receipt["families"]["ETF"]["candidate_without_exact_catalog"] == 1
    assert receipt["families"]["ETF"]["exact_matched_candidate_rows"] == 0


@pytest.mark.parametrize("difference", ("market", "currency", "settlement"))
def test_full_five_field_identity_required_not_ticker_only(difference):
    kw = {difference: {"market": "A3", "currency": "USD", "settlement": "INMEDIATA"}[difference]}
    receipt, by_family = _report(
        catalog=[_catalog("SYNTHETF")],
        candidates=[_candidate("SYNTHETF", ready=1, **kw)])
    row = receipt["families"]["ETFS"]
    assert by_family == {"ETFS": 1}, "Candidate READY remains report-only/unmodified"
    assert row["exact_matched_candidate_rows"] == 0
    assert row["exact_matched_catalog_rows"] == 0
    assert row["catalog_without_exact_candidate"] == 1
    assert row["candidate_without_exact_catalog"] == 1


def test_exact_etf_ready_present_without_creating_etfs_alias():
    receipt, by_family = _report(
        catalog=[_catalog("SYNTHETF", "ETF")],
        candidates=[_candidate("SYNTHETF", "ETF", ready=1)])
    assert by_family == {"ETF": 1}
    etf = receipt["families"]["ETF"]
    assert etf["presence_state"] == "CANDIDATE_GROUP_PRESENT_WITH_READY"
    assert etf["exact_matched_candidate_rows"] == 1
    assert receipt["families"]["ETFS"]["candidate_ready"] is None


@pytest.mark.parametrize("schema", ("absent", "incomplete"))
def test_catalog_absence_or_unverified_schema_reports_unknown_not_exception(schema):
    receipt, by_family = _report(
        catalog=None if schema == "absent" else (),
        candidates=[_candidate("SYNTHETF")],
        incomplete_catalog=schema == "incomplete")
    assert by_family == {"ETFS": 0}
    assert receipt["catalog_schema_verified"] is False
    assert receipt["catalog_missing_columns"]
    row = receipt["families"]["ETFS"]
    assert row["presence_state"] == "CATALOG_TABLE_OR_SCHEMA_UNVERIFIED"
    assert row["catalog_group_present"] is None
    assert row["candidate_ready"] == 0


def _observer(at, **changes):
    state = {
        "process_state": "WAITING_MARKET",
        "session_state": "EQUITY_T1_READONLY",
        "verified_late_readonly_detail": True,
        "heartbeat_at": at,
    }
    return dict(state, **changes)


@pytest.mark.parametrize("when,accepted", (
    ("2026-11-02T19:59:59+00:00", False),  # 16:59 ART remains PAPER normal
    ("2026-11-02T20:00:00+00:00", True),   # 17:00 ART source-only
    ("2026-11-02T20:30:00+00:00", True),   # 17:30 ART source-only
    ("2026-11-02T20:59:59+00:00", True),   # 17:59:59 ART source-only
    ("2026-11-02T21:00:00+00:00", False),  # 18:00 ART closed
    ("2026-10-30T20:30:00+00:00", False),  # pre-COM19031
    ("2026-11-07T20:30:00+00:00", False),  # Saturday
))
def test_readonly_phase_only_inside_official_equity_t1_window(when, accepted):
    assert audit._verified_equity_t1_readonly_phase(
        _observer(when), now=datetime.fromisoformat(when)) is accepted


def test_stale_before_close_heartbeat_cannot_mark_after_close_source_phase():
    assert not audit._verified_equity_t1_readonly_phase(
        _observer("2026-11-02T20:59:00+00:00"),
        now=datetime.fromisoformat("2026-11-02T21:01:00+00:00"))


@pytest.mark.parametrize("changes", (
    {"process_state": "RUNNING"},
    {"session_state": "MARKET_OPEN"},
    {"session_state": "MARKET_CLOSED"},
    {"verified_late_readonly_detail": False},
    {"verified_late_readonly_detail": "True"},
    {"heartbeat_at": "2026-11-02T17:30:00"},  # untrusted naive stamp
))
def test_late_phase_bad_identity_detail_process_or_timestamp_fails_closed(changes):
    assert not audit._verified_equity_t1_readonly_phase(
        _observer("2026-11-02T20:30:00+00:00", **changes),
        now=datetime.fromisoformat("2026-11-02T20:30:00+00:00"))


def test_only_exact_a316_observer_detail_is_trusted_no_prefix_ambiguity():
    exact = ("PAPER entrada/salida cerrado; BYMA T+1 17:00-18:00 "
             "PPI read-only/full-key, sin EOD ni órdenes.")
    assert audit._verified_late_observer_detail(exact)
    assert not audit._verified_late_observer_detail(exact + " UNKNOWN")
    assert not audit._verified_late_observer_detail(exact[:-1])
    assert not audit._verified_late_observer_detail(None)


def _existing_snapshot_fixture():
    # Reuse the existing complete audit fixture; phase is the only new
    # acceptance path. Pin to a file in this repo; no external test package.
    path = Path(__file__).with_name("test_ws_integ_03_runtime_audit.py")
    spec = importlib.util.spec_from_file_location("a336_existing_audit_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._snapshot()


def _freeze_audit_at_nov_two(monkeypatch):
    class NovTwoDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            at = datetime.fromisoformat("2026-11-02T20:30:00+00:00")
            return at.astimezone(tz) if tz is not None else at

    monkeypatch.setattr(audit, "datetime", NovTwoDateTime)


def test_a337_entire_runtime_audit_accepts_source_only_without_opening_paper(monkeypatch):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    # Snapshot sampled in a simulated future session, not the test wall clock.
    monkeypatch.setattr(audit, "_age_seconds", lambda _stamp: 0)
    _freeze_audit_at_nov_two(monkeypatch)
    snapshot = _existing_snapshot_fixture()
    snapshot["database"]["observer"].update(_observer("2026-11-02T20:30:00+00:00"))
    assert snapshot["database"]["observer"]["process_state"] == "WAITING_MARKET"
    assert snapshot["dashboard_truth"]["execution"] == "SIMULATED"
    audit._assert_snapshot(snapshot)


@pytest.mark.parametrize("change", (
    {"real_orders_sent": 1},
    {"verified_late_readonly_detail": False},
    {"session_state": "MARKET_OPEN"},
    {"process_state": "RUNNING"},
    {"heartbeat_at": "2026-11-02T21:00:00+00:00"},
))
def test_a337_late_phase_never_masks_real_order_or_false_source(monkeypatch, change):
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    monkeypatch.setattr(audit, "_age_seconds", lambda _stamp: 0)
    _freeze_audit_at_nov_two(monkeypatch)
    snapshot = _existing_snapshot_fixture()
    snapshot["database"]["observer"].update(_observer("2026-11-02T20:30:00+00:00"))
    snapshot["database"]["observer"].update(change)
    with pytest.raises(RuntimeError, match="REAL_ORDER_COUNTER_INVALID|OBSERVER_PHASE_UNHEALTHY"):
        audit._assert_snapshot(snapshot)
