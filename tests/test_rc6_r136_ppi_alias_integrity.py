"""R136: contradictory provider aliases cannot borrow the preferred alias."""
from copy import deepcopy

import pytest

import bu_instrument_catalog as catalog


def record(family="OPCIONES"):
    return {
        "ticker": "TEST", "instrument_type": family, "market": "BYMA",
        "currency": "ARS", "settlement": "INMEDIATA", "settlement_source": "PPI_FIELD",
        "raw": {"ticker": "TEST", "type": family, "market": "BYMA", "currency": "ARS",
                "settlement": "INMEDIATA", "_discovery_source": "PPI_PRIMARY"},
    }


@pytest.mark.parametrize("family", ["OPCIONES", "ACCIONES", "BONOS"])
@pytest.mark.parametrize("field,value", [
    ("symbol", "OTHER"), ("instrumentType", "FCI"),
    ("_provider_instrument_type", "FCI"), ("symbol", True), ("instrumentType", ["OPCIONES"]),
])
def test_every_nonempty_provider_alias_must_agree(family, field, value):
    current = record(family)
    current["raw"][field] = value
    before = deepcopy(current)
    assert catalog.ppi_option_full_key_evidence_reason(current)
    assert current == before


@pytest.mark.parametrize("family,alias", [("OPCIONES", "OPCIONES"), ("ACCIONES", "ACCIONES"),
                                          ("CEDEARS", "CEDEAR"), ("ETFS", "ETF")])
def test_consistent_aliases_keep_existing_family_normalization(family, alias):
    current = record(family)
    current["raw"].update(symbol=" test ", instrumentType=alias,
                          _provider_instrument_type=alias)
    assert catalog.ppi_option_full_key_evidence_reason(current) == ""


def test_either_single_provider_spelling_and_empty_secondary_are_supported():
    current = record()
    current["raw"]["symbol"] = current["raw"].pop("ticker")
    current["raw"]["instrumentType"] = current["raw"].pop("type")
    assert catalog.ppi_option_full_key_evidence_reason(current) == ""
    current["raw"].update(ticker="", type=None)
    assert catalog.ppi_option_full_key_evidence_reason(current) == ""


def test_whitespace_only_secondary_alias_makes_no_identity_claim():
    current = record()
    current["raw"].update(symbol="  ", instrumentType="\t", _provider_instrument_type=" ")
    assert catalog.ppi_option_full_key_evidence_reason(current) == ""


@pytest.mark.parametrize("family", ["OPCIONES", "ACCIONES", "BONOS"])
def test_shared_identity_guard_retains_external_owner_behavior(family):
    current = record(family)
    current["raw"]["symbol"] = "OTHER"
    # R136 must not silently change a shared helper imported by an externally
    # owned Watch/service. Strictness belongs to the OPTION-only wrapper.
    assert catalog.ppi_full_key_evidence_reason(current) == ""
