#!/usr/bin/env python3
"""RC6 PPI Web authenticated historical collector, fail-closed/read-only.

This is the existing history-shadow engine extended minimally for residual targets.
It keeps the same Playwright/trusted-profile model and network guard. It never
writes the History Store; it emits sanitized evidence for the separate reconciler.
"""
from __future__ import annotations

import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

TRADING="https://trading.portfoliopersonal.com"
SAFE_METHODS={"GET","HEAD","OPTIONS"}
FORBIDDEN_PATH_PARTS=("/orden","/order","/operar/confirm","/confirmar","/cancel","/transfer","/suscribir","/rescatar")
DROP_KEY_PARTS=("account","cuenta","saldo","tenencia","disponible","comitente","cliente","documento","dni","cuit","email","mail","telefono","phone","token","password","passwd","cookie","authorization","secret","session","usuario","username","user_id")
DATE_ALIASES=("date","fecha","fechaCotizacion","datetime","timestamp","time","day","dia","día")
FIELD_ALIASES={
 "openingPrice":("openingPrice","open","apertura"),
 "max":("max","high","maxDia","maximo","máximo"),
 "min":("min","low","minDia","minimo","mínimo"),
 "price":("price","close","cierre","ultOperado","ultimo","último"),
 "volume":("volume","volumen","quantity","cantidad","monto"),
}
ROUTES={
 "ACCIONES":"/Cotizaciones/Acciones","CEDEARS":"/Cotizaciones/Cedears","BONOS":"/Cotizaciones/Bonos",
 "BONOS_USD":"/Cotizaciones/Bonos","ON":"/Cotizaciones/Ons","OBLIGACIONES":"/Cotizaciones/Ons",
 "OPCIONES":"/Cotizaciones/Opciones","FUTUROS":"/Cotizaciones/Futuros","LETRAS":"/Cotizaciones/Letras",
 "ETF":"/Cotizaciones/ETFs","ETFS":"/Cotizaciones/ETFs","FCI":"/Cotizaciones/FCIs","FCIS":"/Cotizaciones/FCIs",
 "CAUCIONES":"/Cotizaciones/Cauciones","LICITACIONES":"/Cotizaciones/Licitaciones","INDICES":"/Cotizaciones/Indices",
}
REPRESENTATIVE_ROUTES=(("ACCIONES","GGAL","/Cotizaciones/Acciones"),("CEDEARS","AAPL","/Cotizaciones/Cedears"),("BONOS","GD30","/Cotizaciones/Bonos"),("OPCIONES","OPCION","/Cotizaciones/Opciones"),("FUTUROS","FUTURO","/Cotizaciones/Futuros"))

def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="microseconds")
def clean_url(value):
 u=urlsplit(str(value)); return f"{u.scheme}://{u.netloc}{u.path}"
def path_forbidden(url): return any(x in urlsplit(str(url)).path.lower() for x in FORBIDDEN_PATH_PARTS)
def key_safe(key):
 low=str(key).lower().replace("-","_"); return not any(x in low for x in DROP_KEY_PARTS)
def _walk(v,depth=0):
 if depth>5:return []
 out=[]
 if isinstance(v,dict):
  out.append(v)
  for k,n in v.items():
   if key_safe(k) and isinstance(n,(dict,list)): out.extend(_walk(n,depth+1))
 elif isinstance(v,list):
  for n in v[:3000]: out.extend(_walk(n,depth+1))
 return out

def _first(row,names):
 low={str(k).lower():v for k,v in row.items() if key_safe(k)}
 for name in names:
  if name.lower() in low and low[name.lower()] not in (None,""): return low[name.lower()]
 return None

def sanitized_history_rows(payload):
 out=[]
 for obj in _walk(payload):
  d=_first(obj,DATE_ALIASES)
  if d in (None,""): continue
  row={"date":d}; hits=0
  for dst,names in FIELD_ALIASES.items():
   v=_first(obj,names)
   if v not in (None,""): row[dst]=v; hits+=1
  if hits>=2: out.append(row)
  if len(out)>=1500: break
 return out

