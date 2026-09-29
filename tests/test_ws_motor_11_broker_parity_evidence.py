import sqlite3
from datetime import datetime, timezone

import cp_contract_evidence_v2_hf6 as evidence
import cq_family_contract_rules_hf6 as rules
import rc6_broker_parity_evidence as parity
import rc6_contract_bridge as bridge
import bu_instrument_catalog as catalog


NOW = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def primary(currency="ARS"):
    return {"ticker": "GD30", "instrument_type": "BONOS", "market": "BYMA",
            "currency": currency, "settlement": "A-24HS",
            "settlement_source": "PPI_FIELD",
            "metadata_json": '{"_discovery_source":"PPI_PRIMARY"}'}


def cache(currency="ARS"):
    return {"schema": "rc6-iol-family-reference-v1",
            "refreshed_at": NOW.isoformat(), "records": [{
                "ticker": "GD30", "instrument_type": "BONOS", "market": "BYMA",
                "currency": currency, "settlement": "A-24HS",
                "financial_contract_v17": {
                    "currency": currency, "cash_multiplier": "0.01",
                    "quantity_step": "1", "minimum_quantity": "1",
                    "fixed_income_evidence": {
                        "quote_basis_nominal": "100", "maturity_date": "2030-07-09"},
                },
            }]}


def test_iol_mcp_cache_reaches_evidence_and_existing_contract_gate(tmp_path):
    store = Store(tmp_path / "copy.db")
    ingested = parity.ingest_iol_structured_cache(store, cache(), [primary()])
    assert len(ingested["written"]) == 1
    assert ingested["real_routes_used"] == []
    rows = evidence.current_records(store, family="BONOS", ticker="GD30",
                                    currency="ARS", settlement="A-24HS")
    assert rows[0]["evidence"]["freshness_basis"] == "CAPTURE_TIMESTAMP_STATIC_ONLY"
    assert rows[0]["evidence"]["provider_timestamp"] is None
    claim = bridge.complements_from_store(store, now=NOW)[0]
    assert claim["financial_contract_v17"] is not None
    assert claim["financial_contract_v17"]["cash_multiplier"] == "0.01"


def test_iol_cannot_create_or_overwrite_ppi_identity():
    unknown = parity.iol_structured_records(cache("USD"), [primary("ARS")])
    assert unknown["accepted"] == []
    assert unknown["blocked"][0]["blocker"] == "BLOCKED_IDENTITY"


def test_partial_iol_option_chain_survives_until_ppi_completes_order_terms():
    payload = {"schema": "rc6-iol-family-reference-v1", "refreshed_at": NOW.isoformat(),
               "records": [{"ticker": "GFGC6000OC", "instrument_type": "OPCIONES",
                            "market": "BCBA", "currency": "ARS", "settlement": "T0",
                            "financial_contract_v17": None,
                            "option_chain_evidence": {"underlying": "GGAL", "option_type": "C",
                                "strike_price": 6000, "expires_at": "2026-10-16T15:30:00-03:00",
                                "contract_lot": 100}}]}
    ppi = [{"ticker": "GFGC6000OC", "instrument_type": "OPCIONES", "market": "BYMA",
            "currency": "ARS", "settlement": "INMEDIATA", "settlement_source": "PPI_FIELD",
            "metadata_json": '{"_discovery_source":"PPI_PRIMARY"}'}]
    result = parity.iol_structured_records(payload, ppi)
    assert len(result["accepted"]) == 1
    partial = result["accepted"][0]["evidence"]
    assert partial["underlying"] == "GGAL" and partial["lot_size"] == 100
    assert "cash_multiplier" not in partial


def test_scraping_is_only_last_resort_for_required_unresolved_field():
    required = rules.FAMILY_CONTRACT_FIELDS["BONOS"]
    assert parity.scraping_fallback_allowed("quantity_step", [], required)
    structured = [{"field": "quantity_step", "value": 1,
                   "source_class": "IOL_STRUCTURED_API"}]
    assert not parity.scraping_fallback_allowed("quantity_step", structured, required)
    assert not parity.scraping_fallback_allowed("tir", [], required)


