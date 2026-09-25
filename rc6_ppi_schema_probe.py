#!/usr/bin/env python3
import json,os
from pathlib import Path
from bd_ppi_readonly_guard import ProductionMarketReader

probes={
 "ACCIONES":("A","A","BYMA"),
 "CEDEARS":("A","A","BYMA"),
 "BONOS":("B","B","BYMA"),
 "LETRAS":("A","A","BYMA"),
 "ON":("A","A","BYMA"),
 "OPCIONES":("A","A","BYMA"),
 "FCI":("A","A","BYMA"),
}
secret=json.loads(Path(os.getenv("PPI_PRODUCTION_SECRET_FILE","/run/secrets/ppi_production.json")).read_text())
r=ProductionMarketReader(secret.get("api_key") or "",secret.get("api_secret") or "")
out={}
try:
    r.login_once()
    for fam,(ticker,name,market) in probes.items():
        try:
            rows=r.search_instruments(ticker,fam,name=name,market=market)
            rows=rows if isinstance(rows,list) else []
            sample=[]
            for x in rows[:3]:
                if not isinstance(x,dict): continue
                safe={}
                for k,v in x.items():
                    kl=str(k).lower()
                    if any(s in kl for s in ("token","secret","account","cuenta","password","credential")): continue
                    if isinstance(v,(str,int,float,bool)) or v is None:
                        safe[k]=v
                    elif isinstance(v,dict):
                        safe[k]={"__keys__":sorted(v.keys())}
                    elif isinstance(v,list):
                        safe[k]={"__list_len__":len(v),"first_type":type(v[0]).__name__ if v else None}
                sample.append(safe)
            out[fam]={"count":len(rows),"keys":sorted({str(k) for x in rows[:20] if isinstance(x,dict) for k in x.keys()}),"sample":sample}
        except Exception as e:
            out[fam]={"error":type(e).__name__+":"+str(e)[:200]}
finally:
    metrics=r.metrics;r.close()
print(json.dumps({"results":out,"metrics":metrics},ensure_ascii=False,sort_keys=True,default=str))
