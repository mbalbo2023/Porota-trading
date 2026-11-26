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
                 "NOT_READY", json.dumps({
                     "_discovery_source": "PPI_PRIMARY" if primary else "IOL_COMPLEMENTARY",
                     "ticker": ticker, "type": family, "market": market,
                     "currency": currency, "settlement": settlement})))

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
    assert all("identity5=AL30:BONOS:BYMA:ARS:INMEDIATA" in detail
               for _, detail in native_receipts)
    assert all("http_status=200" in detail and "market_timestamp_utc=" in detail
               and "wire_received_at_utc=" in detail for _, detail in native_receipts)
    assert all("response_settlement_status=ABSENT;response_settlement_literal=;"
               in detail for _, detail in native_receipts)


@pytest.mark.parametrize("defect", [
    "CURRENT_EMPTY", "CURRENT_DATE_MISSING", "BOOK_ONLY_TIMESTAMP",
    "BOOK_ONE_SIDED", "BOOK_ZERO_SIZE", "BOOK_CROSSED",
])
def test_native_http200_pair_with_unusable_current_or_book_stays_hold(
        catalog_store, defect):
    """HTTP reachability is not usable price/depth; both receipts survive."""
    stamp = datetime.now(timezone.utc).isoformat()
    current = {"date": stamp, "price": 101}
    book = {"date": stamp,
            "bids": [{"position": 1, "price": 100, "quantity": 1}],
            "offers": [{"position": 1, "price": 102, "quantity": 1}]}
    if defect == "CURRENT_EMPTY":
        current = {}
    elif defect == "CURRENT_DATE_MISSING":
        current.pop("date")
    elif defect == "BOOK_ONLY_TIMESTAMP":
        book = {"date": stamp}
    elif defect == "BOOK_ONE_SIDED":
        book.pop("offers")
    elif defect == "BOOK_ZERO_SIZE":
        book["bids"][0]["quantity"] = 0
    elif defect == "BOOK_CROSSED":
        book["bids"][0]["price"] = 105

    class Reader:
        def capture_guarded_native(self, endpoint, five_key, *, priority):
            return {"native_payload": current if endpoint == "current" else book,
                    "guard_wire_receipt": {
                        "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1",
                        "endpoint": endpoint, "http_status_code": 200,
                        "http_method": "GET", "adapter_send_invoked": True,
                        "adapter_response_completed": True,
                        "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
                        "claimed_five_key": list(five_key),
                        "received_at_utc": stamp, "raw_body_sha256": "c" * 64}}

    result = _ppi_direct_rest_observe_only(Reader(), catalog_store, family="BONOS")
    assert result == {"status": "STALE_OR_INVALID", "paper_ready": False,
                      "native_receipts": ["current", "book"]}
    with catalog_store.connect() as c:
        witness = c.execute("""SELECT detail FROM paper_events WHERE
            event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS' ORDER BY id""").fetchall()
        quotes = c.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert len(witness) == 2 and quotes == 0


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
ART_OPEN = datetime(2026, 11, 19, 11, 30, tzinfo=ZoneInfo(
    "America/Argentina/Buenos_Aires"))
OPEN_BATCH = "PPI_DIRECT_REST_OPEN_ONCE:2026-11-19:ALL"
TEN_FAMILIES = (
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
    "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
)


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
                        "reason": "PPI_POSTCLOSE_SUPERVISABLE_EXPOSURE_PRESENT",
                        "native_receipts": ["current"]}
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


def _ready_open_scanner(catalog_store, monkeypatch):
    ensure_initialized(catalog_store)
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    # Isolate *downstream* bounded-probe/coverage contract tests from the new
    # production hard HOLD. No such attestation exists or can be toggled via
    # environment in the deployed scanner; negative admission test below
    # deliberately does not patch this function.
    monkeypatch.setattr(observer, "_ppi_open_global_session_evidence_gate",
                        lambda: None)
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK',real_orders_sent=0")

    class ExistingScanner:
        budget_enabled = True
        metrics = {"authenticated": True, "login_calls": 1}

        def login_once(self):
            raise AssertionError("SECOND_PPI_LOGIN_FORBIDDEN")

    monkeypatch.setattr(observer, "ProductionMarketReader", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError("SECOND_PPI_ACTOR_FORBIDDEN")))
    return ExistingScanner()


def test_open_alternate_queue_writer_cannot_bypass_unverified_global_ppi_session(
        catalog_store, monkeypatch):
    """A raw observer_commands row is not proof of one PPI login globally.

    Neither the OPEN issuer's independent checks nor a scanner-local login
    counter is a trustworthy cross-process singleton attestation. The scanner
    must refuse its own GET even if another writer inserted the same command.
    """
    ensure_initialized(catalog_store)
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    # A new env flag or caller-controlled metrics must not grant authority.
    monkeypatch.setenv("POROTA_PPI_SINGLE_SESSION_GLOBAL_ATTESTED", "1")
    with catalog_store.connect() as c:
        c.execute("UPDATE observer_state SET ppi_auth='OK',real_orders_sent=0")
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)

    class AlreadyLoggedInScanner:
        budget_enabled = True
        metrics = {"authenticated": True, "login_calls": 1,
                   "global_single_session_attested": True}

        def login_once(self):
            pytest.fail("SECOND_PPI_LOGIN_FORBIDDEN")

        def capture_guarded_native(self, *args, **kwargs):
            pytest.fail("PPI_GET_WITHOUT_GLOBAL_SESSION_OWNER_FORBIDDEN")

    reader = AlreadyLoggedInScanner()
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only",
                        lambda *args, **kwargs: pytest.fail(
                            "NO_PROBE_BEFORE_GLOBAL_SESSION_ATTESTATION"))
    assert observer._run_ppi_open_command(
        catalog_store, reader, command_id, OPEN_BATCH, now=ART_OPEN) is reader
    assert tuple(_command_receipt(catalog_store, command_id)) == (
        "HOLD", "PPI_SINGLE_SESSION_GLOBAL_UNVERIFIED")
    with catalog_store.connect() as c:
        assert c.execute("""SELECT COUNT(*) FROM paper_events
            WHERE event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'
              AND detail LIKE ?""", (f"command_id={command_id};%",)).fetchone()[0] == 0


