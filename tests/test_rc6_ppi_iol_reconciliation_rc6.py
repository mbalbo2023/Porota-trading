from datetime import datetime, timezone, timedelta
import pytest
import rc6_ppi_iol_reconciliation_rc6 as recon

def test_matching_last_is_legacy_match_and_partial_contract():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    result = recon.reconcile({"last": 100}, {"last": 100}, now=now)
    assert result["state"] == "MATCH"
    assert result["contract_state"] == "READY_SHADOW_PARTIAL"
    assert result["decision_effect"] == "OBSERVE_ONLY"

def test_divergence_blocks_shadow_contract():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    result = recon.reconcile({"last": 100}, {"last": 110}, now=now)
    assert result["state"] == "PRICE_DIVERGENCE"
    assert result["contract_state"] == "BLOCKED_CONFLICT"

def test_stale_source_blocks_contract():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    old = (now - timedelta(seconds=121)).isoformat()
    result = recon.reconcile({"last": 100, "provider_observed_at": old},
                             {"last": 100, "provider_observed_at": now.isoformat()},
                             now=now)
    assert result["contract_state"] == "BLOCKED_STALE"


def test_iol_can_complete_missing_ppi_prices_for_advisory_comparison():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    result = recon.reconcile({}, {"last": 100, "bid": 99, "ask": 101}, now=now)
    assert result["contract_state"] == "READY_SHADOW_COMPLEMENTED"
    assert result["effective_fields"]["last"] == {"value": 100.0, "source": "IOL"}
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["advisory_comparison_ready"] is True
    assert "shadow_promotion" not in result
    assert not result["selection_eligible"] and not result["entry_authority"]


def test_byma_completes_only_the_remaining_public_field():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    result = recon.reconcile({"last": 100, "bid": 99, "ask": 101}, {}, {"vwap": 100.5}, now=now)
    assert result["effective_fields"]["last"]["source"] == "PPI"
    assert result["effective_fields"]["vwap"] == {"value": 100.5, "source": "BYMA"}


def comparison_quote(**changes):
    return {"ticker": "GGAL", "family": "ACCIONES", "market": "BYMA",
            "currency": "ARS", "settlement": "A-24HS", "last": 100,
            "bid": 99, "ask": 101, "source_at": "2026-09-25T15:00:00+00:00",
            "book_at": "2026-09-25T15:00:00+00:00",
            "received_at": "2026-09-25T15:00:00+00:00", **changes}


@pytest.mark.parametrize("source", ["IOL", "BYMA"])
def test_complement_only_ready_is_advisory_and_never_primary_eligibility(source):
    now = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)
    quote = comparison_quote(source=source)
    result = recon.reconcile({}, quote if source == "IOL" else {},
                             quote if source == "BYMA" else {}, now=now)
    assert result["schema_version"] == 3
    assert result["advisory_comparison_ready"] is True
    assert "shadow_promotion" not in result
    assert result["identity_primary"] is None
    assert result["identity_binding"] == "REFERENCE_COMPARISON_ONLY"
    assert result["effective_fields"]["last"] == {"source": source, "value": 100.0}
    assert result["field_provenance"]["last"]["authority"] != "PPI_PRIMARY"
    assert result["selection_eligible"] is False
    assert result["entry_authority"] is result["live_decision_authority"] is result["real_money_authorized"] is False
    assert result["decision_effect"] == "OBSERVE_ONLY"


@pytest.mark.parametrize("field,value", [("ticker", "YPFD"), ("family", "CEDEARS"),
    ("market", "A3"), ("currency", "USD"), ("settlement", "INMEDIATA")])
def test_advisory_complement_requires_same_five_part_primary_identity(field, value):
    now = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)
    result = recon.reconcile(comparison_quote(source="PPI", bid=None),
                             comparison_quote(source="IOL", **{field: value}), now=now)
    assert result["identity_primary"] == {"ticker": "GGAL", "family": "ACCIONES",
        "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}
    assert result["identity_overwritten"] is False
    assert len(result["identity_conflicts"]) == 1
    assert result["contract_state"] == "BLOCKED_CONFLICT"
    assert result["advisory_comparison_ready"] is False
    assert result["effective_fields"]["bid"] == {"source": None, "value": None}
    assert result["selection_eligible"] is False
    assert result["entry_authority"] is result["live_decision_authority"] is False
    assert "shadow_promotion" not in result


def test_advisory_ready_keeps_separate_primary_selection_and_zero_entry_authority():
    now = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)
    result = recon.reconcile(comparison_quote(source="PPI"), comparison_quote(source="IOL"), now=now)
    assert result["advisory_comparison_ready"] is True
    assert result["selection_eligible"] is True
    assert result["entry_authority"] is result["live_decision_authority"] is False
    summary = recon.summarize_rows(["GGAL"], {"GGAL": {"primary_comparison": result}})
    assert summary["schema_version"] == 3
    assert summary["decision_effect"] == "OBSERVE_ONLY"
