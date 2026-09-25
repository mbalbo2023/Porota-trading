#!/usr/bin/env python3
import json, sqlite3
from collections import Counter
db="/opt/porota-trading/data/paper_v17/observer_v17.db"
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=20)
c.row_factory=sqlite3.Row
c.execute("PRAGMA query_only=ON")
out={}
def exists(t): return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone())
if exists("financial_instrument_catalog"):
    cols={r[1] for r in c.execute("PRAGMA table_info(financial_instrument_catalog)")}
    fam="instrument_type" if "instrument_type" in cols else "family"
    rows=[dict(r) for r in c.execute(f"SELECT {fam} family,status,COUNT(*) n FROM financial_instrument_catalog GROUP BY {fam},status ORDER BY {fam},status")]
    out["financial_instrument_catalog"]=rows
if exists("candidate_universe"):
    cols={r[1] for r in c.execute("PRAGMA table_info(candidate_universe)")}
    fam="instrument_type" if "instrument_type" in cols else "family"
    sel=[x for x in (fam,"status","can_simulate","detail") if x in cols]
    q="SELECT "+",".join(sel)+",COUNT(*) n FROM candidate_universe GROUP BY "+",".join(sel)+" ORDER BY "+fam
    out["candidate_universe"]=[dict(r) for r in c.execute(q)]
if exists("contract_evidence"):
    cols={r[1] for r in c.execute("PRAGMA table_info(contract_evidence)")}
    fam=next((x for x in ("instrument_type","family","asset_class") if x in cols),None)
    if fam:
        out["contract_evidence"]=[dict(r) for r in c.execute(f"SELECT {fam} family,COUNT(*) n FROM contract_evidence GROUP BY {fam} ORDER BY {fam}")]
c.close()
print(json.dumps(out,ensure_ascii=False,sort_keys=True))