def test_open_all_ten_families_same_authenticated_scanner_reader_no_auto_ready(
        catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    observed = []

    def bounded_probe(existing_reader, store, *, family, command_id, pre_wire):
        assert existing_reader is reader and store is catalog_store
        assert command_id > 0 and family in TEN_FAMILIES
        pre_wire()
        observed.append(family)
        if family == "BONOS":
            return {"status": "FRESH_OBSERVED", "identity": ["AL30", "BONOS", "BYMA", "ARS", "INMEDIATA"],
                    "paper_ready": False, "native_receipts": ["current", "book"]}
        return {"status": "CATALOG_IDENTITY_UNAVAILABLE", "paper_ready": False,
                "native_receipts": []}

    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", bounded_probe)
    assert observer._run_ppi_open_command(catalog_store, reader, command_id,
                                          OPEN_BATCH, now=ART_OPEN) is reader
    assert observed == ["ACCIONES", "BONOS"]
    status, raw = _command_receipt(catalog_store, command_id)
    report = json.loads(raw)
    assert status == "EVIDENCE"
    assert len(raw) <= 2000 and report["schema"] == "RC6_PPI_OPEN_TEN_FAMILY_V1"
    assert report["day"] == "2026-11-19"
    assert report["readiness_reason"] == "FULL_READINESS_NOT_EVIDENCED"
    assert report["unattempted_reason"] == "BOUNDED"
    assert report["catalog_scope"] == "PPI_CLAIMED_ONLY"
    assert report["outside_ppi_scope"] == 1  # IOL source is not PPI identity.
    assert set(report) == {"schema", "day", "paper_ready", "order_authority",
                           "readiness_reason", "unattempted_reason", "catalog_scope",
                           "outside_ppi_scope", "statuses", "unverified", "coverage"}
    assert set(report["coverage"]) == set(TEN_FAMILIES)
    assert set(report["unverified"]) == set(TEN_FAMILIES)
    assert report["paper_ready"] is False and report["order_authority"] is False
    assert set(report["statuses"]) == set(TEN_FAMILIES)
    assert report["statuses"]["BONOS"] == "FRESH_OBSERVED"
    assert report["coverage"]["BONOS"] == {
        "enumerated_exact": 1, "attempted": 1, "native_pair_qa_pass": 1,
        "ready": 0, "hold": 1, "unattempted": 0}
    assert report["coverage"]["ACCIONES"]["ready"] == 0
    assert report["coverage"]["ACCIONES"]["attempted"] == 0
    assert report["coverage"]["ACCIONES"]["hold"] == 3
    assert report["unverified"]["ACCIONES"] == 3
    assert reader.metrics["login_calls"] == 1


@pytest.mark.parametrize("reason", [
    "NO_READER", "UNAUTHENTICATED_READER", "NO_BUDGET",
    "RUNTIME_FLAG", "REAL_ORDER_SENT", "AUTH_STATE", "OUTSIDE_OPEN",
])
def test_open_batch_fail_closed_without_any_provider_wire(
        catalog_store, monkeypatch, reason):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    now = ART_OPEN
    if reason == "NO_READER":
        reader = None
    elif reason == "UNAUTHENTICATED_READER":
        reader.metrics = {"authenticated": False, "login_calls": 0}
    elif reason == "NO_BUDGET":
        reader.budget_enabled = False
    elif reason == "RUNTIME_FLAG":
        monkeypatch.delenv("POROTA_PPI_DIRECT_REST_RECOVERY")
    elif reason == "REAL_ORDER_SENT":
        with catalog_store.connect() as c:
            c.execute("UPDATE observer_state SET real_orders_sent=1")
    elif reason == "AUTH_STATE":
        with catalog_store.connect() as c:
            c.execute("UPDATE observer_state SET ppi_auth='ERROR'")
    elif reason == "OUTSIDE_OPEN":
        now = ART_OPEN.replace(hour=17, minute=1)
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError("WIRE_ON_FAILED_GATE_FORBIDDEN")))
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    assert observer._run_ppi_open_command(catalog_store, reader, command_id,
                                          OPEN_BATCH, now=now) is reader
    assert _command_receipt(catalog_store, command_id)[0] == "HOLD"


@pytest.mark.parametrize("name,clock", [
    ("PPI_DIRECT_REST_OPEN_ONCE:2026-11-18:ALL", ART_OPEN),
    ("PPI_DIRECT_REST_OPEN_ONCE:2026-11-19:BONOS", ART_OPEN),
    ("PPI_DIRECT_REST_OPEN_ONCE:2026-11-19:UNKNOWN", ART_OPEN),
    (OPEN_BATCH, ART_OPEN + timedelta(minutes=11)),
])
def test_open_batch_expired_or_wrong_format_never_probes(
        catalog_store, monkeypatch, name, clock):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, name, created_at=ART_OPEN)
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError("UNAUTHORIZED_GET")))
    observer._run_ppi_open_command(catalog_store, reader, command_id, name, now=clock)
    assert _command_receipt(catalog_store, command_id)[0] == "HOLD"


