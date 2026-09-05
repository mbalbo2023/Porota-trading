#!/usr/bin/env python3
from __future__ import annotations
import argparse, getpass, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(os.getenv("POROTA_ROOT","/opt/porota-trading"))
PROFILE=Path(os.getenv("POROTA_CHROME_PROFILE","/home/porotaadmin/porota-browser-lab/chrome-profile"))
SECRET=Path(os.getenv("POROTA_PPI_WEB_SECRET_FILE","/home/porotaadmin/.porota-secrets/ppi_web.env"))
OBSERVER=os.getenv("POROTA_OBSERVER_CONTAINER","porota_production_observer")
OUTDIR=ROOT/"data/contract_evidence/rc4_trusted"
CHROME=os.getenv("POROTA_CHROME_EXECUTABLE","/usr/bin/google-chrome-stable")
LOGIN="https://cuenta.portfoliopersonal.com/login"

sys.path.insert(0,str(ROOT))
import rc4_trusted_browser_contract_collector as base

PIN_HINT=re.compile(
 r"(pin|otp|token|c[oó]digo).{0,100}(mail|correo|email|verific|seguridad|autentic)|"
 r"(mail|correo|email).{0,100}(pin|otp|token|c[oó]digo)|segundo factor|"
 r"dispositivo de confianza|validaci[oó]n de dispositivo",re.I|re.S)

DROP_KEY_PARTS=(
 "cuenta","account","saldo","tenencia","disponible","comitente","cliente",
 "documento","dni","cuit","email","mail","telefono","phone","token","password",
 "passwd","cookie","authorization","secret","session","usuario","username","user_id"
)
SAFE_KEY_PARTS=(
 "ticker","simbolo","símbolo","especie","descripcion","descripción","instrumento",
 "moneda","currency","plazo","dia","día","venc","tasa","tna","minimo","mínimo",
 "minimum","maximo","máximo","maximum","multiplo","múltiplo","step","comision",
 "comisión","porcentaje","derecho","mercado","market","settlement","liquidacion",
 "liquidación","fecha","nominal","lamina","lámina","cutoff","rescate","itemid",
 "instrumentoid","plazoid","monedaid","cantidaddecimales","cantidaddecimalesprecio",
 "operablesubasta"
)

def clean(url):
    u=urlsplit(str(url)); return f"{u.scheme}://{u.netloc}{u.path}"

def parse_secret(path):
    out={}
    for raw in path.read_text(encoding="utf-8").splitlines():
        s=raw.strip()
        if not s or s.startswith("#") or "=" not in s: continue
        k,v=s.split("=",1); out[k.strip()]=v.strip().strip('"').strip("'")
    return out

def body(page,n=24000):
    try:return page.locator("body").inner_text(timeout=4000)[:n]
    except Exception:return ""

def first_visible(page,selectors):
    for sel in selectors:
        try:
            q=page.locator(sel)
            for i in range(min(q.count(),15)):
                e=q.nth(i)
                if e.is_visible(): return e
        except Exception: pass
    return None

def user_control(page):
    return first_visible(page,[
      "input[autocomplete='username']","input[placeholder*='usuario' i]",
      "input[aria-label*='usuario' i]","input[name*='user' i]",
      "input[id*='user' i]","input[type='email']"])

def pass_control(page):
    return first_visible(page,[
      "input[type='password']","input[autocomplete='current-password']",
      "input[placeholder*='contraseña' i]","input[aria-label*='contraseña' i]",
      "input[name*='pass' i]","input[id*='pass' i]"])

def submit_control(page):
    return first_visible(page,[
      "button:has-text('Validar')","button:has-text('Verificar')",
      "button:has-text('Confirmar')","button:has-text('Continuar')",
      "button:has-text('Siguiente')","button:has-text('Ingresar')",
      "button[type='submit']","input[type='submit']"])

def pin_control(page):
    hinted=bool(PIN_HINT.search(body(page)))
    e=first_visible(page,[
      "input[autocomplete='one-time-code']","input[name*='pin' i]","input[id*='pin' i]",
      "input[name*='otp' i]","input[id*='otp' i]","input[name*='token' i]",
      "input[id*='token' i]","input[name*='code' i]","input[id*='code' i]"])
    return e,bool(e or hinted)

def trading_ok(page):
    u=urlsplit(page.url)
    return u.netloc=="trading.portfoliopersonal.com" and "login" not in u.path.lower() and "logout" not in u.path.lower() and u.path!="/404"

