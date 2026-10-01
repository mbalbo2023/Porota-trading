from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_iol_oauth_guardian_runs_offhours_and_premarket_without_market_polling() -> None:
    timer = (ROOT / "systemd/porota-iol-oauth-health-rc6.timer").read_text()
    assert "OnBootSec=2min" in timer
    assert "OnUnitActiveSec=30min" in timer
    assert "09:30:00 America/Argentina/Buenos_Aires" in timer
    assert "10:20:00 America/Argentina/Buenos_Aires" in timer
    assert "AccuracySec=30s" in timer


def test_iol_oauth_health_has_bounded_runtime_and_no_order_tool() -> None:
    service = (ROOT / "systemd/porota-iol-oauth-health-rc6.service").read_text()
    script = (ROOT / "scripts/rc6_iol_oauth_health.py").read_text()
    assert "TimeoutStartSec=60" in service
    assert "POROTA_IOL_OAUTH_REFRESH_SKEW_SECONDS=60" in service
    assert ".list_tools()" in script
    for forbidden in (
        "place_order(", "validate_order(", "place_caucion(",
        "subscribe_fci(", "redeem_fci(", "accept_ddjj(",
    ):
        assert forbidden not in script


def test_market_collector_remains_market_hours_only() -> None:
    timer = (ROOT / "systemd/porota-iol-shadow-collector-rc6.timer").read_text()
    assert "10:30..59:00 America/Argentina/Buenos_Aires" in timer
    assert "11..16:*:00 America/Argentina/Buenos_Aires" in timer
    assert "Persistent=false" in timer


def test_guardian_defers_to_live_collector_and_uses_telegram_outbox() -> None:
    script = (ROOT / "scripts/rc6_iol_oauth_health.py").read_text()
    assert 'ACTIVE_COLLECTOR_HEALTHY' in script
    assert 'MARKET_OPEN_LIVE_FRESH_COLLECTOR' in script
    assert 'mcp_probe_performed"] = False' in script
    assert "paper_notification_outbox" in script
    assert "IOL_MCP_REAUTH_REQUIRED" in script
    assert "priority,created_at,next_attempt_at" in script
    assert '"IOL_MCP_REAUTH_REQUIRED", body, 0' in script
    assert "sendMessage" not in script


def test_guardian_never_enables_iol_execution_tools() -> None:
    script = (ROOT / "scripts/rc6_iol_oauth_health.py").read_text()
    for forbidden in (
        "place_order", "validate_order", "place_caucion",
        "subscribe_fci", "redeem_fci", "accept_ddjj",
        "get_portfolio", "get_balance",
    ):
        assert forbidden not in script
