from datetime import datetime, timedelta, timezone

from rc6_trader_dashboard.projection import Page, Projection


class Store:
    now = datetime(2026, 10, 23, 0, 18, tzinfo=timezone.utc)

    def __init__(self, component, seconds):
        self.component = component
        self.seconds = seconds

    def columns(self, name):
        assert name == "api_health"
        return {"component"}

    def page(self, name, **kwargs):
        assert name == "api_health"
        checked = (self.now - timedelta(seconds=self.seconds)).isoformat()
        return Page("api_health", [{"component": self.component, "state": "VERDE",
                                    "source": "Runtime PAPER", "checked_at": checked}], 1, "AVAILABLE")


def source_row(component, seconds):
    # Offset avoids the independent IOL mounted-cache row.
    return Projection(Store(component, seconds), {"offset": "10"}).sources().rows[0]


def test_internal_paper_check_uses_bounded_observer_cadence(monkeypatch):
    monkeypatch.setenv("PAPER_READINESS_CHECK_SECONDS", "300")
    row = source_row("PAPER_ECONOMIC_GATE_SHADOW", 185)
    assert (row["state"], row["check_freshness"], row["checked_age_seconds"]) == ("VERDE", "FRESH", 185)
    assert row["cadence"] == "CHECK_WITHIN_315s"
    assert source_row("PAPER_ECONOMIC_GATE_SHADOW", 316)["state"] == "NO_VERIFICADO"


def test_untrusted_interval_and_other_sources_remain_fail_closed(monkeypatch):
    monkeypatch.setenv("PAPER_READINESS_CHECK_SECONDS", "3600")
    assert source_row("PAPER_ECONOMIC_GATE_SHADOW", 185)["state"] == "NO_VERIFICADO"
    monkeypatch.setenv("PAPER_READINESS_CHECK_SECONDS", "300")
    row = source_row("OTHER_COMPONENT", 185)
    assert (row["state"], row["check_freshness"], row["cadence"]) == (
        "NO_VERIFICADO", "STALE", "CHECK_WITHIN_180s")
