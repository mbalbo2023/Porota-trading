from decimal import Decimal
import rc6_iol_family_reference as m

def test_fixed_contract_requires_explicit_lot_and_per100_proof():
    asset={"type":"TIT. PUBLICOS","currency":"ARS","market":"BCBA","term":"T1","units_per_lot":100}
    analytics={"calculation_inputs":{"maturity_date":"2030-07-09T00:00:00"}}
    simulation={"nominals":1,"dirty_price_per100":56.4,"amount_invested_ars":874.1}
    shadow={"quote":{"last":87530}}
    c=m._fixed_contract("GD30",asset,analytics,simulation,shadow)
    assert c is not None
    assert c["family"]=="BONOS"
    assert Decimal(c["cash_multiplier"]) > Decimal("0.0095")
    assert c["quantity_step"]=="100"
    assert c["fixed_income_evidence"]["quote_basis_nominal"]=="100"

def test_fixed_contract_fails_closed_without_lot():
    asset={"type":"TIT. PUBLICOS","currency":"ARS","market":"BCBA","term":"T1"}
    assert m._fixed_contract("GD30",asset,{},{"nominals":1,"dirty_price_per100":56.4,"amount_invested_ars":874.1},{"quote":{"last":87530}}) is None

def test_option_contract_requires_explicit_info_and_chain_fields():
    chain={"underlying":"GGAL","options":[{"symbol":"GFGC7000OC","option_type":"C","strike_price":7000,
      "expiration":"2026-10-16T15:30:00-03:00","bid_price":87,"ask_price":88,"volume":10,"is_stale":False}]}
    infos={"GFGC7000OC":{"market":"BCBA","currency":"ARS","term":"T0","units_per_lot":1,"description":"Call"}}
    rows=m._option_records(chain,infos,"2026-09-25T18:00:00+00:00")
    c=rows[0]["financial_contract_v17"]
    assert c["family"]=="OPCIONES" and c["underlying"]=="GGAL"
    assert c["strike"]=="7000.0" and c["option_right"]=="CALL"
    assert c["market"]=="BYMA" and c["settlement"]=="INMEDIATA"
