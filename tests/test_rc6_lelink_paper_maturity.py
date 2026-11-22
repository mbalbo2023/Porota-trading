"""D30N6 cash and terminal replay with synthetic independently pinned receipts."""
from decimal import Decimal
import hashlib
import json

import pytest

from be_paper_engine import PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from rc6_fixed_income_event_ledger import (
    NATIVE_MATURITY_MODE, RECEIPT_SCHEMA, apply_verified_receipt, paper_entitlement_settlement,
)
from rc6_fixed_income_native_exit import terminal_at
from rc6_fixed_income_native_redemption import (
    REVIEW_AUTHORITY, REVIEW_SCHEMA, SCHEDULE_SCHEMA,
    apply_verified_redemption_review, normalized_issuer_schedule_sha256,
)
from rc6_fixed_income_portfolio_bridge import position_at
from rc6_lelink_terms_shadow import BCRA_A3500_SERIES, ISSUER_SERIES, RC55_ART1


PAPER_ID = "PAPER-D30N6"
KEY = ["D30N6", "LETRAS", "BYMA", "ARS", "A-24HS"]
OBLIGATION = "D30N6-AMORTIZATION-2026-11-30"
OPENED = "2026-11-24T12:00:00-03:00"
FIXED = "2026-11-25T00:00:00-03:00"
PUBLISHED = "2026-11-25T16:00:00-03:00"
FIXING_BOOKED = "2026-11-25T16:02:00-03:00"
EX_AT = "2026-11-27T11:00:00-03:00"
RECORD_AT = "2026-11-27T16:00:00-03:00"
PAY = "2026-11-30T15:00:00-03:00"
PAY_CONFIRMED = "2026-11-30T15:02:00-03:00"
PAY_BOOKED = "2026-11-30T15:05:00-03:00"
REVIEWED = "2026-11-30T15:10:00-03:00"
CLOSED = "2026-11-30T15:11:00-03:00"
ISSUER = b"SYNTHETIC issuer source for D30N6"
FIXING = b"SYNTHETIC official-series observation for test only"
PAYMENT = b"SYNTHETIC independent final payment confirmation"


def digest(body):
    return hashlib.sha256(body).hexdigest()


def encoded(receipt):
    return json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()


