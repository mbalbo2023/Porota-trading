import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import bs_instrument_contracts as contracts
import ai_derivatives_engine as derivatives
import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import iol_shadow_collector_rc6 as shadow
import rc6_contract_bridge as bridge
import rc6_iol_family_reference as family_cache
import rc6_paper_family_lifecycle as lifecycle
import rc6_broker_parity_evidence as parity


NOW = datetime(2026, 9, 29, 15, 30, tzinfo=timezone.utc)


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def make_store(tmp_path):
    store = Store(tmp_path / "ws14.db")
    catalog.init_schema(store)
    return store


def ppi_record(ticker, family, *, nominal=1, currency="ARS", market="BYMA",
               settlement="A-24HS"):
    return catalog.normalize_record({
        "ticker": ticker, "type": family, "market": market,
        "currency": currency, "nominalInPrice": nominal,
    }, settlement, NOW.isoformat(), "WS14-PPI")


def test_al30_ymcjo_and_d30n6_one_nominal_policy_is_not_broker_term(tmp_path):
    store = make_store(tmp_path)
    with store.connect() as connection:
        for ticker, family in (("AL30", "BONOS"), ("YMCJO", "OBLIGACIONES"),
                               ("D30N6", "LETRAS")):
            catalog.persist(connection, ppi_record(ticker, family, nominal=100))
    mass.collect(store, run_id="ws14-fixed")
    claims = {row["ticker"]: row for row in bridge.complements_from_store(store, now=NOW)}
    assert set(claims) == {"AL30", "YMCJO", "D30N6"}
    for ticker, claim in claims.items():
        contract = claim["financial_contract_v17"]
        assert Decimal(contract["cash_multiplier"]) == Decimal("0.01")
        assert contract["minimum_quantity"] == contract["quantity_step"] == "1"
        provenance = contract["field_provenance"]
        assert provenance["minimum_quantity"]["source_class"] == "DERIVED_OFFICIAL_RULE"
        current = [r for r in evidence.current_records(store)
                   if r["ticker"] == ticker and r["source_class"] == "DERIVED_OFFICIAL_RULE"][0]
        assert current["evidence"]["broker_minimum_quantity"] == "NO_VERIFICADO"


def test_iol_paper_units_are_not_relabelled_as_broker_terms():
    fixed = parity._map_iol_contract({
        "ticker": "AL30", "instrument_type": "BONOS", "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS",
        "financial_contract_v17": {
            "cash_multiplier": "0.01", "minimum_quantity": "1",
            "quantity_step": "1", "paper_quantity_min": "1",
            "paper_quantity_step": "1",
            "paper_quantity_policy": "ONE_NOMINAL_SIMULATION_UNIT",
            "broker_minimum_quantity": "NO_VERIFICADO",
            "broker_quantity_step": "NO_VERIFICADO",
            "fixed_income_evidence": {"quote_basis_nominal": "100"},
        },
    })
    assert fixed["paper_cash_multiplier"] == "0.01"
    assert fixed["paper_quantity_min"] == fixed["paper_quantity_step"] == "1"
    assert "quantity_min" not in fixed and "quantity_step" not in fixed
    assert fixed["broker_minimum_quantity"] == "NO_VERIFICADO"

    option = parity._map_iol_contract({
        "ticker": "GFGC6000OC", "instrument_type": "OPCIONES",
        "market": "BYMA", "currency": "ARS", "settlement": "INMEDIATA",
        "financial_contract_v17": {
            "cash_multiplier": "100", "minimum_quantity": "1",
            "quantity_step": "1", "paper_quantity_min": "1",
            "paper_quantity_step": "1", "underlying": "GGAL",
            "strike": "6000", "expires_at": "2026-10-16T15:30:00-03:00",
            "option_right": "CALL", "premium_basis": "PER_UNDERLYING_UNIT",
        },
    })
    assert option["cash_multiplier"] == "100"
    assert option["paper_quantity_min"] == option["paper_quantity_step"] == "1"
    assert "quantity_min" not in option and "quantity_step" not in option


