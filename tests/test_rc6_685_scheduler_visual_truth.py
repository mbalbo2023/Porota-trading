"""Regression for the live browser's scheduler status and clock contract."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from rc6_trader_dashboard.datasets import artifact, shadow_rows
from rc6_trader_dashboard.components import badge
from rc6_trader_dashboard.navigation import DESTINATIONS
from rc6_trader_dashboard.shell import render as render_shell
from rc6_trader_dashboard.views_home import attention


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


def capacity_page(now, *, phase="OPEN", capacity_changes=None, fresh=True):
    capacity = {"status": "SHADOW_RECOMMENDATION", "mode": "SHADOW", "safe_limit": 7,
                "evidence_digest": "a" * 64, "configuration_fingerprint": "b" * 64,
                "validated_as_of": "2026-10-21T14:00:00+00:00",
                "expires_at": "2026-10-21T14:02:00+00:00",
                "slots_by_endpoint": {"current": 7}, "real_orders_sent": 0,
                "real_routes": "NOT_CALLED", "production_limit_modified": False}
    capacity.update(capacity_changes or {})
    report = {"as_of": "2026-10-21T14:00:00+00:00", "capacity_open_status": "OPEN_OBSERVATIONS_RECORDED",
              "capacity_policy": {"baseline": "OFF_BASELINE", "status": "OFF"},
              "engines": {"SCALPING": {"phase": phase, "capacity": capacity, "discovery": {}}}}
    projection = SimpleNamespace(now=now, offset=0, shadow_fresh=fresh,
                                 shadow={"report": report, "state": "COMMITTED_COHERENT_SHADOW",
                                         "reason": None})
    return shadow_rows(projection, "capacity")


def test_open_capacity_uses_validated_engine_recommendation_not_worker_status():
    result = capacity_page(datetime(2026, 10, 21, 14, 0, 30, tzinfo=timezone.utc))
    row = result.rows[0]
    assert row["state"] == "SHADOW_RECOMMENDATION"
    assert row["safe_capacity"] == 7
    assert row["endpoint_budgets"] == {"current": 7}
    assert row["baseline"] == "OFF_BASELINE"  # recommendation != admission approval
    assert "política de admisión independiente" in row["reason"]


def test_capacity_remains_unverified_outside_open_or_without_current_evidence():
    open_at = datetime(2026, 10, 21, 14, 0, 30, tzinfo=timezone.utc)
    cases = (
        capacity_page(datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc)),
        capacity_page(open_at, phase="CLOSED"),
        capacity_page(open_at, capacity_changes={"expires_at": "2026-10-21T14:00:00+00:00"}),
        capacity_page(open_at, capacity_changes={"evidence_digest": "bad"}),
        capacity_page(open_at, capacity_changes={"validated_as_of": "2026-10-21T13:00:00+00:00"}),
        capacity_page(open_at, capacity_changes={"safe_limit": 0}),
        capacity_page(open_at, capacity_changes={"real_orders_sent": 1}),
    )
    for result in cases:
        row = result.rows[0]
        assert row["state"] == "NO_VERIFICADO"
        assert row["safe_capacity"] is None and row["endpoint_budgets"] is None
        assert "safe_limit" not in row["capacity"]
        assert "slots_by_endpoint" not in row["capacity"]
    assert capacity_page(open_at, fresh=False).rows == []


def test_header_preserves_factual_off_baseline_for_measured_live_cut():
    destination = DESTINATIONS[0]
    runtime = {"mode": "PRODUCTION_PAPER", "session_state": "MARKET_CLOSED",
               "ppi_auth": "OK", "real_orders_sent": 0,
               "heartbeat_freshness": "FRESH", "market_freshness": "STALE"}
    cut = {"report": {"as_of": (NOW - timedelta(seconds=65)).isoformat(),
                      "capacity_policy": {"status": "OFF_BASELINE"}}}
    p = SimpleNamespace(now=NOW, runtime=runtime, filters={}, shadow=cut, shadow_fresh=True)
    tab = destination.tabs[0][0]
    fresh = render_shell(destination, tab, p, "")
    assert "Selector / capacidad</b><span>OFF_BASELINE</span>" in fresh
    p.shadow_fresh = False
    stale = render_shell(destination, tab, p, "")
    assert "Selector / capacidad</b><span>NO_VERIFICADO</span>" in stale


def test_home_attention_highlights_blocking_ars_daily_risk():
    p = SimpleNamespace(now=NOW, shadow_fresh=True,
                        store=SimpleNamespace(query=lambda *_args: [{"state": "VERDE", "checked_at": NOW.isoformat()}]),
                        runtime={"ppi_auth": "OK", "heartbeat_freshness": "FRESH"},
                        risk=lambda: SimpleNamespace(rows=[
                            {"currency": "ARS", "state": "BASELINE_UNAVAILABLE", "freshness": "FRESH"},
                            {"currency": "USD", "state": "READY", "freshness": "FRESH"}]))
    html, count = attention(p, SimpleNamespace(rows=[]))
    assert count == 1
    assert "Riesgo diario ARS: BASELINE_UNAVAILABLE" in html
    assert "Riesgo diario USD" not in html
