from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_iol_oauth_guardian_runs_every_ten_minutes_without_market_polling() -> None:
    timer = (ROOT / "systemd/porota-iol-oauth-health-rc6.timer").read_text()
    assert "OnBootSec=2min" in timer
    assert "OnUnitActiveSec=10min" in timer
    assert "AccuracySec=15s" in timer
    assert "OnCalendar" not in timer


def test_iol_oauth_health_has_bounded_runtime_and_no_order_tool() -> None:
    service = (ROOT / "systemd/porota-iol-oauth-health-rc6.service").read_text()
    script = (ROOT / "scripts/rc6_iol_oauth_health.py").read_text()
    assert "TimeoutStartSec=60" in service
    assert "POROTA_IOL_OAUTH_REFRESH_SKEW_SECONDS=300" in service
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
