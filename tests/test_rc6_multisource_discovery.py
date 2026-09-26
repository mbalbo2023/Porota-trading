import json
from datetime import date
from pathlib import Path
import rc6_multisource_discovery as m

def test_ppi_query_plan_is_generated_not_ticker_allowlist():
    plan=m.ppi_query_plan(day=date(2026,9,25),prefixes_per_family=2)
    assert plan
    assert all(len(row)==6 for row in plan)
    assert {"ACCIONES","CEDEARS","ETFS","BONOS","LETRAS","OBLIGACIONES","OPCIONES","FUTUROS","CAUCIONES","FCI"} <= {row[5] for row in plan}
    assert all(row[0] in m.PREFIXES for row in plan)

def test_complementary_discovery_includes_family_reference_and_fci(tmp_path: Path):
    (tmp_path/"iol_shadow_latest.json").write_text(json.dumps({"symbols":[]}),encoding="utf-8")
    (tmp_path/"rc6_public_sources_latest.json").write_text(json.dumps({"sources":[]}),encoding="utf-8")
    (tmp_path/"iol_family_reference_latest.json").write_text(json.dumps({
      "records":[{"ticker":"GD30","instrument_type":"BONOS","market":"BCBA","currency":"ARS","settlement":"T1",
        "source":"IOL_COMPLEMENTARY","financial_contract_v17":{"family":"BONOS","cash_multiplier":"0.01"}}],
      "fci":[{"asset":"IOLCAMA","description":"IOL Cash Management","market":"BCBA","currency":"ARS","operable":True}]
    }),encoding="utf-8")
    rows=m.complementary_discovery(tmp_path)
    by={(r["instrument_type"],r["ticker"]):r for r in rows}
    assert by[("BONOS","GD30")]["market"]=="BYMA"
    assert by[("BONOS","GD30")]["settlement"]=="A-24HS"
    assert by[("BONOS","GD30")]["financial_contract_v17"]["cash_multiplier"]=="0.01"
    assert by[("FCI","IOLCAMA")]["source"]=="IOL_COMPLEMENTARY"


def test_iol_shadow_propagates_provider_timestamp_for_freshness(tmp_path: Path):
    (tmp_path/"iol_shadow_latest.json").write_text(json.dumps({
      "refreshed_at":"2026-09-25T21:00:00+00:00",
      "symbols":[{
        "symbol":"AAPL","state":"READY","asset_type":"CEDEARS","market":"BCBA","currency":"ARS","term":"t1",
        "quote":{"last":100,"provider_observed_at":"2026-09-25T20:59:30+00:00"}
      }]
    }),encoding="utf-8")
    (tmp_path/"iol_family_reference_latest.json").write_text("{}",encoding="utf-8")
    (tmp_path/"rc6_public_sources_latest.json").write_text(json.dumps({"sources":[]}),encoding="utf-8")
    rows=m.complementary_discovery(tmp_path)
    row=next(r for r in rows if r["ticker"]=="AAPL")
    assert row["source"]=="IOL_COMPLEMENTARY"
    assert row["observed_at"]=="2026-09-25T20:59:30+00:00"
    assert row["provider_observed_at"]=="2026-09-25T20:59:30+00:00"


def test_discovery_keeps_iol_then_byma_instead_of_collapsing_one_winner(tmp_path: Path):
    (tmp_path/"iol_shadow_latest.json").write_text(json.dumps({
      "refreshed_at":"2026-09-25T21:00:00+00:00",
      "symbols":[{
        "symbol":"AAPL","state":"READY","asset_type":"CEDEARS","market":"BCBA","currency":"ARS","term":"t1",
        "quote":{"last":100,"provider_observed_at":"2026-09-25T20:59:30+00:00"}
      }]
    }),encoding="utf-8")
    (tmp_path/"iol_family_reference_latest.json").write_text("{}",encoding="utf-8")
    (tmp_path/"rc6_public_sources_latest.json").write_text(json.dumps({
      "collected_at":"2026-09-25T21:00:15+00:00",
      "sources":[{"source":"BYMA","observed_at":"2026-09-25T21:00:15+00:00","records":[{
        "family":"CEDEARS","symbol":"AAPL","currency":None,"settlement":"A-24HS"
      }]}]
    }),encoding="utf-8")
    rows=[r for r in m.complementary_discovery(tmp_path) if r["ticker"]=="AAPL"]
    assert [r["source"] for r in rows]==["IOL_COMPLEMENTARY","BYMA_PUBLIC_COMPLEMENTARY"]
    assert rows[1]["identity_evidence"]["currency_explicit"] is False
