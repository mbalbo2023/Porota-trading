import rc6_dynamic_discovery as d

def test_generated_plan_has_all_families_and_no_manual_tickers():
    d.assert_no_manual_ticker_universe()
    plan=d.full_plan({"instrument_types":["ACCIONES","CEDEARS","ETF","BONOS","LETRAS","ON","OPCIONES","FUTUROS","CAUCIONES","FCI"],
                      "markets":["BYMA","ROFEX","A3","NYSE","NASDAQ","OTC"]})
    families={x["canonical_family"] for x in plan}
    assert families==set(d.FAMILY_SPECS)
    generated=[x for x in plan if x["mode"]=="GENERATED_PREFIX"]
    assert generated and all(len(x["ticker_query"])==1 for x in generated)
    assert not any(x["ticker_query"] in {"GGAL","AAPL","AL30"} for x in generated)

def test_provider_aliases_are_canonicalized():
    assert d.canonical_family("ON")=="OBLIGACIONES"
    assert d.canonical_family("ETF")=="ETFS"

def test_bounded_plan_resumes():
    config={"instrument_types":["ACCIONES"],"markets":["BYMA"]}
    a,c1,_=d.batch(config,cursor=0,budget=5)
    b,c2,_=d.batch(config,cursor=c1,budget=5)
    assert len(a)==len(b)==5
    assert a!=b and c2==10