def test_open_batch_no_replay_after_first_attempt(catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    earlier = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    later = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError("DUPLICATE_GET")))
    observer._run_ppi_open_command(catalog_store, reader, later, OPEN_BATCH, now=ART_OPEN)
    assert _command_receipt(catalog_store, earlier)[0] == "RUNNING"
    assert _command_receipt(catalog_store, later)[0] == "HOLD"


def test_open_batch_aborts_remaining_families_after_provider_hold(
        catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    touched = []

    def held_probe(existing_reader, store, *, family, command_id, pre_wire):
        pre_wire()
        touched.append(family)
        if family == "ACCIONES":
            return {"status": "CATALOG_IDENTITY_UNAVAILABLE", "paper_ready": False,
                    "native_receipts": []}
        return {"status": "PROVIDER_OR_CONTRACT_HOLD", "paper_ready": False,
                "native_receipts": []}

    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", held_probe)
    observer._run_ppi_open_command(catalog_store, reader, command_id, OPEN_BATCH, now=ART_OPEN)
    assert touched == ["ACCIONES", "BONOS"]
    status, raw = _command_receipt(catalog_store, command_id)
    report = json.loads(raw)
    assert status == "HOLD"
    assert report["statuses"]["ACCIONES"] == "CATALOG_IDENTITY_UNAVAILABLE"
    assert report["statuses"]["BONOS"] == "PROVIDER_OR_CONTRACT_HOLD"
    assert report["statuses"]["FCI"] == "NOT_ATTEMPTED_ABORTED"
    assert report["coverage"]["FCI"]["attempted"] == 0


def test_open_batch_rechecks_paper_zero_before_book_preserving_current_receipt(
        catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    stamp = ART_OPEN.astimezone(timezone.utc).isoformat()
    endpoints = []

    def guarded_capture(endpoint, five_key, *, priority):
        endpoints.append(endpoint)
        if endpoint != "current":
            raise AssertionError("BOOK_AFTER_PAPER_STATE_CHANGE_FORBIDDEN")
        with catalog_store.connect() as c:
            c.execute("UPDATE observer_state SET real_orders_sent=1")
        return {"native_payload": {"date": stamp, "price": 100},
                "guard_wire_receipt": {
                    "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1", "endpoint": endpoint,
                    "http_status_code": 200, "http_method": "GET",
                    "adapter_send_invoked": True, "adapter_response_completed": True,
                    "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
                    "claimed_five_key": list(five_key), "received_at_utc": stamp,
                    "raw_body_sha256": "a" * 64}}

    reader.capture_guarded_native = guarded_capture
    observer._run_ppi_open_command(catalog_store, reader, command_id, OPEN_BATCH, now=ART_OPEN)
    assert endpoints == ["current"]
    assert _command_receipt(catalog_store, command_id)[0] == "HOLD"
    with catalog_store.connect() as c:
        witness = c.execute("SELECT detail FROM paper_events WHERE event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'").fetchall()
        snapshots = c.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0]
    assert len(witness) == 1 and "endpoint=current" in witness[0][0]
    assert snapshots == 0


def test_open_global_session_attestation_revoked_after_current_blocks_book(
        catalog_store, monkeypatch):
    """A post-Current custody loss cannot be covered by the admission check."""
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    stamp = ART_OPEN.astimezone(timezone.utc).isoformat()
    attested = {"active": True}
    endpoints = []

    def physical_single_owner_for_test_only():
        if not attested["active"]:
            raise ValueError("PPI_SINGLE_SESSION_GLOBAL_UNVERIFIED")

    monkeypatch.setattr(observer, "_ppi_open_global_session_evidence_gate",
                        physical_single_owner_for_test_only)

    def guarded_capture(endpoint, five_key, *, priority):
        endpoints.append(endpoint)
        assert endpoint == "current" and priority == "DISCOVERY"
        attested["active"] = False
        return {"native_payload": {"date": stamp, "price": 100},
                "guard_wire_receipt": {
                    "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1", "endpoint": endpoint,
                    "http_status_code": 200, "http_method": "GET",
                    "adapter_send_invoked": True, "adapter_response_completed": True,
                    "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
                    "claimed_five_key": list(five_key), "received_at_utc": stamp,
                    "raw_body_sha256": "a" * 64}}

    reader.capture_guarded_native = guarded_capture
    observer._run_ppi_open_command(catalog_store, reader, command_id, OPEN_BATCH,
                                   now=ART_OPEN)
    assert endpoints == ["current"]
    status, raw = _command_receipt(catalog_store, command_id)
    report = json.loads(raw)
    assert status == "HOLD"
    assert report["coverage"]["BONOS"]["attempted"] == 1
    assert report["coverage"]["BONOS"]["native_pair_qa_pass"] == 0
    assert report["statuses"]["BONOS"] == "PRE_WIRE_GUARD_HOLD"
    with catalog_store.connect() as c:
        events = c.execute("""SELECT detail FROM paper_events
            WHERE event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'""").fetchall()
    assert len(events) == 1 and "endpoint=current" in events[0][0]


def test_open_command_router_never_constructs_reader(catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    observed = []

    def fake_open(store, original_reader, command_id, name):
        observed.append((store is catalog_store, original_reader is reader, name))
        return original_reader

    monkeypatch.setattr(observer, "_run_ppi_open_command", fake_open)
    assert observer._run_command(catalog_store, reader, (123, OPEN_BATCH)) is reader
    assert observed == [(True, True, OPEN_BATCH)]


def test_open_ten_family_receipt_stays_complete_under_two_kib_worst_plausible(
        catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    monkeypatch.setattr(observer, "_ppi_open_catalog_coverage", lambda store: {
        "enumerated_exact": dict.fromkeys(TEN_FAMILIES, 1000),
        "unverified": dict.fromkeys(TEN_FAMILIES, 1000),
        "catalog_scope": "PPI_CLAIMED_ONLY", "outside_ppi_scope": 1_000_000})
    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", lambda *args, **kw: {
        "status": "CATALOG_IDENTITY_UNAVAILABLE", "paper_ready": False,
        "native_receipts": []})
    observer._run_ppi_open_command(catalog_store, reader, command_id, OPEN_BATCH,
                                   now=ART_OPEN)
    status, raw = _command_receipt(catalog_store, command_id)
    assert status == "HOLD" and len(raw.encode("utf-8")) <= 2000
    result = json.loads(raw)  # no string truncation or schema downgrade
    assert result["outside_ppi_scope"] == 1_000_000
    assert result["unattempted_reason"] == "BOUNDED"
    assert len(result["statuses"]) == len(result["coverage"]) == 10


def test_open_catalog_coverage_excludes_unverified_settlement_and_ambiguous_fivekeys(
        catalog_store):
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog
            SET settlement_source='REQUEST_CANDIDATE'
            WHERE ticker='AL30' AND instrument_type='BONOS'""")
        c.execute("""INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  ("F1", "FCI", "BYMA", "ARS", "INMEDIATA", "REQUEST_CANDIDATE",
                   "OFFHOST", "2026-11-17T17:00:00Z", "OFFHOST", "AVAILABLE",
                   "NOT_READY", json.dumps({"_discovery_source": "PPI_PRIMARY"})))
    coverage = observer._ppi_open_catalog_coverage(catalog_store)
    assert coverage["enumerated_exact"]["BONOS"] == 0
    assert coverage["enumerated_exact"]["FCI"] == 0
    assert coverage["enumerated_exact"]["ACCIONES"] == 0
    assert coverage["unverified"]["BONOS"] == 1
    assert coverage["unverified"]["FCI"] == 1
    assert coverage["unverified"]["ACCIONES"] == 3


