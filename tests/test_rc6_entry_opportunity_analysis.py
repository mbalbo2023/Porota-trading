import json
from pathlib import Path
from rc6_entry_opportunity_analysis import build

def test_opportunity_labels_use_cost_aware_changed_rows(tmp_path):
    f={"schema":"F","rows":[
      {"paper_id":"a","day":"2026-09-01","currency":"ARS","symbol":"A","net_pnl":"-10","candle":{"momentum_3v15":"-0.01","ema9":"99","ema21":"100","rsi14":"45","rsi14_prev":"44","avg_range":"0.01"}},
      {"paper_id":"b","day":"2026-09-02","currency":"ARS","symbol":"B","net_pnl":"-20","candle":{"momentum_3v15":"0.01","ema9":"101","ema21":"100","rsi14":"65","rsi14_prev":"60","avg_range":"0.02"}}]}
    c={"schema":"C","rows":[
      {"paper_id":"a","book":{"book_imbalance":"-0.2"},"breadth":{"breadth_score":"-0.1"},"asset_day_return":"-0.01"},
      {"paper_id":"b","book":{"book_imbalance":"0.2"},"breadth":{"breadth_score":"0.1"},"asset_day_return":"0.01"}]}
    e={"schema":"E","net_targets":{k:{"changed":[{"paper_id":"a"}] if k=="0.0025" else []} for k in ("0.0025","0.005","0.0075","0.01")}}
    paths=[]
    for name,obj in (("f",f),("c",c),("e",e)):
        p=tmp_path/(name+".json");p.write_text(json.dumps(obj));paths.append(p)
    r=build(*paths)
    assert r["targets"]["0.0025"]["all"]["opportunities"]==1
    p=next(x for x in r["targets"]["0.0025"]["profiles"] if x["name"]=="ASSET_AND_MOM_NONPOS")
    assert p["kept"]["n"]==1 and p["kept"]["opportunities"]==1
