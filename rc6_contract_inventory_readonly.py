#!/usr/bin/env python3
import json,sqlite3
from collections import Counter,defaultdict
db="/opt/porota-trading/data/paper_v17/observer_v17.db"
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
out={}
tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "contract_evidence_v2_current" in tables and "contract_evidence_v2_snapshots" in tables:
    rows=[dict(r) for r in c.execute("""SELECT cur.family,cur.ticker,cur.market,cur.settlement,cur.source_class,
        cur.observed_at,s.source_ref,s.evidence_json
      FROM contract_evidence_v2_current cur
      JOIN contract_evidence_v2_snapshots s ON s.snapshot_id=cur.snapshot_id
      ORDER BY cur.family,cur.ticker,cur.market,cur.settlement,cur.source_class""")]
    fam=defaultdict(lambda:{"rows":0,"tickers":set(),"sources":Counter(),"fields":Counter(),"samples":[]})
    for r in rows:
        f=fam[str(r["family"])]
        f["rows"]+=1;f["tickers"].add(str(r["ticker"]));f["sources"][str(r["source_class"])]+=1
        try:e=json.loads(r["evidence_json"] or "{}")
        except Exception:e={}
        if isinstance(e,dict):
            for k,v in e.items():
                if v not in (None,"",[],{}):f["fields"][str(k)]+=1
        if len(f["samples"])<2:
            f["samples"].append({"ticker":r["ticker"],"market":r["market"],"settlement":r["settlement"],
                                 "source_class":r["source_class"],"observed_at":r["observed_at"],
                                 "fields":sorted(k for k,v in e.items() if v not in (None,"",[],{})) if isinstance(e,dict) else []})
    out["v2"]={k:{"rows":v["rows"],"tickers":len(v["tickers"]),"sources":dict(v["sources"]),
                   "fields":dict(v["fields"]),"samples":v["samples"]} for k,v in fam.items()}
else: out["v2"]="TABLES_MISSING"
if "contract_evidence" in tables:
    rows=[dict(r) for r in c.execute("""SELECT instrument_type,status,owner,source,
       missing_fields_json,evidence_json,checked_at FROM contract_evidence ORDER BY instrument_type,ticker""")]
    fam=defaultdict(lambda:{"rows":0,"statuses":Counter(),"owners":Counter(),"missing":Counter(),"fields":Counter()})
    for r in rows:
        f=fam[str(r["instrument_type"])];f["rows"]+=1;f["statuses"][str(r["status"])]+=1;f["owners"][str(r["owner"])]+=1
        try:miss=json.loads(r["missing_fields_json"] or "[]")
        except Exception:miss=[]
        for x in miss if isinstance(miss,list) else []:f["missing"][str(x)]+=1
        try:e=json.loads(r["evidence_json"] or "{}")
        except Exception:e={}
        if isinstance(e,dict):
            for k,v in e.items():
                if v not in (None,"",[],{}):f["fields"][str(k)]+=1
    out["v1"]={k:{"rows":v["rows"],"statuses":dict(v["statuses"]),"owners":dict(v["owners"]),
                   "missing":dict(v["missing"]),"fields":dict(v["fields"])} for k,v in fam.items()}
else: out["v1"]="TABLE_MISSING"
c.close()
print(json.dumps(out,ensure_ascii=False,sort_keys=True))