def test_open_ppi_field_without_raw_literal_full_fivekey_is_unverified(
        catalog_store):
    baseline = observer._ppi_open_catalog_coverage(catalog_store)
    assert baseline["enumerated_exact"]["BONOS"] == 1
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog SET metadata_json=?
            WHERE ticker='AL30' AND instrument_type='BONOS'""",
                  (json.dumps({"_discovery_source": "PPI_PRIMARY", "ticker": "AL30",
                               "type": "BONOS", "market": "BYMA", "currency": "ARS"}),))
    coverage = observer._ppi_open_catalog_coverage(catalog_store)
    assert coverage["enumerated_exact"]["BONOS"] == 0
    assert coverage["unverified"]["BONOS"] == 1
    assert coverage["outside_ppi_scope"] == 1
    assert coverage["catalog_scope"] == "PPI_CLAIMED_ONLY"


def test_open_catalog_denominator_explicitly_counts_invalid_metadata_and_iol_scope(
        catalog_store):
    with catalog_store.connect() as c:
        c.execute("""INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  ("ON_BAD", "OBLIGACIONES", "BYMA", "ARS", "INMEDIATA",
                   "PPI_FIELD", "OFFHOST", "2026-11-19T12:00:00Z", "OFFHOST",
                   "AVAILABLE", "NOT_READY", "{not_json"))
        c.execute("""INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  ("ETF_IOL", "ETFS", "BYMA", "ARS", "INMEDIATA",
                   "IOL_COMPLEMENTARY", "OFFHOST", "2026-11-19T12:00:00Z",
                   "OFFHOST", "AVAILABLE", "NOT_READY",
                   json.dumps({"_discovery_source": "IOL_COMPLEMENTARY"})))
    coverage = observer._ppi_open_catalog_coverage(catalog_store)
    assert coverage["enumerated_exact"]["OBLIGACIONES"] == 0
    assert coverage["unverified"]["OBLIGACIONES"] == 1
    assert coverage["outside_ppi_scope"] == 2  # Existing CEDEAR IOL + ETF IOL.
    assert coverage["catalog_scope"] == "PPI_CLAIMED_ONLY"


def test_open_current_book_probes_request_candidate_without_granting_settlement_or_ready(
        catalog_store, monkeypatch):
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog
            SET settlement_source='REQUEST_CANDIDATE' WHERE ticker='AL30'""")
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    stamp = datetime.now(timezone.utc).isoformat()
    calls = []

    def capture(endpoint, five_key, *, priority):
        calls.append((endpoint, tuple(five_key), priority))
        payload = ({"date": stamp, "price": 101} if endpoint == "current" else
                   {"date": stamp,
                    "bids": [{"position": 1, "price": 100, "quantity": 1}],
                    "offers": [{"position": 1, "price": 102, "quantity": 1}]})
        return {"native_payload": payload, "guard_wire_receipt": {
            "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1", "endpoint": endpoint,
            "http_status_code": 200, "http_method": "GET",
            "adapter_send_invoked": True, "adapter_response_completed": True,
            "request_identity_3key": [five_key[0], five_key[1], five_key[4]],
            "claimed_five_key": list(five_key), "received_at_utc": stamp,
            "raw_body_sha256": "a" * 64}}

    reader.capture_guarded_native = capture
    observer._run_ppi_open_command(catalog_store, reader, command_id,
                                   OPEN_BATCH, now=ART_OPEN)
    status, raw = _command_receipt(catalog_store, command_id)
    report = json.loads(raw)
    assert status == "EVIDENCE" and report["statuses"]["BONOS"] == "FRESH_OBSERVED"
    assert report["coverage"]["BONOS"] == {
        "enumerated_exact": 0, "attempted": 1, "native_pair_qa_pass": 1,
        "ready": 0, "hold": 1, "unattempted": 0}
    assert report["unverified"]["BONOS"] == 1
    assert [r[0] for r in calls] == ["current", "book"]
    assert all(r[1] == ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
               and r[2] == "DISCOVERY" for r in calls)
    with catalog_store.connect() as c:
        catalog = c.execute("""SELECT settlement_source,capability
            FROM financial_instrument_catalog WHERE ticker='AL30'""").fetchone()
        quote = c.execute("""SELECT opening_block_reason FROM market_snapshots
            WHERE symbol='AL30'""").fetchone()
        native = c.execute("""SELECT detail FROM paper_events
            WHERE event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'
            ORDER BY id""").fetchall()
    assert catalog[0] == "REQUEST_CANDIDATE" and catalog[1] == "NOT_READY"
    assert quote[0] == "PPI_DIRECT_REST_OBSERVE_ONLY"
    assert len(native) == 2
    assert all(f"command_id={command_id};" in item[0] and
               "identity5=AL30:BONOS:BYMA:ARS:INMEDIATA" in item[0]
               for item in native)
    assert _ppi_direct_rest_probe_candidates(catalog_store, family="BONOS") == [
        ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]


