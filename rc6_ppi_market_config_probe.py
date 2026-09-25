#!/usr/bin/env python3
import json,os
from pathlib import Path
from bd_ppi_readonly_guard import ProductionMarketReader
secret=json.loads(Path(os.getenv("PPI_PRODUCTION_SECRET_FILE","/run/secrets/ppi_production.json")).read_text())
r=ProductionMarketReader(secret.get("api_key") or "",secret.get("api_secret") or "")
try:
    r.login_once(); x=r.market_configuration()
finally:
    metrics=r.metrics;r.close()
print(json.dumps({"configuration":x,"metrics":metrics},ensure_ascii=False,sort_keys=True))
