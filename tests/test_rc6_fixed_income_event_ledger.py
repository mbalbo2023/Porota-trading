"""Synthetic SQLite transitions; these are not live entitlement/source proofs."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from cf_sale_settlement import conservative_unconfirmed_availability
import rc6_fixed_income_event_ledger as event_ledger
from rc6_fixed_income_event_reducer import EconomicEvent, PositionSeed, reduce_events
from rc6_fixed_income_event_ledger import (
    ENTRY_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED, RECEIPT_SCHEMA,
    apply_verified_receipt, cash_effect, init_schema, snapshot_at,
)
from test_rc6_fixed_income_paper_bridge import position

SOURCE = b"SYNTHETIC issuer payment and entitlement evidence, not real terms"
SOURCE_SHA = hashlib.sha256(SOURCE).hexdigest()
PAY = "2026-10-29T15:00:00+00:00"
KNOWN = "2026-10-29T15:06:00+00:00"
BOOKED = "2026-10-29T16:00:00+00:00"


def lot(tmp_path):
    store, _ = position(tmp_path)
    with store.connect() as db:
        db.execute("DELETE FROM paper_fills WHERE side='SELL_SIMULATED'")
        db.execute("""UPDATE paper_positions SET status='OPEN',closed_at=NULL,
                   exit_price=NULL,exit_cost=NULL,gross_pnl=NULL,net_pnl=NULL,close_reason=NULL""")
        opened = db.execute("SELECT opened_at FROM paper_positions").fetchone()[0]
    init_schema(store)
    return store, conservative_unconfirmed_availability("A-24HS", opened).isoformat()


def receipt(settled, *, kind="COUPON", event_id="coupon-1", nominal="100", redeemed=None,
            cash="2", currency="USD", unit="0.02", effective=PAY, recorded=KNOWN):
    event = {"event_id": event_id, "obligation_id": event_id + "-obligation",
             "paper_id": "PAPER-AL30", "kind": kind, "effective_at": effective,
             "recorded_at": recorded, "source_kind": "SETTLEMENT_PROOF",
             "source_reference": "SYNTHETIC_OFFICIAL_PAYMENT_NOTICE",
             "source_sha256": SOURCE_SHA, "source_at": recorded,
             "payment_currency": currency, "cash_amount": cash,
             "nominal_base": nominal, "nominal_redeemed": redeemed}
    return {"schema": RECEIPT_SCHEMA, "paper_only": True, "real_routes_used": [],
            "review_authority": "RELEASE_REVIEWED_ECONOMIC_EVENT",
            "identity": ["AL30", "BONOS", "BYMA", "ARS", "A-24HS"],
            "event": event, "cash_per_nominal": unit,
            "entitlement": {"rule": "TRADE_BEFORE_EX_AND_SETTLED_BY_RECORD",
                            "source_sha256": SOURCE_SHA,
                            "source_reference": event["source_reference"],
                            "ex_at": "2026-10-29T12:00:00+00:00",
                            "record_at": "2026-10-29T13:00:00+00:00",
                            "paper_settled_at": settled,
                            "settlement_basis": "PAPER_CONSERVATIVE_FULL_SETTLEMENT_DATE",
                            "known_at": recorded}}


def apply(store, data, *, at=BOOKED, source=SOURCE, pin=None):
    raw = json.dumps(data, sort_keys=True).encode()
    return apply_verified_receipt(store, receipt_bytes=raw,
                                  approved_sha256=pin or hashlib.sha256(raw).hexdigest(),
                                  source_bytes=source, applied_at=at)


def counts(store):
    with store.connect() as db:
        return tuple(db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                     for table in ("paper_fi_lots", "paper_fi_economic_events", "paper_fi_cash_entries"))


def test_coupon_atomic_idempotent_currency_and_local_knowledge_cutoff(tmp_path):
    store, settled = lot(tmp_path)
    data = receipt(settled)
    first = apply(store, data)
    assert apply(store, data) == first
    assert counts(store) == (1, 1, 1)
    with store.connect() as db:
        assert cash_effect(db, "ARS", BOOKED) == 0
        assert cash_effect(db, "USD", KNOWN) == 0  # observed elsewhere, not ingested here yet
        assert cash_effect(db, "USD", BOOKED) == Decimal("2")
        assert cash_effect(db, "USD", BOOKED, income_only=True) == Decimal("2")
        assert snapshot_at(db, "PAPER-AL30", KNOWN).cash_by_currency == ()
        assert snapshot_at(db, "PAPER-AL30", BOOKED).cash_by_currency == (("USD", Decimal("2")),)
        assert db.execute("SELECT quantity FROM paper_positions").fetchone()[0] == "100"
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    assert PAPER_ONLY and not ENTRY_AUTHORITY and REAL_ROUTES_USED == ()


def test_partial_amortization_coupon_on_residual_and_maturity(tmp_path):
    store, settled = lot(tmp_path)
    partial = receipt(settled, kind="AMORTIZATION", event_id="amort-1",
                      redeemed="30", cash="30", unit="1")
    assert apply(store, partial).residual_nominal == Decimal("70")
    coupon = receipt(settled, event_id="coupon-2", nominal="70", cash="1.4",
                     effective="2026-11-02T15:00:00+00:00", recorded="2026-11-02T15:06:00+00:00")
    coupon["entitlement"].update(ex_at="2026-11-02T12:00:00+00:00", record_at="2026-11-02T13:00:00+00:00")
    apply(store, coupon, at="2026-11-02T16:00:00+00:00")
    final = receipt(settled, kind="AMORTIZATION", event_id="maturity", nominal="70",
                    redeemed="70", cash="70", unit="1", effective="2026-11-03T15:00:00+00:00",
                    recorded="2026-11-03T15:06:00+00:00")
    final["entitlement"].update(ex_at="2026-11-03T12:00:00+00:00", record_at="2026-11-03T13:00:00+00:00")
    result = apply(store, final, at="2026-11-03T16:00:00+00:00")
    assert result.residual_nominal == 0
    with store.connect() as db:
        assert cash_effect(db, "USD", "2026-11-03T16:00:00+00:00") == Decimal("101.4")
        assert cash_effect(db, "USD", "2026-11-03T16:00:00+00:00", income_only=True) == Decimal("1.4")
        assert snapshot_at(db, "PAPER-AL30", BOOKED).residual_nominal == Decimal("70")
    # Receiving principal is not an artificial SELL_SIMULATED or a rewritten buy.
    assert counts(store) == (1, 3, 3)


def test_coupon_entitlement_uses_nominal_at_record_date_not_current_residual(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled, kind="AMORTIZATION", event_id="amort-1",
                         redeemed="30", cash="30", unit="1"))
    later_coupon = receipt(settled, event_id="later-coupon", nominal="70", cash="1.4",
                           effective="2026-11-02T15:00:00+00:00", recorded="2026-11-02T15:06:00+00:00")
    # This record date preceded the amortization: 100 VN were entitled then.
    # Substituting today's 70 VN would silently alter contractual rights.
    with pytest.raises(ValueError, match="RECORD_DATE_NOMINAL_MISMATCH"):
        apply(store, later_coupon, at="2026-11-02T16:00:00+00:00")
    assert counts(store) == (1, 1, 1)


def test_fixing_is_durable_but_never_credits_cash(tmp_path):
    store, settled = lot(tmp_path)
    data = receipt(settled)
    event = data["event"]
    event.update(kind="FIXING", source_kind="OFFICIAL_FIXING", payment_currency=None,
                 cash_amount=None, nominal_base=None, nominal_redeemed=None,
                 fixing_code="SYNTHETIC_INDEX", fixing_value="1.5", fixing_unit="POINTS")
    data["entitlement"] = None
    result = apply(store, data)
    assert len(result.fixings) == 1 and result.cash_by_currency == ()
    assert counts(store) == (1, 1, 0)


def test_over_amortization_cannot_create_cash_or_negative_nominal(tmp_path):
    store, settled = lot(tmp_path)
    data = receipt(settled, kind="AMORTIZATION", redeemed="101", cash="101", unit="1")
    with pytest.raises(ValueError, match="OVER_AMORTIZATION"):
        apply(store, data)
    assert counts(store) == (0, 0, 0)


@pytest.mark.parametrize("mutation,error", [
    (lambda d: d["identity"].__setitem__(3, "USD"), "RECEIPT_IDENTITY_MISMATCH"),
    (lambda d: d.update(entitlement=None), "ENTITLEMENT_RULE_UNVERIFIED"),
    (lambda d: d["entitlement"].update(ex_at="2026-10-26T12:00:00+00:00"), "NOT_ENTITLED_OR_CLOCK_INVALID"),
    (lambda d: d["entitlement"].update(record_at="2026-10-28T12:00:00+00:00"), "NOT_ENTITLED_OR_CLOCK_INVALID"),
    (lambda d: d["entitlement"].update(paper_settled_at="2026-10-28T12:00:00+00:00"), "SETTLEMENT_MODEL_UNVERIFIED"),
    (lambda d: d["entitlement"].update(source_sha256="a"*64), "ENTITLEMENT_SOURCE_MISMATCH"),
    (lambda d: d["event"].update(cash_amount="200"), "PAYMENT_UNIT_MISMATCH"),
    (lambda d: d["event"].update(nominal_base="99", cash_amount="1.98"), "RECORD_DATE_NOMINAL_MISMATCH"),
    (lambda d: d["event"].update(payment_currency="UNKNOWN"), "Moneda|currency"),
])
def test_invalid_entitlement_or_amount_has_no_partial_state(tmp_path, mutation, error):
    store, settled = lot(tmp_path)
    data = receipt(settled)
    mutation(data)
    with pytest.raises(ValueError, match=error):
        apply(store, data)
    assert counts(store) == (0, 0, 0)


def test_approved_pin_and_source_bytes_are_checked_independently(tmp_path):
    store, settled = lot(tmp_path)
    with pytest.raises(ValueError, match="RECEIPT_PIN_MISMATCH"):
        apply(store, receipt(settled), pin="a"*64)
    with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
        apply(store, receipt(settled), source=b"other document")
    assert counts(store) == (0, 0, 0)


def test_failure_after_event_insert_rolls_back_lot_event_and_cash(tmp_path):
    store, settled = lot(tmp_path)
    with store.connect() as db:
        db.execute("""CREATE TRIGGER test_cash_failure BEFORE INSERT ON paper_fi_cash_entries
                   BEGIN SELECT RAISE(ABORT,'synthetic cash failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic cash failure"):
        apply(store, receipt(settled))
    assert counts(store) == (0, 0, 0)


def test_concurrent_exact_replays_pay_once(tmp_path):
    store, settled = lot(tmp_path)
    data = receipt(settled)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: apply(store, data), range(4)))
    assert results == [results[0]] * 4
    assert counts(store) == (1, 1, 1)


