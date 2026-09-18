from datetime import datetime
from zoneinfo import ZoneInfo

from rc6_snapshot import build_snapshot, write_snapshot

TZ = ZoneInfo("America/Argentina/Buenos_Aires")

def payload(mode="PRODUCTION_PAPER", real_orders_sent=0):
    return {
        "state": {"mode": mode, "real_orders_sent": real_orders_sent},
        "closed": [{"symbol": "GGAL", "opened_at": "2026-09-18T11:00:00-03:00", "closed_at": "2026-09-18T15:00:00-03:00", "net_pnl": "125.5", "close_reason": "TAKE_PROFIT"}],
        "decisions": [{"action": "BUY"}],
    }

def test_verified_snapshot_is_paper_only_and_keeps_operation_trace():
    snap = build_snapshot("postclose", datetime(2026, 9, 18, 17, 15, tzinfo=TZ), payload())
    assert snap["status"] == "VERIFIED"
    assert snap["real_orders_sent"] == 0
    assert snap["metrics"] == {"closed_operations": 1, "net_pnl_ars": 125.5, "winners": 1, "losers": 0, "breakeven": 0, "win_rate_pct": 100.0, "decisions_observed": 1}
    assert snap["operations"] == [{
        "symbol": "GGAL", "type": "PAPER", "opened_at": "2026-09-18T11:00:00-03:00",
        "closed_at": "2026-09-18T15:00:00-03:00", "net_pnl_ars": 125.5,
        "decision_reason": "TAKE_PROFIT", "external_sources": [],
        "counterfactual": "INSUFFICIENT_EVIDENCE",
    }]

def test_nonzero_real_orders_is_fail_closed():
    snap = build_snapshot("preopen", datetime(2026, 9, 18, 10, 10, tzinfo=TZ), payload(real_orders_sent=1))
    assert snap["status"] == "INSUFFICIENT_EVIDENCE"
    assert "REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED" in snap["urgent_alerts"]

def test_write_is_valid_json(tmp_path):
    output = tmp_path / "snapshots" / "latest.json"
    write_snapshot({"schema_version": 1, "status": "VERIFIED"}, output)
    assert output.read_text(encoding="utf-8").startswith("{")


def test_snapshot_reports_zero_closed_operations_without_inventing_win_rate():
    empty = payload()
    empty["closed"] = []
    snap = build_snapshot("postclose", datetime(2026, 9, 18, 17, 15, tzinfo=TZ), empty)
    assert snap["metrics"]["closed_operations"] == 0
    assert snap["metrics"]["net_pnl_ars"] == 0.0
    assert snap["metrics"]["win_rate_pct"] is None