def historical_candidate(payload):
 rows=sanitized_history_rows(payload)
 if not rows:return None
 keys=set().union(*(r.keys() for r in rows))
 return {"candidate":True,"rows_detected":len(rows),"has_date":True,"ohlc_key_hits":sorted(keys & {"openingPrice","max","min","price"}),"volume_key_hits":["volume"] if "volume" in keys else [],"sample_keys":sorted(keys),"full_ohlcv_shape":{"date","openingPrice","max","min","price","volume"} <= keys}
def schema_shape(payload):
 if isinstance(payload,dict): return {"type":"object","keys":sorted(str(k) for k in payload if key_safe(k))[:120]}
 if isinstance(payload,list):
  first=payload[0] if payload and isinstance(payload[0],dict) else {}; return {"type":"array","count":len(payload),"first_keys":sorted(str(k) for k in first if key_safe(k))[:120]}
 return {"type":type(payload).__name__}
def payload_digest(payload): return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(",",":"),default=str).encode()).hexdigest()

def load_targets(path,limit=0):
 if not path:return [{"instrument_type":f,"symbol":s,"market":"","settlement":"","residual_class":"REPRESENTATIVE","route":r} for f,s,r in REPRESENTATIVE_ROUTES]
 rows=[]; seen=set()
 for line in Path(path).read_text(encoding="utf-8").splitlines():
  if not line.strip(): continue
  x=json.loads(line); key=tuple(str(x.get(k,"")).strip().upper() for k in ("symbol","instrument_type","market","settlement"))
  if not all(key[:2]): raise ValueError("TARGET_IDENTITY_INCOMPLETE")
  if key in seen: raise ValueError("TARGET_DUPLICATE")
  seen.add(key); fam=key[1]; route=ROUTES.get(fam)
  rows.append({"symbol":key[0],"instrument_type":fam,"market":key[2],"settlement":key[3],"residual_class":str(x.get("residual_class","")),"route":route})
 rows.sort(key=lambda r:(r["instrument_type"],r["symbol"],r["market"],r["settlement"]))
 return rows[:limit] if limit>0 else rows