def test_open_attempted_is_not_inferred_from_catalog_or_probe_status(
        catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    observed = []

    def probe(existing_reader, store, *, family, command_id, pre_wire):
        pre_wire()
        observed.append(family)
        return ({"status": "FRESH_OBSERVED", "paper_ready": False,
                 "native_receipts": []} if family == "BONOS" else
                {"status": "CATALOG_IDENTITY_UNAVAILABLE", "paper_ready": False,
                 "native_receipts": []})

    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", probe)
    observer._run_ppi_open_command(catalog_store, reader, command_id, OPEN_BATCH,
                                   now=ART_OPEN)
    status, raw = _command_receipt(catalog_store, command_id)
    report = json.loads(raw)
    assert status == "HOLD"
    assert report["coverage"]["BONOS"]["attempted"] == 0
    assert report["coverage"]["BONOS"]["native_pair_qa_pass"] == 0
    assert report["statuses"]["BONOS"] == "PROVIDER_OR_CONTRACT_HOLD"
    assert "FCI" not in observed


def test_open_partial_current_receipt_never_claims_book_or_ready(
        catalog_store, monkeypatch):
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)

    def probe(existing_reader, store, *, family, command_id, pre_wire):
        pre_wire()
        return ({"status": "PROVIDER_OR_CONTRACT_HOLD", "paper_ready": False,
                 "native_receipts": ["current"]} if family == "BONOS" else
                {"status": "CATALOG_IDENTITY_UNAVAILABLE", "paper_ready": False,
                 "native_receipts": []})

    monkeypatch.setattr(observer, "_ppi_direct_rest_observe_only", probe)
    observer._run_ppi_open_command(catalog_store, reader, command_id, OPEN_BATCH,
                                   now=ART_OPEN)
    status, raw = _command_receipt(catalog_store, command_id)
    report = json.loads(raw)
    assert status == "HOLD"
    assert report["coverage"]["BONOS"] == {
        "enumerated_exact": 1, "attempted": 1, "native_pair_qa_pass": 0,
        "ready": 0, "hold": 1, "unattempted": 0}


def test_ppi_diagnostic_rotation_keeps_visiting_every_paired_receipt_family():
    assert set(observer.PPI_DIAGNOSTIC_FAMILY_ROTATION) == (
        observer.OPERATIONAL_FAMILIES - {"CAUCIONES", "FCI"}
    )
    assert observer.PPI_DIAGNOSTIC_FAMILY_ROTATION[-1] == "ETFS"
    cursor, observed = 0, []
    for _ in observer.PPI_DIAGNOSTIC_FAMILY_ROTATION:
        family, cursor = observer._next_ppi_diagnostic_family(cursor)
        observed.append(family)
    assert observed == list(observer.PPI_DIAGNOSTIC_FAMILY_ROTATION)
    assert cursor == 0
    with pytest.raises(ValueError, match="PPI_DIAGNOSTIC_CURSOR_INVALID"):
        observer._next_ppi_diagnostic_family(-1)


