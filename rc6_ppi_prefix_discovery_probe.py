#!/usr/bin/env python3
import json,os
from pathlib import Path
from bd_ppi_readonly_guard import ProductionMarketReader

families=("ACCIONES","CEDEARS","ETF","BONOS","LETRAS","ON","OPCIONES","FUTUROS","FCI")
queries=("A","B","M")
secret=json.loads(Path(os.getenv("PPI_PRODUCTION_SECRET_FILE","/run/secrets/ppi_production.json")).read_text())
r=ProductionMarketReader(secret.get("api_key") or "",secret.get("api_secret") or "")
out={}
try:
    r.login_once()
    for fam in families:
        out[fam]={}
        for q in queries:
            try:
                rows=r.search_instruments(q,fam,name=q,market="BYMA")
                rows=rows if isinstance(rows,list) else []
                out[fam][q]={"count":len(rows),"tickers":[str(x.get("ticker") or "") for x in rows[:10] if isinstance(x,dict)]}
            except Exception as e:
                out[fam][q]={"error":type(e).__name__+":"+str(e)[:120]}
finally:
    metrics=r.metrics;r.close()
print(json.dumps({"results":out,"metrics":metrics},ensure_ascii=False,sort_keys=True))
