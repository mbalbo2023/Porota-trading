from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from rc6_cauciones_contract import CaucionContractError,estimate_ppi_costs_before_market_rights,gross_interest,gross_interest_for_ticker,is_same_day_concertation_window,parse_ticker,placed_side_book_levels,ppi_annual_commission_percent,quote_from_book_level,theoretical_liquidity_date,validate_amount

def test_parse_ticker_pesos_and_dolar():
    assert parse_ticker('PESOS1').term_days==1; assert parse_ticker('pesos120').term_days==120; assert parse_ticker('DOLAR7').currency_prefix=='DOLAR'
@pytest.mark.parametrize('ticker',['PESOS0','DOLAR0','ARS7','PESOS','DOLAR-1',''])
def test_invalid_ticker_rejected(ticker):
    with pytest.raises(CaucionContractError): parse_ticker(ticker)
def test_amount_rules_ars_integer_step_and_minimum():
    assert validate_amount('PESOS7',100000)==Decimal('100000')
    with pytest.raises(CaucionContractError): validate_amount('PESOS7','100000.50')
    with pytest.raises(CaucionContractError): validate_amount('PESOS7',99999)
def test_amount_rules_usd_integer_step_and_minimum():
    assert validate_amount('DOLAR7',100)==Decimal('100')
    with pytest.raises(CaucionContractError): validate_amount('DOLAR7','100.01')
    with pytest.raises(CaucionContractError): validate_amount('DOLAR7',99)
def test_actual_365_support_example(): assert gross_interest_for_ticker('PESOS7',100000,'20.1').quantize(Decimal('0.01'))==Decimal('385.48')
def test_friday_three_calendar_days_liquid_monday(): assert theoretical_liquidity_date(date(2026,9,11),'PESOS3')==date(2026,9,14)
def test_colocadora_uses_bids_only(): assert list(placed_side_book_levels({'bids':[{'price':20.1,'quantity':150000}],'offers':[{'price':20.2,'quantity':200000}]}))==[{'price':20.1,'quantity':150000}]
def test_no_bid_fallback_to_offers():
    with pytest.raises(CaucionContractError): placed_side_book_levels({'offers':[{'price':20.2,'quantity':200000}]})
def test_book_price_is_tna_and_quantity_is_tomadora_amount():
    q=quote_from_book_level('PESOS7',{'price':'20.1','quantity':'150000'}); assert q.tna_percent==Decimal('20.1'); assert q.quantity==Decimal('150000'); assert q.side_source=='BIDS'
def test_support_confirmed_market_window_in_argentina_timezone():
    assert is_same_day_concertation_window(datetime(2026,9,8,13,30,tzinfo=timezone.utc)); assert is_same_day_concertation_window(datetime(2026,9,8,20,0,tzinfo=timezone.utc)); assert not is_same_day_concertation_window(datetime(2026,9,8,13,29,59,tzinfo=timezone.utc)); assert not is_same_day_concertation_window(datetime(2026,9,8,20,0,1,tzinfo=timezone.utc))
def test_commission_contract_exact_for_pesos_and_variable_for_dolar(): assert ppi_annual_commission_percent('PESOS7')==Decimal('2') and ppi_annual_commission_percent('DOLAR7') is None
@pytest.mark.parametrize(('ticker','amount','annual_rate','expected_commission'),[
    ('PESOS7',100000,'2',Decimal('38.36')),
    ('DOLAR7',100,'0.5',Decimal('0.01')),
])
def test_partial_cost_estimate_never_certifies_full_account_costs(
        ticker,amount,annual_rate,expected_commission):
    estimate=estimate_ppi_costs_before_market_rights(
        ticker,amount,'20.1',ppi_commission_annual_percent=annual_rate,iva_percent='21')
    assert estimate.ppi_commission.quantize(Decimal('0.01'))==expected_commission
    assert estimate.market_rights_known is False
    assert estimate.budget_is_authoritative_for_full_costs is False


@pytest.mark.parametrize("capital,tna,days", [
    ("NaN", "20", 7), ("Infinity", "20", 7), ("100000", "NaN", 7),
    ("100000", "Infinity", 7), ("100000", "20", True),
    ("100000", "20", 0),
])
def test_caucion_interest_never_accepts_nonfinite_or_invalid_tenor(capital, tna, days):
    with pytest.raises(CaucionContractError):
        gross_interest(capital, tna, days)


@pytest.mark.parametrize("field,value", [
    ("price", "NaN"), ("price", "Infinity"),
    ("quantity", "NaN"), ("quantity", "Infinity"),
    ("quantity", "-1"),
])
def test_caucion_bid_book_rejects_nonfinite_and_negative_liquidity(field, value):
    level = {"price": "20.1", "quantity": "150000"}
    level[field] = value
    with pytest.raises(CaucionContractError):
        quote_from_book_level("PESOS3", level)


def test_zero_bid_quantity_stays_explicit_empty_book_not_broker_fill():
    # The downstream SHADOW classifier labels this EMPTY_PLACED_BID.
    quote = quote_from_book_level("PESOS3", {"price": "20.1", "quantity": "0"})
    assert quote.quantity == Decimal(0) and quote.side_source == "BIDS"


@pytest.mark.parametrize("field,value", [
    ("ppi_commission_annual_percent", "NaN"),
    ("ppi_commission_annual_percent", "Infinity"),
    ("iva_percent", "NaN"), ("iva_percent", "Infinity"),
])
def test_caucion_partial_fee_model_never_normalizes_nonfinite_inputs(field, value):
    kwargs = {"ppi_commission_annual_percent": "2", "iva_percent": "21"}
    kwargs[field] = value
    with pytest.raises(CaucionContractError):
        estimate_ppi_costs_before_market_rights("PESOS3", "100000", "20.1", **kwargs)


def test_synthetic_bid_fee_and_theoretical_settlement_stay_non_authoritative():
    bid = quote_from_book_level("PESOS3", {"price": "20.1", "quantity": "150000"})
    amount = validate_amount(bid.ticker, "100000")
    estimate = estimate_ppi_costs_before_market_rights(
        bid.ticker, amount, bid.tna_percent,
        ppi_commission_annual_percent="2", iva_percent="21")
    assert estimate.gross_interest > 0
    assert estimate.net_before_market_rights_and_other_taxes < estimate.gross_interest
    assert estimate.market_rights_known is False
    assert estimate.budget_is_authoritative_for_full_costs is False
    # Calendar-day settlement is only theoretical, not a broker confirmation.
    assert theoretical_liquidity_date(date(2026, 9, 11), bid.ticker) == date(2026, 9, 14)