def test_ppi_diagnostic_rotation_advances_on_provider_error_and_wraps(catalog_store):
    first = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
    second = ("AL31", "BONOS", "BYMA", "ARS", "INMEDIATA")
    with catalog_store.connect() as c:
        c.execute("""INSERT INTO financial_instrument_catalog
            SELECT ?,instrument_type,market,currency,settlement,'REQUEST_CANDIDATE',
                   description,last_seen_at,run_id,status,capability,
                   json_set(json_remove(metadata_json, '$.settlement'), '$.ticker', ?)
            FROM financial_instrument_catalog WHERE ticker='AL30'""", (second[0], second[0]))

    class MissingMarketData:
        def capture_guarded_native(self, endpoint, key, *, priority):
            assert (endpoint, key, priority) == ("current", first, "DISCOVERY")
            raise ValueError("PPI_NATIVE_CURRENT_UNAVAILABLE")

    result = _ppi_direct_rest_observe_only(
        MissingMarketData(), catalog_store, family="BONOS", confirm_settlement=True)
    assert result["status"] == "PROVIDER_OR_CONTRACT_HOLD"
    assert tuple(result["identity"]) == first
    assert _ppi_direct_rest_probe_candidates(catalog_store, family="BONOS", after=first) == [second]
    assert _ppi_direct_rest_probe_candidates(catalog_store, family="BONOS", after=second) == [first]


def test_native_discovery_prioritizes_recorded_trades_for_pending_exact_keys(catalog_store):
    key = ("AL99", "BONOS", "BYMA", "ARS", "INMEDIATA")
    _support_schema(catalog_store)
    with catalog_store.connect() as c:
        c.execute("""UPDATE financial_instrument_catalog
          SET settlement_source='REQUEST_CANDIDATE',
              metadata_json=json_remove(metadata_json, '$.settlement') WHERE ticker='AL30'""")
        c.execute("""INSERT INTO financial_instrument_catalog
          SELECT 'AL99',instrument_type,market,currency,settlement,settlement_source,
                 description,last_seen_at,run_id,status,capability,
                 json_set(metadata_json,'$.ticker','AL99')
          FROM financial_instrument_catalog WHERE ticker='AL30'""")
        observer.financial_catalog.sync_candidate_universe(c, "2026-11-24T16:00:00Z")
        c.execute("""INSERT INTO market_snapshots
          (source,observed_at,symbol,asset_class,settlement,last,bid,ask,bid_size,ask_size,
           currency,market,book_at,trade_at,last_kind)
          VALUES ('PRODUCTION_PAPER','2026-11-24T15:00:00+00:00','AL99','BONOS',
                  'INMEDIATA','100','99','101','5','5','ARS','BYMA',
                  '2026-11-24T15:00:00+00:00','2026-11-24T15:00:00+00:00','TRADE')""")

    assert _ppi_direct_rest_probe_candidates(
        catalog_store, family="BONOS", pending_settlement=True) == [key]
    assert _ppi_direct_rest_probe_candidates(
        catalog_store, family="BONOS", after=key, pending_settlement=True) == [
            ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]
    with catalog_store.connect() as c:
        assert c.execute("""SELECT can_simulate FROM candidate_identity_v2
                             WHERE ticker='AL99' AND instrument_type='BONOS'""").fetchone()[0] == 0


def test_native_discovery_uses_spare_turns_and_keeps_exact_cursor(catalog_store, monkeypatch):
    families = observer.PPI_DIAGNOSTIC_FAMILY_ROTATION
    monkeypatch.setattr(observer, "_market_open", lambda: True)
    monkeypatch.setattr(observer, "_ppi_direct_rest_probe_candidates",
                        lambda store, *, family, after, pending_settlement:
                        [(family, family, "BYMA", "ARS", "INMEDIATA")] if after is None else [])
    observed = []

    def capture(reader, store, key, *, pre_wire, **_):
        pre_wire()
        observed.append(key)
        return {"status": "FRESH_OBSERVED", "native_receipts": ["current", "book"],
                "settlement_attested": key[1] == families[0]}

    monkeypatch.setattr(observer, "_observe_native_candidate", capture)
    after = {}
    pulse = observer.time.monotonic()
    cursor, _, result = observer._discover_native_settlements(
        object(), catalog_store, slots=2, deadline=pulse + 20, cursor=0, after=after,
        last_pulse=pulse)
    assert [key[1] for key in observed] == list(families[:2])
    assert after == dict(zip(families, observed))
    assert cursor == 2
    assert result == {"slots": 2, "attempted": 2, "fresh_pairs": 2, "attested": 1}


def test_open_rotation_materializes_one_bounded_plan_for_multiple_same_family_keys(
        catalog_store, monkeypatch):
    family = "BONOS"
    keys = [(f"AL{i}", family, "BYMA", "ARS", "INMEDIATA") for i in range(30, 33)]
    reads = []

    def batch(_store, *, limit, family, after, pending_settlement):
        reads.append((limit, family, after, pending_settlement))
        return keys[1:]

    monkeypatch.setattr(observer, "PPI_DIAGNOSTIC_FAMILY_ROTATION", (family,))
    monkeypatch.setattr(observer, "_market_open", lambda: True)
    monkeypatch.setattr(observer, "_ppi_direct_rest_probe_candidates", lambda *_a, **_kw: keys[:1])
    monkeypatch.setattr(observer, "_ppi_rest_probe_keys", batch)
    monkeypatch.setattr(observer, "_observe_native_candidate", lambda *_a, **_kw: {
        "status": "FRESH_OBSERVED", "native_receipts": ["current", "book"], "settlement_attested": True})
    pulse = observer.time.monotonic()
    after = {}
    _, _, result = observer._discover_native_settlements(
        object(), catalog_store, slots=3, deadline=pulse + 10, cursor=0, after=after, last_pulse=pulse)
    assert reads == [(2, family, keys[0], True)]
    assert after == {family: keys[-1]} and result["attested"] == 3


