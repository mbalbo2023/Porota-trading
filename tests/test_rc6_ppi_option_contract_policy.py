from datetime import datetime, timezone
import json
from decimal import Decimal

import bs_instrument_contracts as contracts
import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge
import rc6_ppi_option_contract_policy as policy


NOW = "2026-10-03T01:36:06+00:00"


def row(ticker, family, *, description="", capability="READY_PAPER_SPOT",
        status="AVAILABLE", market="BYMA", currency="ARS",
        settlement="INMEDIATA", discovery="PPI_PRIMARY"):
    # Explicitly synthetic provider-full-key positive fixture.  The documented
    # real SearchInstrument response does NOT supply settlement; never copy
    # this fixture shape into a live PPI response parser or source claim.
    return {
        "ticker": ticker,
        "instrument_type": family,
        "market": market,
        "currency": currency,
        "settlement": settlement,
        "settlement_source": "PPI_FIELD",
        "status": status,
        "capability": capability,
        "last_seen_at": NOW,
        "description": description,
        "metadata_json": json.dumps({
            "_discovery_source": discovery,
            "_provider_instrument_type": family,
            "nominalInPrice": 1,
            "_fixture_synthetic_full_key": True,
            "ticker": ticker,
            "type": family,
            "market": market,
            "currency": currency,
            "settlement": settlement,
        }),
    }


def option(ticker="YPFC40500O", description="Opción compra YPFD AR$ 40500.00 Vto. 16/10/2026"):
    return row(ticker, "OPCIONES", description=description,
               capability="NEEDS_OPTION_CONTRACT")


def test_strict_ppi_description_parses_only_explicit_terms():
    parsed = policy.parse_ppi_option_description(
        "Opción compra YPFD AR$ 40500.00 Vto. 16/10/2026")
    assert parsed == {
        "underlying": "YPFD",
        "strike": "40500",
        "option_right": "CALL",
        "expires_at": "2026-10-16T15:30:00-03:00",
    }
    put = policy.parse_ppi_option_description(
        "Opción venta AAPL AR$ 14000.00 Vto. 18/12/2026")
    assert put["option_right"] == "PUT"
    assert put["underlying"] == "AAPL"


def test_description_parser_does_not_infer_from_ticker_or_old_series():
    assert policy.parse_ppi_option_description("CEPC23360C") is None
    assert policy.parse_ppi_option_description(
        "Opción compra YPFD AR$ 40500.00 Vto. 16/06/2026") is None
    assert policy.parse_ppi_option_description(
        "Opción compra YPFD USD 40.00 Vto. 16/10/2026") is None


def test_underlying_index_requires_current_exact_ppi_family():
    rows = [
        row("YPFD", "ACCIONES"),
        row("AAPL", "CEDEARS"),
        row("STALE", "ACCIONES", status="STALE"),
        row("IOL", "ACCIONES", discovery="IOL_COMPLEMENTARY"),
    ]
    assert policy.underlying_family_index(rows) == {
        "YPFD": "ACCIONES", "AAPL": "CEDEARS"}


def test_underlying_family_conflict_remains_fail_closed():
    rows = [row("DUP", "ACCIONES"), row("DUP", "CEDEARS")]
    assert "DUP" not in policy.underlying_family_index(rows)


def test_only_usd_deferred_ppi_underlying_cannot_derive_ars_option_contract():
    foreign = row("AAPL", "CEDEARS", currency="USD_MEP", settlement="A-24HS")
    option_row = option("APLC14000O", "Opción compra AAPL AR$ 14000.00 Vto. 16/10/2026")
    assert policy.underlying_family_index([foreign]) == {}
    records, skipped, identities = mass.planned_records([foreign, option_row])
    assert identities == {"CEDEARS": 1, "OPCIONES": 1}
    assert skipped["OPTION_STANDARD_CONTRACT_POLICY_UNRESOLVED"] == 1
    assert not any(record["source_class"] == "DERIVED_OFFICIAL_RULE"
                   and record["ticker"] == "APLC14000O" for record in records)


