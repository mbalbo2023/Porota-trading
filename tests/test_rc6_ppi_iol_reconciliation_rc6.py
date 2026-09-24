from datetime import datetime, timezone, timedelta
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


def test_iol_can_complete_missing_ppi_prices_for_shadow():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    result = recon.reconcile({}, {"last": 100, "bid": 99, "ask": 101}, now=now)
    assert result["contract_state"] == "READY_SHADOW_COMPLEMENTED"
    assert result["effective_fields"]["last"] == {"value": 100.0, "source": "IOL"}
    assert result["decision_effect"] == "OBSERVE_ONLY"\n    assert result["shadow_promotion"] is True


def test_byma_completes_only_the_remaining_public_field():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    result = recon.reconcile({"last": 100, "bid": 99, "ask": 101}, {}, {"vwap": 100.5}, now=now)
    assert result["effective_fields"]["last"]["source"] == "PPI"
    assert result["effective_fields"]["vwap"] == {"value": 100.5, "source": "BYMA"}
