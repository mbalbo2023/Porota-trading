#!/usr/bin/env python3
import json
from pathlib import Path
p=Path("/opt/porota-trading/data/market/rc6_instrument_evidence_latest.json")
out={"exists":p.exists(),"path":str(p)}
if p.exists():
    x=json.loads(p.read_text(encoding="utf-8"))
    out.update({
        "generated_at":x.get("generated_at"),
        "source_order":x.get("source_order"),
        "universe":x.get("universe"),
        "counts":x.get("counts"),
        "families":x.get("families"),
        "decision_effect":x.get("decision_effect"),
        "paper_shadow_only":x.get("paper_shadow_only"),
        "real_money_authorized":x.get("real_money_authorized"),
    })
print(json.dumps(out,ensure_ascii=False,sort_keys=True))
