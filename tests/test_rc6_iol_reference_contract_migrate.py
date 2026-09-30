import json

import pytest

from scripts import rc6_iol_reference_contract_migrate as migration


def test_legacy_empty_cache_becomes_explicit_source_unavailable_without_zero_fill():
    legacy = {
        "schema": "rc6-iol-family-reference-v1",
        "refreshed_at": "2026-09-29T19:58:06+00:00",
        "cache_state": "CACHE_FRESH",
        "records": [], "fci": [], "cauciones": {"ARS": [], "USD": []},
        "section_states": {"caucion:ARS": "SOURCE_UNAVAILABLE"},
        "real_money_authorized": False,
    }
    result = migration.migrate(legacy, migrated_at="2026-09-30T03:00:00+00:00")
    assert result["cache_state"] == "SOURCE_UNAVAILABLE"
    assert result["fallback_order"] == migration.FALLBACK_ORDER
    assert result["continuation_state"] == migration.CONTINUATION_STATE
    assert result["records"] == [] and result["cauciones"] == {"ARS": [], "USD": []}
    assert "last_known_good_at" not in result
    assert result["contract_migration"]["zero_fill"] is False
    assert result["contract_migration"]["real_routes"] == []


def test_migration_is_idempotent_and_preserves_a_real_lkg():
    original = {
        "refreshed_at": "2026-09-29T19:58:06+00:00",
        "last_known_good_at": "2026-09-29T18:00:00+00:00",
        "cache_state": "CACHE_FRESH", "live_sections": [],
        "records": [{"ticker": "GD30", "last": 42.5}],
        "fci": [], "cauciones": {}, "section_states": {"fixed_income": "LKG_FRESH"},
    }
    first = migration.migrate(original, migrated_at="2026-09-30T03:00:00+00:00")
    second = migration.migrate(first, migrated_at="2026-09-30T04:00:00+00:00")
    assert first == second
    assert first["cache_state"] == "CACHE_FRESH"
    assert first["records"] == original["records"]
    assert first["last_known_good_at"] == original["last_known_good_at"]


def test_migration_refuses_real_money_authority():
    with pytest.raises(ValueError, match="REAL_MONEY"):
        migration.migrate({"real_money_authorized": True})
