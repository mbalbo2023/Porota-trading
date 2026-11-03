from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from bt_caucion_paper import CaucionOffer
from di_caucion_cash_sweep_runtime_hf6 import (
    CASH_SWEEP_ORDER_ROUTING_ALLOWED,
    CashObligation,
    ObligationSnapshot,
    exact_fee_offers,
    required_reserve,
)

TZ=ZoneInfo("America/Argentina/Buenos_Aires")


def test_cash_sweep_has_no_real_order_routing():
    assert CASH_SWEEP_ORDER_ROUTING_ALLOWED is False


def test_reserve_is_verified_obligations_not_fixed_percentage():
    now=datetime(2026,9,3,16,30,tzinfo=TZ)
    deadline=datetime(2026,9,4,10,30,tzinfo=TZ)
    snap=ObligationSnapshot(
        observed_at=now.isoformat(),source="TEST_LEDGER",complete=True,
        obligations=(
            CashObligation("ARS",Decimal("1200"),(now+timedelta(hours=2)).isoformat(),"FEE","LEDGER"),
            CashObligation("ARS",Decimal("300"),(now+timedelta(hours=4)).isoformat(),"OTHER","LEDGER"),
            CashObligation("ARS",Decimal("999"),(deadline+timedelta(hours=1)).isoformat(),"LATER","LEDGER"),
            CashObligation("USD_MEP",Decimal("500"),(now+timedelta(hours=2)).isoformat(),"OTHER","LEDGER"),
        ))
    assert required_reserve(snap,currency="ARS",liquidity_deadline=deadline)==Decimal("1500")


def test_incomplete_obligation_snapshot_is_fail_closed():
    now=datetime(2026,9,3,16,30,tzinfo=TZ)
    snap=ObligationSnapshot(now.isoformat(),"TEST",False,())
    try:
        required_reserve(snap,currency="ARS",liquidity_deadline=now+timedelta(days=1))
    except ValueError as exc:
        assert str(exc)=="OBLIGATION_SNAPSHOT_INCOMPLETE"
    else:
        raise AssertionError("incomplete reserve snapshot must not be accepted")


def _offer(*,fees=None,fee_principal=None):
    now=datetime(2026,9,3,16,30,tzinfo=TZ)
    return CaucionOffer(
        instrument_id="TEST",currency="ARS",annual_rate_fraction=Decimal("0.30"),
        start_date=now.date().isoformat(),maturity_at=(now+timedelta(days=1)).isoformat(),
        quoted_at=now.isoformat(),available_principal=Decimal("100000"),
        minimum_principal=Decimal("1000"),principal_step=Decimal("1000"),
        day_count_basis=365,fee_payment="MATURITY",metadata_source="VERIFIED_TEST",
        quoted_total_fees=fees,fee_quote_principal=fee_principal)


def test_automatic_sweep_accepts_only_exact_fee_budget_offers():
    legacy=_offer()
    exact=_offer(fees=Decimal("10"),fee_principal=Decimal("50000"))
    assert exact_fee_offers([legacy,exact])==[exact]
