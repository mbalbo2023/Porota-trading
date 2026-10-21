"""A coherent cut must have the same liveness in the home and worker views."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rc6_trader_dashboard.projection import Page, Projection
from rc6_shadow_runtime.worker import durable_report_view


def test_worker_shadow_uses_measured_generation_budget():
    now = datetime(2026, 10, 21, 14, 20, tzinfo=timezone.utc)
    projection = Projection(SimpleNamespace(now=now, one=lambda table: {}))
    projection.__dict__["runtime"] = {"heartbeat_at": now.isoformat(),
                                       "heartbeat_age": 0, "heartbeat_freshness": "FRESH",
                                       "process_state": "RUNNING"}
    projection.risk = lambda: Page("daily-risk")
    projection.__dict__["shadow_liveness"] = {"budget_seconds": 113.222,
                                               "basis": "TWO_MEASURED_CYCLES_SOURCE_CAPPED"}
    for elapsed, expected in ((35, "FRESH"), (114, "STALE")):
        stamp = (now - timedelta(seconds=elapsed)).isoformat()
        projection.__dict__["shadow"] = {
            "state": "COMMITTED_COHERENT_SHADOW", "status": {"as_of": stamp},
            "report": {"as_of": stamp}}
        projection.__dict__["shadow_fresh"] = expected == "FRESH"
        row = next(row for row in projection.workers().rows
                   if row["worker"] == "Selector dinámico SHADOW")
        assert row["freshness"] == expected
        assert row["heartbeat_budget_seconds"] == 113.222
    projection.__dict__["shadow"]["state"] = "NO_VERIFICADO"
    projection.__dict__["shadow_fresh"] = False
    row = next(row for row in projection.workers().rows
               if row["worker"] == "Selector dinámico SHADOW")
    assert row["freshness"] == "NO_VERIFICADO"


def test_committed_view_keeps_actual_observation_coverage():
    for source, expected in ((False, False), (True, True),
                             ("NO_VERIFICADO", "NO_VERIFICADO")):
        report = {"engines": {}, "input_quality": {"observation_read_truncated": source}}
        view = durable_report_view(report, catalog_ready_count=0)
        assert view["input_quality"]["observation_read_truncated"] == expected


def test_factual_paper_heartbeat_matches_bounded_observer_idle(monkeypatch):
    now = datetime(2026, 10, 21, 18, 12, tzinfo=timezone.utc)
    monkeypatch.setenv("PAPER_OBSERVER_INTERVAL_SECONDS", "60")
    for elapsed, expected in ((31, "FRESH"), (61, "FRESH"), (76, "STALE")):
        store = SimpleNamespace(now=now, one=lambda table: {
            "heartbeat_at": (now-timedelta(seconds=elapsed)).isoformat(),
            "process_state": "RUNNING"} if table == "observer_state" else {})
        runtime = Projection(store).runtime
        assert runtime["heartbeat_budget_seconds"] == 75
        assert runtime["heartbeat_freshness"] == expected
    for value in ("invalid", "3600"):
        monkeypatch.setenv("PAPER_OBSERVER_INTERVAL_SECONDS", value)
        store = SimpleNamespace(now=now, one=lambda table: {
            "heartbeat_at": (now-timedelta(seconds=31)).isoformat(),
            "process_state": "RUNNING"} if table == "observer_state" else {})
        runtime = Projection(store).runtime
        assert runtime["heartbeat_budget_seconds"] == 30
        assert runtime["heartbeat_freshness"] == "STALE"
