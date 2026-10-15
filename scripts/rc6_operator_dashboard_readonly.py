#!/usr/bin/env python3
"""Bounded internal dashboard and connector audit; no provider or order calls."""
from __future__ import annotations

import json
import re
import subprocess
import sys

SCHEMA = "rc6.operator-dashboard-readonly.v1"
ROUTES = (
    "/", "/en-vivo", "/en-vivo/oportunidades", "/trading",
    "/trading/scalping", "/universo", "/instrumentos", "/riesgo",
    "/analitica", "/sistema", "/sistema/integraciones",
    "/sistema/workers", "/sistema/scheduler",
)

# Runs inside the existing dashboard container. Its token never leaves that
# container; only allowlisted route and provider-state facts reach Actions.
CHILD = r'''
import json, os, re, sqlite3, time, urllib.error, urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

routes = ("/", "/en-vivo", "/en-vivo/oportunidades", "/trading",
          "/trading/scalping", "/universo", "/instrumentos", "/riesgo",
          "/analitica", "/sistema", "/sistema/integraciones",
          "/sistema/workers", "/sistema/scheduler")
result = {"schema":"rc6.operator-dashboard-readonly.v1", "read_only":True,
          "routes":[], "sources":[], "source_status":"NO_VERIFICADO"}
token = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
if token:
 for path in routes:
  entry={"path":path, "http":None, "nav":False, "content":False}
  started=time.monotonic()
  try:
   req=urllib.request.Request("http://127.0.0.1:8000"+path,
       headers={"Authorization":"Bearer "+token, "Cache-Control":"no-cache"})
   with urllib.request.urlopen(req,timeout=6) as response:
    raw=response.read(524289); entry["http"]=response.status
   if len(raw)>524288: entry["error"]="BODY_LIMIT"
   else:
    html=raw.decode("utf-8","replace")
    entry["nav"]="id='porota-canonical-nav'" in html or 'id="porota-canonical-nav"' in html
    entry["content"]="id='terminal-content'" in html or 'id="terminal-content"' in html
    if path=="/": entry["attention"]="Atención ahora" in html
  except urllib.error.HTTPError as exc: entry["http"]=exc.code
  except (OSError,ValueError): entry["error"]="HTTP_UNAVAILABLE"
  finally:
   entry["elapsed_ms"]=min(60000,round((time.monotonic()-started)*1000))
   entry["slow_2s"]=entry["elapsed_ms"]>2000
  result["routes"].append(entry)
else: result["dashboard_status"]="AUTH_NOT_CONFIGURED"

try:
 db="/app/data/paper_v17/observer_v17.db"
 with sqlite3.connect("file:"+quote(db,safe="/")+"?mode=ro",uri=True,timeout=.2) as conn:
  conn.execute("PRAGMA query_only=ON")
  rows=conn.execute("SELECT component,state,checked_at,last_success_at FROM api_health "
      "WHERE component LIKE 'PPI%' OR component LIKE 'IOL%' OR component LIKE 'BYMA%' "
      "OR component LIKE 'A3%' OR component LIKE 'ROFEX%' OR component LIKE 'GEMINI%' "
      "ORDER BY component LIMIT 81").fetchall()
  result["source_truncated"]=len(rows)>80
  for component,state,checked,success in rows[:80]:
   if not isinstance(component,str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:/.-]{0,63}",component): continue
   safe=state if isinstance(state,str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,40}",state) else "NO_VERIFICADO"
   def age(value):
    try:
     stamp=datetime.fromisoformat(str(value).replace("Z","+00:00"))
     seconds=(datetime.now(timezone.utc)-stamp).total_seconds()
     return int(seconds) if stamp.tzinfo and 0<=seconds<=365*86400 else None
    except (TypeError,ValueError): return None
   result["sources"].append({"component":component,"state":safe,
       "checked_age_seconds":age(checked),"last_success_age_seconds":age(success)})
  result["source_status"]=("TRUNCATED" if result["source_truncated"] else
      "BOUNDED" if result["sources"] else "EMPTY")
except sqlite3.Error: result["source_status"]="SQLITE_UNAVAILABLE"
print(json.dumps(result,sort_keys=True,separators=(",",":")))
'''


def audit_host(*, runner=subprocess.run):
    try:
        proc = runner(["docker", "exec", "porota_production_dashboard", "python", "-B", "-c", CHILD],
                      capture_output=True, text=True, timeout=85, check=False)
        data = json.loads(proc.stdout) if proc.returncode == 0 and len(proc.stdout) < 32768 else {}
        if data.get("schema") != SCHEMA or data.get("read_only") is not True:
            raise ValueError("INVALID_AUDIT_RECEIPT")
        incoming = data.get("routes")
        routes = []
        if not isinstance(incoming, list):
            raise ValueError("INVALID_ROUTES")
        for item in incoming:
            if not isinstance(item, dict) or item.get("path") not in ROUTES:
                raise ValueError("INVALID_ROUTE")
            code = item.get("http")
            routes.append({"path": item["path"], "http": code if type(code) is int and 100 <= code <= 599 else None,
                           "nav": item.get("nav") is True, "content": item.get("content") is True,
                           "elapsed_ms": item.get("elapsed_ms") if type(item.get("elapsed_ms")) is int and 0 <= item["elapsed_ms"] <= 60000 else None,
                           "slow_2s": item.get("slow_2s") is True,
                           "attention": item.get("attention") is True if item["path"] == "/" else None,
                           "error": item.get("error") if item.get("error") in {"BODY_LIMIT", "HTTP_UNAVAILABLE"} else None})
        if tuple(row["path"] for row in routes) not in (ROUTES, ()):
            raise ValueError("INCOMPLETE_ROUTES")
        sources = []
        for item in data.get("sources", []):
            if not isinstance(item, dict):
                continue
            name, state = item.get("component"), item.get("state")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:/.-]{0,63}", name)
                    or not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,40}", state)):
                continue
            safe = {"component": name, "state": state}
            for key in ("checked_age_seconds", "last_success_age_seconds"):
                value = item.get(key)
                safe[key] = value if type(value) is int and 0 <= value <= 365 * 86400 else None
            sources.append(safe)
        complete = (len(routes) == len(ROUTES) and all(
            row["http"] == 200 and row["nav"] and row["content"] and row["error"] is None
            for row in routes) and data.get("source_status") == "BOUNDED" and sources)
        status = ("ROUTES_DEGRADED" if any(row["slow_2s"] for row in routes) else "ROUTES_VERIFIED") if complete else "NO_VERIFICADO"
        return {"schema": SCHEMA, "read_only": True, "status": status,
                "routes": routes, "sources": sources[:80],
                "source_status": data.get("source_status") if data.get("source_status") in
                {"BOUNDED", "TRUNCATED", "EMPTY", "SQLITE_UNAVAILABLE", "NO_VERIFICADO"} else "NO_VERIFICADO"}
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return {"schema": SCHEMA, "read_only": True, "status": "NO_VERIFICADO",
                "routes": [], "sources": [], "source_status": "NO_VERIFICADO"}


if __name__ == "__main__":
    if sys.argv[1:] != ["dashboard-host"]:
        raise SystemExit("INVALID_ACTION")
    print(json.dumps(audit_host(),sort_keys=True,separators=(",",":")))
