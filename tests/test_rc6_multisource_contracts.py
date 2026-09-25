import rc6_multisource_contracts as c

def test_sources_complement_missing_fields():
    records=[
      {"source_class":"PPI_STRUCTURED_API","evidence":{"market":"BYMA","currency":"ARS","settlement":"A-24HS"}},
      {"source_class":"IOL_MCP","evidence":{"price_quote_unit":100,"quantity_step":1,"maturity_date":"2030-07-09"}},
    ]
    r=c.readiness("BONOS",records)
    assert r["status"]=="READY_PAPER_CONTRACT"
    assert r["paper_simulatable"] is True
    assert r["provenance"]["price_quote_unit"]["source"]=="IOL_MCP"
    assert r["real_money_authorized"] is False

def test_conflict_blocks_instead_of_picking_source():
    records=[
      {"source_class":"PPI_STRUCTURED_API","evidence":{"currency":"ARS"}},
      {"source_class":"IOL_MCP","evidence":{"currency":"USD"}},
    ]
    r=c.readiness("BONOS",records)
    assert r["status"]=="BLOCKED_CONFLICT"
    assert "currency" in r["conflicts"]

def test_option_needs_multiplier_even_when_chain_completes_other_fields():
    rows=[
      {"source_class":"PPI_STRUCTURED_API","evidence":{"market":"BYMA","currency":"ARS","settlement":"INMEDIATA"}},
      {"source_class":"IOL_MCP","evidence":{"underlying":"GGAL","put_call":"CALL","strike":4200,
       "expiry_at":"2026-10-16T15:30:00-03:00","quantity_step":1}},
    ]
    r=c.readiness("OPCIONES",rows)
    assert r["status"]=="PENDING_CONTRACT_EVIDENCE"
    assert r["missing"]==["contract_multiplier"]

def test_fixed_income_contract_uses_explicit_quote_basis():
    rows=[
      {"source_class":"PPI_STRUCTURED_API","evidence":{"market":"BYMA","currency":"ARS","settlement":"A-24HS"}},
      {"source_class":"IOL_MCP","evidence":{"price_quote_unit":100,"quantity_step":1,"maturity_date":"2030-07-09"}},
    ]
    spec=c.fixed_income_instrument_contract("AL30","BONOS",rows)
    assert spec["cash_multiplier"]=="0.01"
    assert spec["quantity_step"]=="1"
