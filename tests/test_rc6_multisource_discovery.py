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
