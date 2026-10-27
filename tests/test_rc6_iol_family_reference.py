import json
import sqlite3
from decimal import Decimal
import rc6_iol_family_reference as m


def test_fixed_contract_requires_explicit_unit_lot_and_one_nominal_proof():
    asset={"type":"TIT. PUBLICOS","currency":"ARS","market":"BCBA","term":"T1","units_per_lot":100,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:independent-quantity-rule"}}
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
    assert contract["quantity_step"]=="1"
    assert contract["fixed_income_evidence"]["quote_basis_nominal"]=="100"
    assert contract["fixed_income_evidence"]["iol_unit_price"]==874.1
    assert contract["fixed_income_evidence"]["iol_lot_price"]==87410


def test_fixed_contract_proves_usd_variant_without_using_ars_fx_ratio():
    asset={"type":"TIT. PUBLICOS","currency":"USD","market":"BCBA","term":"T1","units_per_lot":100,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:independent-quantity-rule"}}
    analytics={"calculation_inputs":{"maturity_date":"2030-07-09T00:00:00"}}
    simulation={"nominals":1,"dirty_price_per100":54.45,"amount_invested":0.5445,
                "amount_invested_ars":813.9}
    quote={"unit_price":0.5419,"trade":{"lot_price":{"currency":"USD","value":54.19}}}
    contract=m._fixed_contract(
        "AL30D",asset,analytics,simulation,quote,
        identity_family="BONOS",identity_currency="USD",identity_settlement="A-24HS")
    assert contract is not None
    assert contract["currency"]=="USD"
    assert Decimal(contract["cash_multiplier"]) == Decimal("0.01")


def test_fixed_contract_fails_closed_without_explicit_lot_price():
    asset={"type":"TIT. PUBLICOS","currency":"ARS","market":"BCBA","term":"T1","units_per_lot":100,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:independent-quantity-rule"}}
    simulation={"nominals":1,"dirty_price_per100":56.4,"amount_invested_ars":874.1}
    assert m._fixed_contract(
        "GD30",asset,{},simulation,{"unit_price":874.1},
        identity_family="BONOS",identity_currency="ARS",
        identity_settlement="A-24HS") is None


def _flow_pair():
    analytics={"calculation_date":"2026-10-25T23:00:00-03:00",
               "calculation_inputs":{"maturity_date":"2030-07-09T00:00:00"},
               "cashflow":[{"period_number":1,"date":"2030-07-09T00:00:00",
                            "opening_balance":64,"interest":0.24,"amortization":64,
                            "cash_flow":64.24,"residual_balance":0}],
               "next_coupon_date":"2021-07-09T00:00:00"}
    simulation={"calculation_date":"2026-10-25T23:00:01-03:00",
                "settlement_date":"2026-10-26T00:00:00-03:00",
                "maturity_date":"2030-07-09T00:00:00","nominals":1,
                "payment_currency":"USD","total_to_collect":0.6424,
                "cash_flows":[{"period_number":1,"date":"2030-07-09T00:00:00",
                               "opening_balance":0.64,"interest":0.0024,
                               "amortization":0.64,"cash_flow":0.6424,
                               "residual_balance":0}]}
    return analytics,simulation


def test_iol_flow_units_reconcile_without_confusing_ars_price_with_usd_payment():
    analytics,simulation=_flow_pair()
    result=m._fixed_flow_reference(analytics,simulation,100,
                                   received_at="2026-10-26T02:00:03+00:00")
    assert result["status"]=="RECONCILED_PROVIDER_CALCULATION"
    assert result["flows"]==[{"payment_date":"2030-07-09","amount":"0.6424","currency":"USD"}]
    assert result["nominal_unit"]=="CASH_PER_NOMINAL"
    assert result["contractual_authority"]=="ISSUER_TERMS_NOT_VERIFIED"
    assert result["market_quote_freshness"]=="NO_VERIFICADO"
    assert result["entry_authority"] is False
    assert result["source_calculated_at"]==analytics["calculation_date"]
    # The stale next-coupon field is neither trusted nor serialized as a flow.
    assert "2021" not in json.dumps(result)


def test_iol_flow_reference_rejects_unit_currency_clock_and_horizon_conflicts():
    analytics,simulation=_flow_pair()
    def reference(a=analytics,s=simulation,lot=100,received="2026-10-26T02:00:03+00:00"):
        return m._fixed_flow_reference(a,s,lot,received_at=received)
    changed=json.loads(json.dumps(simulation));changed["cash_flows"][0]["cash_flow"]=0.6425
    assert reference(s=changed)["status"]=="NO_VERIFICADO"
    assert reference(lot=1)["reason"]=="FLOW_UNIT_MISMATCH"
    changed=json.loads(json.dumps(simulation));changed.pop("payment_currency")
    assert reference(s=changed)["reason"]=="PAYMENT_CURRENCY_UNVERIFIED"
    assert reference(received="2026-10-26T01:59:59+00:00")["status"]=="NO_VERIFICADO"
    changed=json.loads(json.dumps(simulation));changed["cash_flows"][0]["date"]="2030-07-10"
    assert reference(s=changed)["reason"]=="FLOW_DATE_OR_PERIOD_MISMATCH"
    changed=json.loads(json.dumps(simulation));changed["cash_flows"][0]["interest"]="NaN"
    assert reference(s=changed)["status"]=="NO_VERIFICADO"
    changed=json.loads(json.dumps(analytics));changed["calculation_date"]="2026-10-24T22:59:00-03:00"
    assert reference(a=changed)["reason"]=="CALCULATION_CLOCK_UNVERIFIED"


def test_iol_flow_reference_checks_internal_balance_and_period_even_when_provider_views_agree():
    analytics,simulation=_flow_pair()
    analytics["cashflow"][0]["opening_balance"]=65
    simulation["cash_flows"][0]["opening_balance"]=0.65
    assert m._fixed_flow_reference(analytics,simulation,100,
        received_at="2026-10-26T02:00:03+00:00")["reason"]=="FLOW_BALANCE_CONSERVATION_INVALID"

    analytics,simulation=_flow_pair()
    analytics["cashflow"][0].update(date="2027-07-09T00:00:00",
        amortization=32,cash_flow=32.24,residual_balance=32)
    analytics["cashflow"].append({"period_number":2,"date":"2030-07-09T00:00:00",
        "opening_balance":32,"interest":0.12,"amortization":32,
        "cash_flow":32.12,"residual_balance":0})
    simulation["cash_flows"][0].update(date="2027-07-09T00:00:00",
        amortization=0.32,cash_flow=0.3224,residual_balance=0.32)
    simulation["cash_flows"].append({"period_number":2,"date":"2030-07-09T00:00:00",
        "opening_balance":0.32,"interest":0.0012,"amortization":0.32,
        "cash_flow":0.3212,"residual_balance":0})
    simulation["total_to_collect"]=0.6436

    def result():
        return m._fixed_flow_reference(analytics,simulation,100,
            received_at="2026-10-26T02:00:03+00:00")

    assert result()["status"]=="RECONCILED_PROVIDER_CALCULATION"
    analytics["cashflow"][1].update(opening_balance=33,amortization=33)
    simulation["cash_flows"][1].update(opening_balance=0.33,amortization=0.33)
    assert result()["reason"]=="FLOW_BALANCE_CONSERVATION_INVALID"
    analytics["cashflow"][1].update(opening_balance=32,amortization=32,period_number=4)
    simulation["cash_flows"][1].update(opening_balance=0.32,amortization=0.32,period_number=4)
    assert result()["reason"]=="FLOW_PERIOD_SEQUENCE_INVALID"


def test_option_observation_keeps_byma_lot_without_certifying_series_adjustment():
    chain={"underlying":"GGAL","options":[{"symbol":"GFGC7000OC","option_type":"C","strike_price":7000,
      "expiration":"2026-10-16T15:30:00-03:00","bid_price":87,"ask_price":88,"volume":10,"is_stale":False}]}
    infos={"GFGC7000OC":{"symbol":"GFGC7000OC","type":"OPCIONES","market":"BCBA","currency":"ARS","term":"T1","units_per_lot":1,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:quantity-rule"},"premium_basis":"PER_UNDERLYING_UNIT","description":"Call"}}
    rows=m._option_records(
        chain,infos,"2026-09-25T18:00:00+00:00",
        underlying_info={"symbol":"GGAL","type":"ACCIONES","currency":"USD"})
    row=rows[0]
    assert row["financial_contract_v17"] is None
    assert (row["market"],row["currency"],row["settlement"]) == ("BYMA","ARS","INMEDIATA")
    assert row["option_chain_evidence"]["standard_lot_reference"]==100
    assert "contract_lot" not in row["option_chain_evidence"]
    assert row["option_chain_evidence"]["series_identity_state"]=="EXACT_IOL_ASSET_INFO"
    assert row["option_chain_evidence"]["series_adjustment_state"]=="NO_VERIFICADO"


def test_option_lot_policy_pauses_non_equity_standard_series():
    raw={"symbol":"OPT1","option_type":"C","strike_price":10,
         "expiration":"2026-10-16T15:30:00-03:00","bid_price":1,"ask_price":2,
         "volume":1,"is_stale":False}
    info={"OPT1":{"market":"BCBA","currency":"ARS","units_per_lot":1,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:quantity-rule"},"premium_basis":"PER_UNDERLYING_UNIT"}}
    cedear=m._option_records(
        {"underlying":"AAPL","options":[raw]},info,"2026-09-25T18:00:00+00:00",
        underlying_info={"type":"CEDEARS","currency":"ARS"})[0]["financial_contract_v17"]
    bond=m._option_records(
        {"underlying":"AL30","options":[raw]},info,"2026-09-25T18:00:00+00:00",
        underlying_info={"type":"TIT. PUBLICOS","currency":"ARS"})[0]["financial_contract_v17"]
    assert cedear is None
    assert bond is None


def test_option_contract_stays_incomplete_when_underlying_family_is_unknown():
    chain={"underlying":"X","options":[{"symbol":"OPT1","option_type":"C","strike_price":10,
      "expiration":"2026-10-16T15:30:00-03:00","volume":1,"is_stale":False}]}
    info={"OPT1":{"market":"BCBA","currency":"ARS","units_per_lot":1,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:quantity-rule"},"premium_basis":"PER_UNDERLYING_UNIT"}}
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
            "asset_type":"OPCIONES","currency":"ARS","units_per_lot":1,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:quantity-rule"},"premium_basis":"PER_UNDERLYING_UNIT",
            "quote":{"last":88,"provider_observed_at":"2026-09-25T16:59:00-03:00"},
        }]
    }),encoding="utf-8")

    class Fake:
        def call(self,name,args):
            if name=="get_asset_info" and args["symbol"]=="GD30":
                return {"symbol":"GD30","type":"TIT. PUBLICOS","currency":"ARS",
                        "market":"BCBA","term":"T1","units_per_lot":100,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:independent-quantity-rule"}}
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
                        "market":"BCBA","term":"T1","units_per_lot":1,"order_terms":{"quantity_step":"1","minimum_quantity":"1","source_ref":"fixture:quantity-rule"},"premium_basis":"PER_UNDERLYING_UNIT"}
            if name=="get_asset_info" and args["symbol"]=="GFGC7000OC":
                return {"symbol":"GFGC7000OC","type":"OPCIONES","currency":"ARS",
                        "market":"BCBA","term":"T1","units_per_lot":1}
            if name=="get_fci_funds":
                return {"result":[{"asset":"FUND1","currency":"ARS","operable":True}]}
            if name=="get_caucion_rates":
                return {"result":[{"days":3,"rate":18.0,"min_amount":100000}]}
            raise AssertionError((name,args))

    result=m.collect(Fake(),root=tmp_path,db_path=str(db))
    keyed={(r["instrument_type"],r["ticker"]):r for r in result["records"]}
    assert keyed[("BONOS","GD30")]["financial_contract_v17"]["cash_multiplier"]=="0.01"
    option=keyed[("OPCIONES","GFGC7000OC")]
    assert option["financial_contract_v17"] is None
    assert option["option_chain_evidence"]["standard_lot_reference"]==100
    assert option["currency"]=="ARS"
    assert result["fci"][0]["asset"]=="FUND1"
    assert result["cauciones"]["ARS"][0]["days"]==3
    assert result["cauciones"]["USD"][0]["days"]==3
    assert result["errors"]==[]


def test_option_expiry_without_offset_is_bound_to_argentina_time():
    assert m._option_expiry("2026-10-16T15:30:00") == "2026-10-16T15:30:00-03:00"
    assert m._option_expiry("2026-10-16T18:30:00+00:00") == "2026-10-16T18:30:00+00:00"


def test_option_observation_expiry_is_aware_without_contract():
    chain={"underlying":"GGAL","options":[{
        "symbol":"GFGC7000OC","option_type":"C","strike_price":7000,
        "expiration":"2026-10-16T15:30:00","bid_price":87,"ask_price":88,
        "volume":10,"is_stale":False}]}
    infos={"GFGC7000OC":{"symbol":"GFGC7000OC","type":"OPCIONES","market":"BCBA","currency":"ARS","units_per_lot":1}}
    row=m._option_records(
        chain,infos,"2026-09-25T18:00:00+00:00",
        underlying_info={"symbol":"GGAL","type":"ACCIONES","currency":"ARS"})[0]
    assert row["option_chain_evidence"]["expires_at"] == "2026-10-16T15:30:00-03:00"
    assert row["option_chain_evidence"]["standard_lot_reference"] == 100
    assert row["financial_contract_v17"] is None


def test_option_identity_cannot_inherit_underlying_or_boolean_adjustment():
    chain={"underlying":"GGAL","options":[{
        "symbol":"GFGC7000OC","option_type":"C","strike_price":7000,
        "expiration":"2026-12-18T15:30:00","is_stale":False,
        "adjusted_series_unverified":False}]}
    underlying={"symbol":"GGAL","type":"ACCIONES","market":"BCBA","currency":"ARS"}
    row=m._option_records(chain,{},"2026-10-27T01:00:00Z",underlying_info=underlying)[0]
    assert row["market"] is None and row["currency"] is None
    assert row["financial_contract_v17"] is None
    mismatch={"GFGC7000OC":{"symbol":"OTHER","type":"OPCIONES","market":"BCBA",
                            "currency":"USD","adjusted_series_unverified":False}}
    row=m._option_records(chain,mismatch,"2026-10-27T01:00:00Z",underlying_info=underlying)[0]
    assert row["market"] is None and row["currency"] is None
    exact={"GFGC7000OC":dict(mismatch["GFGC7000OC"],symbol="GFGC7000OC")}
    row=m._option_records(chain,exact,"2026-10-27T01:00:00Z",underlying_info=underlying)[0]
    assert row["currency"]=="USD"  # The series differs from its ARS share.
    assert row["financial_contract_v17"] is None
    assert row["option_chain_evidence"]["series_adjustment_state"]=="NO_VERIFICADO"


def test_collect_bounds_series_info_calls_and_keeps_unlooked_series_unknown(tmp_path):
    db=tmp_path/"catalog.db"
    con=sqlite3.connect(db)
    con.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT)")
    con.execute("INSERT INTO financial_instrument_catalog VALUES('GGAL','ACCIONES','BYMA','ARS','A-24HS','AVAILABLE')")
    con.commit();con.close()
    options=[{"symbol":f"OPT{i}","option_type":"C","strike_price":100+i,
              "expiration":"2026-12-18T15:30:00","volume":10-i,"is_stale":False}
             for i in range(m.MAX_OPTION_SERIES_INFO+2)]
    class Fake:
        def __init__(self,fail=False): self.info=[];self.fail=fail
        def call(self,name,args):
            if name=="get_options_chain": return {"underlying":"GGAL","options":options}
            if name=="get_asset_info":
                self.info.append(args["symbol"])
                if args["symbol"]=="GGAL":
                    return {"symbol":"GGAL","type":"ACCIONES","market":"BCBA","currency":"ARS"}
                if self.fail: raise RuntimeError("series info unavailable")
                return {"symbol":args["symbol"],"type":"OPCIONES","market":"BCBA","currency":"ARS"}
            if name in {"get_fci_funds","get_caucion_rates"}: return {"result":[{"sample":True}]}
            raise AssertionError((name,args))
    client=Fake()
    result=m.collect(client,root=tmp_path,db_path=str(db))
    assert client.info==["GGAL"]+[f"OPT{i}" for i in range(m.MAX_OPTION_SERIES_INFO)]
    assert len(result["records"])==len(options)
    assert all(row["financial_contract_v17"] is None for row in result["records"])
    assert [row["currency"] for row in result["records"]]==["ARS"]*m.MAX_OPTION_SERIES_INFO+[None,None]
    failed=Fake(fail=True)
    fallback=m.collect(failed,root=tmp_path/"failed",db_path=str(db))
    assert failed.info==["GGAL","OPT0"]  # No retry storm for optional identity.
    assert fallback["errors"]==["OPTIONS:GGAL:SERIES_INFO_UNAVAILABLE:OPT0"]
    assert fallback["section_states"]["options:GGAL"]=="LIVE_FRESH"
    assert all(row["currency"] is None and row["financial_contract_v17"] is None
               for row in fallback["records"])


def test_collect_sanitizes_legacy_option_contract_when_chain_fails(tmp_path):
    db=tmp_path/"catalog.db"
    con=sqlite3.connect(db)
    con.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,status TEXT)")
    con.execute("INSERT INTO financial_instrument_catalog VALUES('GGAL','ACCIONES','BYMA','ARS','A-24HS','AVAILABLE')")
    con.commit();con.close()
    path=tmp_path/"iol_family_reference_latest.json"
    path.write_text(json.dumps({"section_observed_at":{"options:GGAL":"2026-10-26T17:00:00Z"},
        "records":[{"ticker":"GFGC7000OC","instrument_type":"OPCIONES","market":"BYMA",
                    "currency":"ARS","settlement":"INMEDIATA",
                    "quote":{"bid":10,"ask":12,"is_stale":False},
                    "financial_contract_v17":{"family":"OPCIONES","cash_multiplier":"100"},
                    "option_chain_evidence":{"underlying":"GGAL","contract_lot":100}}]}),encoding="utf-8")
    class Fake:
        def call(self,name,args):
            if name=="get_options_chain": raise RuntimeError("offline")
            if name in {"get_fci_funds","get_caucion_rates"}: return {"result":[{"sample":True}]}
            raise AssertionError((name,args))
    result=m.collect(Fake(),root=tmp_path,db_path=str(db))
    row=result["records"][0]
    assert row["financial_contract_v17"] is None
    assert row["market"] is None and row["currency"] is None
    assert row["quote"]["is_stale"] is True
    assert row["option_chain_evidence"]["series_adjustment_state"]=="NO_VERIFICADO"
    assert row["option_chain_evidence"]["legacy_contract_lot_unverified"]==100
    assert row["option_chain_evidence"]["legacy_financial_contract_unverified"]["cash_multiplier"]=="100"