def main():
 ap=argparse.ArgumentParser(); ap.add_argument("--profile",required=True); ap.add_argument("--output",required=True); ap.add_argument("--targets-jsonl"); ap.add_argument("--limit",type=int,default=0); ap.add_argument("--chrome",default=os.getenv("POROTA_CHROME_EXECUTABLE","/usr/bin/google-chrome-stable")); args=ap.parse_args()
 targets=load_targets(args.targets_jsonl,args.limit)
 out={"schema":"POROTA_RC6_PPI_WEB_HISTORY_RESIDUAL_V2","generated_at":now_iso(),"auth_status":"UNKNOWN","canonical_write":"DENY","db_write":"NO","real_orders_sent":0,"targets":[],"responses":[],"captures":[],"blocked_nonread":[],"blocked_forbidden_paths":[]}
 target=Path(args.output); target.parent.mkdir(parents=True,exist_ok=True); profile=Path(args.profile)
 if not profile.is_dir(): out["auth_status"]="BLOCKED_AUTH_PROFILE_MISSING"; target.write_text(json.dumps(out,ensure_ascii=False,indent=2)); os.chmod(target,0o600); return 4
 try:
  from playwright.sync_api import sync_playwright
 except Exception as exc:
  out["auth_status"]="BLOCKED_PLAYWRIGHT_UNAVAILABLE"; out["error"]=type(exc).__name__; target.write_text(json.dumps(out,indent=2)); os.chmod(target,0o600); return 4
 try:
  with sync_playwright() as pw:
   ctx=pw.chromium.launch_persistent_context(user_data_dir=str(profile),executable_path=args.chrome,headless=True,locale="es-AR",timezone_id="America/Argentina/Buenos_Aires",viewport={"width":1440,"height":1000},args=["--no-sandbox","--disable-dev-shm-usage"])
   def guard(route,request):
    m=request.method.upper()
    if path_forbidden(request.url): out["blocked_forbidden_paths"].append({"method":m,"url":clean_url(request.url)}); return route.abort()
    if m not in SAFE_METHODS: out["blocked_nonread"].append({"method":m,"url":clean_url(request.url)}); return route.abort()
    return route.continue_()
   ctx.route("**/*",guard); page=ctx.pages[0] if ctx.pages else ctx.new_page(); active={"target":None,"capture":False}; seen=set()
   def on_response(response):
    try:
     if not active["capture"] or response.request.method.upper()!="GET" or response.status>=400:return
     u=clean_url(response.url); host=urlsplit(u).netloc.lower()
     if not (host.endswith("portfoliopersonal.com") or host.endswith("ppi.com.ar")):return
     if "json" not in str(response.headers.get("content-type") or "").lower():return
     payload=response.json(); rows=sanitized_history_rows(payload)
     if not rows:return
     t=active["target"]; key=(u,t["symbol"],t["instrument_type"],payload_digest(rows))
     if key in seen:return
     seen.add(key); meta={"url":u,"status":response.status,"symbol":t["symbol"],"instrument_type":t["instrument_type"],"market":t["market"],"settlement":t["settlement"],"residual_class":t["residual_class"],"schema":schema_shape(payload),"payload_sha256":payload_digest(payload),"rows_sha256":payload_digest(rows),"rows":rows}
     out["captures"].append(meta); out["responses"].append({k:v for k,v in meta.items() if k!="rows"})
    except Exception: pass
   page.on("response",on_response); page.goto(TRADING+"/",wait_until="domcontentloaded",timeout=45000); page.wait_for_timeout(1000); u=urlsplit(page.url)
   if u.netloc!="trading.portfoliopersonal.com" or "login" in u.path.lower(): out["auth_status"]="BLOCKED_AUTH_SESSION_EXPIRED"
   else:
    out["auth_status"]="AUTHENTICATED_TRUSTED_DEVICE"
    for t in targets:
     row={**t,"reached":False,"captures_before":len(out["captures"])}; route=t.get("route")
     if not route: row["error"]="UNSUPPORTED_FAMILY_ROUTE"; out["targets"].append(row); continue
     try:
      active.update(target=t,capture=False); page.goto(TRADING+route,wait_until="domcontentloaded",timeout=45000); page.wait_for_timeout(900); pu=urlsplit(page.url); row["url"]=clean_url(page.url); row["reached"]=pu.netloc=="trading.portfoliopersonal.com" and "login" not in pu.path.lower()
      if row["reached"]:
       active["capture"]=True
       boxes=page.locator('input[type="search"], input[placeholder*="Buscar" i], input[placeholder*="especie" i]')
       if boxes.count()>0:
        try: boxes.first.fill(t["symbol"],timeout=2500); page.wait_for_timeout(1800)
        except Exception: pass
       else: page.wait_for_timeout(1200)
      active["capture"]=False
     except Exception as exc: row["error"]=type(exc).__name__; active["capture"]=False
     row["captures_after"]=len(out["captures"]); row["captures_added"]=row["captures_after"]-row["captures_before"]; out["targets"].append(row)
   ctx.close()
 except Exception as exc:
  if out["auth_status"]=="UNKNOWN": out["auth_status"]="BLOCKED_BROWSER_ERROR"
  out["error"]=type(exc).__name__+":"+str(exc)[:200]
 out["summary"]={"targets":len(out["targets"]),"captures":len(out["captures"]),"rows":sum(len(x["rows"]) for x in out["captures"]),"blocked_nonread":len(out["blocked_nonread"]),"blocked_forbidden_paths":len(out["blocked_forbidden_paths"])}
 target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8"); os.chmod(target,0o600); print(json.dumps({"auth_status":out["auth_status"],**out["summary"],"canonical_write":"DENY","real_orders_sent":0},sort_keys=True)); return 0 if out["auth_status"]=="AUTHENTICATED_TRUSTED_DEVICE" else 4
if __name__=="__main__": raise SystemExit(main())
