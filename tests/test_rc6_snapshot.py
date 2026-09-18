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


import json


def test_postclose_evidence_reads_iol_cache_without_authorizing_ready(tmp_path):
    cache = tmp_path / "iol_shadow_latest.json"
    cache.write_text(json.dumps({
        "source": "IOL_MCP",
        "mode": "SHADOW",
        "refreshed_at": "2026-09-18T19:59:00+00:00",
        "symbols": [{
            "symbol": "GGAL", "state": "READY",
            "captured_at": "2026-09-18T19:59:00+00:00",
            "asset_type": "ACCIONES", "currency": "ARS", "units_per_lot": 1,
            "primary_comparison": {"state": "MATCH"},
        }],
    }), encoding="utf-8")
    from rc6_snapshot import build_postclose_evidence
    evidence = build_postclose_evidence(
        datetime(2026, 9, 18, 17, 15, tzinfo=TZ), cache
    )
    assert evidence["status"] == "OBSERVED"
    assert evidence["ready_paper_authorized"] is False
    assert evidence["decision_effect"] == "OBSERVE_ONLY"
    assert evidence["contract_gate"] == "SOURCE_UNAVAILABLE_BY_SCOPE"
    assert evidence["counts"] == {"observed": 1, "ready": 1, "unavailable": 0}


def test_postclose_evidence_fails_closed_when_iol_cache_is_stale(tmp_path):
    cache = tmp_path / "iol_shadow_latest.json"
    cache.write_text(json.dumps({
        "source": "IOL_MCP", "mode": "SHADOW",
        "refreshed_at": "2026-09-17T20:00:00+00:00", "symbols": [],
    }), encoding="utf-8")
    from rc6_snapshot import build_postclose_evidence
    evidence = build_postclose_evidence(
        datetime(2026, 9, 18, 17, 15, tzinfo=TZ), cache
    )
    assert evidence["status"] == "INSUFFICIENT_EVIDENCE"
    assert evidence["reason"] == "IOL_SHADOW_CACHE_STALE"
