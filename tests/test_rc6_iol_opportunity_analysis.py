from rc6_iol_opportunity_analysis import build

def test_iol_analysis_uses_only_persisted_good_fresh_rows():
    p={"rows":[
      {"currency":"ARS","day":"2026-09-23","opp_0.0025":1,"opp_0.005":0,
       "ppi_last":"100","ppi_bid":"99","ppi_ask":"101","iol_state":"READY","iol_quality":"GOOD","iol_freshness":"FRESH",
       "iol_currency":"ARS","iol_last":"101","iol_bid":"100","iol_ask":"101","iol_spread_pct":"0.1","iol_variation_pct":"2","iol_cash_volume":"1000","iol_age_seconds":"30"},
      {"currency":"ARS","day":"2026-09-23","opp_0.0025":0,"opp_0.005":0,
       "ppi_last":"100","ppi_bid":"99","ppi_ask":"101","iol_state":"UNAVAILABLE","iol_quality":"UNKNOWN","iol_freshness":"UNKNOWN"}
    ]}
    r=build(p)
    assert r["coverage"]["ars_rows"]==2
    assert r["coverage"]["good_fresh_ready"]==1
    assert r["targets"]["0.0025"]["good_fresh_ready"]["positive"]==1
    assert r["decision_effect"]=="NO_FACTUAL_BINDING"
