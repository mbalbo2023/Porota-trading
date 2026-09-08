from datetime import date
from decimal import Decimal

import pytest

from rc6_cauciones_contract import (
    CaucionContractError,
    gross_interest_for_ticker,
    parse_ticker,
    placed_side_book_levels,
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
