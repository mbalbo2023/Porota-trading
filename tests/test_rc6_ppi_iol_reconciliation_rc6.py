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