def _full(family):
    payload = {field: "X" for field in rules.FAMILY_CONTRACT_FIELDS[family]}
    for field in rules.POSITIVE_CONTRACT_FIELDS & set(payload):
        payload[field] = 1
    for field in rules.FAMILY_DYNAMIC_FIELDS[family]:
        payload[field] = {
            "operable": True, "available_to_operate": True,
            "market_session_state": "OPEN", "subscription_status": "AVAILABLE",
            "auction_status": "OPEN", "margin_requirement": 100,
            "initial_margin": 100, "maintenance_margin": 80,
            "margin_requirement": 100,
            "nav_value": 10, "nav_date": "2026-09-28",
            "available_principal": 100000, "tna": 0.15, "expiry_at": "2026-12-31",
        }.get(field, "X")
    return [{"source_class": "PPI_STRUCTURED_API",
             "observed_at": NOW.isoformat(), "evidence": {
                 **payload, "provider_timestamp": NOW.isoformat(),
                 "freshness_basis": "PROVIDER_TIMESTAMP",
             }}]


def test_fci_executor_is_connected_and_missing_axes_remain_independent():
    ready_fci = parity.classify_instrument("FCI", _full("FCI"), now=NOW)
    assert ready_fci["status"] == "READY_PAPER_CANDIDATE"
    assert ready_fci["blocker_class"] == "READY_PAPER_CANDIDATE"
    missing = parity.classify_instrument("BONOS", [], now=NOW)
    assert missing["blocker_class"] == "BLOCKED_DATA"


def test_dynamic_without_provider_timestamp_is_not_fresh_and_both_axes_report():
    result = parity.classify_instrument("FUTUROS", [], now=NOW)
    assert result["status"] == "MISSING_CONTRACT"
    assert result["missing_contract"]
    assert result["missing_dynamic"] == ["margin_requirement"]
    assert {"BLOCKED_DATA", "BLOCKED_DYNAMIC_DATA"} <= set(result["blocker_axes"])

    records = _full("FUTUROS")
    records[0]["evidence"].pop("provider_timestamp")
    records[0]["evidence"]["freshness_basis"] = "CAPTURE_TIMESTAMP_STATIC_ONLY"
    result = parity.classify_instrument("FUTUROS", records, now=NOW)
    assert result["status"] == "MISSING_DYNAMIC"
    assert result["missing_dynamic"] == ["margin_requirement"]


def test_option_open_does_not_require_exercise_but_full_lifecycle_does():
    payload = {field: "X" for field in rules.FAMILY_CONTRACT_FIELDS["OPCIONES"]
               if field != "exercise_style"}
    for field in rules.POSITIVE_CONTRACT_FIELDS & set(payload):
        payload[field] = 1
    payload.update({"operable": True, "market_session_state": "OPEN"})
    records = [{"source_class": "PPI_STRUCTURED_API",
                "observed_at": NOW.isoformat(), "evidence": payload}]
    assert parity.classify_instrument("OPCIONES", records, profile="OPEN", now=NOW)["blocker_class"] == "READY_PAPER_CANDIDATE"
    assert parity.classify_instrument("OPCIONES", records, profile="FULL", now=NOW)["blocker_class"] == "BLOCKED_EVENT_LIFECYCLE"


def test_module_has_no_real_route_surface():
    assert parity.REAL_ROUTES_USED == ()
    assert not any(name.startswith(("place_", "validate_", "accept_", "redeem_", "subscribe_"))
                   for name in vars(parity))