def key_ok(key):
    s=str(key).lower().replace("-","_")
    if any(x in s for x in DROP_KEY_PARTS): return False
    if s=="id": return False
    return any(x in s for x in SAFE_KEY_PARTS)

def safe_value(v,depth=0):
    if depth>4:return None
    if isinstance(v,dict):
        out={}
        for k,n in v.items():
            if key_ok(k):
                sv=safe_value(n,depth+1)
                if sv not in (None,"",[],{}): out[str(k)]=sv
            elif isinstance(n,(dict,list)):
                sv=safe_value(n,depth+1)
                if sv not in (None,"",[],{}): out[str(k)]=sv
        return out
    if isinstance(v,list):
        return [x for x in (safe_value(x,depth+1) for x in v[:500]) if x not in (None,"",[],{})][:500]
    if isinstance(v,(str,int,float,bool)) or v is None:
        return v
    return str(v)[:300]

def payload_rows(payload):
    x=payload.get("payload") if isinstance(payload,dict) else payload
    for _ in range(4):
        if isinstance(x,dict) and "payload" in x:
            x=x.get("payload")
        else: break
    if isinstance(x,list): raw=x
    elif isinstance(x,dict):
        lists=[v for v in x.values() if isinstance(v,list)]
        raw=max(lists,key=len) if lists else [x]
    else: raw=[]
    out=[]
    for r in raw[:1000]:
        if not isinstance(r,dict): continue
        s=safe_value(r)
        if isinstance(s,dict) and s: out.append(s)
    return out

def schema_shape(payload):
    def shape(v,depth=0):
        if depth>3:return type(v).__name__
        if isinstance(v,dict):
            return {str(k):shape(n,depth+1) for k,n in list(v.items())[:80]
                    if not any(x in str(k).lower() for x in DROP_KEY_PARTS)}
        if isinstance(v,list):
            return {"type":"array","count":len(v),"first":shape(v[0],depth+1) if v else None}
        return type(v).__name__
    return shape(payload)

def sanitize_endpoint(url,payload):
    if "CaucionesOperables" in url:
        return {"kind":"CaucionesOperables","rows":payload_rows(payload),"schema":schema_shape(payload)}
    return base.sanitize_endpoint(url,payload)

def runtime_gid():
    try:
        p=subprocess.run(["docker","exec",OBSERVER,"id","-g"],capture_output=True,text=True,timeout=10)
        return int(p.stdout.strip()) if p.returncode==0 else 1000
    except Exception:return 1000

def due_jobs(force):
    if force:return list(base.ROUTES.keys())
    p=subprocess.run(["docker","exec",OBSERVER,"python","/app/rc4_contract_due_job.py"],
                     capture_output=True,text=True,timeout=30)
    if p.returncode!=0:return []
    try:
        d=json.loads(p.stdout); return [j for j in d.get("due_jobs",[]) if j in base.ROUTES]
    except Exception:return []

