from pathlib import Path

import pytest

import da_dashboard_ux_hf6 as ux
import db_dashboard_logs_hf6 as logs
import db_a3_cem_normalizer_hf6 as cem


def test_scalping_is_top_level_operational_destination():
    ux.assert_ux_invariants()
    top={item.href for item in ux.TOP_NAV}
    assert "/trading" in top
    assert "/instrumentos" in top
    assert "/scalping" in top
    assert "/scalping" not in ux.LEGACY_ROUTE_REDIRECTS
    assert ux.families_for_group("futuros") == ("FUTUROS",)


def test_log_resolver_stays_under_root(tmp_path: Path):
    observer=tmp_path/"observer_runtime.log"
    observer.write_text("a\nb\nc\n",encoding="utf-8")
    sources=logs.discover_sources(tmp_path)
    assert sources[0].source_id == "observer"
    assert logs.tail_lines(sources[0],2) == ["b","c"]
    assert logs.source_by_id("observer",tmp_path).path == observer.resolve()


def test_log_resolver_does_not_follow_symlink(tmp_path: Path):
    outside=tmp_path.parent/(tmp_path.name+"-outside.log")
    outside.write_text("secret",encoding="utf-8")
    link=tmp_path/"observer_runtime.log"
    link.symlink_to(outside)
    assert logs.discover_sources(tmp_path) == []


def test_cem_symbol_classification():
    future=cem.normalize_symbol({"symbol":"DLR/X","cfiCode":"FXXXSX","maturityDate":"2026-10-30T00:00:00Z"})
    option=cem.normalize_symbol({"symbol":"OPT/X","cfiCode":"OCASPS","strikePrice":100})
    assert future.family == "FUTUROS"
    assert option.family == "OPCIONES"
    assert cem.CEM_EXECUTION_ALLOWED is False


def test_cem_settlement_price_is_metadata_not_identity():
    row={
        "dateTime":"2026-09-02T17:00:00Z",
        "symbol":"DLR/OCT26",
        "settlement":1450.5,
        "open":1440.0,
        "high":1460.0,
        "low":1435.0,
        "close":1455.0,
        "volume":100,
        "openInterest":200,
    }
    with pytest.raises(ValueError,match="SETTLEMENT_IDENTITY_REQUIRED"):
        cem.closing_to_candle(row,instrument_type="FUTUROS",market="A3",settlement_identity="")
    candle=cem.closing_to_candle(row,instrument_type="FUTUROS",market="A3",settlement_identity="DAILY_ADJUSTMENT")
    assert candle.settlement == "DAILY_ADJUSTMENT"
    assert candle.metadata["cem_settlement_price"] == 1450.5
    assert candle.source == "A3_CEM_CLOSING"


def test_cem_tick_never_becomes_execution_source():
    tick=cem.normalize_tick({"symbol":"DLR/OCT26","price":1455.0,"volume":3,"dateTime":"2026-09-02T16:59:59Z"})
    assert tick["execution_allowed"] is False
    assert tick["source"] == "A3_CEM_TICK"