def test_multi_currency_or_different_settlement_underlying_is_ambiguous():
    ars = row("AAPL", "CEDEARS")
    usd = row("AAPL", "CEDEARS", currency="USD_MEP", settlement="A-24HS")
    deferred = row("YPFD", "ACCIONES", settlement="A-24HS")
    assert policy.underlying_family_index([ars, usd, deferred]) == {}
    assert policy.underlying_family_index([ars, ars.copy()]) == {"AAPL": "CEDEARS"}


def test_underlying_needs_ppi_settlement_and_recorded_observation_clock():
    valid = row("YPFD", "ACCIONES")
    request_only = row("REQ", "ACCIONES")
    request_only["settlement_source"] = "REQUEST_CANDIDATE"
    unclocked = row("NOCLK", "ACCIONES")
    unclocked["last_seen_at"] = "2026-10-03T01:36:06"
    invalid_clock = row("BADCLK", "ACCIONES")
    invalid_clock["last_seen_at"] = "NOT_A_TIMESTAMP"
    iol_only = row("IOL", "ACCIONES", discovery="IOL_COMPLEMENTARY")
    not_available = row("STALE", "ACCIONES", status="STALE")
    result = policy.underlying_family_index([
        valid, request_only, unclocked, invalid_clock, iol_only, not_available,
    ])
    assert result == {"YPFD": "ACCIONES"}


def test_standard_action_option_uses_byma_100_lot_and_paper_one_contract():
    evidence_row = policy.standard_long_option_evidence(
        option(), {"YPFD": "ACCIONES"})
    assert evidence_row["cash_multiplier"] == "100"
    assert evidence_row["paper_quantity_min"] == "1"
    assert evidence_row["paper_quantity_step"] == "1"
    assert evidence_row["broker_minimum_quantity"] == "NO_VERIFICADO"
    assert evidence_row["broker_quantity_step"] == "NO_VERIFICADO"
    assert evidence_row["holder_margin_policy"] == "FULL_PREMIUM_MAX_LOSS"
    assert evidence_row["writer_margin_scope"] == "NOT_APPLICABLE_TO_LONG_OPTION_PATH"


def test_standard_cedear_option_uses_byma_10_lot():
    evidence_row = policy.standard_long_option_evidence(
        option("APLC14000O", "Opción compra AAPL AR$ 14000.00 Vto. 16/10/2026"),
        {"AAPL": "CEDEARS"})
    assert evidence_row["cash_multiplier"] == "10"
    assert evidence_row["underlying_family"] == "CEDEARS"


def test_unverified_alias_and_unstructured_option_stay_blocked():
    apbr = option("PBRC28000O", "Opción compra APBR AR$ 28000.00 Vto. 16/10/2026")
    assert policy.standard_long_option_evidence(apbr, {}) is None
    assert policy.standard_long_option_evidence(
        option("CEPC23360C", "CEPC23360C"), {"CEPU": "ACCIONES"}) is None


def test_only_missing_contract_cohort_receives_new_rule():
    underlying = {"YPFD": "ACCIONES"}
    review = option()
    review["capability"] = "CONTRACT_EVIDENCE_REVIEW_REQUIRED"
    ready = option()
    ready["capability"] = "READY_PAPER_OPTION_LONG"
    assert policy.standard_long_option_evidence(review, underlying) is None
    assert policy.standard_long_option_evidence(ready, underlying) is None


def test_wrong_identity_dimensions_stay_fail_closed():
    underlying = {"YPFD": "ACCIONES"}
    wrong_market = option(); wrong_market["market"] = "A3"
    wrong_currency = option(); wrong_currency["currency"] = "USD_MEP"
    wrong_term = option(); wrong_term["settlement"] = "A-24HS"
    not_ppi = option(); not_ppi["metadata_json"] = json.dumps(
        {"_discovery_source": "IOL_COMPLEMENTARY"})
    assert policy.standard_long_option_evidence(wrong_market, underlying) is None
    assert policy.standard_long_option_evidence(wrong_currency, underlying) is None
    assert policy.standard_long_option_evidence(wrong_term, underlying) is None
    assert policy.standard_long_option_evidence(not_ppi, underlying) is None


