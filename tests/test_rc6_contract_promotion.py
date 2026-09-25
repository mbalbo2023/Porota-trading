import rc6_contract_promotion as p

def record(kind="BONOS"):
    return {"ticker":"AL30","instrument_type":kind,"market":"BYMA","currency":"ARS",
            "settlement":"UNKNOWN","capability":"NEEDS_SETTLEMENT",
            "last_seen_at":"2026-09-25T12:00:00+00:00","raw":{"nominalInPrice":100}}

def test_fixed_income_promotes_only_with_completed_multisource_contract():
    sources=[{"source_class":"IOL_MCP","source_ref":"IOL","observed_at":"2026-09-25T12:00:00+00:00",
              "evidence":{"market":"BYMA","currency":"ARS","settlement":"A-24HS",
                          "price_quote_unit":100,"quantity_step":1,"maturity_date":"2030-07-09"}}]
    plan=p.promotion_plan(record(),sources)
    assert plan["promote_spot"] is True
    assert plan["instrument_contract"]["cash_multiplier"]=="0.01"
    assert plan["real_money_authorized"] is False

def test_ppi_raw_nominal_is_not_silently_used_as_quote_basis():
    plan=p.promotion_plan(record(),[])
    assert plan["promote_spot"] is False
    assert "price_quote_unit" in plan["readiness"]["missing"]

def test_specialized_option_does_not_promote_through_spot_executor():
    r=record("OPCIONES")
    sources=[{"source_class":"IOL_MCP","evidence":{"market":"BYMA","currency":"ARS","settlement":"INMEDIATA",
        "underlying":"GGAL","put_call":"CALL","strike":4200,"expiry_at":"2026-10-16T15:30:00-03:00",
        "quantity_step":1,"contract_multiplier":100}}]
    plan=p.promotion_plan(r,sources)
    assert plan["readiness"]["contract_complete"] is True
    assert plan["readiness"]["paper_simulatable"] is False
    assert plan["promote_spot"] is False