def test_sources_complement_field_by_field_until_open_contract_is_complete():
    ppi = {"market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
           "quantity_min": 1, "quantity_step": 1}
    iol = {"price_quote_unit": 100}
    derived = {"cash_multiplier": "0.01"}
    records = [
        {"source_class": "PPI_AUTHENTICATED_DOM", "observed_at": NOW.isoformat(),
         "evidence": ppi},
        {"source_class": "IOL_STRUCTURED_API", "observed_at": NOW.isoformat(),
         "evidence": iol},
        {"source_class": "DERIVED_OFFICIAL_RULE", "observed_at": NOW.isoformat(),
         "evidence": derived},
    ]
    assert rules.evaluate_family("BONOS", records[:1], profile="OPEN", now=NOW)["status"] == "MISSING_CONTRACT"
    assert rules.evaluate_family("BONOS", records[:2], profile="OPEN", now=NOW)["status"] == "MISSING_CONTRACT"
    complete = rules.evaluate_family("BONOS", records, profile="OPEN", now=NOW)
    assert complete["status"] == "READY_PAPER_CANDIDATE"
    assert set(complete["event_missing"]) == {"maturity_date", "payment_currency",
                                               "coupon_terms", "amortization_terms"}


def test_fci_inventory_and_caucion_sections_are_not_dropped_but_require_ppi_binding():
    payload = {"schema": "rc6-iol-family-reference-v1", "refreshed_at": NOW.isoformat(),
               "records": [],
               "fci": [{"asset": "IOLCAMA", "market": "BCBA", "currency": "ARS",
                         "description": "IOL Cash Management", "operable": True}],
               "cauciones": {"ARS": [{"term_days": 1, "tna": 15.9,
                                        "minimum_amount": 100000,
                                        "due_date": "2026-09-30"}]}}
    without_primary = parity.iol_structured_records(payload, [])
    assert without_primary["accepted"] == []
    assert {row["source_section"] for row in without_primary["blocked"]} == {"fci", "cauciones"}

    ppi_fci = {"ticker": "IOLCAMA", "instrument_type": "FCI", "market": "BYMA",
               "currency": "ARS", "settlement": "INMEDIATA", "settlement_source": "PPI_FIELD",
               "metadata_json": '{"_discovery_source":"PPI_PRIMARY"}'}
    ppi_caucion = {"ticker": "PESOS1", "instrument_type": "CAUCIONES", "market": "BYMA",
                   "currency": "ARS", "settlement": "A-24HS", "term_days": 1,
                   "side": "COLOCADORA", "settlement_source": "PPI_FIELD",
                   "metadata_json": '{"_discovery_source":"PPI_PRIMARY"}'}
    bound = parity.iol_structured_records(payload, [ppi_fci, ppi_caucion])
    assert {row["family"] for row in bound["accepted"]} == {"FCI", "CAUCIONES"}
    caucion = next(row for row in bound["accepted"] if row["family"] == "CAUCIONES")
    assert caucion["evidence"]["annual_rate_fraction"] == "0.159"
    assert caucion["evidence"]["freshness_basis"] == "LIVE_RESPONSE_CAPTURE"
    assert caucion["evidence"]["paper_fill_policy"] == "CONSERVATIVE_NOTIONAL_CAP"


def test_complementary_option_fields_reach_real_catalog_capability(tmp_path):
    store = Store(tmp_path / "option-complement.db")
    identity = dict(family="OPCIONES", ticker="GFGC6000OC", market="BYMA",
                    currency="ARS", settlement="INMEDIATA")
    for source, ref, payload in [
        ("PPI_AUTHENTICATED_DOM", "ppi:ticket",
         {"currency": "ARS", "cash_multiplier": 100,
          "quantity_min": 1, "quantity_step": 1}),
        ("IOL_STRUCTURED_API", "iol:chain",
         {"underlying": "GGAL", "put_call": "CALL", "strike": 6000,
          "expiry_at": "2026-10-16T15:30:00-03:00"}),
    ]:
        evidence.record_snapshot(store, **identity, source_class=source,
                                 source_ref=ref, observed_at=NOW.isoformat(), evidence=payload)
    claim = bridge.complements_from_store(store, now=NOW)[0]
    assert claim["financial_contract_v17"] is not None
    primary = catalog.normalize_record(
        {"ticker": "GFGC6000OC", "type": "OPCIONES", "market": "BYMA",
         "currency": "ARS"}, "INMEDIATA", NOW.isoformat(), "ppi-primary")
    completed = catalog.complete_with_complement(primary, claim)
    assert completed["capability"] == "READY_PAPER_OPTION_LONG"