IMPORTER=r'''
import json,sqlite3,sys,uuid
from pathlib import Path
import cp_contract_evidence_v2_hf6 as ce

DB="/app/data/paper_v17/observer_v17.db"
CAP=sys.argv[1]

class Store:
    def __init__(self,p): self.path=p
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row; return c

def nonempty(d):
    return {k:v for k,v in d.items() if v not in (None,"",[],{})}

def pick(obj,names):
    want={x.lower().replace("-","_") for x in names}
    if isinstance(obj,dict):
        for k,v in obj.items():
            kk=str(k).lower().replace("-","_")
            if kk in want and v not in (None,"",[],{}): return v
        for v in obj.values():
            z=pick(v,names)
            if z not in (None,"",[],{}): return z
    elif isinstance(obj,list):
        for v in obj:
            z=pick(v,names)
            if z not in (None,"",[],{}): return z
    return None

def candidate_map(store):
    with store.connect() as c:
        tabs={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "candidate_universe" not in tabs:return {}
        out={}
        for r in c.execute("SELECT ticker,instrument_type,market,settlement,status FROM candidate_universe WHERE status='AVAILABLE'"):
            d=dict(r); out.setdefault(str(d.get("ticker") or "").upper(),[]).append(d)
        return out

raw=json.loads(Path(CAP).read_text(encoding="utf-8"))
jobs=list(dict.fromkeys(raw.get("jobs") or []))
store=Store(DB); cmap=candidate_map(store)
runs={}
for job in jobs:
    rid="rc4-"+uuid.uuid4().hex; runs[job]=rid
    ce.start_run(store,run_id=rid,job_key=job,detail="same-context authenticated XHR; partial evidence remains fail-closed")

total=changed=conflicts=0; notes=[]
for ek,item in (raw.get("endpoints") or {}).items():
    kind=item.get("kind")
    job=item.get("observed_job") or ""
    route=item.get("observed_route") or ""
    source=item.get("source_url") or str(ek).split("|",1)[0]

    if kind=="CaucionesOperables":
        rows=item.get("rows") or []
        if not rows:
            ev={"evidence_scope":"ENDPOINT_SCHEMA_ONLY","provider_schema":item.get("schema") or {},
                "source_job":job,"source_route":route,
                "readiness_guard":"NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"}
            try:
                r=ce.record_snapshot(store,family="CAUCIONES",ticker="*",market="UNKNOWN",
                    settlement="UNKNOWN",source_class="PPI_AUTHENTICATED_XHR",source_ref=source,evidence=nonempty(ev))
                total+=1; changed+=int(bool(r.get("changed"))); notes.append("CAUCIONES_SCHEMA_ONLY_PARTIAL")
            except Exception as exc: notes.append("CAUCIONES_SCHEMA_"+type(exc).__name__)
        for row in rows:
            ticker=str(pick(row,["ticker","simbolo","símbolo","especie"]) or "*").upper()
            market=str(pick(row,["market","mercado"]) or "UNKNOWN")
            settlement=str(pick(row,["settlement","liquidacion","liquidación","plazo_liquidacion"]) or "UNKNOWN")
            ev=nonempty({**row,"evidence_scope":"PARTIAL_AUTHENTICATED_XHR",
                         "source_job":job,"source_route":route,
                         "readiness_guard":"NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"})
            try:
                r=ce.record_snapshot(store,family="CAUCIONES",ticker=ticker,market=market,
                    settlement=settlement,source_class="PPI_AUTHENTICATED_XHR",source_ref=source,evidence=ev)
                total+=1; changed+=int(bool(r.get("changed")))
            except Exception as exc: notes.append("CAUCIONES_"+type(exc).__name__)

    elif kind=="InstrumentosOperables":
        for ev0 in item.get("rows") or []:
            ticker=str(ev0.get("ticker") or "").upper()
            if not ticker: continue
            ev=nonempty({k:v for k,v in ev0.items() if k!="ticker"})
            ev.update({"evidence_scope":"PARTIAL_AUTHENTICATED_XHR","source_job":job,
                       "source_route":route,"readiness_guard":"NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"})
            if job=="CONTRACT_EVIDENCE_AUCTIONS":
                targets=[{"instrument_type":"LICITACIONES","market":"UNKNOWN","settlement":"UNKNOWN"}]
            elif job=="CONTRACT_EVIDENCE_CAUCIONES":
                targets=[{"instrument_type":"CAUCIONES","market":"UNKNOWN","settlement":"UNKNOWN"}]
            else:
                targets=cmap.get(ticker,[])
            if not targets:
                notes.append("UNMAPPED_INSTRUMENTOS_OPERABLES_"+ticker); continue
            for ident in targets:
                try:
                    r=ce.record_snapshot(store,family=ident.get("instrument_type"),ticker=ticker,
                        market=ident.get("market") or "UNKNOWN",settlement=ident.get("settlement") or "UNKNOWN",
                        source_class="PPI_AUTHENTICATED_XHR",source_ref=source,evidence=ev)
                    total+=1; changed+=int(bool(r.get("changed")))
                except Exception as exc: notes.append("INSTRUMENTOS_"+type(exc).__name__)

    elif kind=="DatosTecnicos":
        ev0=item.get("row") or {}; ticker=str(ev0.get("ticker") or "").upper()
        for ident in cmap.get(ticker,[]):
            if ce.normalize_family(ident.get("instrument_type")) not in {"BONOS","LETRAS","ON","LEBAC","NOBAC"}: continue
            ev=nonempty({k:v for k,v in ev0.items() if k!="ticker"})
            ev.update({"source_job":job,"source_route":route})
            try:
                r=ce.record_snapshot(store,family=ident.get("instrument_type"),ticker=ticker,
                    market=ident.get("market"),settlement=ident.get("settlement"),
                    source_class="PPI_AUTHENTICATED_XHR",source_ref=source,evidence=ev)
                total+=1; changed+=int(bool(r.get("changed")))
            except Exception as exc: notes.append("DATOSTECNICOS_"+type(exc).__name__)
    elif kind=="SubyacenteOpciones":
        notes.append("OPTION_UNDERLYING_CATALOG_OBSERVED_NOT_CONTRACT")
    elif kind=="SCHEMA_ONLY":
        fam={"CONTRACT_EVIDENCE_AUCTIONS":"LICITACIONES",
             "CONTRACT_EVIDENCE_CAUCIONES":"CAUCIONES"}.get(job)
        if fam:
            ev={"evidence_scope":"ENDPOINT_SCHEMA_ONLY","provider_schema":item.get("schema") or {},
                "source_job":job,"source_route":route,
                "readiness_guard":"NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"}
            try:
                r=ce.record_snapshot(store,family=fam,ticker="*",market="UNKNOWN",settlement="UNKNOWN",
                    source_class="PPI_AUTHENTICATED_XHR",source_ref=source,evidence=nonempty(ev))
                total+=1; changed+=int(bool(r.get("changed"))); notes.append("SCHEMA_ONLY_PARTIAL")
            except Exception as exc: notes.append("SCHEMA_"+type(exc).__name__)

state="AMARILLO"
detail=f"records={total}; changed={changed}; partial_fail_closed=YES; notes={','.join(sorted(set(notes)))[:1200]}"
for job,rid in runs.items():
    ce.finish_run(store,run_id=rid,state=state,records=total,changed=changed,conflicts=conflicts,detail=detail)
print(json.dumps({"state":state,"auth_status":raw.get("auth_status"),"records":total,
                  "changed":changed,"conflicts":conflicts,"notes":sorted(set(notes)),
                  "real_orders_sent":0},ensure_ascii=False,sort_keys=True))
'''

