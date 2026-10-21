"""Project the actual exporter schema, preserving unknown success clocks."""
from datetime import datetime, timezone
from types import SimpleNamespace

from rc6_trader_dashboard.datasets import artifact


def test_scheduler_exporter_fields_are_visible_without_inventing_success():
    stamp = datetime(2026, 10, 21, 14, 35, tzinfo=timezone.utc)
    payload = {"recorded_at": stamp.isoformat(), "timers": [
        {"unit": "porota-example-rc6.timer", "enabled_state": "enabled",
         "active_state": "active", "next_elapse": stamp.isoformat(),
         "last_trigger": stamp.isoformat(), "description": "Example",
         "service_active_enter": stamp.isoformat(), "last_result": "success"}]}
    projection = SimpleNamespace(now=stamp, offset=0,
                                 bounded_file=lambda *_args, **_kwargs: (payload, "AVAILABLE"))
    page = artifact(projection, "scheduler")
    row = page.rows[0]
    assert (row["state"], row["freshness"], row["enabled"], row["active"]) == (
        "ACTIVE", "FRESH", "enabled", "active")
    assert row["next_run"] == stamp.isoformat()
    assert row["last_run"] == stamp.isoformat()
    assert row["last_success"] is None
