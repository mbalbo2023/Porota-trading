from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from rc6_cauciones_contract import (
    BOOK_DEPTH_PROOF,
    BOOK_RATE_PROOF,
    COLOCADORA_SIDE_PROOF,
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
    with pytest.raises(CaucionContractError):
        validate_amount("PESOS7", "100000.50")
    with pytest.raises(CaucionContractError):
        validate_amount("PESOS7", 99999)


def test_amount_rules_usd_integer_step_and_minimum():
    assert validate_amount("DOLAR7", 100) == Decimal("100")
    with pytest.raises(CaucionContractError):
        validate_amount("DOLAR7", "100.01")
    with pytest.raises(CaucionContractError):
        validate_amount("DOLAR7", 99)


def test_actual_365_support_example():
    assert gross_interest_for_ticker("PESOS7", 100000, "20.1").quantize(Decimal("0.01")) == Decimal("385.48")


def test_friday_three_calendar_days_liquid_monday():
    assert theoretical_liquidity_date(date(2026, 9, 11), "PESOS3") == date(2026, 9, 14)


def test_raw_book_never_assumes_colocadora_is_bids():
    raw = {"bids": [{"price": 20.1, "quantity": 150000}], "offers": [{"price": 20.2, "quantity": 200000}]}
    with pytest.raises(CaucionContractError, match="semantics not validated"):
        placed_side_book_levels(raw)


def test_no_bid_or_offer_fallback_without_explicit_semantic_proof():
    with pytest.raises(CaucionContractError, match="semantics not validated"):
        placed_side_book_levels({"offers": [{"price": 20.2, "quantity": 200000}]}, side_key="offers")


def test_explicit_fixture_proof_can_select_side_without_hardcoding_direction():
    raw = {"bids": [{"price": 20.1, "quantity": 150000}], "offers": [{"price": 20.2, "quantity": 200000}]}
    assert list(placed_side_book_levels(raw, side_key="offers", semantic_proof=COLOCADORA_SIDE_PROOF)) == raw["offers"]
    assert list(placed_side_book_levels(raw, side_key="bids", semantic_proof=COLOCADORA_SIDE_PROOF)) == raw["bids"]


def test_raw_book_level_cannot_become_tna_or_depth_without_three_proofs():
    level = {"price": "20.1", "quantity": "150000"}
    with pytest.raises(CaucionContractError, match="side->colocadora"):
        quote_from_book_level("PESOS7", level)
    with pytest.raises(CaucionContractError, match="price->TNA"):
        quote_from_book_level(
            "PESOS7",
            level,
            side_key="bids",
            side_semantic_proof=COLOCADORA_SIDE_PROOF,
        )
    with pytest.raises(CaucionContractError, match="quantity->executable principal"):
        quote_from_book_level(
            "PESOS7",
            level,
            side_key="bids",
            side_semantic_proof=COLOCADORA_SIDE_PROOF,
            rate_semantic_proof=BOOK_RATE_PROOF,
        )


def test_fully_proven_fixture_book_level_is_parsed_without_direction_assumption():
    q = quote_from_book_level(
        "PESOS7",
        {"price": "20.1", "quantity": "150000"},
        side_key="offers",
        side_semantic_proof=COLOCADORA_SIDE_PROOF,
        rate_semantic_proof=BOOK_RATE_PROOF,
        depth_semantic_proof=BOOK_DEPTH_PROOF,
    )
    assert q.tna_percent == Decimal("20.1")
    assert q.quantity == Decimal("150000")
    assert q.side_source == "OFFERS"


def test_support_confirmed_market_window_in_argentina_timezone():
    assert is_same_day_concertation_window(datetime(2026, 9, 8, 13, 30, tzinfo=timezone.utc))
    assert is_same_day_concertation_window(datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc))
    assert not is_same_day_concertation_window(datetime(2026, 9, 8, 13, 29, 59, tzinfo=timezone.utc))
    assert not is_same_day_concertation_window(datetime(2026, 9, 8, 20, 0, 1, tzinfo=timezone.utc))


def test_commission_contract_exact_for_pesos_and_variable_for_dolar():
    assert ppi_annual_commission_percent("PESOS7") == Decimal("2")
    assert ppi_annual_commission_percent("DOLAR7") is None


def test_cost_estimate_excludes_market_rights():
    e = estimate_ppi_costs_before_market_rights(
        "PESOS7",
        100000,
        "20.1",
        ppi_commission_annual_percent="2",
        iva_percent="21",
    )
    assert e.ppi_commission.quantize(Decimal("0.01")) == Decimal("38.36")
    assert e.market_rights_known is False
    assert e.budget_is_authoritative_for_full_costs is True
