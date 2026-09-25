#!/usr/bin/env python3
import json,os
from pathlib import Path
from bd_ppi_readonly_guard import ProductionMarketReader
s=json.loads(Path(os.getenv("PPI_PRODUCTION_SECRET_FILE","/run/secrets/ppi_production.json")).read_text())
r=ProductionMarketReader(s.get("api_key") or "",s.get("api_secret") or "")
try:
    r.login_once()
    cur=r.current("GFGC4200OC","OPCIONES","INMEDIATA")
    book=r.book("GFGC4200OC","OPCIONES","INMEDIATA")
    print(json.dumps({"current":cur,"book":book,"metrics":r.metrics},ensure_ascii=False,sort_keys=True,default=str))
finally:r.close()