def test_event_id_collision_and_obligation_replay_preserve_prior_cash(tmp_path):
    store, settled = lot(tmp_path)
    data = receipt(settled)
    apply(store, data)
    changed = receipt(settled, cash="3", unit="0.03")
    with pytest.raises(ValueError, match="RECEIPT_REPLAY_CONFLICT"):
        apply(store, changed)
    data["event"]["event_id"] = "another-receipt-same-obligation"
    with pytest.raises(ValueError, match="OBLIGATION_REPLAY_CONFLICT"):
        apply(store, data)
    assert counts(store) == (1, 1, 1)


def test_capacity_bounds_new_events_but_keeps_exact_replay_idempotent(tmp_path, monkeypatch):
    store, settled = lot(tmp_path)
    monkeypatch.setattr(event_ledger, "MAX_LOT_EVENTS", 2)
    first = receipt(settled, event_id="coupon-1")
    second = receipt(settled, event_id="coupon-2")
    apply(store, first)
    full = apply(store, second)
    assert apply(store, first) == full
    assert apply(store, second) == full
    with pytest.raises(ValueError, match="FI_LEDGER_EVENT_BOUND_EXCEEDED"):
        apply(store, receipt(settled, event_id="coupon-3"))
    assert counts(store) == (1, 2, 2)
    with store.connect() as db:
        assert cash_effect(db, "USD", BOOKED) == Decimal("4")


