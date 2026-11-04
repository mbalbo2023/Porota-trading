"""A652: hypothetical PPI realtime settlement join never grants PAPER READY."""

from copy import deepcopy

import pytest

from rc6_ppi_realtime_settlement_join_offhost import propose_five_key


SEARCH_AT = "2026-11-04T19:29:00+00:00"
RECEIVED = "2026-11-04T19:30:03+00:00"
EVALUATED = "2026-11-04T19:30:05+00:00"


def search():
    return {"ticker": "GGAL", "description": "Synthetic GGAL",
            "currency": "Pesos", "type": "Acciones", "market": "BYMA"}


def stream():
    # Fields match the published PPI Python SDK `Response Books` *shape*;
    # values are wholly synthetic, not a provider-authenticated capture.
    return {"Ticker": "GGAL", "Type": "ACCIONES", "Settlement": "A-48HS",
            "Date": "2026-11-04T16:30:00-03:00", "Bids": [], "Offers": [],
            "Trade": False}


def selection():
    # A subscriber PRESELECTS settlement: it is not independent discovery.
    return {"ticker": "GGAL", "type": "ACCIONES", "settlement": "A-48HS"}


def attempt(rows=None, pushed=None, selected=None, **clock):
    return propose_five_key(
        [search()] if rows is None else rows,
        stream() if pushed is None else pushed,
        selection() if selected is None else selected,
        search_observed_at=clock.get("search_at", SEARCH_AT),
        stream_received_at=clock.get("received", RECEIVED),
        evaluated_at=clock.get("evaluated", EVALUATED))


def assert_non_admitting(result):
    assert result["paper_instrument_ready"] is False
    assert result["full_five_key_authoritative"] is False
    assert result["provider_session_authenticated"] is False
    assert result["source_capture_signature_verified"] is False
    assert result["provider_response_signed"] is False
    assert result["settlement_independently_discovered"] is False
    assert result["settlement_subscription_preselected"] is True
    assert result["ppi_realtime_subscription_available_runtime"] == "NO_VERIFICADO"
    assert result["pit_book1_decision_book2_verified"] is False
    assert result["runtime_real_orders_sent"] == "NO_VERIFICADO"
    assert result["ppi_gets_by_guard"] == 0
    assert result["socket_connections_by_guard"] == 0
    assert result["host_writes_by_guard"] == 0


def test_documented_search4_and_realtime3_join_only_to_untrusted_candidate():
    result = attempt()
    assert result["state"] == "EVIDENCE_ONLY_NO_GO"
    assert result["reason"] == "STRUCTURAL_JOIN_ONLY_SUBSCRIPTION_BOOTSTRAP_AND_SOURCE_UNVERIFIED"
    assert result["candidate_five_key"] == {
        "ticker": "GGAL", "family": "ACCIONES", "market": "BYMA",
        "currency": "ARS", "settlement": "A-48HS"}
    assert result["search_four_key_observed"] is True
    assert result["stream_settlement_field_observed"] is True
    assert result["clock_pair_structurally_bounded"] is True
    assert_non_admitting(result)


def test_market_currency_siblings_are_ambiguous_even_if_subscribed_tenor_matches():
    sibling_currency = search() | {"currency": "USD_MEP"}
    sibling_market = search() | {"market": "A3"}
    for rows in ([search(), sibling_currency], [search(), sibling_market]):
        result = attempt(rows=rows)
        assert result["reason"] == "SEARCH_MARKET_OR_CURRENCY_SIBLING_AMBIGUOUS"
        assert result["state"] == "BLOQUEADO"
        assert result["candidate_five_key"] is None
        assert_non_admitting(result)


def test_identical_search_duplicate_does_not_invent_a_sibling():
    result = attempt(rows=[search(), deepcopy(search())])
    assert result["state"] == "EVIDENCE_ONLY_NO_GO"
    assert_non_admitting(result)


def test_same_ticker_other_family_not_chosen_or_merged_into_matching_type():
    another = search() | {"type": "BONOS"}
    result = attempt(rows=[another, search()])
    assert result["state"] == "EVIDENCE_ONLY_NO_GO"
    assert result["candidate_five_key"]["family"] == "ACCIONES"
    assert_non_admitting(result)


@pytest.mark.parametrize("mutate,expected", [
    (lambda s: s.pop("Settlement"), "REALTIME_EMITTED_IDENTITY_INCOMPLETE"),
    (lambda s: s.__setitem__("Settlement", "INMEDIATA"),
     "REALTIME_SUBSCRIPTION_EMITTED_IDENTITY_CONFLICT"),
    (lambda s: s.__setitem__("Ticker", "GFGC"),
     "REALTIME_SUBSCRIPTION_EMITTED_IDENTITY_CONFLICT"),
    (lambda s: s.__setitem__("Type", "CEDEARS"),
     "REALTIME_SUBSCRIPTION_EMITTED_IDENTITY_CONFLICT"),
    (lambda s: s.__setitem__("Trade", True), "REALTIME_BOOK_ENVELOPE_UNVERIFIED"),
    (lambda s: s.__setitem__("Bids", "invalid"), "REALTIME_BOOK_ENVELOPE_UNVERIFIED"),
    (lambda s: s.__setitem__("Date", "2026-11-04T19:30:00"),
     "REALTIME_CLOCK_MISSING_OR_NAIVE"),
    (lambda s: s.__setitem__("Date", "2026-11-04T19:31:00Z"),
     "REALTIME_CLOCK_STALE_REVERSED_OR_FUTURE"),
])
def test_missing_conflicting_or_unclocked_provider_field_never_promoted(mutate, expected):
    pushed = stream()
    mutate(pushed)
    result = attempt(pushed=pushed)
    assert result["state"] == "BLOQUEADO"
    assert result["reason"] == expected
    assert result["candidate_five_key"] is None
    assert_non_admitting(result)


def test_stream_late_versus_claimed_receipt_is_rejected_even_with_settlement_field():
    result = attempt(received="2026-11-04T19:33:00+00:00",
                     evaluated="2026-11-04T19:33:01+00:00")
    assert result["state"] == "BLOQUEADO"
    assert result["reason"] == "REALTIME_CLOCK_STALE_REVERSED_OR_FUTURE"
    assert_non_admitting(result)


def test_no_search_match_cannot_gain_market_currency_from_subscription():
    result = attempt(rows=[search() | {"ticker": "AL30"}])
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "SEARCH_FOUR_KEY_NOT_FOUND"
    assert result["candidate_five_key"] is None
    assert_non_admitting(result)


def test_search_matching_row_missing_market_or_currency_must_hold():
    for missing in ("market", "currency"):
        value = search()
        value.pop(missing)
        result = attempt(rows=[value])
        assert result["state"] == "BLOQUEADO"
        assert result["reason"] == "SEARCH_MATCH_FOUR_KEY_INVALID"
        assert_non_admitting(result)


def test_unknown_or_missing_subscription_settlement_never_bootstraps():
    for selected in ({"ticker": "GGAL", "type": "ACCIONES", "settlement": "UNKNOWN"},
                     {"ticker": "GGAL", "type": "ACCIONES"}):
        result = attempt(selected=selected)
        assert result["state"] == "BLOQUEADO"
        assert result["candidate_five_key"] is None
        assert_non_admitting(result)
