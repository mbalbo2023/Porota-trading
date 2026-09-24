from rc6_alpha_profile_comparison import build

def test_profile_gate_does_not_promote_negative_validation():
    master={"trades":[
      {"paper_id":"a","currency":"ARS","asset_class":"ACCIONES","opened_at":"2026-09-01T15:00:00+00:00","net_pnl":"10","decision_score":"0.64"},
      {"paper_id":"b","currency":"ARS","asset_class":"ACCIONES","opened_at":"2026-09-20T15:00:00+00:00","net_pnl":"-20","decision_score":"0.64"},
    ]}
    pt={"rows":[
      {"paper_id":"a","candle":{"momentum_3v15":"-0.01","ema9":"99","ema21":"100","rsi14":"40"}},
      {"paper_id":"b","candle":{"momentum_3v15":"-0.01","ema9":"99","ema21":"100","rsi14":"40"}},
    ]}
    ctx={"rows":[
      {"paper_id":"a","book":{"book_imbalance":"-0.1"},"breadth":{"breadth_score":"-0.2"},"asset_day_return":"-0.01"},
      {"paper_id":"b","book":{"book_imbalance":"-0.1"},"breadth":{"breadth_score":"-0.2"},"asset_day_return":"-0.01"},
    ]}
    nt={k:{"changed":[],"unmodeled_rows":[]} for k in ("0.0025","0.005","0.0075","0.01")}
    r=build(master,pt,ctx,{"net_targets":nt})
    assert r["conclusion"]=="NO_PROFILE_PASSES_PREDEFINED_ROBUSTNESS_GATE"
    assert r["candidate_gate_passed"]==[]