@pytest.mark.parametrize("prior_kind", ["COUPON", "AMORTIZATION"])
def test_late_amortization_cannot_retroactively_change_paid_entitlement(tmp_path, prior_kind):
    store, settled = lot(tmp_path)
    prior = receipt(settled, kind=prior_kind, event_id="already-paid", cash="2",
                    redeemed="2" if prior_kind == "AMORTIZATION" else None,
                    unit="1" if prior_kind == "AMORTIZATION" else "0.02",
                    effective="2026-11-02T15:00:00+00:00", recorded="2026-11-02T15:06:00+00:00")
    prior["entitlement"].update(ex_at="2026-11-02T12:00:00+00:00", record_at="2026-11-02T13:00:00+00:00")
    apply(store, prior, at="2026-11-02T16:00:00+00:00")
    # Economically before the prior record date; learned/imported later.
    late = receipt(settled, kind="AMORTIZATION", event_id="late-amort", nominal="100",
                   redeemed="30", cash="30", unit="1", effective=PAY,
                   recorded="2026-11-03T15:06:00+00:00")
    if prior_kind == "COUPON":
        # Demonstrate why a new durable guard is required: knowledge-time
        # ordering in the arithmetic reducer alone accepts this contradiction.
        with store.connect() as db:
            seed = PositionSeed(**json.loads(db.execute("SELECT seed_json FROM paper_fi_lots").fetchone()[0]))
        result = reduce_events(seed, [EconomicEvent(**prior["event"]), EconomicEvent(**late["event"])],
                               as_of="2026-11-03T16:00:00+00:00")
        assert result.residual_nominal == Decimal("70")
    with pytest.raises(ValueError, match="FI_LEDGER_RETROACTIVE_AMORTIZATION_CHANGES_ENTITLEMENT"):
        apply(store, late, at="2026-11-03T16:00:00+00:00")
    assert counts(store) == (1, 1, 1)
    with store.connect() as db:
        assert cash_effect(db, "USD", "2026-11-03T16:00:00+00:00") == Decimal("2")
        assert snapshot_at(db, "PAPER-AL30", "2026-11-03T16:00:00+00:00").residual_nominal == (
            Decimal("98") if prior_kind == "AMORTIZATION" else Decimal("100"))


