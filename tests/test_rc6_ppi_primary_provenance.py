"""Contract field ownership follows authority, independently of corroboration."""
from datetime import datetime, timezone
from itertools import permutations

import pytest

from cp_contract_evidence_v2_hf6 import evidence_hash
from rc6_contract_bridge import normalize_group

NOW = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)


def receipt(source, **fields):
    evidence = {"currency": "ARS", "cash_multiplier": "0.01",
                "quantity_step": "1", "quantity_min": "1", **fields}
    return {"family": "BONOS", "ticker": "GD30", "market": "BYMA",
            "currency": "ARS", "settlement": "A-24HS",
            "source_class": source, "source_ref": "fixture:" + source,
            "observed_at": NOW.isoformat(), "evidence": evidence,
            "evidence_hash": evidence_hash(evidence)}


@pytest.mark.parametrize("order", list(permutations([
    "PPI_STRUCTURED_API", "IOL_STRUCTURED_API", "BYMA_STRUCTURED_API"])))
def test_corroboration_does_not_replace_ppi_field_provenance(order):
    result = normalize_group([receipt(source) for source in order], now=NOW)
    assert result["contract_bridge"]["status"] == "NORMALIZED"
    provenance = result["contract_bridge"]["field_provenance"]
    assert set(provenance) == {"cash_multiplier", "quantity_step", "minimum_quantity"}
    assert all(p["source_class"] == "PPI_STRUCTURED_API" for p in provenance.values())
    assert all(p["source_ref"] == "fixture:PPI_STRUCTURED_API" for p in provenance.values())


@pytest.mark.parametrize("first,second", [
    ("PPI_STRUCTURED_API", "PPI_AUTHENTICATED_WEB"),
    ("PPI_AUTHENTICATED_WEB", "IOL_STRUCTURED_API"),
    ("IOL_STRUCTURED_API", "BYMA_STRUCTURED_API"),
])
def test_field_source_precedence_when_primary_api_is_not_available(first, second):
    result = normalize_group([receipt(second), receipt(first)], now=NOW)
    assert result["contract_bridge"]["field_provenance"]["cash_multiplier"]["source_class"] == first


def test_missing_ppi_field_can_be_completed_without_relabelling_other_fields():
    ppi = receipt("PPI_STRUCTURED_API", quantity_min=None)
    iol = receipt("IOL_STRUCTURED_API")
    result = normalize_group([ppi, iol], now=NOW)
    assert result["contract_bridge"]["status"] == "NORMALIZED"
    provenance = result["contract_bridge"]["field_provenance"]
    assert provenance["minimum_quantity"]["source_class"] == "IOL_STRUCTURED_API"
    assert provenance["quantity_step"]["source_class"] == "PPI_STRUCTURED_API"
    assert provenance["cash_multiplier"]["source_class"] == "PPI_STRUCTURED_API"


def test_contradictory_complement_still_blocks_the_contract():
    result = normalize_group([receipt("PPI_STRUCTURED_API"),
                              receipt("IOL_STRUCTURED_API", cash_multiplier="1")], now=NOW)
    assert result["financial_contract_v17"] is None
    assert "CONFLICT:cash_multiplier" in result["contract_bridge"]["gaps"]
    assert result["contract_bridge"]["field_provenance"]["cash_multiplier"]["source_class"] == "PPI_STRUCTURED_API"


def test_complement_only_preserves_its_actual_origin():
    result = normalize_group([receipt("IOL_STRUCTURED_API")], now=NOW)
    assert result["contract_bridge"]["status"] == "NORMALIZED"
    assert all(p["source_class"] == "IOL_STRUCTURED_API"
               for p in result["contract_bridge"]["field_provenance"].values())