def test_fci_without_broker_minimum_uses_internal_risk_budget(tmp_path):
    store = make_store(tmp_path)
    primary = ppi_record("FUND.PPI.A", "FCI", settlement="INMEDIATA")
    with store.connect() as connection:
        catalog.persist(connection, primary)
    mass.collect(store, run_id="ws14-fci")
    claim = bridge.complements_from_store(store, now=NOW)[0]
    completed = catalog.complete_with_complement(primary, claim)
    assert completed["capability"] == "READY_PAPER_FCI_SUBSCRIPTION"
    terms = catalog.contract_for(completed)
    assert terms.paper_subscription_policy == "INTERNAL_RISK_BUDGET_BY_AMOUNT"
    assert terms.paper_subscription_min == Decimal("1000")
    try:
        terms.subscription_amount("999.99")
    except ValueError as exc:
        assert "BELOW_MINIMUM" in str(exc)
    else:
        raise AssertionError("FCI PAPER accepted less than the ARS 1.000 internal minimum")
    assert terms.subscription_amount("1234.56") == Decimal("1234.56")
    assert terms.broker_subscription_min == "NO_VERIFICADO"


def test_source_failure_preserves_last_known_good_quote_and_static_metadata(tmp_path):
    class Healthy:
        def call(self, name, arguments):
            if name == "get_asset_quote":
                return {"unit_price": 100, "trade": {"lot_price": 100}}
            return {"type": "ACCIONES", "currency": "ARS", "units_per_lot": 1}

    class Down:
        def call(self, name, arguments):
            raise RuntimeError("SOURCE_UNAVAILABLE")

    policy = shadow.CollectionPolicy(min_interval_seconds=1, retry_attempts=0)
    first = shadow.run_batch(["GGAL"], Healthy(), root=tmp_path, policy=policy,
                             now=lambda: NOW)
    second = shadow.run_batch(["GGAL"], Down(), root=tmp_path, policy=policy,
                              now=lambda: NOW)
    row = second["symbols"][0]
    assert first["symbols"][0]["quote"]["last"] == row["quote"]["last"] == 100
    assert row["state"] == "CACHE_FRESH"
    assert row["source_state"] == "SOURCE_UNAVAILABLE"
    assert row["asset_type"] == "ACCIONES" and row["units_per_lot"] == 1


def test_stale_quote_cache_degrades_only_dynamic_data_and_keeps_metadata(tmp_path):
    class Healthy:
        def call(self, name, arguments):
            if name == "get_asset_quote":
                return {"unit_price": 100, "trade": {"lot_price": 100}}
            return {"type": "ACCIONES", "currency": "ARS", "units_per_lot": 1}

    class Down:
        def call(self, name, arguments):
            raise RuntimeError("SOURCE_UNAVAILABLE")

    policy = shadow.CollectionPolicy(min_interval_seconds=1, retry_attempts=0)
    shadow.run_batch(["GGAL"], Healthy(), root=tmp_path, policy=policy,
                     now=lambda: NOW)
    stale = shadow.run_batch(["GGAL"], Down(), root=tmp_path, policy=policy,
                             now=lambda: NOW.replace(hour=16))
    row = stale["symbols"][0]
    assert row["state"] == "CACHE_STALE"
    assert row["source_state"] == "SOURCE_UNAVAILABLE"
    assert row["asset_type"] == "ACCIONES"
    assert row["currency"] == "ARS" and row["units_per_lot"] == 1


def test_empty_family_responses_preserve_nonempty_last_known_good(tmp_path):
    db = tmp_path / "catalog.db"
    connection = sqlite3.connect(db)
    connection.execute("""CREATE TABLE financial_instrument_catalog(
      ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
      status TEXT,description TEXT)""")
    connection.commit(); connection.close()
    prior = {
        "schema": family_cache.SCHEMA, "refreshed_at": NOW.isoformat(),
        "last_known_good_at": NOW.isoformat(), "rotation": {}, "records": [],
        "fci": [{"asset": "FUND1", "currency": "ARS"}],
        "cauciones": {"ARS": [{"days": 1, "rate": 20}], "USD": [{"days": 1, "rate": 5}]},
    }
    (tmp_path / "iol_family_reference_latest.json").write_text(json.dumps(prior))

    class Empty:
        def call(self, name, arguments):
            return {"result": []}

    result = family_cache.collect(Empty(), root=tmp_path, db_path=str(db), now=NOW)
    assert result["fci"] == prior["fci"]
    assert result["cauciones"] == prior["cauciones"]
    assert result["cache_state"] == "CACHE_FRESH"
    assert result["section_states"]["fci"] == "EMPTY_UNEXPECTED"
    assert result["section_observed_at"] == {}
    assert any("EMPTY_UNEXPECTED" in item for item in result["errors"])


