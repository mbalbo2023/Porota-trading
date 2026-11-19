"""P0: exact PPI catalog observation even with zero simulation-ready rows."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from be_paper_engine import PaperStore
from bu_instrument_catalog import init_schema
from bf_production_paper_observer import (
    _ppi_direct_rest_probe_candidates, _ppi_direct_rest_observe_only,
    _run_ppi_postclose_command, _support_schema,
)
import bf_production_paper_observer as observer
from rc6_paper_family_lifecycle import ensure_initialized


@pytest.fixture
def catalog_store(tmp_path):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)

    def add(ticker, family, market, currency, settlement, *, primary=True,
            status="AVAILABLE"):
        with store.connect() as c:
            c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (ticker, family, market, currency, settlement, "PPI_FIELD",
                 "OFFHOST", "2026-11-17T17:00:00Z", "OFFHOST", status,
                 "NOT_READY", json.dumps({"_discovery_source":
                     "PPI_PRIMARY" if primary else "IOL_COMPLEMENTARY"})))

    add("GGAL", "ACCIONES", "BYMA", "ARS", "INMEDIATA")
    add("GGAL", "ACCIONES", "BYMA", "USD", "INMEDIATA")
    add("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
    add("AAPL", "CEDEARS", "BYMA", "ARS", "INMEDIATA", primary=False)
    add("ZZZ", "ACCIONES", "BYMA", "ARS", "INMEDIATA", status="UNAVAILABLE")
    return store


def test_observational_candidates_do_not_require_ready_but_reject_ambiguity(catalog_store):
    assert _ppi_direct_rest_probe_candidates(catalog_store, limit=3) == [
        ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]


def test_documented_ppi_search_without_settlement_can_be_probed_observe_only(catalog_store):
    """SearchInstrument can omit settlement; an explicit request remains probe-only.

    A successful native Current+Book GET must verify the actual request at
    runtime.  Neither the catalog settlement hint nor this selection grants
    readiness or the capability to simulate a trade.
    """
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog
                     SET settlement_source='REQUEST_CANDIDATE',status='STALE'
                     WHERE ticker='AL30'""")
    assert _ppi_direct_rest_probe_candidates(catalog_store, limit=3) == [
        ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]


