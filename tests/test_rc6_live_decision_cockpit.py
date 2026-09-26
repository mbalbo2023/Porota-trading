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
            {"decided_at": now.isoformat(), "symbol": "YPFD", "action": "HOLD", "score": 0.99, "reason": "spread"},
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
    assert "Trazabilidad · últimas 10 decisiones" in rendered
    assert "Contrafáctico LIVE" in rendered
    assert "NONE_OBSERVE_ONLY" in rendered
    assert rendered.count("<td><b>S") == site.MAX_ROWS
    assert "sin controles de ejecución" in rendered


def test_preopen_baseline_is_bounded_and_read_only(tmp_path):
    path = tmp_path / "preopen_latest.json"
    path.write_text(json.dumps({"phase": "preopen", "status": "VERIFIED"}), encoding="utf-8")
    now = datetime(2026, 9, 18, 10, 10, tzinfo=TZ)
    observer = {
        "decisions": [
            {"decided_at": now.isoformat(), "symbol": f"S{i}", "action": "HOLD", "score": i, "reason": "baseline"}
            for i in range(15)
        ]
    }

    result = live.capture_preopen_baseline(observer, now, path)
    stored = json.loads(path.read_text(encoding="utf-8"))

    assert result == {"status": "VERIFIED", "decision_sample": live.MAX_ROWS}
    assert stored["decision_sample_read_only"] is True
    assert len(stored["decision_sample"]) == live.MAX_ROWS



def test_trader_workstation_metrics_and_render(monkeypatch, tmp_path):
    now = datetime(2026, 9, 18, 14, 32, tzinfo=TZ)
    observer = {
        "state": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "regime": "MIXED"},
        "quotes": [
            {"symbol": "GGAL", "asset_class": "ACCIONES", "bid": "100", "ask": "101", "observed_at": now.isoformat()},
            {"symbol": "AAPL", "asset_class": "CEDEARS", "bid": "50", "ask": "50.5", "observed_at": now.isoformat()},
        ],
        "decisions": [
            {"decided_at": now.isoformat(), "symbol": "GGAL", "action": "BUY", "score": 0.82, "reason": "signal", "strategy_version": "s1"},
            {"decided_at": now.isoformat(), "symbol": "AAPL", "action": "HOLD", "score": 0.51, "reason": "spread", "strategy_version": "s1"},
        ],
        "open": [{
            "paper_id": "p1", "symbol": "GGAL", "asset_class": "ACCIONES", "currency": "ARS",
            "quantity": "2", "entry_price": "100", "current_price": "102", "stop_price": "95",
            "target_price": "112", "unrealized_pnl": "4", "opened_at": now.isoformat(),
        }],
        "closed": [
            {"paper_id": "c1", "symbol": "GGAL", "asset_class": "ACCIONES", "currency": "ARS", "status": "CLOSED", "net_pnl": "100", "close_reason": "TAKE_PROFIT"},
            {"paper_id": "c2", "symbol": "AAPL", "asset_class": "CEDEARS", "currency": "ARS", "status": "CLOSED", "net_pnl": "-50", "close_reason": "STOP"},
        ],
        "equity": {"equity": "1000050"},
        "balances_by_currency": [{"currency": "ARS", "cash": "900000", "exposure": "100050", "unrealized_pnl": "4", "realized_pnl": "50", "equity": "1000054"}],
        "daily_risk": [{"day": "2026-09-18", "currency": "ARS", "state": "OK", "baseline_equity": "1000000", "daily_pnl": "50", "loss_budget": "30000", "detail": "OK"}],
        "valuation_quality": [{"currency": "ARS", "state": "FRESH"}],
        "exit_intents": [],
        "notification_counts": [],
        "_cockpit_db": {
            "gates": [{"evaluated_at": now.isoformat(), "symbol": "GGAL", "technical_gate": "PASS", "ai_gate": "PASS", "patrimonial_gate": "PASS", "final_result": "OPENED_SIMULATED", "reason": "OK", "paper_id": "p1"}],
            "fills": [{"filled_at": now.isoformat(), "paper_id": "p1", "side": "BUY", "quantity": "2", "price": "100", "costs": "1.5", "slippage": "0.25", "symbol": "GGAL", "currency": "ARS", "asset_class": "ACCIONES"}],
            "family_coverage": [{"instrument_type": "ACCIONES", "declared": 1, "queries": 2, "observed_count": 20, "ready_paper_count": 18, "discovery_status": "READY", "checked_at": now.isoformat()}],
            "catalog_status": [],
            "api_health": [{"component": "PPI", "state": "GREEN", "detail": "OK", "checked_at": now.isoformat(), "last_success_at": now.isoformat(), "source": "PPI"}],
            "source_sync": [{"source": "IOL", "status": "READY", "last_attempt_at": now.isoformat(), "last_success_at": now.isoformat(), "items": 20, "detail": "OK"}],
        },
    }
    result = live.build_payload(observer, {}, {}, {}, {}, now)
    assert result["market"]["quotes"] == 2
    assert result["market"]["valid_books"] == 2
    assert result["performance"]["by_currency"]["ARS"]["trades"] == 2
    assert result["performance"]["by_currency"]["ARS"]["net_pnl"] == 50.0
    assert result["decision_funnel"]["actions"]["BUY"] == 1
    assert result["families"][0]["quotes"] >= 1
    assert result["positions"][0]["symbol"] == "GGAL"
    assert result["execution"]["by_currency"]["ARS"]["fills"] == 1
    assert result["execution"]["by_currency"]["ARS"]["avg_slippage"] == 0.25
    assert result["gate_matrix"]["final_results"]["OPENED_SIMULATED"] == 1
    assert result["family_readiness"][0]["ready_paper_count"] == 18
    assert result["source_health"]["api_health"][0]["component"] == "PPI"

    snapshots = tmp_path / "snapshots"
    reports = tmp_path / "reports"
    snapshots.mkdir()
    reports.mkdir()
    (snapshots / "live_latest.json").write_text(json.dumps(result), encoding="utf-8")
    monkeypatch.setattr(site, "LIVE", snapshots / "live_latest.json")
    monkeypatch.setattr(site, "PREOPEN", snapshots / "preopen_latest.json")
    monkeypatch.setattr(site, "POSTCLOSE", snapshots / "postclose_latest.json")
    monkeypatch.setattr(site, "LATEST", snapshots / "latest.json")
    monkeypatch.setattr(site, "POST_REVIEW", reports / "postclose_review_latest.json")
    rendered = site.render()
    assert "Posiciones y riesgo" in rendered
    assert "Mercado y liquidez" in rendered
    assert "Ejecución y costos" in rendered
    assert "Estrategia y gates" in rendered
    assert "Readiness canónico por familia" in rendered
    assert "Salud de APIs / adaptadores" in rendered
    assert "Performance" in rendered
    assert "Riesgo diario por moneda" in rendered
    assert "Sin total multi-moneda" in rendered
