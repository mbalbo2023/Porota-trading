from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from rc6_postclose_review import build_review, write_review

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def snapshot(**overrides):
    value = {
        "status": "VERIFIED", "phase": "postclose",
        "generated_at": "2026-09-19T17:15:00-03:00",
        "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
        "paper_safety_status": "VERIFIED",
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


def test_postclose_no_ppi_eod_session_receipt_cannot_certify_report():
    now = datetime(2026, 11, 11, 18, 30, tzinfo=TZ)
    paper = snapshot(generated_at="2026-11-11T18:25:00-03:00")
    review = build_review(paper, now)
    assert review["status"] == "PROVISIONAL_POSTCLOSE_SOURCE_PENDING"
    assert "PPI_EOD_CURRENT_SESSION_SOURCE_PENDING" in review["findings"]


def test_postclose_unverified_paper_snapshot_is_not_masked_as_eod_pending():
    now = datetime(2026, 11, 11, 18, 30, tzinfo=TZ)
    unsafe = snapshot(status="INSUFFICIENT_EVIDENCE",
                      paper_safety_status="VERIFIED",
                      generated_at="2026-11-11T18:25:00-03:00",
                      urgent_alerts=["REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED"])
    review = build_review(unsafe, now)
    assert review["status"] == "INSUFFICIENT_EVIDENCE"
    assert "SNAPSHOT_ALERT:REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED" in review["findings"]


@pytest.mark.parametrize("overrides,reason", (
    ({"mode": "LIVE"}, "MODE_NOT_PRODUCTION_PAPER"),
    ({"mode": "SIMULATION"}, "MODE_NOT_PRODUCTION_PAPER"),
    ({"mode": None}, "MODE_NOT_PRODUCTION_PAPER"),
    ({"real_orders_sent": 1}, "REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED"),
    ({"real_orders_sent": "0"}, "REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED"),
    ({"real_orders_sent": 0.0}, "REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED"),
    ({"real_orders_sent": False}, "REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED"),
    ({"real_orders_sent": None}, "REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED"),
    ({"urgent_alerts": None}, "SNAPSHOT_ALERTS_UNVERIFIED"),
    ({"urgent_alerts": 0}, "SNAPSHOT_ALERTS_UNVERIFIED"),
))
def test_eod_unsafe_paper_is_red_not_masked_as_provisional_source(overrides, reason):
    now = datetime(2026, 11, 11, 18, 30, tzinfo=TZ)
    adversarial = snapshot(status="PROVISIONAL_POSTCLOSE_SOURCE_PENDING",
                           generated_at="2026-11-11T18:25:00-03:00",
                           **overrides)
    review = build_review(adversarial, now)
    assert review["status"] == "INSUFFICIENT_EVIDENCE"
    assert reason in review["findings"]
    assert review["real_orders_authorized"] is False
    assert review["automatic_strategy_change"] is False
