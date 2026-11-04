"""Synthetic PAPER contract tests; request hashes are not real issuer approval."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest

from rc6_fixed_income_event_ledger import cash_effect
from rc6_fixed_income_native_exit_contract import (
    ENTRY_AUTHORITY, NATIVE_EXIT_AUTHORITY, PAPER_ONLY, REAL_ROUTES_USED, SCHEMA,
    assess_blocked_exit, init_schema,
)
from rc6_fixed_income_portfolio_bridge import position_at
from test_rc6_fixed_income_event_ledger import BOOKED, apply, lot, receipt
import test_rc6_fixed_income_native_redemption as redemption_fixture

SOURCE_SHA = "a" * 64  # synthetic attempt metadata, never release authority


def payload(*, kind="MARKET_SALE", nominal="70", request_id="exit-a", **overrides):
    value = {
        "schema": SCHEMA, "paper_only": True, "real_routes_used": [],
        "paper_id": "PAPER-AL30", "request_id": request_id,
        "kind": kind, "as_of": BOOKED,
        "requested_nominal": nominal if kind == "MARKET_SALE" else "0",
        "quote_currency": "ARS" if kind == "MARKET_SALE" else None,
        "bid": "103" if kind == "MARKET_SALE" else None,
        "available_nominal": "80" if kind == "MARKET_SALE" else None,
        "fee_amount": "0.61" if kind == "MARKET_SALE" else None,
        "fee_currency": "ARS" if kind == "MARKET_SALE" else None,
        "settlement_currency": "ARS", "source_reference": "SYNTHETIC_QUOTE_OR_ISSUER",
        "source_sha256": SOURCE_SHA,
        "source_at": "2026-10-29T15:00:00+00:00",
        "known_at": "2026-10-29T15:06:00+00:00",
        "fixing_at": None, "fx_source_sha256": None,
    }
    value.update(overrides)
    return value


def assess(store, value, pin=None):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return assess_blocked_exit(store, request_bytes=raw,
                               approved_sha256=pin or hashlib.sha256(raw).hexdigest())


def state(store):
    with store.connect() as db:
        return {
            "position": tuple(db.execute("SELECT status,quantity,entry_cost,closed_at FROM paper_positions").fetchone()),
            "fills": db.execute("SELECT count(*) FROM paper_fills").fetchone()[0],
            "events": db.execute("SELECT count(*) FROM paper_fi_economic_events").fetchone()[0],
            "cash_rows": db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0],
            "cash_ars": cash_effect(db, "ARS", BOOKED),
            "cash_usd": cash_effect(db, "USD", BOOKED),
            "orders": db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0],
        }


def amortized(store, settled):
    item = receipt(settled, kind="AMORTIZATION", event_id="partial", redeemed="30",
                   cash="30", unit="1", currency="USD")
    apply(store, item)
    with store.connect() as db:
        assert position_at(db, "PAPER-AL30", BOOKED).residual_nominal == Decimal("70")


def test_market_sale_uses_residual_not_original_and_preserves_original_cash(tmp_path):
    store, settled = lot(tmp_path)
    amortized(store, settled)
    init_schema(store)
    before = state(store)
    over = assess(store, payload(nominal="100", request_id="over"))
    assert "FI_EXIT_QUANTITY_EXCEEDS_RESIDUAL" in over["blockers"]
    valid_quantity = assess(store, payload())
    assert "FI_EXIT_QUANTITY_EXCEEDS_RESIDUAL" not in valid_quantity["blockers"]
    assert valid_quantity["residual_nominal"] == "70"
    assert valid_quantity["status"] == "BLOCKED"
    assert valid_quantity["blockers"][-1] == "FI_EXIT_ENTITLEMENT_AWARE_SALE_LEDGER_UNBOUND"
    assert state(store) == before
    assert PAPER_ONLY and not (ENTRY_AUTHORITY or NATIVE_EXIT_AUTHORITY or REAL_ROUTES_USED)


def test_negative_fee_depth_unit_fx_and_future_fixing_are_specific(tmp_path):
    store, settled = lot(tmp_path)
    amortized(store, settled)
    init_schema(store)
    cases = (
        (dict(available_nominal="69"), "FI_EXIT_QUOTE_DEPTH_INSUFFICIENT"),
        (dict(requested_nominal="0.5"), "FI_EXIT_NOMINAL_CONTRACT_STEP_INVALID"),
        (dict(fee_currency="USD"), "FI_EXIT_FEE_CURRENCY_MISMATCH"),
        (dict(quote_currency="USD"), "FI_EXIT_QUOTE_CURRENCY_MISMATCH"),
        (dict(settlement_currency="USD"), "FI_EXIT_CROSS_CURRENCY_FX_UNVERIFIED"),
        (dict(settlement_currency="USD", fx_source_sha256=SOURCE_SHA),
         "FI_EXIT_CROSS_CURRENCY_CASH_LEDGER_UNBOUND"),
        (dict(fixing_at="2026-11-25T18:00:00+00:00"), "FI_EXIT_FUTURE_FIXING"),
    )
    before = state(store)
    for index, (mutation, expected) in enumerate(cases):
        value = payload(request_id=f"negative-{index}")
        value.update(mutation)
        assert expected in assess(store, value)["blockers"]
    assert state(store) == before
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_native_exit_preflights").fetchone()[0] == len(cases)


def test_replay_conflict_concurrent_retry_and_transaction_rollback(tmp_path):
    store, settled = lot(tmp_path)
    amortized(store, settled)
    init_schema(store)
    request = payload()
    before = state(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: assess(store, request), range(4)))
    assert results == [results[0]] * 4
    changed = payload(bid="104")
    with pytest.raises(ValueError, match="FI_EXIT_PREFLIGHT_REPLAY_CONFLICT"):
        assess(store, changed)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_native_exit_preflights").fetchone()[0] == 1
        db.execute("""CREATE TRIGGER reject_next_preflight BEFORE INSERT ON paper_fi_native_exit_preflights
                      BEGIN SELECT RAISE(ABORT,'synthetic journal write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="synthetic journal write failure"):
        assess(store, payload(request_id="new-fails"))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_native_exit_preflights").fetchone()[0] == 1
        db.execute("DROP TRIGGER reject_next_preflight")
    assert assess(store, payload(request_id="new-fails"))["status"] == "BLOCKED"
    assert state(store) == before


def test_exact_retry_rechecks_lot_after_a_later_event(tmp_path):
    store, settled = lot(tmp_path)
    amortized(store, settled)
    init_schema(store)
    first = payload()
    assert assess(store, first)["status"] == "BLOCKED"
    later = receipt(settled, event_id="later-coupon", nominal="70", cash="1.4",
                    currency="USD", effective="2026-11-02T15:00:00+00:00",
                    recorded="2026-11-02T15:06:00+00:00")
    later["entitlement"].update(ex_at="2026-11-02T12:00:00+00:00",
                                record_at="2026-11-02T13:00:00+00:00",
                                known_at="2026-11-02T15:06:00+00:00")
    apply(store, later, at="2026-11-02T16:00:00+00:00")
    with pytest.raises(ValueError, match="FI_EXIT_PREFLIGHT_STATE_CHANGED"):
        assess(store, first)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_native_exit_preflights").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 2


def test_maturity_without_review_and_future_fixing_cannot_close(tmp_path):
    store, settled = lot(tmp_path)
    final = receipt(settled, kind="AMORTIZATION", event_id="maturity", redeemed="100",
                    cash="100", unit="1", currency="USD")
    apply(store, final)
    init_schema(store)
    before = state(store)
    result = assess(store, payload(kind="MATURITY", request_id="maturity",
                                   fixing_at="2026-11-25T18:00:00+00:00"))
    assert "FI_EXIT_FUTURE_FIXING" in result["blockers"]
    assert "FI_EXIT_FINAL_REDEMPTION_REVIEW_MISSING" in result["blockers"]
    assert "FI_EXIT_MATURITY_CLOSE_LEDGER_UNBOUND" in result["blockers"]
    assert "FI_EXIT_NOT_FULLY_REDEEMED" not in result["blockers"]
    assert state(store) == before


def test_even_reviewed_terminal_cash_is_never_paid_or_closed_twice():
    fixture = redemption_fixture.NativeRedemptionReviewTest(
        "test_bond_full_redemption_review_is_idempotent_and_never_pays_or_closes")
    fixture.setUp()
    try:
        fixture._book()
        fixture._apply()
        init_schema(fixture.store)
        before = state(fixture.store)
        result = assess(fixture.store, payload(kind="MATURITY", request_id="reviewed"))
        assert "FI_EXIT_FINAL_REDEMPTION_REVIEW_MISSING" not in result["blockers"]
        assert result["blockers"] == ["FI_EXIT_MATURITY_CLOSE_LEDGER_UNBOUND"]
        assert state(fixture.store) == before
        with fixture.store.connect() as db:
            assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1
            assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize("mutation,error", [
    (dict(paper_only=False), "FI_EXIT_PAPER_ONLY_REQUIRED"),
    (dict(source_at="2026-11-02T00:00:00+00:00"), "FI_EXIT_SOURCE_CLOCK_INVALID"),
    (dict(requested_nominal="-1"), "FI_EXIT_NOMINAL"),
    (dict(fee_amount="-1"), "FI_EXIT_FEE"),
])
def test_invalid_request_never_writes(tmp_path, mutation, error):
    store, settled = lot(tmp_path)
    amortized(store, settled)
    init_schema(store)
    with pytest.raises(ValueError, match=error):
        assess(store, payload(**mutation))
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_native_exit_preflights").fetchone()[0] == 0