def test_late_amortization_after_paid_coupon_record_date_preserves_rights(tmp_path):
    store, settled = lot(tmp_path)
    coupon = receipt(settled, event_id="paid-coupon", effective="2026-11-02T15:00:00+00:00",
                     recorded="2026-11-02T15:06:00+00:00")
    coupon["entitlement"].update(ex_at="2026-11-02T12:00:00+00:00", record_at="2026-11-02T13:00:00+00:00")
    apply(store, coupon, at="2026-11-02T16:00:00+00:00")
    late = receipt(settled, kind="AMORTIZATION", event_id="late-after-record", redeemed="30",
                   cash="30", unit="1", effective="2026-11-02T14:00:00+00:00",
                   recorded="2026-11-03T15:06:00+00:00")
    result = apply(store, late, at="2026-11-03T16:00:00+00:00")
    assert result.residual_nominal == Decimal("70")
    assert result.cash_by_currency == (("USD", Decimal("32")),)
    assert counts(store) == (1, 2, 2)


def test_market_partial_sale_is_explicitly_unsupported_not_assumed_entitled(tmp_path):
    store, settled = lot(tmp_path)
    with store.connect() as db:
        db.execute("""INSERT INTO paper_fills(paper_id,source,side,filled_at,quantity,price,costs,slippage)
                   VALUES('PAPER-AL30','PRODUCTION_PAPER','SELL_SIMULATED',?,'10','100','0','0')""", (PAY,))
    with pytest.raises(ValueError, match="INTACT_BUY_LOT_REQUIRED"):
        apply(store, receipt(settled))
    assert counts(store) == (0, 0, 0)


def test_changed_buy_identity_and_paper_safety_never_credit_cash(tmp_path):
    store, settled = lot(tmp_path)
    apply(store, receipt(settled))
    with store.connect() as db:
        db.execute("UPDATE paper_positions SET entry_price='101'")
        db.execute("UPDATE paper_fills SET price='101'")
    with pytest.raises(ValueError, match="IMMUTABLE_SEED_CHANGED"):
        apply(store, receipt(settled, event_id="coupon-2"))
    with store.connect() as db:
        db.execute("UPDATE observer_state SET mode='LIVE'")
    with pytest.raises(ValueError, match="PAPER_SAFETY_REQUIRED"):
        apply(store, receipt(settled))
    assert counts(store) == (1, 1, 1)