def test_future_without_broker_margin_uses_full_notional_paper_reserve():
    payload = {
        "currency": "ARS", "cash_multiplier": "1000", "quantity_min": "1",
        "quantity_step": "1", "underlying": "USDARS",
        "expiry_at": "2026-12-31T15:00:00-03:00",
        "paper_margin_policy": "CONSERVATIVE_NOTIONAL_RATE",
        "paper_margin_rate": "1", "broker_margin": "NO_VERIFICADO",
    }
    row = dict(family="FUTUROS", ticker="DLR/DIC26", market="A3", currency="ARS",
               settlement="INMEDIATA", source_class="DERIVED_OFFICIAL_RULE",
               source_ref="POROTA_PAPER_FUTURES_MARGIN_POLICY:v1",
               observed_at=NOW.isoformat(), evidence=payload,
               evidence_hash=evidence.evidence_hash(payload))
    claim = bridge.normalize_group([row], now=NOW)
    contract = contracts.contract_from_metadata(
        "DLR/DIC26", "FUTUROS", claim["financial_contract_v17"])
    assert contract.initial_margin is None
    assert contract.paper_margin_policy == "CONSERVATIVE_NOTIONAL_RATE"
    assert contract.cash_required("1400", "1") == Decimal("1400000")
    assert contract.margin_deficit("1200000", "1", price="1400") == Decimal("200000")
    spec = derivatives.describe_future("DLR/DIC26", {
        "contractMultiplier": 1000,
        "paperMarginPolicy": "CONSERVATIVE_NOTIONAL_RATE",
        "paperMarginRate": 1,
        "expirationDate": "2026-12-31",
    })
    assert spec.capable is True
    assert derivatives.size_future(
        10_000_000, spec, entry_price=1400, stop_price=1390,
        risk_pct=1, free_margin_ars=1_500_000) == 1


def test_paper_only_route_invariants():
    assert lifecycle.PAPER_ONLY is True
    assert lifecycle.REAL_ROUTES_USED == ()
    assert family_cache.SCHEMA == "rc6-iol-family-reference-v1"


def test_caucion_without_depth_reaches_paper_capability():
    primary = {
        "ticker": "PESOS1", "instrument_type": "CAUCIONES", "market": "BYMA",
        "currency": "ARS", "settlement": "INMEDIATA", "term_days": 1,
        "side": "COLOCADORA", "status": "STALE", "capability": "NEEDS_CAUCION_TERMS",
        "settlement_source": "PPI_FIELD", "last_seen_at": NOW.isoformat(),
        "raw": {"_discovery_source": "PPI_PRIMARY"},
    }
    payload = {
        "schema": family_cache.SCHEMA, "refreshed_at": NOW.isoformat(),
        "cache_state": "LIVE_FRESH", "section_states": {"caucion:ARS": "LIVE_FRESH"},
        "section_observed_at": {"caucion:ARS": NOW.isoformat()}, "records": [], "fci": [],
        "cauciones": {"ARS": [{"days": 1, "rate": 21.6, "min_amount": 100000,
                                  "due_date": "2026-09-30"}]},
    }
    record = parity.iol_structured_records(payload, [primary])["accepted"][0]
    record["evidence_hash"] = evidence.evidence_hash(record["evidence"])
    claim = bridge.normalize_group([record], now=NOW)
    assert claim["paper_caucion_contract_v1"]["paper_fill_policy"] == "CONSERVATIVE_NOTIONAL_CAP"
    completed = catalog.complete_with_complement(primary, claim)
    assert completed["capability"] == "READY_PAPER_CAUCION_PLACING"
    assert "available_principal" not in record["evidence"]