@pytest.mark.parametrize("settlement_source,status", [
    ("PPI_FIELD", "AVAILABLE"),
    ("REQUEST_CANDIDATE", "STALE"),
])
def test_probe_uses_exact_current_and_book_but_never_simulates(
        catalog_store, settlement_source, status):
    stamp = datetime.now(timezone.utc).isoformat()
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog
                     SET settlement_source=?,status=? WHERE ticker='AL30'""",
                  (settlement_source, status))

    class SyntheticReader:
        def __init__(self):
            self.calls = []

        def capture_guarded_native(self, endpoint, five_key, *, priority):
            self.calls.append((endpoint, tuple(five_key), priority))
            payload = (
                {"date": stamp, "price": 101} if endpoint == "current" else
                {"date": stamp,
                 "bids": [{"position": 1, "price": 100, "quantity": 1}],
                 "offers": [{"position": 1, "price": 102, "quantity": 1}]})
            return {"native_payload": payload, "guard_wire_receipt": {
                "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1",
                "endpoint": endpoint, "http_status_code": 200,
                "http_method": "GET", "adapter_send_invoked": True,
                "adapter_response_completed": True,
                "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
                "claimed_five_key": list(five_key),
                "received_at_utc": stamp,
                "raw_body_sha256": "a" * 64,
                "guard_budget_receipt_sha256": "b" * 64}}

    reader = SyntheticReader()
    observed = _ppi_direct_rest_observe_only(reader, catalog_store)
    assert observed["status"] == "FRESH_OBSERVED"
    assert observed["identity"] == ["AL30", "BONOS", "BYMA", "ARS", "INMEDIATA"]
    assert observed["paper_ready"] is False
    assert [x[0] for x in reader.calls] == ["current", "book"]
    assert all(call[1] == ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
               and call[2] == "DISCOVERY" for call in reader.calls)
    with catalog_store.connect() as c:
        quotes = c.execute("SELECT opening_block_reason,market,currency FROM market_snapshots").fetchall()
        native_receipts = c.execute("""SELECT event_type,detail FROM paper_events
            WHERE event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'
            ORDER BY id""").fetchall()
    assert len(quotes) == 1 and tuple(quotes[0]) == (
        "PPI_DIRECT_REST_OBSERVE_ONLY", "BYMA", "ARS")
    assert len(native_receipts) == 2
    assert ["endpoint=" + endpoint in detail for (_, detail), endpoint in zip(
        native_receipts, ("current", "book"))] == [True, True]
    assert all("wire_sha256=" + "a" * 64 in detail for _, detail in native_receipts)
    assert all("http_status=200" in detail and "market_timestamp_utc=" in detail
               and "wire_received_at_utc=" in detail for _, detail in native_receipts)


def test_no_primary_exact_catalog_stays_off_wire(catalog_store):
    with catalog_store.connect() as c:
        c.execute("UPDATE financial_instrument_catalog SET status='UNAVAILABLE' WHERE ticker='AL30'")

    class NoProviderReader:
        def current(self, *args):
            raise AssertionError("UNATTESTED_PROVIDER_GET_FORBIDDEN")

    observed = _ppi_direct_rest_observe_only(NoProviderReader(), catalog_store)
    assert observed["status"] == "CATALOG_IDENTITY_UNAVAILABLE"
    assert observed["paper_ready"] is False


def test_current_http200_receipt_survives_subsequent_book_failure(catalog_store):
    class PartialReader:
        def capture_guarded_native(self, endpoint, five_key, *, priority):
            if endpoint == "book":
                raise OSError("OFFHOST_SYNTHETIC_BOOK_UNAVAILABLE")
            return {"native_payload": {"price": 99}, "guard_wire_receipt": {
                "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1", "endpoint": "current",
                "http_status_code": 200, "http_method": "GET",
                "adapter_send_invoked": True, "adapter_response_completed": True,
                "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
                "claimed_five_key": list(five_key), "raw_body_sha256": "d" * 64}}

    observed = _ppi_direct_rest_observe_only(PartialReader(), catalog_store)
    assert observed["status"] == "PROVIDER_OR_CONTRACT_HOLD"
    assert observed["paper_ready"] is False
    with catalog_store.connect() as c:
        native = c.execute("""SELECT detail FROM paper_events WHERE
             event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'""").fetchall()
        quotes = c.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert len(native) == 1 and "endpoint=current" in native[0][0]
    assert "wire_sha256=" + "d" * 64 in native[0][0]
    assert quotes == 0


ART_POSTCLOSE = datetime(2026, 11, 18, 21, 45, tzinfo=ZoneInfo(
    "America/Argentina/Buenos_Aires"))


def _queued_command(store, name, *, created_at=None):
    _support_schema(store)
    with store.connect() as c:
        cursor = c.execute("""INSERT INTO observer_commands(created_at,command,status)
            VALUES(?,?,?)""", ((created_at or ART_POSTCLOSE).isoformat(), name,
                                 "RUNNING"))
        return cursor.lastrowid


def _command_receipt(store, command_id):
    with store.connect() as c:
        return c.execute("SELECT status,result FROM observer_commands WHERE id=?",
                         (command_id,)).fetchone()


