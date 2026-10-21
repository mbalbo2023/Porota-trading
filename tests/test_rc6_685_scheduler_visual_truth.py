"""Regression for the live browser's scheduler status and clock contract."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from rc6_trader_dashboard.datasets import artifact
from rc6_trader_dashboard.components import badge


NOW = datetime(2026, 10, 21, 20, 30, tzinfo=timezone.utc)


def page(recorded_at, result="success", *, payload_state="OK"):
    payload = {"state": payload_state, "recorded_at": recorded_at,
               "timers": [{"unit": "porota-scheduler-export-rc6.timer",
                           "enabled_state": "enabled", "active_state": "active",
                           "last_result": result, "exec_main_status": "203" if result != "success" else "0",
                           "last_trigger": "Wed 2026-10-21 20:25:49 UTC",
                           "next_elapse": "Wed 2026-10-21 20:50:08 UTC"}]}
    projection = SimpleNamespace(now=NOW, offset=0,
                                 bounded_file=lambda name, root=None: (payload, "AVAILABLE"))
    return artifact(projection, "scheduler")


def test_fresh_export_proves_timer_only_and_renders_systemd_utc_clocks():
    result = page((NOW - timedelta(minutes=2)).isoformat())
    assert result.state == "FRESH"
    assert result.rows[0]["state"] == "TIMER_ACTIVE"
    assert result.rows[0]["freshness"] == "FRESH"
    assert result.rows[0]["last_run"] == "2026-10-21T20:25:49+00:00"
    assert result.rows[0]["next_run"] == "2026-10-21T20:50:08+00:00"


def test_active_timer_does_not_hide_failed_service_or_stale_snapshot():
    recent = (NOW - timedelta(minutes=2)).isoformat()
    failed = page(recent, "exit-code")
    assert failed.rows[0]["state"] == "SERVICE_FAILED"
    assert failed.rows[0]["reason"] == "Service result: exit-code"
    assert "badge-blocked" in badge(failed.rows[0]["state"])
    stale = page((NOW - timedelta(minutes=4)).isoformat(), "success")
    assert stale.state == "STALE"
    assert stale.rows[0]["state"] == "NO_VERIFICADO"
    assert stale.rows[0]["freshness"] == "STALE"
    assert page(recent, payload_state="ERROR").state == "NO_VERIFICADO"
    assert page(recent, payload_state="ERROR").rows[0]["state"] == "NO_VERIFICADO"
    assert page(None).state == "NO_VERIFICADO"
    assert page(None).rows[0]["state"] == "NO_VERIFICADO"


def test_scheduler_service_uses_explicit_interpreter_for_readable_nonexecutables():
    unit = Path("systemd/porota-scheduler-export-rc6.service").read_text()
    assert "ExecStart=/usr/bin/bash /opt/porota-trading/scripts/porota_export_scheduler_state_rc6.sh" in unit
    assert "ExecStart=/opt/porota-trading/scripts/porota_export_scheduler_state_rc6.sh" not in unit
