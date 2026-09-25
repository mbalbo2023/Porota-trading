import rc6_iol_contract_evidence as n

def test_fixed_income_uses_iol_per100_and_cross_source_integer_proof():
    out=n.fixed_income_evidence(
      {"symbol":"AL30","market":"BCBA","currency":"ARS","term":"T1","units_per_lot":100},
      {"calculation_inputs":{"maturity_date":"2030-07-09T00:00:00"}},
      {"nominals":1,"clean_price_per100":54.18,"maturity_date":"2030-07-09T00:00:00",
       "payment_currency":"USD","cash_flows":[{"date":"2027-01-01"}]},
      integer_quantity_proven=True)
    assert out["market"]=="BYMA"
    assert out["settlement"]=="A-24HS"
    assert out["price_quote_unit"]==100
    assert out["quantity_step"]==1

def test_option_does_not_infer_economic_multiplier_from_lot():
    info={"symbol":"GFGC4200OC","description":"Call GGAL $4.200,00 Vencimiento: 16/10/2026",
          "market":"BCBA","currency":"ARS","term":"T0","units_per_lot":1}
    underlying=n.option_underlying_hint(info)
    out=n.option_evidence(info,{"symbol":"GFGC4200OC","option_type":"C","strike_price":4200,
      "expiration":"2026-10-16T15:30:00"},underlying=underlying)
    assert out["underlying"]=="GGAL"
    assert out["put_call"]=="CALL"
    assert out["quantity_step"]=="1"
    assert "contract_multiplier" not in out

def test_fci_quote_is_not_silently_called_nav():
    out=n.fci_evidence({"market":"BCBA","currency":"ARS","term":"T0","units_per_lot":1},
                       {"operable":True,"currency":"ARS","market":"BCBA"},
                       {"unit_price":195.48,"trade":{"timestamp":"2026-09-25T01:00:10-03:00"}})
    assert out["unit_price"]==195.48
    assert "nav" not in out

def test_caucion_null_rate_stays_missing():
    out=n.caucion_evidence({"days":1,"due_date":"2026-09-26T00:00:00","rate":None,"min_amount":100000})
    assert out["term_days"]==1 and out["minimum_principal"]==100000
    assert "annual_rate" not in out
