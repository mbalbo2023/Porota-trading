from datetime import datetime, timezone, timedelta
import rc6_ppi_iol_reconciliation_rc6 as recon
import rc6_cauciones_shadow_evidence as cauciones

def test_equity_complement_is_explicit_and_non_authoritative():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    result = recon.reconcile(
        {"last": 100, "bid": 99, "ask": 101, "provider_observed_at": now.isoformat()},
        {"last": 100, "bid": 99, "ask": 101, "cash_volume": 500000,
         "provider_observed_at": now.isoformat()},
        now=now,
    )
    assert result["contract_state"] == "READY_SHADOW_COMPLEMENTED"
    assert result["fields"]["cash_volume"]["state"] == "COMPLEMENTED_SECONDARY"
    assert result["real_money_authorized"] is False

def test_equity_sizes_and_spread_match():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    quote = {"last": 100, "bid": 99, "ask": 101, "bid_size": 10, "ask_size": 12,
             "provider_observed_at": now.isoformat()}
    result = recon.reconcile(quote, quote, now=now)
    assert result["contract_state"] == "READY_SHADOW"
    assert result["fields"]["spread_pct"]["state"] == "MATCH"

def test_cauciones_require_contract_and_freshness():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    payload = {
        "instrument_id": "CAUCION-TEST", "market": "BYMA", "currency": "ARS",
        "settlement": "INMEDIATA", "term_days": 1, "rate": 35.0,
        "amount": 100000, "guarantee": "MONEY_MARKET",
        "liquidation_at": now.isoformat(), "provider_observed_at": now.isoformat(),
    }
    result = cauciones.evaluate(payload, payload, now=now)
    assert result["state"] == "READY_SHADOW"
    assert result["real_money_authorized"] is False

def test_cauciones_block_on_critical_conflict():
    now = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    p = {"instrument_id": "CAUCION-A", "provider_observed_at": now.isoformat()}
    s = {"instrument_id": "CAUCION-B", "provider_observed_at": now.isoformat()}
    result = cauciones.evaluate(p, s, now=now)
    assert "instrument_id" in result["conflicts"]
    assert result["state"] == "BLOCKED_CONFLICT"
