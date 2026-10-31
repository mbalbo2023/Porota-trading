from datetime import datetime, timezone

import pytest

import rc6_family_readiness as readiness
from rc6_ppi_iol_reconciliation_rc6 import reconcile


def _verified_comparison(state="READY_PAPER_SHADOW", **changes):
    """Full primary PPI evidence, compatible with the real reconciler schema."""
    comparison = {
        "contract_state": state,
        "primary_source": "PPI",
        "identity_binding": "EXPLICIT_PRIMARY_IDENTITY",
        "freshness": {"PPI": "FRESH", "IOL": "UNKNOWN", "BYMA": "UNKNOWN"},
        "field_provenance": {
            field: {"source": "PPI", "freshness": "FRESH"}
            for field in ("last", "bid", "ask")
        },
        "selection_eligible": True,
        "review_status": "NO_CURRENT_CONFLICT",
    }
    comparison.update(changes)
    return comparison


def _ypfd_report(comparison):
    return readiness.evaluate(
        [{"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}],
        [{"symbol": "YPFD", "asset_type": "ACCIONES", "market": "BYMA", "currency": "ARS", "term": "A-24HS",
          "primary_comparison": comparison}],
    )


def test_ready_instrument_promotes_only_paper():
    report = _ypfd_report(_verified_comparison())
    item = report["instruments"][0]
    assert item["paper_contract_ready"] is True
    assert item["paper_auto_enabled"] is False
    assert item["entry_authority"] is False
    assert "ENTRY_SIGNAL_ECONOMICS_AND_ADMISSION_NOT_EVALUATED" in item["reasons"]
    assert item["real_money_authorized"] is False
    assert next(row for row in report["families"] if row["family"] == "ACCIONES")["state"] == "READY_CONTRACT_ONLY"
    assert report["paper_auto_promotion"] is False


def test_missing_iol_is_explicit_pending_gap():
    report = readiness.evaluate(
        [{"family": "CEDEARS", "symbol": "AAPL", "market": "BYMA", "settlement": "A-24HS"}],
        [],
    )
    item = report["instruments"][0]
    assert item["paper_auto_enabled"] is False
    assert "PPI_IOL_COMPARISON_NOT_PUBLISHED" in item["reasons"]
    assert report["families"][0]["state"] == "PENDING"


def test_one_family_does_not_block_another():
    report = readiness.evaluate(
        [{"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"},
         {"family": "BONOS", "symbol": "AL30", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}],
        [{"symbol": "YPFD", "asset_type": "ACCIONES", "market": "BYMA", "currency": "ARS", "term": "A-24HS",
          "primary_comparison": _verified_comparison()}],
    )
    states = {item["family"]: item["state"] for item in report["families"]}
    assert states["ACCIONES"] == "READY_CONTRACT_ONLY"
    assert states["BONOS"] == "PENDING"


def test_only_explicit_paper_states_are_simulatable():
    assert "READY_PAPER" in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_PAPER_SHADOW" in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_SHADOW" not in readiness.PAPER_SIMULATABLE_STATES
    assert "READY_SHADOW_PARTIAL" not in readiness.PAPER_SIMULATABLE_STATES


def test_complemented_source_state_does_not_authorize_real_money():
    report = readiness.evaluate(
        [{"family": "BONOS", "symbol": "GD30", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}],
        [{"symbol": "GD30", "asset_type": "BONOS", "market": "BYMA", "currency": "ARS", "term": "A-24HS",
          "primary_comparison": {
              **_verified_comparison(),
              "complemented": ["tir", "duration"],
              "comparison_complete": True,
          }}],
    )
    item = report["instruments"][0]
    assert item["paper_contract_ready"] is True
    assert item["paper_auto_enabled"] is False
    assert item["real_money_authorized"] is False


def test_partial_shadow_is_observable_but_not_paper_simulatable():
    report = readiness.evaluate(
        [{"family": "CAUCIONES", "symbol": "CAUCION-1", "market": "BYMA"}],
        [{"symbol": "CAUCION-1", "asset_type": "CAUCIONES",
          "primary_comparison": {
              "contract_state": "READY_SHADOW_PARTIAL",
              "comparison_complete": True,
          }}],
    )
    item = report["instruments"][0]
    assert item["paper_auto_enabled"] is False
    assert item["real_money_authorized"] is False


def test_audit_keeps_all_families_visible_when_capture_is_empty():
    report = readiness.evaluate([], [])
    families = {item["family"] for item in report["families"]}
    assert {"BONOS", "OBLIGACIONES", "CAUCIONES", "LETRAS", "FCI", "FUTUROS", "OPCIONES"} <= families
    assert readiness.CANONICAL_PAPER_FAMILIES <= families
    assert readiness.EXPLORATORY_FAMILIES <= families
    assert all(item["state"] == "PENDING" for item in report["families"])


def test_a228_market_expansions_and_legacy_subtypes_never_fabricate_paper_authority():
    catalog = [{"family": family, "symbol": "SIMULADO", "market": "BYMA",
                "currency": "ARS", "settlement": "A-24HS"}
               for family in ("INDICES", "ACCIONES_USA", "FCI_EXTERIOR",
                              "LEBAC", "NOBAC", "LICITACIONES", "CANJES")]
    reference = [{"asset_type": row["family"], "symbol": "SIMULADO",
                  "market": "BYMA", "currency": "ARS", "term": "A-24HS",
                  "primary_comparison": _verified_comparison()}
                 for row in catalog]
    result = readiness.evaluate(catalog, reference)
    assert len(result["canonical_paper_families"]) == 10
    assert result["exploratory_shadow_families"] == [
        "ACCIONES_USA", "FCI_EXTERIOR", "INDICES"]
    for row in result["instruments"]:
        assert row["contract_state"] == "READY_PAPER_SHADOW"
        assert row["paper_contract_ready"] is False
        assert row["paper_auto_enabled"] is False
        assert row["entry_authority"] is False
        assert "NO_INDEPENDENT_PAPER_ENGINE_FOR_NAMESPACE" in row["reasons"]
    summary = {row["family"]: row for row in result["families"]}
    assert summary["INDICES"]["family_role"] == "BENCHMARK_SHADOW_ONLY"
    assert summary["ACCIONES_USA"]["family_role"] == "CROSS_BORDER_DISCOVERY_SHADOW"
    assert summary["FCI_EXTERIOR"]["family_role"] == "CROSS_BORDER_DISCOVERY_SHADOW"
    assert summary["LEBAC"]["parent_paper_family"] == "LETRAS"
    assert summary["NOBAC"]["parent_paper_family"] == "LETRAS"
    assert summary["CANJES"]["family_role"] == "SOURCE_SUBTYPE_OR_CONTEXT"


def test_ticker_only_or_cross_family_observation_never_promotes_paper_contract():
    catalog = [{"family": "BONOS", "symbol": "COLLIDE", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}]
    ready = {"primary_comparison": _verified_comparison()}
    for observation in (
        {"symbol": "COLLIDE", "asset_type": "ACCIONES", "market": "BYMA", "currency": "ARS", "term": "A-24HS"},
        {"symbol": "COLLIDE", "asset_type": "BONOS"},
        {"symbol": "COLLIDE", "asset_type": "BONOS", "market": "BYMA", "currency": "ARS", "term": "CI"},
        {"symbol": "COLLIDE", "asset_type": "BONOS", "market": "BYMA", "currency": "USD", "term": "A-24HS"},
    ):
        result = readiness.evaluate(catalog, [observation | ready])
        bond = next(item for item in result["instruments"] if item["family"] == "BONOS"
                    and item["market"] == "BYMA" and item["settlement"] == "A-24HS")
        assert not bond["paper_contract_ready"] and not bond["paper_auto_enabled"]


def test_duplicate_exact_identity_requires_review():
    catalog = [{"family": "ON", "symbol": "SAME", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}]
    row = {"symbol": "SAME", "asset_type": "OBLIGACIONES", "market": "BYMA", "currency": "ARS",
           "term": "A-24HS", "primary_comparison": _verified_comparison()}
    result = readiness.evaluate(catalog, [row, row])
    instrument = result["instruments"][0]
    assert instrument["family"] == "OBLIGACIONES"
    assert instrument["contract_state"] == "BLOCKED_CONFLICT"
    assert "DUPLICATE_EXACT_IDENTITY_REVIEW_REQUIRED" in instrument["reasons"]
    assert not instrument["paper_auto_enabled"]


def test_published_market_and_settlement_aliases_match_exact_identity():
    result = readiness.evaluate(
        [{"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA",
          "currency": "ARS", "settlement": "A-24HS"}],
        [{"asset_type": "ACCIONES", "symbol": "YPFD", "market": "BCBA",
          "currency": "ARS", "term": "T1",
          "primary_comparison": _verified_comparison()}],
    )
    assert len(result["instruments"]) == 1
    assert result["instruments"][0]["paper_contract_ready"] is True
    assert result["instruments"][0]["paper_auto_enabled"] is False


@pytest.mark.parametrize("freshness", [
    None,
    {},
    {"IOL": "FRESH"},
    {"PPI": "NO_VERIFICADO"},
    {"PPI": "UNKNOWN"},
    {"PPI": "STALE"},
    {"PPI": {"state": "NO_VERIFICADO"}},
])
def test_ready_label_without_verified_primary_ppi_freshness_fails_closed(freshness):
    comparison = _verified_comparison()
    if freshness is None:
        comparison.pop("freshness")
    else:
        comparison["freshness"] = freshness
    result = _ypfd_report(comparison)
    instrument = result["instruments"][0]
    assert instrument["contract_state"] == "INSUFFICIENT_EVIDENCE"
    assert not instrument["paper_contract_ready"]
    assert not instrument["entry_authority"]
    assert "PPI_PRIMARY_FRESHNESS_NOT_VERIFIED" in instrument["reasons"]
    assert next(f for f in result["families"] if f["family"] == "ACCIONES")["state"] == "PENDING"


@pytest.mark.parametrize("change", [
    {"primary_source": "IOL"},
    {"identity_binding": "REFERENCE_COMPARISON_ONLY"},
    {"selection_eligible": False},
    {"selection_eligible": None},
    {"comparison_complete": False},
    {"background_comparison_complete": False},
    {"review_status": "CONFLICT_REVIEW_REQUIRED"},
    {"identity_conflicts": [{"source": "IOL"}]},
    {"field_provenance": {"last": {"source": "PPI", "freshness": "NO_VERIFICADO"}}},
    {"field_provenance": {"last": {"source": "IOL", "freshness": "FRESH"}}},
])
def test_incomplete_or_non_primary_comparison_cannot_fabricate_paper_ready(change):
    instrument = _ypfd_report(_verified_comparison(**change))["instruments"][0]
    assert instrument["contract_state"] == "INSUFFICIENT_EVIDENCE"
    assert instrument["paper_contract_ready"] is False
    assert instrument["paper_auto_enabled"] is False


def test_explicit_complete_comparison_is_accepted_without_producer_eligibility_flag():
    comparison = _verified_comparison(
        comparison_complete=True,
        freshness={"PPI": {"state": "FRESH"}, "IOL": {"state": "STALE"}},
    )
    comparison.pop("selection_eligible")
    item = _ypfd_report(comparison)["instruments"][0]
    assert item["contract_state"] == "READY_PAPER_SHADOW"
    assert item["paper_contract_ready"] is True
    assert not item["entry_authority"]


def test_real_reconciliation_provenance_ppi_api_is_not_mistaken_for_secondary():
    clock = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    primary = {
        "ticker": "YPFD", "symbol": "YPFD", "family": "ACCIONES",
        "asset_type": "ACCIONES", "market": "BYMA", "currency": "ARS",
        "settlement": "A-24HS", "term": "A-24HS", "source": "PPI_API",
        "provider_observed_at": clock.isoformat(), "received_at": clock.isoformat(),
        "book_at": clock.isoformat(), "last": 100, "bid": 99, "ask": 101,
    }
    comparison = reconcile(primary, {}, now=clock)
    assert comparison["selection_eligible"] is True
    assert all(comparison["field_provenance"][field]["source"] == "PPI_API"
               for field in ("last", "bid", "ask"))
    # The reconciler has not promoted any PAPER state: the fixture models a
    # later explicit source-readiness publication using its genuine proof.
    comparison["contract_state"] = "READY_PAPER_SHADOW"
    item = _ypfd_report(comparison)["instruments"][0]
    assert item["paper_contract_ready"] is True
    assert item["contract_state"] == "READY_PAPER_SHADOW"
    assert not item["entry_authority"]


def test_missing_evidence_never_counts_as_complete_even_with_ready_label():
    item = _ypfd_report({"contract_state": "READY_PAPER_SHADOW"})["instruments"][0]
    assert item["contract_state"] == "INSUFFICIENT_EVIDENCE"
    assert not item["paper_contract_ready"]
    assert "PPI_PRIMARY_FRESHNESS_NOT_VERIFIED" in item["reasons"]
    assert "PRIMARY_COMPARISON_COMPLETENESS_NOT_VERIFIED" in item["reasons"]


@pytest.mark.parametrize("with_observation", [True, False])
def test_duplicate_catalog_exact_identity_blocks_even_with_verified_ppi(with_observation):
    catalog = [
        {"family": "ON", "symbol": "SAME", "market": "BCBA", "currency": "ARS", "settlement": "T1"},
        {"family": "OBLIGACIONES", "symbol": "SAME", "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"},
    ]
    observed = ([{"symbol": "SAME", "asset_type": "OBLIGACIONES", "market": "BYMA",
                  "currency": "ARS", "term": "A-24HS", "primary_comparison": _verified_comparison()}]
                if with_observation else [])
    result = readiness.evaluate(catalog, observed)
    instrument = result["instruments"][0]
    assert len(result["instruments"]) == 1
    assert instrument["contract_state"] == "BLOCKED_CONFLICT"
    assert not instrument["paper_contract_ready"]
    assert "DUPLICATE_CATALOG_EXACT_IDENTITY_REVIEW_REQUIRED" in instrument["reasons"]
    assert next(f for f in result["families"] if f["family"] == "OBLIGACIONES")["state"] == "BLOCKED"
    assert not instrument["entry_authority"] and not instrument["real_money_authorized"]


def test_same_ticker_different_exact_currency_identity_is_not_a_duplicate():
    catalog = [
        {"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA", "currency": "ARS", "settlement": "T1"},
        {"family": "ACCIONES", "symbol": "YPFD", "market": "BYMA", "currency": "USD", "settlement": "T1"},
    ]
    observed = [{"asset_type": row["family"], "symbol": row["symbol"], "market": row["market"],
                 "currency": row["currency"], "term": row["settlement"],
                 "primary_comparison": _verified_comparison()} for row in catalog]
    result = readiness.evaluate(catalog, observed)
    assert len(result["instruments"]) == 2
    assert all(item["paper_contract_ready"] for item in result["instruments"])
    assert all("DUPLICATE_CATALOG_EXACT_IDENTITY_REVIEW_REQUIRED" not in item["reasons"]
               for item in result["instruments"])
