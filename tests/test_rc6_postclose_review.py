from datetime import datetime
from zoneinfo import ZoneInfo

from rc6_postclose_review import build_review, write_review

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def snapshot(**overrides):
    value = {
        "status": "VERIFIED", "phase": "postclose",
        "generated_at": "2026-09-19T17:15:00-03:00",
        "metrics": {"closed_operations": 2, "net_pnl_ars": 55.5, "win_rate_pct": 50.0, "decisions_observed": 7},
        "operations": [{"symbol": "GGAL", "net_pnl_ars": 10.0}],
        "urgent_alerts": [],
    }
    value.update(overrides)
    return value


def test_current_snapshot_produces_human_only_review():
    review = build_review(snapshot(), datetime(2026, 9, 19, 18, 0, tzinfo=TZ))
    assert review["status"] == "VERIFIED"
    assert review["automatic_strategy_change"] is False
    assert review["real_orders_authorized"] is False
    assert review["decision_authority"] == "HUMAN_REVIEW_REQUIRED"
    assert review["metrics"]["net_pnl_ars"] == 55.5


def test_negative_results_are_findings_not_automatic_strategy_changes():
    review = build_review(snapshot(metrics={"closed_operations": 1, "net_pnl_ars": -20, "win_rate_pct": 0, "decisions_observed": 3}), datetime(2026, 9, 19, 18, 0, tzinfo=TZ))
    assert "NET_PNL_NEGATIVE" in review["findings"]
    assert "WIN_RATE_BELOW_50_PCT" in review["findings"]
    assert review["automatic_strategy_change"] is False


def test_stale_snapshot_fails_closed(tmp_path):
    review = build_review(snapshot(generated_at="2026-09-18T17:15:00-03:00"), datetime(2026, 9, 19, 18, 0, tzinfo=TZ))
    assert review["status"] == "INSUFFICIENT_EVIDENCE"
    assert "POSTCLOSE_SNAPSHOT_MISSING_OR_STALE" in review["findings"]
    output = tmp_path / "review.json"
    write_review(review, output)
    assert output.is_file()