def test_option_itself_requires_provider_settlement_not_request_candidate():
    underlying = {"YPFD": "ACCIONES"}
    inferred = option()
    inferred["settlement_source"] = "REQUEST_CANDIDATE"
    # Match documented SearchInstrument semantics: no provider settlement.
    raw = json.loads(inferred["metadata_json"])
    raw.pop("settlement")
    inferred["metadata_json"] = json.dumps(raw)
    assert policy.standard_long_option_evidence(inferred, underlying) is None
    records, skipped, identities = mass.planned_records([
        row("YPFD", "ACCIONES"), inferred,
    ])
    assert identities == {"ACCIONES": 1}
    assert skipped["PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"] == 1
    assert not any(record["source_class"] == "DERIVED_OFFICIAL_RULE"
                   and record["ticker"] == "YPFC40500O" for record in records)


def test_option_mass_never_promotes_contradictory_synthetic_full_key():
    contradictory = option()
    raw = json.loads(contradictory["metadata_json"])
    raw["market"] = "ROFEX"
    contradictory["metadata_json"] = json.dumps(raw)
    records, skipped, identities = mass.planned_records([contradictory])
    assert records == []
    assert identities == {}
    assert skipped["PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"] == 1


def test_option_itself_requires_aware_observation_clock_without_claiming_pit():
    underlying = {"YPFD": "ACCIONES"}
    for known_at in (None, "2026-10-03T01:36:06", "INVALID_TIMESTAMP"):
        unclocked = option()
        unclocked["last_seen_at"] = known_at
        assert policy.standard_long_option_evidence(unclocked, underlying) is None
    assert policy.standard_long_option_evidence(option(), underlying) is not None


def test_mass_evidence_normalizes_to_existing_long_option_contract_path():
    primary_underlying = row("YPFD", "ACCIONES")
    primary_option = option()
    records, skipped, identities = mass.planned_records(
        [primary_underlying, primary_option])
    assert identities == {"ACCIONES": 1, "OPCIONES": 1}
    option_records = [r for r in records if r["ticker"] == "YPFC40500O"]
    assert len(option_records) == 2
    derived = next(r for r in option_records
                   if r["source_class"] == "DERIVED_OFFICIAL_RULE")
    assert derived["source_ref"] == policy.OPTION_DERIVED_SOURCE_REF
    assert derived["evidence"]["cash_multiplier"] == "100"
    assert derived["evidence"]["broker_minimum_quantity"] == "NO_VERIFICADO"
    for record in option_records:
        record["evidence_hash"] = evidence.evidence_hash(record["evidence"])
    claim = bridge.normalize_group(
        option_records, now=datetime(2026, 10, 3, 2, 0, tzinfo=timezone.utc))
    assert claim["contract_bridge"]["status"] == "NORMALIZED"
    contract = contracts.contract_from_metadata(
        "YPFC40500O", "OPCIONES", claim["financial_contract_v17"])
    assert contract.option_right == "CALL"
    assert contract.underlying == "YPFD"
    assert contract.cash_multiplier == Decimal("100")
    assert contract.option_max_loss("250", "1") == 25000


def test_mass_rule_does_not_modify_review_or_alias_cohorts():
    primary_underlying = row("YPFD", "ACCIONES")
    review = option(); review["capability"] = "CONTRACT_EVIDENCE_REVIEW_REQUIRED"
    records, skipped, identities = mass.planned_records([primary_underlying, review])
    option_records = [r for r in records if r["ticker"] == "YPFC40500O"]
    assert len(option_records) == 1
    assert option_records[0]["source_class"] == "PPI_STRUCTURED_API"
    assert "OPTION_STANDARD_CONTRACT_POLICY_UNRESOLVED" not in skipped