@pytest.mark.parametrize(("second_read", "reason"), [
    ("429", "PPI_NATIVE_HTTP_429"),
    ("exit", "PPI_OPEN_EXIT_EXPOSURE_PRESENT"),
])
def test_native_discovery_stops_on_wire_backoff_and_new_exit(
        catalog_store, monkeypatch, second_read, reason):
    from rc6_ppi_gateway_reader_adapter import GatewayEvidenceRejected

    key = ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")
    monkeypatch.setattr(observer, "_market_open", lambda: True)
    monkeypatch.setattr(observer, "_ppi_direct_rest_probe_candidates",
                        lambda *_args, **kw: [key] if kw["family"] == "BONOS" else [])
    sent = []
    monkeypatch.setattr(catalog_store, "open_positions",
                        lambda: [object()] if sent and second_read == "exit" else [])

    class Reader:
        def capture_guarded_native(self, endpoint, identity, *, priority):
            sent.append(endpoint)
            if endpoint == "book":
                raise GatewayEvidenceRejected("PPI_NATIVE_HTTP_429")
            data = {"date": observer.now_iso(), "price": 100}
            return {"native_payload": data, "guard_wire_receipt": {
                "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1", "endpoint": endpoint,
                "http_method": "GET", "http_status_code": 200, "adapter_send_invoked": True,
                "adapter_response_completed": True,
                "received_at_utc": data["date"],
                "request_identity_3key": [identity[0], identity[1], identity[4]],
                "claimed_five_key": list(identity), "raw_body_sha256": "a" * 64}}

    pulse = observer.time.monotonic()
    after = {}
    _, _, result = observer._discover_native_settlements(
        Reader(), catalog_store, slots=10, deadline=pulse + 20, cursor=0,
        after=after, last_pulse=pulse)
    assert sent == (["current", "book"] if second_read == "429" else ["current"])
    assert after == ({} if second_read == "429" else {"BONOS": key})
    assert result == {"slots": 10, "attempted": 1, "fresh_pairs": 0,
                      "attested": 0, "reason": reason}


def test_complementary_duplicate_does_not_suppress_unique_primary_ppi_probe(catalog_store):
    # Same native request 3-key but only ONE PPI-primary identity. An IOL
    # complementary alias is not an independently authenticated PPI collision.
    with catalog_store.connect() as c:
        c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("AL30", "BONOS", "BYMA", "USD", "INMEDIATA",
                   "IOL_COMPLEMENTARY", "OFFHOST", "2026-11-20T15:00:00Z",
                   "RUN_TEST", "AVAILABLE", "NOT_READY",
                   json.dumps({"_discovery_source": "IOL_COMPLEMENTARY"})))
    assert _ppi_direct_rest_probe_candidates(catalog_store, limit=3) == [
        ("AL30", "BONOS", "BYMA", "ARS", "INMEDIATA")]
    # Two genuinely distinct PPI-PRIMARY keys sharing the request would be
    # blocked by the existing GGAL/ARS versus GGAL/USD fixture.


@pytest.mark.parametrize("payload,expected", [
    ({"settlement": "a-24hs", "instrument": {"settlement": "T1"}}, ("EXPLICIT", "a-24hs")),
    ({"Settlement": "CI", "identity": {"term": "INMEDIATA"}}, ("EXPLICIT", "CI")),
    ({"settlement": "CI", "instrument": {"settlement": "T1"}}, ("UNVERIFIED", "")),
    ({"settlement": "CI\n"}, ("UNVERIFIED", "")),
    ({"date": "2026-11-20T14:00:00Z", "price": 123.4}, ("ABSENT", "")),
])
def test_native_settlement_preserves_literal_and_compares_alias_meaning(payload, expected):
    assert observer.native_ppi_response_settlement(payload) == expected


@pytest.mark.parametrize("family,ticker,expected", [
    ("FCI", "FUNDX", "FCI_CURRENT_REFERENCE_OBSERVED"),
    ("CAUCIONES", "PESOS1", "CAUCION_CURRENT_REFERENCE_OBSERVED"),
])
def test_native_reference_family_never_requires_spot_book_or_marks_ready(
        tmp_path, family, ticker, expected):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)
    key = (ticker, family, "BYMA", "ARS", "INMEDIATA")
    with store.connect() as c:
        c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (*key, "REQUEST_CANDIDATE", "OFFHOST",
                   datetime.now(timezone.utc).isoformat(), "RUN_TEST",
                   "STALE", "NOT_READY", json.dumps({
                       "_discovery_source": "PPI_PRIMARY", "ticker": ticker,
                       "type": family, "market": "BYMA", "currency": "ARS"})))
    stamp = datetime.now(timezone.utc).isoformat()

    class NativeReferenceReader:
        def __init__(self):
            self.methods = []

        def capture_guarded_native(self, endpoint, five_key, *, priority):
            self.methods.append(endpoint)
            assert endpoint == "current" and priority == "DISCOVERY"
            assert tuple(five_key) == key
            return {"native_payload": {"date": stamp, "price": 123.4, "settlement": "CI"},
                    "guard_wire_receipt": {
                        "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1",
                        "endpoint": "current", "http_status_code": 200,
                        "http_method": "GET", "adapter_send_invoked": True,
                        "adapter_response_completed": True,
                        "request_identity_3key": [ticker, family, "INMEDIATA"],
                        "claimed_five_key": list(key), "received_at_utc": stamp,
                        "raw_body_sha256": "a" * 64}}

    reader = NativeReferenceReader()
    result = observer._ppi_direct_rest_observe_only(reader, store, family=family)
    assert reader.methods == ["current"]
    assert result["status"] == expected and result["paper_ready"] is False
    assert result["native_receipts"] == ["current"]
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
        assert c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM paper_events WHERE event_type='PPI_DIRECT_REST_REFERENCE_OBSERVED'").fetchone()[0] == 1
        witness = c.execute("SELECT detail FROM paper_events WHERE event_type='PPI_DIRECT_REST_NATIVE_WIRE_WITNESS'").fetchone()[0]
        assert "response_settlement_status=EXPLICIT;response_settlement_literal=CI;" in witness


