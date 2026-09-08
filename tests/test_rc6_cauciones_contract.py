from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from rc6_cauciones_contract import (
    CaucionContractError,
    estimate_ppi_costs_before_market_rights,
    gross_interest_for_ticker,
    is_same_day_concertation_window,
    parse_ticker,
    placed_side_book_levels,
    ppi_annual_commission_percent,
    quote_from_book_level,
    theoretical_liquidity_date,
    validate_amount,
)


def test_parse_ticker_pesos_and_dolar():
    assert parse_ticker("PESOS1").term_days == 1
    assert parse_ticker("pesos120").term_days == 120
    assert parse_ticker("DOLAR7").currency_prefix == "DOLAR"


@pytest.mark.parametrize("ticker", ["PESOS0", "DOLAR0", "ARS7", "PESOS", "DOLAR-1", ""])
def test_invalid_ticker_rejected(ticker):
    with pytest.raises(CaucionContractError):
        parse_ticker(ticker)


def test_amount_rules_ars_integer_step_and_minimum():
    assert validate_amount("PESOS7", 100000) == Decimal("100000")
    assert validate_amount("PESOS7", 100001) == Decimal("100001")
    with pytest.raises(CaucionContractError):
        validate_amount("PESOS7", "100000.50")
    with pytest.raises(CaucionContractError):
        validate_amount("PESOS7", 99999)


def test_amount_rules_usd_integer_step_and_minimum():
    assert validate_amount("DOLAR7", 100) == Decimal("100")
    assert validate_amount("DOLAR7", 101) == Decimal("101")
    with pytest.raises(CaucionContractError):
        validate_amount("DOLAR7", "100.01")
    with pytest.raises(CaucionContractError):
        validate_amount("DOLAR7", 99)


def test_actual_365_support_example():
    interest = gross_interest_for_ticker("PESOS7", 100000, "20.1")
    assert interest.quantize(Decimal("0.01")) == Decimal("385.48")


def test_friday_three_calendar_days_liquid_monday():
    assert theoretical_liquidity_date(date(2026, 9, 11), "PESOS3") == date(2026, 9, 14)


def test_month_and_year_boundary_are_calendar_arithmetic_only():
    assert theoretical_liquidity_date(date(2026, 12, 31), "PESOS1") == date(2027, 1, 1)


def test_colocadora_uses_bids_only():
    book = {"bids": [{"price": 20.1, "quantity": 150000}], "offers": [{"price": 20.2, "quantity": 200000}]}
    levels = list(placed_side_book_levels(book))
    assert levels == [{"price": 20.1, "quantity": 150000}]


def test_no_bid_fallback_to_offers():
    with pytest.raises(CaucionContractError):
        placed_side_book_levels({"offers": [{"price": 20.2, "quantity": 200000}]})


def test_book_price_is_tna_and_quantity_is_tomadora_amount():
    quote = quote_from_book_level("PESOS7", {"price": "20.1", "quantity": "150000"})
    assert quote.tna_percent == Decimal("20.1")
    assert quote.quantity == Decimal("150000")
    assert quote.side_source == "BIDS"


def test_support_confirmed_market_window_in_argentina_timezone():
    # 13:30 UTC == 10:30 Argentina in September 2026.
    assert is_same_day_concertation_window(datetime(2026, 9, 8, 13, 30, tzinfo=timezone.utc))
    assert is_same_day_concertation_window(datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc))
    assert not is_same_day_concertation_window(datetime(2026, 9, 8, 13, 29, 59, tzinfo=timezone.utc))
    assert not is_same_day_concertation_window(datetime(2026, 9, 8, 20, 0, 1, tzinfo=timezone.utc))
    with pytest.raises(CaucionContractError):
        is_same_day_concertation_window(datetime(2026, 9, 8, 10, 30))


def test_commission_contract_exact_for_pesos_and_variable_for_dolar():
    assert ppi_annual_commission_percent("PESOS7") == Decimal("2")
    assert ppi_annual_commission_percent("DOLAR7") is None


def test_cost_estimate_requires_explicit_iva_and_excludes_market_rights():
    est = estimate_ppi_costs_before_market_rights(
        "PESOS7", 100000, "20.1", ppi_commission_annual_percent="2", iva_percent="21"
    )
    assert est.ppi_commission.quantize(Decimal("0.01")) == Decimal("38.36")
    assert est.iva_on_ppi_commission.quantize(Decimal("0.01")) == Decimal("8.05")
    assert est.market_rights_known is False
    assert est.budget_is_authoritative_for_full_costs is True


def test_dolar_commission_support_caps_at_one_percent_but_does_not_invent_exact_rate():
    estimate_ppi_costs_before_market_rights(
        "DOLAR7", 100, "5", ppi_commission_annual_percent="0.75", iva_percent="21"
    )
    with pytest.raises(CaucionContractError):
        estimate_ppi_costs_before_market_rights(
            "DOLAR7", 100, "5", ppi_commission_annual_percent="1.01", iva_percent="21"
        )
