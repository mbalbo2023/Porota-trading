import json
from rc6_trade_cohort_analysis import build

def trade(pid,net,gross,cost,mfe,mae,score=.64,reason="EOD_PAPER"):
    return {"paper_id":pid,"currency":"ARS","net_pnl":str(net),"gross_pnl":str(gross),
      "entry_cost":str(cost/2),"exit_cost":str(cost/2),"entry_price":"100","quantity":"10",
      "contract_cash_multiplier":"1","decision_score":str(score),"momentum":"0.004","spread":"0.001",
      "close_reason":reason,"opened_at":"2026-09-23T14:00:00+00:00","duration_minutes":60,
      "mfe_mae":{"status":"MEDIDO","mfe_exec_return":str(mfe),"mae_exec_return":str(mae)},
      "evidence_hash_valid":True,"economics":{"passed":True},
      "post_exit_recovery":{"status":"MEDIDO","max_return_30m":"0.0","max_return_60m":"0.005","max_return_120m":"0.015"}}

def test_analysis_separates_gross_cost_and_net():
    master={"trades":[trade("a",100,200,100,.03,-.005),trade("b",-150,50,200,.01,-.02)]}
    r=build(master)
    a=r["by_currency"]["ARS"]
    assert a["n"]==2 and a["wins"]==1 and a["losses"]==1
    assert a["gross_pnl"]==250
    assert a["costs"]==300
    assert a["net_pnl"]==-50
    assert r["ars"]["gross_positive_but_net_negative"]==1
    assert r["ars"]["target_5pct_reached_by_mfe"]==0
    # Synthetic rows are EOD, so stop-recovery cohort remains empty.
    assert r["ars"]["stop_recovery"]["stop_trades"]==0
