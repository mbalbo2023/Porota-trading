from decimal import Decimal
import rc6_iol_family_reference as m


def test_fixed_contract_requires_explicit_unit_lot_and_one_nominal_proof():
    asset={"type":"TIT. PUBLICOS","currency":"ARS","market":"BCBA","term":"T1","units_per_lot":100}
    analytics={"calculation_inputs":{"maturity_date":"2030-07-09T00:00:00"}}
    simulation={"nominals":1,"dirty_price_per100":56.4,"amount_invested":0.584,
                "amount_invested_ars":874.1}
    quote={"unit_price":874.1,"trade":{"lot_price":{"currency":"ARS","value":87410}}}
    contract=m._fixed_contract(
        "GD30",asset,analytics,simulation,quote,
        identity_family="BONOS",identity_currency="ARS",identity_settlement="A-24HS")
    assert contract is not None
    assert contract["family"]=="BONOS"
    assert Decimal(contract["cash_multiplier"]) == Decimal("0.01")
    assert contract["quantity_step"]=="100"
    assert contract["fixed_income_evidence"]["quote_basis_nominal"]=="100"
    assert contract["fixed_income_evidence"]["iol_unit_price"]==874.1
    assert contract["fixed_income_evidence"]["iol_lot_price"]==87410


def test_fixed_contract_proves_usd_variant_without_using_ars_fx_ratio():
    asset={"type":"TIT. PUBLICOS","currency":"USD","market":"BCBA","term":"T1","units_per_lot":100}
    analytics={"calculation_inputs":{"maturity_date":"2030-07-09T00:00:00"}}
    simulation={"nominals":1,"dirty_price_per100":54.45,"amount_invested":0.5445,
                "amount_invested_ars":813.9}
    quote={"unit_price":0.5419,"trade":{"lot_price":{"currency":"USD","value":54.19}}}
    contract=m._fixed_contract(
        "AL30D",asset,analytics,simulation,quote,
        identity_family="BONOS",identity_currency="USD_MEP",identity_settlement="A-24HS")
    assert contract is not None
    assert contract["currency"]=="USD_MEP"
    assert Decimal(contract["cash_multiplier"]) == Decimal("0.01")


def test_fixed_contract_fails_closed_without_explicit_lot_price():
    asset={"type":"TIT. PUBLICOS","currency":"ARS","market":"BCBA","term":"T1","units_per_lot":100}
    simulation={"nominals":1,"dirty_price_per100":56.4,"amount_invested_ars":874.1}
    assert m._fixed_contract(
        "GD30",asset,{},simulation,{"unit_price":874.1},
        identity_family="BONOS",identity_currency="ARS",
        identity_settlement="A-24HS") is None


def test_option_contract_uses_byma_underlying_lot_not_iol_order_step():
    chain={"underlying":"GGAL","options":[{"symbol":"GFGC7000OC","option_type":"C","strike_price":7000,
      "expiration":"2026-10-16T15:30:00-03:00","bid_price":87,"ask_price":88,"volume":10,"is_stale":False}]}
    infos={"GFGC7000OC":{"market":"BCBA","currency":"ARS","term":"T0","units_per_lot":1,"description":"Call"}}
    rows=m._option_records(
        chain,infos,"2026-09-25T18:00:00+00:00",
        underlying_info={"type":"ACCIONES","currency":"ARS"})
    contract=rows[0]["financial_contract_v17"]
    assert contract["family"]=="OPCIONES" and contract["underlying"]=="GGAL"
    assert contract["strike"]=="7000.0" and contract["option_right"]=="CALL"
    assert contract["market"]=="BYMA" and contract["settlement"]=="INMEDIATA"
    assert contract["cash_multiplier"]=="100"
    assert contract["quantity_step"]=="1"


def test_option_lot_policy_distinguishes_cedear_and_public_bond():
    raw={"symbol":"OPT1","option_type":"C","strike_price":10,
         "expiration":"2026-10-16T15:30:00-03:00","bid_price":1,"ask_price":2,
         "volume":1,"is_stale":False}
    info={"OPT1":{"market":"BCBA","currency":"ARS","units_per_lot":1}}
    cedear=m._option_records(
        {"underlying":"AAPL","options":[raw]},info,"2026-09-25T18:00:00+00:00",
        underlying_info={"type":"CEDEARS","currency":"ARS"})[0]["financial_contract_v17"]
    bond=m._option_records(
        {"underlying":"AL30","options":[raw]},info,"2026-09-25T18:00:00+00:00",
        underlying_info={"type":"TIT. PUBLICOS","currency":"ARS"})[0]["financial_contract_v17"]
    assert cedear["cash_multiplier"]=="10"
    assert bond["cash_multiplier"]=="1000"


def test_option_contract_stays_incomplete_when_underlying_family_is_unknown():
    chain={"underlying":"X","options":[{"symbol":"OPT1","option_type":"C","strike_price":10,
      "expiration":"2026-10-16T15:30:00-03:00","volume":1,"is_stale":False}]}
    info={"OPT1":{"market":"BCBA","currency":"ARS","units_per_lot":1}}
    row=m._option_records(chain,info,"2026-09-25T18:00:00+00:00",
                          underlying_info={"type":"UNKNOWN"})[0]
    assert row["financial_contract_v17"] is None
