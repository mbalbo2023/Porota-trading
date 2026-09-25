#!/usr/bin/env python3
import json,os
from pathlib import Path
from bd_ppi_readonly_guard import ProductionMarketReader

families=("ACCIONES","CEDEARS","ETF","BONOS","LETRAS","ON","OPCIONES","FUTUROS","CAUCIONES","FCI")
secret=json.loads(Path(os.getenv("PPI_PRODUCTION_SECRET_FILE","/run/secrets/ppi_production.json")).read_text())
r=ProductionMarketReader(secret.get("api_key") or "",secret.get("api_secret") or "")
out={}
try:
    r.login_once()
    m=r._market()
    for fam in families:
        try:
            payload=m.search_instrument("","","BYMA",fam)
            rows=payload if isinstance(payload,list) else []
            out[fam]={"status":"OK","count":len(rows),"sample":[
                {k:x.get(k) for k in ("ticker","type","market","currency","settlement","description") if isinstance(x,dict) and x.get(k) not in (None,"")}
                for x in rows[:3] if isinstance(x,dict)
            ]}
        except Exception as e:
            out[fam]={"status":type(e).__name__,"detail":str(e)[:240]}
finally:
    metrics=r.metrics
    r.close()
print(json.dumps({"results":out,"metrics":metrics},ensure_ascii=False,sort_keys=True))
