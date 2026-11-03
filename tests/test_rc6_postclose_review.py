from datetime import datetime
import json
from zoneinfo import ZoneInfo

import pytest

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


@pytest.mark.parametrize("name,raw", [
    ("net_pnl_ars", float("nan")),
    ("net_pnl_ars", float("inf")),
    ("net_pnl_ars", "-Infinity"),
    ("net_pnl_ars", "1e309"),
    ("net_pnl_ars", False),
    ("win_rate_pct", float("nan")),
    ("win_rate_pct", True),
    ("win_rate_pct", 120.0),
    ("win_rate_pct", -1.0),
])
def test_nonfinite_boolean_or_out_of_range_financial_metric_blocks_review(name, raw):
    data = snapshot()
    data["metrics"][name] = raw
    review = build_review(data, datetime(2026, 9, 19, 18, 0, tzinfo=TZ))
    assert review["status"] == "INSUFFICIENT_EVIDENCE"
    assert "POSTCLOSE_FINANCIAL_METRICS_INVALID" in review["findings"]
    assert review["automatic_strategy_change"] is False
    assert review["real_orders_authorized"] is False
    assert review["metrics"][name] is None
    json.dumps(review, allow_nan=False)


@pytest.mark.parametrize("name,raw", [
    ("closed_operations", float("nan")),
    ("closed_operations", float("inf")),
    ("closed_operations", False),
    ("closed_operations", True),
    ("closed_operations", 0.5),
    ("closed_operations", -1),
    ("decisions_observed", float("nan")),
    ("decisions_observed", float("inf")),
    ("decisions_observed", False),
    ("decisions_observed", 0.5),
])
def test_malformed_counters_never_certify_review_or_crash(name, raw):
    data = snapshot()
    data["metrics"][name] = raw
    review = build_review(data, datetime(2026, 9, 19, 18, 0, tzinfo=TZ))
    assert review["status"] == "INSUFFICIENT_EVIDENCE"
    assert "POSTCLOSE_COUNTERS_INVALID" in review["findings"]
    assert review["automatic_strategy_change"] is False
    assert review["real_orders_authorized"] is False
    json.dumps(review, allow_nan=False)


def test_decimal_string_counters_preserve_valid_legacy_report():
    data = snapshot()
    data["metrics"]["closed_operations"] = "2"
    data["metrics"]["decisions_observed"] = "7"
    review = build_review(data, datetime(2026, 9, 19, 18, 0, tzinfo=TZ))
    assert review["status"] == "VERIFIED"
    assert review["metrics"]["closed_operations"] == 2
    assert review["metrics"]["decisions_observed"] == 7


def test_write_review_rejects_nonfinite_payload_atomically(tmp_path):
    output = tmp_path / "review.json"
    with pytest.raises(ValueError):
        write_review({"metrics": {"win_rate_pct": float("inf")}}, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".postclose-review.*"))