def test_postclose_probe_uses_existing_reader_and_never_opens_second_session(
        catalog_store, monkeypatch):
    ensure_initialized(catalog_store)
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK',real_orders_sent=0")
    command_name = "PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS"
    command_id = _queued_command(catalog_store, command_name)
    reader = object()
    observed = []

    def probe(existing_reader, store, *, family, command_id, pre_wire):
        assert existing_reader is reader and store is catalog_store
        assert family == "BONOS" and command_id > 0
        pre_wire()  # contract must remain safe immediately before sending
        observed.append(family)
        return {"status": "STALE_OR_INVALID", "paper_ready": False}

    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", probe)
    monkeypatch.setattr(observer, "ProductionMarketReader", lambda *a, **kw:
                        (_ for _ in ()).throw(AssertionError("SECOND_LOGIN_FORBIDDEN")))
    assert _run_ppi_postclose_command(catalog_store, reader, command_id,
                                      command_name, now=ART_POSTCLOSE) is reader
    assert observed == ["BONOS"]
    status, result = _command_receipt(catalog_store, command_id)
    assert status == "EVIDENCE" and "STALE_OR_INVALID" in result
    assert "paper_ready" in result and "false" in result


@pytest.mark.parametrize("day,family,hour", [
    ("2026-11-17", "BONOS", 21),  # expired, never run next session
    ("2026-11-18", "UNKNOWN", 21),
    ("2026-11-18", "BONOS", 15),  # open market
    ("2026-11-18", "BONOS", 23),  # postclose time-to-live expired
])
def test_postclose_invalid_or_expired_command_is_zero_wire(
        catalog_store, monkeypatch, day, family, hour):
    ensure_initialized(catalog_store)
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK'")
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("WIRE_ON_INVALID_COMMAND_FORBIDDEN")))
    name = f"PPI_DIRECT_REST_POSTCLOSE_ONCE:{day}:{family}"
    command_id = _queued_command(catalog_store, name)
    result = _run_ppi_postclose_command(
        catalog_store, object(), command_id, name,
        now=ART_POSTCLOSE.replace(hour=hour))
    assert result is not None
    assert _command_receipt(catalog_store, command_id)[0] == "HOLD"


def test_postclose_no_reader_is_hold_without_authentication(catalog_store, monkeypatch):
    ensure_initialized(catalog_store)
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    name = "PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS"
    command_id = _queued_command(catalog_store, name)
    assert _run_ppi_postclose_command(catalog_store, None, command_id, name,
                                      now=ART_POSTCLOSE) is None
    assert _command_receipt(catalog_store, command_id)[0] == "HOLD"


def test_postclose_stale_command_created_today_cannot_be_replayed(
        catalog_store, monkeypatch):
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK'")
    name = "PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS"
    command_id = _queued_command(catalog_store, name,
                                  created_at=ART_POSTCLOSE - timedelta(minutes=30))
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("STALE_COMMAND_WIRE_FORBIDDEN")))
    _run_ppi_postclose_command(catalog_store, object(), command_id, name,
                               now=ART_POSTCLOSE)
    assert tuple(_command_receipt(catalog_store, command_id)) == (
        "HOLD", "PPI_POSTCLOSE_COMMAND_ISSUE_TTL_EXPIRED")


def test_postclose_duplicate_attempt_never_retries_after_partial_crash(
        catalog_store, monkeypatch):
    ensure_initialized(catalog_store)
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    name = "PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS"
    earlier = _queued_command(catalog_store, name)
    later = _queued_command(catalog_store, name)
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK'")
    assert _run_ppi_postclose_command(catalog_store, object(), later, name,
                                      now=ART_POSTCLOSE) is not None
    assert _command_receipt(catalog_store, earlier)[0] == "RUNNING"
    assert _command_receipt(catalog_store, later)[0] == "HOLD"


def test_postclose_candidates_are_family_specific_no_iol_takeover(catalog_store):
    assert _ppi_direct_rest_probe_candidates(catalog_store, family="BONOS") == [
        ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]
    assert _ppi_direct_rest_probe_candidates(catalog_store, family="FCI") == []