def import_capture(path):
    rel=path.relative_to(ROOT/"data")
    p=subprocess.run(["docker","exec","-i",OBSERVER,"python","-",f"/app/data/{rel.as_posix()}"],
                     input=IMPORTER,text=True,capture_output=True,timeout=120)
    return p.returncode,(p.stdout or p.stderr).strip()[:1800]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--force",action="store_true"); args=ap.parse_args()
    OUTDIR.mkdir(parents=True,exist_ok=True)
    gid=runtime_gid()
    try: os.chown(OUTDIR,0,gid)
    except Exception: pass
    os.chmod(OUTDIR,0o750)
    jobs=due_jobs(args.force)
    if not jobs:
        print("STATUS=CACHED_NOT_DUE"); return 0

    out={"schema":"POROTA_RC4_PPI_TRUSTED_CONTRACT_2",
         "generated_at":datetime.now(timezone.utc).isoformat(),
         "auth_status":"UNKNOWN","jobs":jobs,"routes":[],"endpoints":{},
         "blocked_nonread":[],"continue_clicked":False,"amount_filled":False,
         "price_filled":False,"real_orders_sent":0}
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path=OUTDIR/f"contract_{stamp}.json"

    sec=parse_secret(SECRET) if SECRET.is_file() else {}
    user=sec.get("PPI_WEB_USERNAME",""); password=sec.get("PPI_WEB_PASSWORD","")
    if not PROFILE.is_dir(): out["auth_status"]="BLOCKED_AUTH_PROFILE_MISSING"
    elif not user or not password: out["auth_status"]="BLOCKED_AUTH_LOCAL_SECRET_INCOMPLETE"
    else:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                ctx=pw.chromium.launch_persistent_context(
                    user_data_dir=str(PROFILE),executable_path=CHROME,headless=True,
                    locale="es-AR",timezone_id="America/Argentina/Buenos_Aires",
                    viewport={"width":1440,"height":1000},
                    args=["--no-sandbox","--disable-dev-shm-usage"])
                page=ctx.pages[0] if ctx.pages else ctx.new_page()
                strict={"enabled":False}
                active={"job":"","route":""}

                def guard(route,request):
                    method=request.method.upper()
                    if method in base.SAFE_METHODS:return route.continue_()
                    u=urlsplit(request.url)
                    row={"method":method,"url":clean(request.url)}
                    if strict["enabled"]:
                        out["blocked_nonread"].append(row); return route.abort()
                    if u.netloc=="cuenta.portfoliopersonal.com": return route.continue_()
                    if u.netloc in {"www.google.com","ad.doubleclick.net","www.google-analytics.com","px.ads.linkedin.com","e.clarity.ms"}:
                        return route.continue_()
                    if u.netloc=="trading.portfoliopersonal.com" and u.path=="/api/logger":
                        return route.continue_()
                    out["blocked_nonread"].append(row); return route.abort()
                ctx.route("**/*",guard)

                def on_response(response):
                    try:
                        if response.request.method.upper()!="GET" or not any(x in response.url for x in base.TARGETS): return
                        if response.status>=400:return
                        payload=response.json(); san=sanitize_endpoint(response.url,payload)
                        if san is None:return
                        san={**san,"status":response.status,"source_url":clean(response.url),
                             "observed_job":active["job"],"observed_route":active["route"]}
                        k=clean(response.url)+"|"+active["job"]+"|"+active["route"]
                        out["endpoints"][k]=san
                    except Exception: pass
                page.on("response",on_response)

                auth=False
                for _ in range(8):
                    try:
                        page.goto(base.TRADING+"/",wait_until="domcontentloaded",timeout=45000); page.wait_for_timeout(1100)
                        if trading_ok(page): auth=True; break
                    except Exception: pass
                    page.goto(LOGIN,wait_until="domcontentloaded",timeout=45000); page.wait_for_timeout(900)
                    pin,hinted=pin_control(page)
                    if hinted:
                        if not sys.stdin.isatty(): out["auth_status"]="BLOCKED_AUTH_2FA_REQUIRED"; break
                        if pin is None: out["auth_status"]="BLOCKED_AUTH_2FA_INPUT_NOT_FOUND"; break
                        print("PPI solicita PIN/OTP. Ingresalo una sola vez:",flush=True)
                        code=getpass.getpass("PPI PIN: ").strip()
                        if not code: out["auth_status"]="BLOCKED_AUTH_2FA_EMPTY"; break
                        pin.fill(code); del code
                        b=submit_control(page)
                        if not b: out["auth_status"]="BLOCKED_AUTH_2FA_SUBMIT_NOT_FOUND"; break
                        b.click(); page.wait_for_timeout(3200); continue
                    u=user_control(page); p=pass_control(page); acted=False
                    if u:
                        try:
                            if not u.input_value():u.fill(user)
                            acted=True
                        except Exception: pass
                    if p:
                        try:p.fill(password); acted=True
                        except Exception: pass
                    if acted:
                        b=submit_control(page)
                        if not b: out["auth_status"]="BLOCKED_AUTH_LOGIN_SUBMIT_NOT_FOUND"; break
                        b.click(); page.wait_for_timeout(3000); continue
                    back=first_visible(page,["button:has-text('Cambiar usuario')","a:has-text('Cambiar usuario')",
                                             "button:has-text('Volver')","a:has-text('Volver')"])
                    if back: back.click(); page.wait_for_timeout(1200); continue

                if auth:
                    out["auth_status"]="AUTHENTICATED_TRUSTED_DEVICE"; strict["enabled"]=True
                    todo=[]
                    for job in jobs:
                        for route in base.ROUTES[job]:
                            todo.append((job,route))
                    for idx,(job,route) in enumerate(todo,1):
                        active["job"]=job; active["route"]=route
                        print(f"ROUTE={idx}/{len(todo)}|{job}|{route}",flush=True)
                        row={"job":job,"requested":route,"reached":False}
                        try:
                            page.goto(base.TRADING+route,wait_until="domcontentloaded",timeout=45000)
                            page.wait_for_timeout(1200)
                            pu=urlsplit(page.url); row["url"]=clean(page.url); row["title"]=page.title()[:180]
                            row["reached"]=pu.netloc=="trading.portfoliopersonal.com" and "login" not in pu.path.lower() and "logout" not in pu.path.lower()
                            row["controls"]=base.safe_controls(page)
                        except Exception as exc: row["error"]=type(exc).__name__
                        out["routes"].append(row)
                elif out["auth_status"]=="UNKNOWN":
                    out["auth_status"]="BLOCKED_AUTH_SESSION_EXPIRED"
                ctx.close()
        except Exception as exc:
            if out["auth_status"]=="UNKNOWN":out["auth_status"]="BLOCKED_BROWSER_ERROR"
            out["error"]=type(exc).__name__+":"+str(exc)[:300]

    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    try: os.chown(path,0,gid)
    except Exception: pass
    os.chmod(path,0o640)
    rc,imp=import_capture(path)
    print(json.dumps({"state":out["auth_status"],"jobs":jobs,"routes":len(out["routes"]),
                      "endpoints":len(out["endpoints"]),"blocked_nonread":len(out["blocked_nonread"]),
                      "real_orders_sent":0,"import_rc":rc,"import":imp,"capture":str(path)},
                     ensure_ascii=False))
    return 0 if rc==0 else 1

if __name__=="__main__": raise SystemExit(main())
