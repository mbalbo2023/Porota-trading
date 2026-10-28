import json
from datetime import date
from pathlib import Path
import rc6_multisource_discovery as m
import bu_instrument_catalog as catalog

def test_ppi_query_plan_is_generated_not_ticker_allowlist():
    plan=m.ppi_query_plan(day=date(2026,9,25),prefixes_per_family=2)
    assert plan
    assert all(len(row)==6 for row in plan)
    assert {"ACCIONES","CEDEARS","ETFS","BONOS","LETRAS","OBLIGACIONES","OPCIONES","FUTUROS","CAUCIONES","FCI"} <= {row[5] for row in plan}
    assert all(row[0] in m.PREFIXES for row in plan if row[5] != "CAUCIONES")


def test_ppi_query_plan_uses_official_caucion_alias_instead_of_rotating_prefixes():
    plan=m.ppi_query_plan(day=date(2026,9,25),prefixes_per_family=4)
    cauciones=[row for row in plan if row[5]=="CAUCIONES"]
    assert cauciones == [("CAUCION","CAUCIONES","INMEDIATA","BYMA",True,"CAUCIONES")]
    assert all(row[0] != "CAUCION" for row in plan if row[5] != "CAUCIONES")

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


def test_legacy_iol_option_sidecar_is_observed_without_repromoting_contract(tmp_path: Path):
    path=tmp_path/"iol_family_reference_latest.json"
    legacy={"records":[{"ticker":"GFGC6000OC","instrument_type":"OPCIONES",
        "market":"BYMA","currency":"ARS","settlement":"INMEDIATA",
        "financial_contract_v17":{"family":"OPCIONES","cash_multiplier":"100",
                                  "premium_basis":"PER_UNDERLYING_UNIT"},
        "option_chain_evidence":{"underlying":"GGAL","strike_price":6000,
                                 "contract_lot":100}}]}
    path.write_text(json.dumps(legacy),encoding="utf-8")
    row=m._family_reference_rows(path)[0]
    assert row["financial_contract_v17"] is None
    assert row["market"] == "UNKNOWN"
    assert row["currency"] is None
    assert row["identity_evidence"]["currency_explicit"] is False
    assert row["identity_evidence"]["market_explicit"] is False
    assert row["option_chain_evidence"]["legacy_contract_lot_unverified"]==100
    assert row["option_chain_evidence"]["legacy_financial_contract_unverified"]["cash_multiplier"]=="100"
    normalized=catalog.normalize_complementary_record(row,"2026-10-27T01:00:00Z","fixture")
    assert not normalized["capability"].startswith("READY_PAPER_")
    primary=catalog.normalize_record({"ticker":"GFGC6000OC","type":"OPCIONES",
        "market":"BYMA","currency":"ARS"},"INMEDIATA", "2026-10-27T01:00:00Z","fixture-ppi")
    assert catalog.complete_with_complement(primary,row)["capability"]=="NEEDS_OPTION_CONTRACT"
    assert m._family_reference_rows(path)==[row]
    assert json.loads(path.read_text(encoding="utf-8"))==legacy  # Pure read; raw evidence unchanged.


def test_exact_iol_option_identity_still_does_not_supply_adjustment_contract(tmp_path: Path):
    path=tmp_path/"iol_family_reference_latest.json"
    path.write_text(json.dumps({"records":[{"ticker":"GFGC6000OC",
        "instrument_type":"OPCIONES","market":"BCBA","currency":"ARS",
        "settlement":"INMEDIATA","financial_contract_v17":None,
        "option_chain_evidence":{"underlying":"GGAL","strike_price":6000,
            "series_identity_state":"EXACT_IOL_ASSET_INFO",
            "series_adjustment_state":"NO_VERIFICADO"}}]}),encoding="utf-8")
    row=m._family_reference_rows(path)[0]
    assert row["market"]=="BYMA" and row["currency"]=="ARS"
    assert row["identity_evidence"]["currency_explicit"] is True
    assert row["financial_contract_v17"] is None


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
      "sources":[{"source":"BYMA","status":"SCRAPED_PUBLIC_DATA","http_status":200,"observed_at":"2026-09-25T21:00:15+00:00","records":[{
        "family":"CEDEARS","symbol":"AAPL","currency":None,"settlement":"A-24HS"
      }]}]
    }),encoding="utf-8")
    rows=[r for r in m.complementary_discovery(tmp_path) if r["ticker"]=="AAPL"]
    assert [r["source"] for r in rows]==["IOL_COMPLEMENTARY","BYMA_PUBLIC_COMPLEMENTARY"]
    assert rows[1]["identity_evidence"]["currency_explicit"] is False
    assert rows[1]["observed_at"] is None
    assert rows[1]["capture_observed_at"]=="2026-09-25T21:00:15+00:00"
    assert rows[1]["provider_observed_at"] is None
    assert rows[1]["freshness_basis"]=="STRUCTURED_CAPTURE_TIMESTAMP"


def test_byma_provider_timestamp_has_precedence_over_capture_timestamp(tmp_path: Path):
    (tmp_path/"iol_shadow_latest.json").write_text('{"symbols":[]}',encoding="utf-8")
    (tmp_path/"iol_family_reference_latest.json").write_text("{}",encoding="utf-8")
    (tmp_path/"rc6_public_sources_latest.json").write_text(json.dumps({
      "collected_at":"2026-09-25T21:00:15+00:00",
      "sources":[{"source":"BYMA","status":"REACHABLE_STRUCTURED_DATA","http_status":200,
        "observed_at":"2026-09-25T21:00:15+00:00","records":[{
        "family":"ACCIONES","symbol":"GGAL","timestamp":"2026-09-25T20:59:59+00:00"
      }]}]
    }),encoding="utf-8")
    row=m.complementary_discovery(tmp_path)[0]
    assert row["observed_at"]=="2026-09-25T20:59:59+00:00"
    assert row["provider_observed_at"]=="2026-09-25T20:59:59+00:00"
    assert row["freshness_basis"]=="PROVIDER_TIMESTAMP"
