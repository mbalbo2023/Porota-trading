#!/usr/bin/env python3
import json,sqlite3
from collections import defaultdict
db="/opt/porota-trading/data/paper_v17/observer_v17.db"
c=sqlite3.connect(f"file:{db}?mode=ro",uri=True,timeout=20);c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON")
families=("BONOS","LETRAS","ON","FUTUROS","OPCIONES","CAUCIONES","ETF","FCI")
out={}
for fam in families:
    rows=[dict(r) for r in c.execute("""SELECT cur.family,cur.ticker,cur.market,cur.settlement,cur.source_class,
      cur.observed_at,s.source_ref,s.evidence_json
      FROM contract_evidence_v2_current cur
      JOIN contract_evidence_v2_snapshots s ON s.snapshot_id=cur.snapshot_id
      WHERE cur.family=? ORDER BY CASE WHEN cur.ticker='*' THEN 1 ELSE 0 END,cur.ticker,cur.source_class LIMIT 12""",(fam,))]
    samples=[]
    for r in rows:
        try:e=json.loads(r["evidence_json"] or "{}")
        except Exception:e={}
        # evidence registry already rejects sensitive fields; still keep only contract-shaped keys.
        keep={}
        if isinstance(e,dict):
            for k,v in e.items():
                kl=str(k).lower()
                if any(x in kl for x in ("contract","observed","identity","semantic","header","field","completeness","readiness","row","sample","instrument","currency","commission","price","quantity","provider","source")):
                    keep[k]=v
        samples.append({k:r[k] for k in ("ticker","market","settlement","source_class","observed_at","source_ref")} | {"evidence":keep})
    out[fam]=samples
c.close()
print(json.dumps(out,ensure_ascii=False,sort_keys=True))
