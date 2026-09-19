import json
from datetime import datetime
from zoneinfo import ZoneInfo

import rc6_live_decision_cockpit as live
import rc6_private_site_8766 as site

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def test_live_payload_is_read_only_and_bounded():
    now = datetime(2026, 9, 18, 14, 32, tzinfo=TZ)
    observer = {
        "state": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "regime": "MIXED"},
        "decisions": [
            {"decided_at": now.isoformat(), "symbol": "GGAL", "action": "BUY", "score": 0.82, "reason": "signal"},
            {"decided_at": now.isoformat(), "symbol": "YPFD", "action": "HOLD", "score": 0.60, "reason": "spread"},
        ],
        "open": [{"symbol": "GGAL", "quantity": 2, "entry_price": 100}],
        "closed": [],
    }
    iol = {
        "source": "IOL_MCP", "mode": "SHADOW", "decision_effect": "OBSERVE_ONLY",
        "symbols": [{
            "symbol": "GGAL", "state": "READY", "captured_at": now.isoformat(),
            "primary_comparison": {"state": "MATCH"},
        }],
    }
    pre = {
        "generated_at": datetime(2026, 9, 18, 10, 10, tzinfo=TZ).isoformat(),
        "decision_sample": [{"symbol": "GGAL", "action": "HOLD"}],
    }

    result = live.build_payload(observer, iol, {}, pre, {}, now)

    assert result["status"] == "VERIFIED"
    assert result["read_only"] is True
    assert result["decision_authority"] == "OBSERVE_ONLY"
    assert result["automatic_strategy_change"] is False
    assert result["real_orders_authorized"] is False
    assert result["real_orders_sent"] == 0
    assert result["top_opportunities"][0]["symbol"] == "GGAL"
    assert result["why_not_traded"][0]["symbol"] == "YPFD"
    assert result["changes_from_preopen"]["changes"] == [{"symbol": "GGAL", "from": "HOLD", "to": "BUY"}]
    assert result["iol"]["fresh_ready"] == 1
    assert result["iol"]["influence_on_live_decision"] == "NONE_OBSERVE_ONLY"
    assert result["risk"]["estimated_notional_ars"] == 200.0


def test_private_site_renders_live_and_limits_rows(monkeypatch, tmp_path):
    snapshots = tmp_path / "snapshots"
    reports = tmp_path / "reports"
    snapshots.mkdir()
    reports.mkdir()

    now = datetime.now(TZ).isoformat()
    live_payload = {
        "generated_at": now, "phase": "live", "status": "VERIFIED",
        "mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "market_regime": "N/D",
        "top_opportunities": [
            {"symbol": f"S{i}", "action": "BUY", "score": 100-i, "reason": "test", "decided_at": now}
            for i in range(15)
        ],
        "why_not_traded": [],
        "iol": {"universe_observed": 8, "fresh_ready": 8, "price_divergence": 0,
                "influence_on_live_decision": "NONE_OBSERVE_ONLY", "decision_effect": "OBSERVE_ONLY"},
        "risk": {"open_positions": 1},
        "changes_from_preopen": {"status": "INSUFFICIENT_EVIDENCE", "label": "Sin baseline", "changes": []},
        "runtime": {"decisions_visible": 100, "closed_positions_visible": 3},
        "urgent_alerts": [],
    }
    (snapshots / "live_latest.json").write_text(json.dumps(live_payload), encoding="utf-8")
    (snapshots / "postclose_latest.json").write_text(json.dumps({
        "generated_at": now, "phase": "postclose", "status": "VERIFIED",
        "mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "metrics": {},
    }), encoding="utf-8")

    monkeypatch.setattr(site, "LIVE", snapshots / "live_latest.json")
    monkeypatch.setattr(site, "PREOPEN", snapshots / "preopen_latest.json")
    monkeypatch.setattr(site, "POSTCLOSE", snapshots / "postclose_latest.json")
    monkeypatch.setattr(site, "LATEST", snapshots / "latest.json")
    monkeypatch.setattr(site, "POST_REVIEW", reports / "postclose_review_latest.json")

    rendered = site.render()

    assert "Decision Cockpit privado" in rendered
    assert "Por qué NO operó" in rendered
    assert "NONE_OBSERVE_ONLY" in rendered
    assert rendered.count("<td><b>S") == site.MAX_ROWS
    assert "sin controles de ejecución" in rendered
