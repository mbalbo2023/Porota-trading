from rc6_decision_label_analysis import build

def test_buy_vs_hold_and_score_auc():
    p={"rows":[
      {"day":"2026-09-21","action":"BUY","stored_score":"0.70","momentum":"0.01","spread":"0.001","samples":8,"opp_0.0025":0,"opp_0.005":0,"opp_0.0075":0,"opp_0.01":0},
      {"day":"2026-09-22","action":"HOLD","stored_score":"0.55","momentum":"-0.01","spread":"0.002","samples":8,"opp_0.0025":1,"opp_0.005":1,"opp_0.0075":0,"opp_0.01":0},
      {"day":"2026-09-23","action":"HOLD","stored_score":"0.56","momentum":"-0.02","spread":"0.001","samples":8,"opp_0.0025":1,"opp_0.005":0,"opp_0.0075":0,"opp_0.01":0},
    ]}
    r=build(p);x=r["targets"]["0.0025"]
    assert x["by_action"]["BUY"]["rate_pct"]=="0"
    assert x["by_action"]["HOLD"]["rate_pct"]=="100"
    assert x["auc"]["score"]=="0"