@pytest.fixture
def store(tmp_path):
    paper = PaperStore(str(tmp_path / "lelink.sqlite"))
    # Model an intact opening lot explicitly. This creates no entry rule.
    features = {
        "financial_contract": {
            "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
            "cash_multiplier": "0.01", "quantity_step": "1", "minimum_quantity": "1",
            "metadata_source": "SYNTHETIC_NOMINAL_CONTRACT",
        },
        "contract_cash_multiplier": "0.01",
        "fixed_income_exit_plan": {
            "mode": NATIVE_MATURITY_MODE, "strategy_id": "FI_LETRA_PAPER_V1",
            "verified": True, "max_hold_minutes": None, "nominal_rebase_required": True,
            "exit_at": PAY, "quantity_vn": "100", "issuer_source_sha256": digest(ISSUER),
            "issuer_source_reference": RC55_ART1, "reviewed_obligation_id": OBLIGATION,
            "ex_at": EX_AT, "record_at": RECORD_AT,
        },
    }
    with paper.connect() as db:
        db.execute("""INSERT INTO paper_positions
            (paper_id,source,strategy_version,symbol,asset_class,settlement,status,quantity,
             entry_price,entry_cost,stop_price,target_price,opened_at,features_json,currency,
             market,currency_source)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            PAPER_ID, "PRODUCTION_PAPER", "SYNTHETIC_NO_ENTRY", KEY[0], KEY[1], KEY[4],
            "OPEN", "100", "150000", "0.61", "140000", "160000", OPENED,
            json.dumps(features), KEY[3], KEY[2], "SYNTHETIC"))
        db.execute("""INSERT INTO paper_fills
            (paper_id,source,side,filled_at,quantity,price,costs,slippage)
            VALUES(?,?,?,?,?,?,?,?)""", (
            PAPER_ID, "PRODUCTION_PAPER", "BUY_SIMULATED", OPENED, "100", "150000", "0.61", "0"))
    return paper


def envelope(event):
    return {"schema": RECEIPT_SCHEMA, "paper_only": True, "real_routes_used": [],
            "review_authority": "RELEASE_REVIEWED_ECONOMIC_EVENT", "identity": KEY,
            "event": dict(event, paper_id=PAPER_ID), "entitlement": None}


def fixing_receipt(**overrides):
    return envelope({
        "event_id": "fixing", "obligation_id": OBLIGATION, "kind": "FIXING",
        "effective_at": FIXED, "recorded_at": PUBLISHED, "source_at": PUBLISHED,
        "source_kind": "OFFICIAL_FIXING", "source_reference": BCRA_A3500_SERIES,
        "source_sha256": digest(FIXING), "fixing_code": "BCRA_A3500",
        "fixing_unit": "ARS_PER_USD", "fixing_value": "1512.50", **overrides,
    })


def payment_receipt(**overrides):
    receipt = envelope({
        "event_id": "payment", "obligation_id": OBLIGATION, "kind": "AMORTIZATION",
        "effective_at": PAY, "recorded_at": PAY_CONFIRMED, "source_at": PAY_CONFIRMED,
        "source_kind": "SETTLEMENT_PROOF", "source_reference": "SYNTHETIC_SETTLEMENT",
        "source_sha256": digest(PAYMENT), "payment_currency": "ARS",
        "cash_amount": "151250.00", "nominal_base": "100", "nominal_redeemed": "100",
        **overrides,
    })
    settled, basis = paper_entitlement_settlement(
        KEY[4], OPENED, family=KEY[1], market=KEY[2], currency=KEY[3])
    receipt["entitlement"] = {
        "rule": "TRADE_BEFORE_EX_AND_SETTLED_BY_RECORD", "source_sha256": digest(PAYMENT),
        "source_reference": "SYNTHETIC_SETTLEMENT", "ex_at": EX_AT, "record_at": RECORD_AT,
        "paper_settled_at": settled.isoformat(), "settlement_basis": basis,
        "known_at": PAY_CONFIRMED,
    }
    receipt["cash_per_nominal"] = "1512.50"
    return receipt


def book(store, receipt, source, at):
    raw = encoded(receipt)
    return apply_verified_receipt(store, receipt_bytes=raw, approved_sha256=digest(raw),
                                  source_bytes=source, applied_at=at)


def review(store, **schedule_overrides):
    event = payment_receipt()["event"]
    schedule = {
        "schema": SCHEDULE_SCHEMA, "review_authority": "INDEPENDENT_ISSUER_SCHEDULE_REVIEWED",
        "identity": KEY, "issuer_id": "TESORO_NACIONAL_ARGENTINA", "series_id": ISSUER_SERIES,
        "instrument_class": "BILL", "bopreal_classification": "NOT_BOPREAL_INDEPENDENTLY_REVIEWED",
        "source_sha256": digest(ISSUER), "source_reference": RC55_ART1,
        "source_at": "2026-09-14T12:00:00-03:00", "known_at": FIXING_BOOKED,
        "maturity_at": PAY, "principal_currency": "ARS", "final_obligation_id": OBLIGATION,
        "redemption_basis": "OFFICIAL_INDEXED_FACE", "indexation": "BCRA_A3500",
        "redemption_face_official": True, "obligations": [{key: event[key] for key in (
            "obligation_id", "kind", "effective_at", "payment_currency", "cash_amount",
            "nominal_base", "nominal_redeemed")}],
        **schedule_overrides,
    }
    raw = encoded({
        "schema": REVIEW_SCHEMA, "paper_only": True, "real_routes_used": [],
        "review_authority": REVIEW_AUTHORITY, "all_maturity_obligations_reviewed": True,
        "paper_id": PAPER_ID, "identity": KEY, "final_event_id": "payment",
        "paid_obligation_ids": [OBLIGATION], "final_principal_cash": "151250.00",
        "final_nominal_redeemed": "100", "expected_principal_by_currency": [["ARS", "151250.00"]],
        "expected_cash_by_currency": [["ARS", "151250.00"]], "issuer_schedule": schedule,
    })
    return apply_verified_redemption_review(
        store, receipt_bytes=raw, approved_sha256=digest(raw), issuer_bytes=ISSUER,
        approved_issuer_sha256=digest(ISSUER), applied_at=REVIEWED,
        approved_schedule_sha256=normalized_issuer_schedule_sha256(schedule))


def test_d30n6_uses_one_published_fixing_and_one_payment_for_native_terminal(store):
    broker = PaperBroker(store, initial_cash="500000", daily_loss_pct=None)
    supervisor = PositionExitSupervisor(broker, clock_fn=lambda: CLOSED)
    root = store.open_positions()[0]
    fixing = book(store, fixing_receipt(), FIXING, FIXING_BOOKED)
    assert fixing.residual_nominal == Decimal("100") and fixing.cash_by_currency == ()
    assert supervisor.supervise(root, None, FIXING_BOOKED).state == "OPEN"
    cash_before_payment = broker._cash(as_of=FIXING_BOOKED, currency="ARS")
    paid = book(store, payment_receipt(), PAYMENT, PAY_BOOKED)
    assert paid.residual_nominal == 0 and paid.cash_by_currency == (("ARS", Decimal("151250.00")),)
    assert supervisor.supervise(root, None, PAY_BOOKED).state == "OPEN"
    assert review(store)["reconciled_cash_event_count"] == 1
    assert supervisor.supervise(root, None, CLOSED).state == "CLOSED"
    assert broker._cash(as_of=CLOSED, currency="ARS") == cash_before_payment + Decimal("151250.00")
    assert book(store, payment_receipt(), PAYMENT, CLOSED) == paid
    with store.connect() as db:
        assert position_at(db, PAPER_ID, FIXING_BOOKED).residual_nominal == Decimal("100")
        assert terminal_at(db, PAPER_ID, PAY_BOOKED) is None
        result = terminal_at(db, PAPER_ID, CLOSED)
        assert result["sell_fills_created"] == 0 and result["economic_cash_written"] == "0"
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT side FROM paper_fills").fetchone()[0] == "BUY_SIMULATED"
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state").fetchone()) == (
            "PRODUCTION_PAPER", 0)


@pytest.mark.parametrize("overrides,at,reason", [
    ({"effective_at": "2026-11-24T00:00:00-03:00"}, FIXING_BOOKED, "A3500_FIXING_DATE_MISMATCH"),
    ({"fixing_unit": "USD_PER_ARS"}, FIXING_BOOKED, "A3500_PROVENANCE_OR_UNIT_UNKNOWN"),
    ({"source_reference": "PPI_USD_MARKET_PRICE"}, FIXING_BOOKED, "A3500_PROVENANCE_OR_UNIT_UNKNOWN"),
    ({"obligation_id": "other"}, FIXING_BOOKED, "FI_LEDGER_OPENING_OBLIGATION_MISMATCH"),
    ({}, OPENED, "A3500_NOT_KNOWN_AT_PIT"),
])
def test_d30n6_bad_or_future_fixing_never_enters_the_ledger(store, overrides, at, reason):
    with pytest.raises(ValueError, match=reason):
        book(store, fixing_receipt(**overrides), FIXING, at)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_economic_events").fetchone()[0] == 0


@pytest.mark.parametrize("hazard,reason", [
    ("no_fixing", "FI_LELINK_EXACT_FIXING_REQUIRED"),
    ("wrong_obligation", "FI_LELINK_FIXING_OBLIGATION_MISMATCH"),
    ("wrong_amount", "FI_LELINK_PAYMENT_ECONOMICS_MISMATCH"),
    ("wrong_currency", "FI_LELINK_PAYMENT_ECONOMICS_MISMATCH"),
    ("wrong_payment_day", "FI_LELINK_PAYMENT_ECONOMICS_MISMATCH"),
    ("partial", "FI_LELINK_PAYMENT_ECONOMICS_MISMATCH"),
    ("same_source", "FI_LELINK_PAYMENT_REQUIRES_DISTINCT_SETTLEMENT_PROOF"),
])
def test_d30n6_unbound_payment_never_credits_cash(store, hazard, reason):
    if hazard == "no_fixing":
        # A legacy M1 plan has no separately frozen cash or obligation ID.
        with store.connect() as db:
            db.execute("""UPDATE paper_positions SET features_json=json_remove(
                features_json, '$.fixed_income_exit_plan.reviewed_obligation_id')""")
    else:
        book(store, fixing_receipt(), FIXING, FIXING_BOOKED)
    receipt = payment_receipt()
    source = PAYMENT
    if hazard == "wrong_obligation":
        receipt["event"]["obligation_id"] = "other"
    elif hazard == "wrong_amount":
        receipt["event"]["cash_amount"] = "150000"
        receipt["cash_per_nominal"] = "1500"
    elif hazard == "wrong_currency":
        receipt["event"]["payment_currency"] = "USD"
    elif hazard == "wrong_payment_day":
        receipt["event"]["effective_at"] = "2026-11-29T15:00:00-03:00"
    elif hazard == "partial":
        receipt["event"].update(nominal_redeemed="50", cash_amount="75625.00")
    elif hazard == "same_source":
        source = FIXING
        receipt["event"]["source_sha256"] = digest(FIXING)
        receipt["entitlement"]["source_sha256"] = digest(FIXING)
    with pytest.raises(ValueError, match=reason):
        book(store, receipt, source, PAY_BOOKED)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 0


@pytest.mark.parametrize("changes", [
    {"redemption_basis": "FIXED_KNOWN_FACE", "indexation": "NONE"},
    {"series_id": "WRONG_ISSUER_SERIES"},
    {"known_at": OPENED},
])
def test_d30n6_final_review_requires_exact_current_indexed_schedule(store, changes):
    book(store, fixing_receipt(), FIXING, FIXING_BOOKED)
    book(store, payment_receipt(), PAYMENT, PAY_BOOKED)
    with pytest.raises(ValueError, match="FI_LELINK_ISSUER_SCHEDULE_UNVERIFIED"):
        review(store, **changes)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_native_redemption_reviews").fetchone()[0] == 0
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
