import json
import sqlite3
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


def test_collect_builds_fixed_option_fci_and_caucion_evidence_together(tmp_path):
    db=tmp_path/"catalog.db"
    con=sqlite3.connect(db)
    con.execute("""CREATE TABLE financial_instrument_catalog(
        ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT)""")
    con.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?)",[
        ("GD30","BONOS","BYMA","ARS","A-24HS","AVAILABLE"),
        ("GGAL","ACCIONES","BYMA","ARS","A-24HS","AVAILABLE"),
    ])
    con.commit(); con.close()
    (tmp_path/"iol_family_reference_latest.json").write_text(json.dumps({
        "rotation":{"fixed_index":0,"option_underlying_index":1},
        "records":[],"fci":[],"cauciones":{}
    }),encoding="utf-8")
    (tmp_path/"iol_shadow_latest.json").write_text(json.dumps({
        "symbols":[{
            "symbol":"GFGC7000OC","market":"BCBA","term":"t1","state":"READY",
            "asset_type":"OPCIONES","currency":"ARS","units_per_lot":1,
            "quote":{"last":88,"provider_observed_at":"2026-09-25T16:59:00-03:00"},
        }]
    }),encoding="utf-8")

    class Fake:
        def call(self,name,args):
            if name=="get_asset_info" and args["symbol"]=="GD30":
                return {"symbol":"GD30","type":"TIT. PUBLICOS","currency":"ARS",
                        "market":"BCBA","term":"T1","units_per_lot":100}
            if name=="get_fixed_income_analytics":
                return {"calculation_inputs":{"maturity_date":"2030-07-09T00:00:00"},
                        "prices":{"dirty_price":56.4}}
            if name=="simulate_fixed_income_by_nominals":
                return {"nominals":1,"dirty_price_per100":56.4,
                        "amount_invested":0.584,"amount_invested_ars":874.1,
                        "maturity_date":"2030-07-09T00:00:00"}
            if name=="get_asset_quote":
                return {"unit_price":874.1,"trade":{"lot_price":{"value":87410}}}
            if name=="get_options_chain":
                return {"underlying":"GGAL","options":[{
                    "symbol":"GFGC7000OC","option_type":"C","strike_price":7000,
                    "expiration":"2026-10-16T15:30:00-03:00","bid_price":87,
                    "ask_price":88,"volume":10,"is_stale":False}]}
            if name=="get_asset_info" and args["symbol"]=="GGAL":
                return {"symbol":"GGAL","type":"ACCIONES","currency":"ARS",
                        "market":"BCBA","term":"T1","units_per_lot":1}
            if name=="get_fci_funds":
                return {"result":[{"asset":"FUND1","currency":"ARS","operable":True}]}
            if name=="get_caucion_rates":
                return {"result":[{"days":3,"rate":18.0,"min_amount":100000}]}
            raise AssertionError((name,args))

    result=m.collect(Fake(),root=tmp_path,db_path=str(db))
    keyed={(r["instrument_type"],r["ticker"]):r for r in result["records"]}
    assert keyed[("BONOS","GD30")]["financial_contract_v17"]["cash_multiplier"]=="0.01"
    assert keyed[("OPCIONES","GFGC7000OC")]["financial_contract_v17"]["cash_multiplier"]=="100"
    assert result["fci"][0]["asset"]=="FUND1"
    assert result["cauciones"]["ARS"][0]["days"]==3
    assert result["cauciones"]["USD"][0]["days"]==3
    assert result["errors"]==[]