def test_guard_failure_between_current_and_book_has_exact_rca_and_current_witness(
        catalog_store):
    class Reader:
        def capture_guarded_native(self, endpoint, five_key, *, priority):
            if endpoint != "current":
                raise AssertionError("BOOK_MUST_BE_BLOCKED_BEFORE_SOCKET")
            return {"native_payload": {"date": "2026-11-18T20:00:00Z", "price": 100},
                    "guard_wire_receipt": {
                        "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1",
                        "endpoint": endpoint, "http_status_code": 200,
                        "http_method": "GET", "adapter_send_invoked": True,
                        "adapter_response_completed": True,
                        "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
                        "claimed_five_key": list(five_key), "raw_body_sha256": "a" * 64}}

    checks = []

    def pre_wire():
        checks.append(1)
        if len(checks) > 1:
            raise ValueError("PPI_POSTCLOSE_SUPERVISABLE_EXPOSURE_PRESENT")

    observed = _ppi_direct_rest_observe_only(
        Reader(), catalog_store, family="BONOS", command_id=42,
        pre_wire=pre_wire)
    assert observed == {"status": "PRE_WIRE_GUARD_HOLD", "paper_ready": False,
                        "reason": "PPI_POSTCLOSE_SUPERVISABLE_EXPOSURE_PRESENT"}
    with catalog_store.connect() as c:
        witness = c.execute("""SELECT detail FROM paper_events
            WHERE event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'""").fetchall()
    assert len(witness) == 1 and "command_id=42" in witness[0][0]


@pytest.mark.parametrize("status", ["OPEN", "UNEXPECTED"])
def test_postclose_rejects_unsettled_caucion_before_any_provider_read(
        catalog_store, monkeypatch, status):
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    name = "PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS"
    command_id = _queued_command(catalog_store, name)
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK'")
        c.execute("""INSERT INTO paper_cauciones(
            paper_id,request_id,request_fingerprint,source,instrument_id,currency,
            status,principal,annual_rate_fraction,interest_days,day_count_basis,
            gross_interest,total_fees,fee_payment,opened_at,maturity_at,terms_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "PAPER_CAUCION_1", "REQ_1", "hash", "PRODUCTION_PAPER", "ARS1",
            "ARS", status, "1000", "0.5", 1, 365, "2", "1", "0",
            ART_POSTCLOSE.isoformat(), "2026-11-19T17:00:00-03:00", "{}"))
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("WIRE_ON_EXPOSED_CAUCION_FORBIDDEN")))
    assert _run_ppi_postclose_command(catalog_store, object(), command_id,
                                      name, now=ART_POSTCLOSE) is not None
    assert tuple(_command_receipt(catalog_store, command_id)) == (
        "HOLD", "PPI_POSTCLOSE_CAUCION_EXPOSURE_PRESENT")


def test_postclose_admin_cancelled_caucion_requires_validated_receipt(
        catalog_store, monkeypatch):
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    name = "PPI_DIRECT_REST_POSTCLOSE_ONCE:2026-11-18:BONOS"
    command_id = _queued_command(catalog_store, name)
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK'")
        c.execute("""INSERT INTO paper_cauciones(
            paper_id,request_id,request_fingerprint,source,instrument_id,currency,
            status,principal,annual_rate_fraction,interest_days,day_count_basis,
            gross_interest,total_fees,fee_payment,opened_at,maturity_at,terms_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            "PAPER_CANCEL_UNVERIFIED", "REQ_CANCEL", "hash", "PRODUCTION_PAPER",
            "ARS1", "ARS", "CANCELLED_ADMIN", "1000", "0.5", 1, 365,
            "2", "1", "0", ART_POSTCLOSE.isoformat(),
            "2026-11-19T17:00:00-03:00", "{}"))
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("UNVERIFIED_CANCELLATION_WIRE_FORBIDDEN")))
    _run_ppi_postclose_command(catalog_store, object(), command_id, name,
                               now=ART_POSTCLOSE)
    assert tuple(_command_receipt(catalog_store, command_id)) == (
        "HOLD", "PPI_POSTCLOSE_CAUCION_RECEIPT_UNVERIFIED")