@pytest.mark.parametrize("bad_reference", [False, True])
def test_open_receipts_distinguish_fci_and_caucion_references_from_book(
        catalog_store, monkeypatch, bad_reference):
    with catalog_store.connect() as c:
        for ticker, family in (("FUNDX", "FCI"), ("PESOS1", "CAUCIONES")):
            raw = {"_discovery_source": "PPI_PRIMARY", "ticker": ticker,
                   "type": family, "market": "BYMA", "currency": "ARS"}
            c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ticker, family, "BYMA", "ARS", "INMEDIATA", "REQUEST_CANDIDATE",
                       "OFFHOST", ART_OPEN.isoformat(), "OFFHOST", "AVAILABLE",
                       "NOT_READY", json.dumps(raw)))
    reader = _ready_open_scanner(catalog_store, monkeypatch)
    command_id = _queued_command(catalog_store, OPEN_BATCH, created_at=ART_OPEN)
    stamp = datetime.now(timezone.utc).isoformat()
    calls = []

    def capture(endpoint, key, *, priority):
        calls.append((key[0], endpoint))
        if endpoint == "current":
            payload = {"date": stamp, "price": None if bad_reference and key[1] == "FCI" else 101}
        else:
            payload = {"date": stamp,
                       "bids": [{"position": 1, "price": 100, "quantity": 1}],
                       "offers": [{"position": 1, "price": 102, "quantity": 1}]}
        return {"native_payload": payload, "guard_wire_receipt": {
            "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1", "endpoint": endpoint,
            "adapter_send_invoked": True, "adapter_response_completed": True,
            "http_status_code": 200, "http_method": "GET",
            "request_identity_3key": [key[0], key[1], key[4]], "claimed_five_key": list(key),
            "received_at_utc": stamp, "raw_body_sha256": "a" * 64}}

    reader.capture_guarded_native = capture
    observer._run_ppi_open_command(catalog_store, reader, command_id, OPEN_BATCH, now=ART_OPEN)
    status, raw = _command_receipt(catalog_store, command_id)
    assert status == "EVIDENCE"
    report = json.loads(raw)
    assert report["statuses"]["CAUCIONES"] == "CAUCION_CURRENT_REFERENCE_OBSERVED"
    assert report["statuses"]["FCI"] == (
        "REFERENCE_UNUSABLE" if bad_reference else "FCI_CURRENT_REFERENCE_OBSERVED")
    assert report["paper_ready"] is report["order_authority"] is False
    for family in ("CAUCIONES", "FCI"):
        assert report["coverage"][family] == {
            "enumerated_exact": 0, "attempted": 1, "native_pair_qa_pass": 0,
            "ready": 0, "hold": 1, "unattempted": 0}
    assert calls == [("AL30", "current"), ("AL30", "book"),
                     ("PESOS1", "current"), ("FUNDX", "current")]
    with catalog_store.connect() as c:
        assert [row[0] for row in c.execute("SELECT symbol FROM market_snapshots")] == ["AL30"]


@pytest.mark.parametrize("bad_price", [0, -1, None])
def test_native_reference_unusable_never_becomes_fresh_or_ready(tmp_path, bad_price):
    store = PaperStore(str(tmp_path / "paper.sqlite"))
    init_schema(store)
    key = ("FUNDX", "FCI", "BYMA", "ARS", "INMEDIATA")
    with store.connect() as c:
        c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (*key, "REQUEST_CANDIDATE", "OFFHOST",
                   datetime.now(timezone.utc).isoformat(), "RUN_TEST",
                   "STALE", "NOT_READY", json.dumps({
                       "_discovery_source": "PPI_PRIMARY", "ticker": key[0],
                       "type": "FCI", "market": "BYMA", "currency": "ARS"})))
    stamp = datetime.now(timezone.utc).isoformat()

    class Ref:
        def capture_guarded_native(self, endpoint, five_key, *, priority):
            assert endpoint == "current"
            return {"native_payload": {"date": stamp, "price": bad_price},
                    "guard_wire_receipt": {
                        "schema": "RC6_PPI_NATIVE_WIRE_RECEIPT_V1",
                        "endpoint": "current", "http_status_code": 200,
                        "http_method": "GET", "adapter_send_invoked": True,
                        "adapter_response_completed": True,
                        "request_identity_3key": [key[0], key[1], key[4]],
                        "claimed_five_key": list(key),
                        "raw_body_sha256": "f" * 64}}
    observed = observer._ppi_direct_rest_observe_only(Ref(), store, family="FCI")
    assert observed["status"] == "REFERENCE_UNUSABLE"
    assert observed["paper_ready"] is False
    with store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM market_snapshots").fetchone()[0] == 0
