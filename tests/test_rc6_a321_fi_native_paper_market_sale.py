"""A321 synthetic sources exercise the PAPER transaction, never PPI authority."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest

from be_paper_engine import PaperBroker, Quote
from bs_instrument_contracts import InstrumentContract
from rc6_fixed_income_event_ledger import _canonical, apply_verified_receipt
from rc6_fixed_income_paper_market_sale import (
    BOOK_SCHEMA, FEE_SCHEMA, REASON, apply_paper_market_sale,
    sale_cash_effect, sale_rows_checked,
)
from rc6_fixed_income_portfolio_bridge import position_at
from rc6_fixed_income_native_exit import apply_reviewed_maturity
from test_rc6_fixed_income_native_redemption import NativeRedemptionReviewTest

BOOK = "2026-10-30T15:59:58+00:00"
OBSERVED = "2026-10-30T16:00:00+00:00"
APPLIED = "2026-10-30T16:00:01+00:00"
AFTER_T1 = "2026-11-03T04:00:00+00:00"


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(value):
    return _canonical(value).encode("utf-8")


def lot(family="BONOS"):
    fixture = NativeRedemptionReviewTest(
        "test_bond_full_redemption_review_is_idempotent_and_never_pays_or_closes")
    fixture.setUp()
    if family != "BONOS":
        fixture._set_family(family)
    broker = PaperBroker(fixture.store, initial_cash="1000",
                         daily_loss_pct=None, participation="1")
    with fixture.store.connect() as db:
        p = db.execute("SELECT * FROM paper_positions").fetchone()
        spec = json.loads(p["features_json"])["financial_contract"]
    contract = InstrumentContract(**spec)
    q = Quote(p["symbol"],p["asset_class"],p["settlement"],
              last=Decimal("110"),bid=Decimal("110"),ask=Decimal("111"),
              bid_size=Decimal("100"),ask_size=Decimal("100"),
              observed_at=OBSERVED,book_at=BOOK,contract=contract,
              currency="ARS",market="BYMA",
              metadata_source="PPI_PRIMARY_LIVE_BOOK:SYNTHETIC_NOT_AUTHENTICATED")
    fixture.store.add_quote(q)
    return fixture,broker,q


def sale_evidence(broker,q,*,nominal="100",override_book=None,override_fee=None):
    root = [q.symbol,q.asset_class,q.market,q.currency,q.settlement]
    book = {
        "schema":BOOK_SCHEMA,"paper_only":True,
        "provider":"PPI_PRIMARY_READ_ONLY","route":"MarketData/Book",
        "identity":root,"book_at":q.book_at,"observed_at":q.observed_at,
        "bid":str(q.bid),"ask":str(q.ask),
        "bid_size":str(q.bid_size),"ask_size":str(q.ask_size),
        "quantity_unit":"VN","metadata_source":q.metadata_source,
    }
    price = (q.bid*(1-broker.slippage)).quantize(Decimal("0.0001"))
    cost = broker._cost(price*q.contract.cash_multiplier,
                        Decimal(nominal),q.asset_class,currency=q.currency)
    fee = {
        "schema":FEE_SCHEMA,"paper_only":True,
        "identity":root,"paper_id":"PAPER-AL30",
        "nominal":str(Decimal(nominal)),"exit_price":str(price),
        "exit_fee":str(cost),"currency":q.currency,
        "slippage":str(broker.slippage),"policy":"PAPER_COST_MODEL_NOT_PPI_ACCOUNT_FEE",
    }
    book.update(override_book or {})
    fee.update(override_fee or {})
    return _raw(book), _raw(fee)


def run(fixture,broker,q,*,nominal="100",request="first",book=None,fee=None,at=APPLIED):
    book,fee = (book,fee) if book is not None and fee is not None else sale_evidence(
        broker,q,nominal=nominal)
    return apply_paper_market_sale(
        fixture.store,broker,paper_id="PAPER-AL30",request_id=request,
        nominal=nominal,quote=q,book_bytes=book,approved_book_sha256=_hash(book),
        fee_bytes=fee,approved_fee_sha256=_hash(fee),applied_at=at)


@pytest.mark.parametrize("family",["BONOS","LETRAS","OBLIGACIONES"])
def test_native_full_sale_paper_modeled_cash_is_never_issuer_cash(family,monkeypatch):
    fx,broker,q = lot(family)
    try:
        initial = broker._cash(APPLIED,"ARS")
        assert initial == Decimal("899.39")
        result = run(fx,broker,q)
        assert result["closed_paper"] is True
        assert result["reason"] == REASON
        assert result["broker_order_sent"] is False
        assert result["real_orders_sent"] == 0
        assert result["settlement_proof"] == "PAPER_MODELED_NOT_ACCOUNT_MOVEMENT"
        assert broker._cash(APPLIED,"ARS") == initial
        with fx.store.connect() as db:
            assert db.execute("SELECT status,close_reason FROM paper_positions").fetchone()[:] == (
                "CLOSED",REASON)
            assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 2
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM paper_fi_economic_events").fetchone()[0] == 1
            assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
            assert sale_cash_effect(db,"ARS",APPLIED,pending=True) == Decimal(result["net_proceeds"])
            assert sale_cash_effect(db,"ARS",APPLIED,pending=False) == 0
            assert position_at(db,"PAPER-AL30",APPLIED).residual_nominal == 0
        assert fx.store.exit_positions() == ([],[])
        assert len(fx.store.recent_closed()) == 1
        monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE","true")
        assert broker._cash(AFTER_T1,"ARS") == initial + Decimal(result["net_proceeds"])
        assert run(fx,broker,q,at=AFTER_T1) == result
    finally:
        fx.doCleanups()


def test_partial_then_final_sale_consumes_same_book_depth_and_no_double_basis(monkeypatch):
    fx,broker,q = lot()
    try:
        first = run(fx,broker,q,nominal="30")
        with fx.store.connect() as db:
            assert position_at(db,"PAPER-AL30",APPLIED).residual_nominal == 70
        opened = fx.store.open_position("AL30")
        assert opened["quantity"] == "70"
        assert len(fx.store.exit_positions()[0]) == 1
        later = "2026-10-30T16:00:02+00:00"
        second = run(fx,broker,q,nominal="70",request="second",at=later)
        assert second["closed_paper"]
        with fx.store.connect() as db:
            assert len(sale_rows_checked(db,"PAPER-AL30")) == 2
            assert db.execute("SELECT count(*) FROM paper_book_consumption").fetchone()[0] == 2
            assert sum((Decimal(r[0]) for r in db.execute(
                "SELECT nominal FROM paper_fi_native_sales")),Decimal(0)) == Decimal(100)
            assert sum((Decimal(r[0]) for r in db.execute(
                "SELECT allocated_entry_cost FROM paper_fi_native_sales")),Decimal(0)) == Decimal("0.61")
            assert position_at(db,"PAPER-AL30",later).residual_nominal == 0
        assert broker._cash(later,"ARS") == Decimal("899.39")
        monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE","true")
        assert broker._cash(AFTER_T1,"ARS") == (
            Decimal("899.39") + Decimal(first["net_proceeds"]) +
            Decimal(second["net_proceeds"]))
    finally:
        fx.doCleanups()


@pytest.mark.parametrize("mutation,error",[
    (("book",{"bid":"111"}),"FI_NATIVE_SALE_EXACT_PPI_BOOK_PIT_REQUIRED"),
    (("book",{"quantity_unit":"LOT"}),"FI_NATIVE_SALE_EXACT_PPI_BOOK_PIT_REQUIRED"),
    (("fee",{"exit_fee":"0"}),"FI_NATIVE_SALE_FEE_MODEL_RECEIPT_MISMATCH"),
    (("fee",{"currency":"USD"}),"FI_NATIVE_SALE_FEE_MODEL_RECEIPT_MISMATCH"),
])
def test_wrong_source_unit_price_or_fee_cannot_change_cash(mutation,error):
    fx,broker,q = lot()
    try:
        baseline = broker._cash(APPLIED,"ARS")
        kind,changes=mutation
        book,fee=sale_evidence(broker,q,
            override_book=changes if kind=="book" else None,
            override_fee=changes if kind=="fee" else None)
        with pytest.raises(ValueError,match=error):
            run(fx,broker,q,book=book,fee=fee)
        assert broker._cash(APPLIED,"ARS")==baseline
        with fx.store.connect() as db:
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0]==0
    finally:
        fx.doCleanups()


def test_depth_replay_conflict_and_atomic_rollback():
    fx,broker,q=lot()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _: run(fx,broker,q,nominal="30"),range(4)))
        assert results == [results[0]]*4
        with pytest.raises(ValueError,match="FI_NATIVE_SALE_REPLAY_CONFLICT"):
            run(fx,broker,q,nominal="40")
        with fx.store.connect() as db:
            db.execute("""CREATE TRIGGER fail_sale_event BEFORE INSERT ON paper_fi_native_sales
              BEGIN SELECT RAISE(ABORT,'synthetic sale receipt crash'); END""")
        with pytest.raises(sqlite3.IntegrityError,match="synthetic sale receipt crash"):
            run(fx,broker,q,nominal="40",request="fail")
        with fx.store.connect() as db:
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0]==2
            assert db.execute("SELECT count(*) FROM paper_fi_economic_events").fetchone()[0]==1
    finally:
        fx.doCleanups()


def test_new_request_cannot_rewind_ledger_book_clock_behind_prior_sale():
    fx,broker,q=lot()
    try:
        first=run(fx,broker,q,nominal="30")
        with pytest.raises(ValueError,match="FI_NATIVE_SALE_BOOKING_CLOCK_ROLLBACK"):
            run(fx,broker,q,nominal="70",request="historically-earlier",
                at=OBSERVED)
        with fx.store.connect() as db:
            assert len(sale_rows_checked(db,"PAPER-AL30"))==1
            assert db.execute("SELECT count(*) FROM paper_fi_economic_events").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0]==2
            assert position_at(db,"PAPER-AL30",APPLIED).residual_nominal==70
        assert broker._cash(APPLIED,"ARS")==Decimal("899.39")
        assert first["broker_order_sent"] is False
    finally:
        fx.doCleanups()


def test_injected_generic_fi_sell_cannot_be_valued_as_native_cash_or_exit():
    fx,broker,q=lot()
    try:
        with fx.store.connect() as db:
            db.execute("""INSERT INTO paper_fills
                (paper_id,source,side,filled_at,quantity,price,costs,slippage)
                VALUES(?,?,?,?,?,?,?,?)""",
                ("PAPER-AL30","PRODUCTION_PAPER","SELL_SIMULATED",
                 APPLIED,"100","110","0","0"))
            db.execute("""UPDATE paper_positions SET status='CLOSED',closed_at=?,
                close_reason='LEGACY_GENERIC_FI_SELL',exit_price='110',
                exit_cost='0',gross_pnl='10',net_pnl='10'
                WHERE paper_id='PAPER-AL30'""",(APPLIED,))
        with pytest.raises(ValueError,match="FI_GENERIC_SELL_NATIVE_RECEIPT_REQUIRED"):
            broker._cash(APPLIED,"ARS")
        with pytest.raises(ValueError,match="FI_GENERIC_SELL_NATIVE_RECEIPT_REQUIRED"):
            broker.mark_equity({},as_of=APPLIED)
        with fx.store.connect() as db:
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0]==0
            assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0]==0
    finally:
        fx.doCleanups()


def test_no_unapproved_cross_currency_conversion_or_stale_source():
    fx,broker,q = lot()
    try:
        bad = replace(q,currency="USD")
        with pytest.raises(ValueError,match="FI_NATIVE_SALE_EXACT_PPI_BOOK_PIT_REQUIRED"):
            run(fx,broker,bad)
        with pytest.raises(ValueError,match="FI_NATIVE_SALE_EXACT_PPI_BOOK_PIT_REQUIRED"):
            run(fx,broker,q,at="2026-10-30T19:00:00+00:00")
        with fx.store.connect() as db:
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0] == 0
    finally:
        fx.doCleanups()


def test_coupon_record_date_after_partial_sale_books_only_remaining_vn():
    fx,broker,q=lot()
    try:
        sold=run(fx,broker,q,nominal="30")
        coupon=fx._economic_receipt(event_id="post-sale-coupon")
        coupon["event"].update(
            kind="COUPON",effective_at="2026-11-03T15:00:00+00:00",
            recorded_at="2026-11-03T15:06:00+00:00",
            source_at="2026-11-03T15:06:00+00:00",
            nominal_base="70",nominal_redeemed=None,cash_amount="1.4")
        coupon["cash_per_nominal"]="0.02"
        coupon["entitlement"].update(
            ex_at="2026-11-03T12:00:00+00:00",
            record_at="2026-11-03T13:00:00+00:00",
            known_at="2026-11-03T15:06:00+00:00")
        fx._book(economic=coupon,at="2026-11-03T16:00:00+00:00")
        with fx.store.connect() as db:
            view=position_at(db,"PAPER-AL30","2026-11-03T16:00:00+00:00")
            assert view.residual_nominal == Decimal(70)
            assert view.coupon_by_currency == (("ARS",Decimal("1.4")),)
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0]==1
        assert broker._cash("2026-11-03T16:00:00+00:00","ARS") == Decimal("900.79")
        assert sold["broker_order_sent"] is False
    finally:
        fx.doCleanups()


def test_late_pre_sale_record_date_coupon_keeps_earned_rights_without_double_credit():
    fx,broker,q=lot()
    try:
        run(fx,broker,q,nominal="30")
        coupon=fx._economic_receipt(event_id="late-coupon")
        coupon["event"].update(
            kind="COUPON",effective_at="2026-11-03T15:00:00+00:00",
            recorded_at="2026-11-03T15:06:00+00:00",
            source_at="2026-11-03T15:06:00+00:00",
            nominal_base="100",nominal_redeemed=None,cash_amount="2")
        coupon["cash_per_nominal"]="0.02"
        coupon["entitlement"].update(
            ex_at="2026-10-29T12:00:00+00:00",
            record_at="2026-10-29T13:00:00+00:00",
            known_at="2026-11-03T15:06:00+00:00")
        fx._book(economic=coupon,at="2026-11-03T16:00:00+00:00")
        with fx.store.connect() as db:
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0]==1
            view=position_at(db,"PAPER-AL30","2026-11-03T16:00:00+00:00")
            assert view.coupon_by_currency == (("ARS",Decimal(2)),)
            assert view.residual_nominal == Decimal(70)
        assert broker._cash("2026-11-03T16:00:00+00:00","ARS") == Decimal("901.39")
    finally:
        fx.doCleanups()


def test_late_record_date_coupon_after_full_market_exit_survives_risk_replay():
    fx,broker,q=lot()
    try:
        sold=run(fx,broker,q)
        coupon=fx._economic_receipt(event_id="late-full-exit-coupon")
        coupon["event"].update(
            kind="COUPON",effective_at="2026-11-03T15:00:00+00:00",
            recorded_at="2026-11-03T15:06:00+00:00",
            source_at="2026-11-03T15:06:00+00:00",
            nominal_base="100",nominal_redeemed=None,cash_amount="2")
        coupon["cash_per_nominal"]="0.02"
        coupon["entitlement"].update(
            ex_at="2026-10-29T12:00:00+00:00",
            record_at="2026-10-29T13:00:00+00:00",
            known_at="2026-11-03T15:06:00+00:00")
        known="2026-11-03T16:00:00+00:00"
        fx._book(economic=coupon,at=known)
        with fx.store.connect() as db:
            view=position_at(db,"PAPER-AL30",known)
            assert view.residual_nominal==0
            assert view.coupon_by_currency==(("ARS",Decimal(2)),)
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0]==2
        assert broker._cash(known,"ARS")==Decimal("901.39")
        equity=broker.mark_equity({},as_of=known)
        assert equity["ARS"]["realized_pnl"]==Decimal(sold["net_pnl"])+2
        audit=PaperBroker(fx.store,initial_cash="1000",daily_loss_pct="5")
        state=audit.daily_risk.evaluate(known)["ARS"]
        assert Decimal(state["last_equity"])==Decimal(1000)+Decimal(sold["net_pnl"])+2
        assert fx.store.exit_positions()==([],[])
    finally:
        fx.doCleanups()


def test_retroactive_issuer_amortization_cannot_rewrite_previously_sold_vn():
    fx,broker,q=lot()
    try:
        sold=run(fx,broker,q,nominal="30")
        economic=fx._economic_receipt(redeemed="30",event_id="retro-principal")
        economic["event"].update(
            effective_at="2026-10-30T15:00:00+00:00",
            recorded_at="2026-11-03T15:06:00+00:00",
            source_at="2026-11-03T15:06:00+00:00")
        economic["entitlement"].update(
            ex_at="2026-10-29T12:00:00+00:00",
            record_at="2026-10-29T13:00:00+00:00",
            known_at="2026-11-03T15:06:00+00:00")
        with pytest.raises(ValueError,match="FI_LEDGER_RETROACTIVE_AMORTIZATION_CHANGES_SOLD_BASIS"):
            fx._book(economic=economic,at="2026-11-03T16:00:00+00:00")
        with fx.store.connect() as db:
            assert position_at(db,"PAPER-AL30",APPLIED).residual_nominal==70
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0]==1
        assert broker._cash(APPLIED,"ARS")==Decimal("899.39")
        assert sold["broker_order_sent"] is False
    finally:
        fx.doCleanups()


def test_issuer_coupon_foreign_currency_after_sale_never_converts_to_sale_currency():
    fx,broker,q=lot()
    try:
        run(fx,broker,q,nominal="30")
        coupon=fx._economic_receipt(event_id="usd-coupon")
        coupon["event"].update(
            kind="COUPON",effective_at="2026-11-03T15:00:00+00:00",
            recorded_at="2026-11-03T15:06:00+00:00",
            source_at="2026-11-03T15:06:00+00:00",
            nominal_base="70",nominal_redeemed=None,
            payment_currency="USD",cash_amount="0.7")
        coupon["cash_per_nominal"]="0.01"
        coupon["entitlement"].update(
            ex_at="2026-11-03T12:00:00+00:00",
            record_at="2026-11-03T13:00:00+00:00",
            known_at="2026-11-03T15:06:00+00:00")
        known="2026-11-03T16:00:00+00:00"
        fx._book(economic=coupon,at=known)
        assert broker._cash(known,"ARS")==Decimal("899.39")
        assert broker._cash(known,"USD")==Decimal("0.7")
        with fx.store.connect() as db:
            view=position_at(db,"PAPER-AL30",known)
            assert view.residual_nominal==70
            assert view.coupon_by_currency==(("USD",Decimal("0.7")),)
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0]==1
        report=broker.mark_equity({},as_of=known)["ARS"]
        assert report["realized_pnl"]==Decimal("2.613400")
        audit=PaperBroker(fx.store,initial_cash="1000",daily_loss_pct="5")
        assert audit.daily_risk.evaluate(known)["ARS"]["state"] != "READY"
    finally:
        fx.doCleanups()


def test_partial_issuer_amortization_then_sale_remaining_vn_preserves_both_cash_legs(monkeypatch):
    fx,broker,q=lot()
    try:
        fx._book(redeemed="30")
        before=broker._cash(APPLIED,"ARS")
        assert before == Decimal("929.39")
        result=run(fx,broker,q,nominal="70")
        assert result["closed_paper"]
        assert broker._cash(APPLIED,"ARS") == before
        with fx.store.connect() as db:
            view=position_at(db,"PAPER-AL30",APPLIED)
            assert view.residual_nominal == 0
            assert view.redeemed_nominal == Decimal(30)
            assert view.principal_by_currency == (("ARS",Decimal(30)),)
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        equity=broker.mark_equity({},as_of=APPLIED)
        assert equity["ARS"]["pending_proceeds"] == Decimal(result["net_proceeds"])
        monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE","true")
        assert broker._cash(AFTER_T1,"ARS")==before+Decimal(result["net_proceeds"])
        risk_broker=PaperBroker(fx.store,initial_cash="1000",daily_loss_pct="5")
        assert risk_broker.daily_risk.evaluate(AFTER_T1)["ARS"]["state"] != "INVALID_LEDGER"
    finally:
        fx.doCleanups()


def test_partial_market_sale_then_remaining_issuer_maturity_deduplicates_principal_and_sale(monkeypatch):
    fx,broker,q=lot()
    try:
        sale=run(fx,broker,q,nominal="30")
        final=fx._economic_receipt(redeemed="70",event_id="final")
        final["event"].update(nominal_base="70",
            effective_at="2026-11-03T15:00:00+00:00",
            recorded_at="2026-11-03T15:06:00+00:00",
            source_at="2026-11-03T15:06:00+00:00")
        final["entitlement"].update(
            ex_at="2026-11-03T12:00:00+00:00",
            record_at="2026-11-03T13:00:00+00:00",
            known_at="2026-11-03T15:06:00+00:00")
        booked="2026-11-03T16:00:00+00:00"
        fx._book(economic=final,at=booked)
        review=fx._review()
        review.update(final_event_id="final",
            paid_obligation_ids=["final-obligation"],
            final_principal_cash="70",final_nominal_redeemed="70",
            expected_principal_by_currency=[["ARS","70"]],
            expected_cash_by_currency=[["ARS","70"]])
        schedule=review["issuer_schedule"]
        schedule["maturity_at"]=final["event"]["effective_at"]
        schedule["final_obligation_id"]="final-obligation"
        schedule["obligations"]=[{key:final["event"][key] for key in (
            "obligation_id","kind","effective_at","payment_currency",
            "cash_amount","nominal_base","nominal_redeemed")}]
        accepted=fx._apply(review,at=booked)
        terminal_at="2026-11-03T17:00:00+00:00"
        before_cash=broker._cash(terminal_at,"ARS")
        terminal=apply_reviewed_maturity(
            fx.store,paper_id="PAPER-AL30",request_id="remaining-native-maturity",
            approved_review_receipt_sha256=accepted["receipt_sha256"],
            applied_at=terminal_at)
        assert terminal["economic_cash_written"]=="0"
        assert terminal["sell_fills_created"]==0
        assert broker._cash(terminal_at,"ARS")==before_cash
        with fx.store.connect() as db:
            view=position_at(db,"PAPER-AL30",terminal_at)
            assert view.initial_nominal==100 and view.residual_nominal==0
            assert view.redeemed_nominal==70
            assert view.principal_by_currency==(("ARS",Decimal(70)),)
            assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fi_native_sales").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0]==2
            assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0]==0
        assert len(fx.store.recent_closed())==1
        assert fx.store.exit_positions()==([],[])
        historical,realized=broker._positions_at(APPLIED,broker.store.connect())
        assert historical and historical[0]["quantity"]=="70"
        assert len(realized)==1 and realized[0]["close_reason"]==REASON
        equity=broker.mark_equity({},as_of=terminal_at)
        assert equity["ARS"]["pending_proceeds"]==Decimal(sale["net_proceeds"])
        combined_pnl=Decimal(sale["net_pnl"])+Decimal(terminal["realized_pnl"])
        assert equity["ARS"]["realized_pnl"]==combined_pnl
        risk=PaperBroker(fx.store,initial_cash="1000",daily_loss_pct="5")
        risk_snapshot=risk.daily_risk.evaluate(terminal_at)["ARS"]
        assert Decimal(risk_snapshot["last_equity"])==Decimal(1000)+combined_pnl
        assert risk_snapshot["state"] != "INVALID_LEDGER"
        monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE","true")
        assert broker._cash("2026-11-04T04:00:00+00:00","ARS") == (
            Decimal("899.39")+Decimal(70)+Decimal(sale["net_proceeds"]))
    finally:
        fx.doCleanups()
