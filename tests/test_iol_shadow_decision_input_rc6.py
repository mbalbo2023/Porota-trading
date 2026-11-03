from datetime import datetime, timezone
import json
import iol_shadow_decision_input_rc6 as adapter

def cache(tmp_path, data):
    (tmp_path / "iol_shadow_latest.json").write_text(json.dumps(data), encoding="utf-8")

def test_reads_only_fresh_row(tmp_path):
    cache(tmp_path, {"schema_version":3,"run_id":"r1","universe_source":"DB_OPERATIONAL","symbols":[{"symbol":"AAPL","state":"READY","captured_at":"2026-09-19T13:00:00+00:00","quote":{"last":10},"primary_comparison":{"state":"MATCH"}}]})
    got=adapter.read_for_decision("aapl", root=tmp_path, now=datetime(2026,9,19,13,1,tzinfo=timezone.utc))
    assert got["mode"] == "SHADOW_DUAL_EVALUATION"
    assert got["decision_effect"] == "NO_FACTUAL_BINDING"
    assert got["freshness"] == "FRESH" and got["quality"] == "GOOD"
    assert got["coverage"]["universe_source"] == "DB_OPERATIONAL"

def test_cache_timestamp_never_substitutes_row_timestamp(tmp_path):
    cache(tmp_path, {"schema_version":3,"refreshed_at":"2026-09-19T13:10:00+00:00","symbols":[{"symbol":"GGAL","state":"READY","quote":{"last":10}}]})
    got=adapter.read_for_decision("GGAL", root=tmp_path, now=datetime(2026,9,19,13,10,tzinfo=timezone.utc))
    assert got["freshness"] == "UNKNOWN" and got["quality"] == "DEGRADED"

def test_invalid_missing_and_stale_fail_closed(tmp_path):
    assert adapter.read_for_decision("YPF",root=tmp_path)["reason"] == "CACHE_MISSING_OR_INVALID"
    cache(tmp_path,{"schema_version":3,"symbols":[{"symbol":"YPF","state":"READY","captured_at":"2026-09-19T12:00:00+00:00","quote":{"last":1}}]})
    got=adapter.read_for_decision("YPF",root=tmp_path,now=datetime(2026,9,19,13,tzinfo=timezone.utc))
    assert got["freshness"] == "STALE" and got["quality"] == "DEGRADED"

def test_coverage_never_infers_completed_cycle(tmp_path):
    cache(tmp_path,{"schema_version":3,"symbols":[{"symbol":"YPF","state":"READY","captured_at":"2026-09-19T13:00:00+00:00","quote":{"last":1}}]})
    got=adapter.read_for_decision("YPF",root=tmp_path,now=datetime(2026,9,19,13,tzinfo=timezone.utc))
    assert got["coverage"]["cycle_status"] == "UNKNOWN"
    assert got["coverage"]["expected"] is None


def test_shadow_decision_input_carries_quote_and_asset_metadata_with_field_coverage(tmp_path):
    cache(tmp_path, {
        "schema_version": 3,
        "symbols": [{
            "symbol": "GGAL", "market": "BCBA", "term": "t1", "state": "READY",
            "captured_at": "2026-09-19T13:00:00+00:00",
            "asset_type": "ACCIONES", "currency": "ARS", "units_per_lot": 1,
            "quote": {"last": 100, "bid": 99, "ask": 101, "spread_pct": 2.02,
                      "variation_pct": 1.5, "cash_volume": 250000},
        }],
    })
    got = adapter.read_for_decision(
        "GGAL", root=tmp_path, now=datetime(2026, 9, 19, 13, 1, tzinfo=timezone.utc)
    )
    assert got["decision_effect"] == "NO_FACTUAL_BINDING"
    assert got["metadata"] == {"asset_type": "ACCIONES", "currency": "ARS", "units_per_lot": 1}
    assert got["quote"]["bid"] == 99
    assert got["quote"]["cash_volume"] == 250000
    assert got["field_coverage"]["present_count"] == got["field_coverage"]["expected_count"]
    assert got["field_coverage"]["missing"] == []
