from rc6_alpha_tuning_analysis import build
def test_findings_detect_score_and_target_signals():
    def t(pid,out,score,mfe,mae,gross,net,ret):
        return {"paper_id":pid,"outcome":out,"currency":"ARS","net_pnl":str(net),"gross_pnl":str(gross),
        "net_return_pct":str(ret),"entry_cost":"1","exit_cost":"1","decision_score":str(score),
        "mfe_mae":{"mfe_exec_return":str(mfe/100),"mae_exec_return":str(mae/100)},
        "close_reason":"EOD_PAPER","strategy_version":"v","opened_at":"2026-09-23T14:00:00+00:00",
        "decision_shadow":None,"iol":None}
    master={"schema":"X","integrity":{"immutable_evidence_verified":0,"mfe_mae_measured":2},
            "trades":[t("1","WIN",".63",2.1,-.4,10,8,1),t("2","LOSS",".80",.2,-2.1,1,-1,-1)]}
    r=build(master)
    assert r["totals"]["n"]==2
    assert r["signal"]["score_ge_070"]["wins"]==0
    assert r["excursions"]["mfe_ge_5pct"]==0
    assert r["friction"]["gross_positive_net_negative"]==1
