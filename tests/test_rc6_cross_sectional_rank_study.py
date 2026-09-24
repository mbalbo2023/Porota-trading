from rc6_cross_sectional_rank_study import build

def row(k,day,minute,trend,label):
    return {"decision_key":k,"decided_at":minute+":00+00:00","day":day,"currency":"ARS",
      "features":{"history_trend50":str(trend),"spread":"0.001","book_imbalance":"0","score":"0.5"},
      "labels":{"hit_net_0.0025_before_stop":label,"hit_net_0.005_before_stop":label}}

def test_low_trend_ranks_first_without_using_label():
    rows=[]
    for day in ("2026-09-21","2026-09-22","2026-09-23"):
        for i in range(120):
            m=f"2026-09-{day[-2:]}T14:{i%60:02d}"
            rows.append(row(f"{day}-a-{i}",day,m,-0.2,1))
            rows.append(row(f"{day}-b-{i}",day,m,0.2,0))
    r=build({"rows":rows})
    x=r["profiles"]["0.0025"]["TREND50_LOW_TOP1"]
    assert x["train"]["rate_pct"]=="100"
    assert x["validation"]["rate_pct"]=="100"
    assert x["gate"]["min_validation_n"] is False
    assert x["gate"]["passes_rank_gate"] is False
