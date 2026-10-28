from __future__ import annotations

import bf_production_paper_observer as observer
import cp_history_ingest_policy_hf6 as policy


def test_ppi_history_policy_covers_current_paper_families():
    expected = {
        "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
        "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
    }
    assert expected <= policy.OPERATIONAL_HISTORY_FAMILIES
    for family in expected:
        assert policy.history_collection_capability(
            family, status="AVAILABLE", identity_complete=True
        ) == "READONLY_HISTORY_ALLOWED"


def test_data912_fallback_does_not_expand_with_ppi_primary_scope():
    assert policy.DATA912_FALLBACK_FAMILIES == {"ACCIONES", "CEDEARS"}
    assert policy.data912_fallback_allowed("ACCIONES") is True
    assert policy.data912_fallback_allowed("CEDEARS") is True
    for family in ("BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"):
        assert policy.data912_fallback_allowed(family) is False


def test_history_transport_uses_literal_provider_family_alias():
    metadata = {
        "raw": {
            "_provider_instrument_type": "ON",
            "_discovery_source": "PPI_PRIMARY",
        }
    }
    assert observer._history_provider_instrument_type(metadata, "OBLIGACIONES") == "ON"
    assert observer._history_provider_instrument_type({"raw": {}}, "BONOS") == "BONOS"


def test_history_transport_mapping_never_changes_canonical_storage_family():
    metadata = {"raw": {"_provider_instrument_type": "ETF"}}
    provider = observer._history_provider_instrument_type(metadata, "ETFS")
    assert provider == "ETF"
    assert "ETFS" in policy.OPERATIONAL_HISTORY_FAMILIES
    assert "ETF" not in policy.OPERATIONAL_HISTORY_FAMILIES
