#!/usr/bin/env python3
"""RC6 authenticated trusted-device PPI Contract Evidence collector.

Safety contract:
- existing trusted Chrome profile only; never accepts username/password/OTP;
- V3: third-party mutations abort silently; known first-party telemetry aborts; every other PPI mutation aborts + fail-closes;
- never fills quantity/price and never clicks an order/confirmation control;
- persists only sanitized endpoint evidence and route metadata;
- has no broker/order imports.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from rc6_ppi_contract_normalizer import instrumentos_operables, option_underlyings, bond_technical

TRADING = "https://trading.portfoliopersonal.com"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TARGETS = ("InstrumentosOperables", "CaucionesOperables", "ConfiguracionOperatoriaSimplificada",
           "SubyacenteOpciones", "DatosTecnicos")
ROUTES = {
    'CONTRACT_EVIDENCE_DYNAMIC': ['/Cotizaciones/Bonos'],
    'CONTRACT_EVIDENCE_CAUCIONES': ['/Cotizaciones/Cauciones'],
    'CONTRACT_EVIDENCE_AUCTIONS': ['/Cotizaciones/Licitaciones'],
    'CONTRACT_EVIDENCE_DERIVATIVES': ['/Cotizaciones/Opciones','/Cotizaciones/Futuros'],
    'CONTRACT_EVIDENCE_STATIC': ['/Cotizaciones/Acciones','/Cotizaciones/Cedears','/Cotizaciones/Bonos','/Cotizaciones/Letras','/Cotizaciones/Ons','/Cotizaciones/FCIs'],
    'CONTRACT_EVIDENCE_FULL_BROWSER': ['/Cotizaciones/FCIs','/Cotizaciones/FCIsExterior','/Cotizaciones/Acciones','/Cotizaciones/AccionesUSA','/Cotizaciones/Bonos','/Cotizaciones/Cauciones','/Cotizaciones/Cedears','/Cotizaciones/ETFs','/Cotizaciones/Futuros','/Cotizaciones/Letras','/Cotizaciones/Licitaciones','/Cotizaciones/Ons','/Cotizaciones/Opciones','/Cotizaciones/Indices','/Cotizaciones/Monedas','/Cotizaciones/Tasas'],
}
DROP_KEY_PARTS=("cuenta","account","saldo","tenencia","disponible","comitente","cliente","documento","dni","cuit","email","mail","telefono","phone","token","password","passwd","cookie","authorization","secret","session","usuario","username","user_id")
SAFE_KEY_PARTS=("ticker","simbolo","símbolo","especie","descripcion","descripción","instrumento","moneda","currency","plazo","dia","día","venc","tasa","tna","minimo","mínimo","minimum","maximo","máximo","maximum","multiplo","múltiplo","step","comision","comisión","porcentaje","derecho","mercado","market","settlement","liquidacion","liquidación","fecha","nominal","lamina","lámina","cutoff","rescate","itemid","instrumentoid","plazoid","monedaid","cantidaddecimales","cantidaddecimalesprecio","operablesubasta")

def assert_safe_route_catalog():
    for job,routes in ROUTES.items():
        if not routes: raise RuntimeError("CONTRACT_EVIDENCE_EMPTY_ROUTE_JOB:"+job)
        for route in routes:
            if not str(route).startswith('/Cotizaciones/'):
                raise RuntimeError('CONTRACT_EVIDENCE_UNSAFE_ROUTE:'+str(route))

def clean_url(url):
    u=urlsplit(str(url)); return f"{u.scheme}://{u.netloc}{u.path}"

def key_ok(key):
    s=str(key).lower().replace('-','_')
    if any(x in s for x in DROP_KEY_PARTS) or s=='id': return False
    return any(x in s for x in SAFE_KEY_PARTS)

def safe_value(v,depth=0):
    if depth>4:return None
    if isinstance(v,dict):
        out={}
        for k,nested in v.items():
            if key_ok(k):
                sv=safe_value(nested,depth+1)
                if sv not in (None,'',[],{}): out[str(k)]=sv
            elif isinstance(nested,(dict,list)):
                sv=safe_value(nested,depth+1)
                if sv not in (None,'',[],{}): out[str(k)]=sv
        return out
    if isinstance(v,list): return [x for x in (safe_value(x,depth+1) for x in v[:500]) if x not in (None,'',[],{})][:500]
    if isinstance(v,(str,int,float,bool)) or v is None:return v
    return str(v)[:300]

def payload_rows(payload):
    x=payload.get('payload') if isinstance(payload,dict) else payload
    for _ in range(4):
        if isinstance(x,dict) and 'payload' in x:x=x.get('payload')
        else:break
    if isinstance(x,list):raw=x
    elif isinstance(x,dict):
        lists=[v for v in x.values() if isinstance(v,list)]; raw=max(lists,key=len) if lists else [x]
    else:raw=[]
    out=[]
    for row in raw[:1000]:
        if not isinstance(row,dict):continue
        sanitized=safe_value(row)
        if isinstance(sanitized,dict) and sanitized:out.append(sanitized)
    return out

def schema_shape(payload):
    if isinstance(payload,dict):return {'type':'object','keys':sorted(str(k) for k in payload.keys() if not any(x in str(k).lower() for x in DROP_KEY_PARTS))[:80]}
    if isinstance(payload,list):return {'type':'array','count':len(payload),'first_keys':sorted(str(k) for k in payload[0].keys())[:80] if payload and isinstance(payload[0],dict) else []}
    return {'type':type(payload).__name__}

def sanitize_endpoint(url,payload):
    if 'InstrumentosOperables' in url:return {'kind':'InstrumentosOperables','rows':instrumentos_operables(payload)}
    if 'SubyacenteOpciones' in url:return {'kind':'SubyacenteOpciones','rows':option_underlyings(payload)}
    if 'DatosTecnicos' in url:return {'kind':'DatosTecnicos','row':bond_technical(payload)}
    if 'CaucionesOperables' in url:return {'kind':'CaucionesOperables','rows':payload_rows(payload),'schema':schema_shape(payload)}
    if 'ConfiguracionOperatoriaSimplificada' in url:return {'kind':'SCHEMA_ONLY','schema':schema_shape(payload)}
    return None

def main():
    assert_safe_route_catalog()
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--jobs',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default=os.getenv('POROTA_CHROME_EXECUTABLE','/usr/bin/google-chrome-stable')); args=ap.parse_args()
    jobs=[j for j in args.jobs.split(',') if j in ROUTES]
    out={'schema':'POROTA_RC6_PPI_TRUSTED_CONTRACT_V1','generated_at':datetime.now(timezone.utc).isoformat(),'auth_status':'UNKNOWN','jobs':jobs,'routes':[],'endpoints':{},'blocked_nonread':[],'continue_clicked':False,'amount_filled':False,'price_filled':False,'real_orders_sent':0}
    target=Path(args.output); target.parent.mkdir(parents=True,exist_ok=True)
    if not Path(args.profile).is_dir():
        out['auth_status']='BLOCKED_AUTH_PROFILE_MISSING'; target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600); return 4
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        out['auth_status']='BLOCKED_PLAYWRIGHT_UNAVAILABLE'; out['error']=type(exc).__name__; target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600); return 4
    try:
        with sync_playwright() as pw:
            ctx=pw.chromium.launch_persistent_context(user_data_dir=args.profile,executable_path=args.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
            def guard(route,request):
                method=request.method.upper()
                if method in SAFE_METHODS:return route.continue_()
                u=urlsplit(request.url); host=u.netloc.lower(); path=u.path.rstrip('/') or '/'
                ppi_hosts={'trading.portfoliopersonal.com','api.portfoliopersonal.com','cuenta.portfoliopersonal.com'}
                if host not in ppi_hosts:return route.abort()
                if method=='POST' and host=='trading.portfoliopersonal.com' and path=='/api/logger':return route.abort()
                if method=='POST' and host=='api.portfoliopersonal.com' and path=='/api/v1/zendesk/zendesk-session':return route.abort()
                out['blocked_nonread'].append({'method':method,'url':clean_url(request.url)}); return route.abort()
            ctx.route('**/*',guard)
            page=ctx.pages[0] if ctx.pages else ctx.new_page(); active={'job':'','route':''}
            def on_response(response):
                try:
                    if response.request.method.upper()!='GET' or response.status>=400 or not any(x in response.url for x in TARGETS):return
                    sanitized=sanitize_endpoint(response.url,response.json())
                    if sanitized is None:return
                    key=clean_url(response.url)+'|'+active['job']+'|'+active['route']; out['endpoints'][key]={'status':response.status,'source_url':clean_url(response.url),'observed_job':active['job'],'observed_route':active['route'],**sanitized}
                except Exception:pass
            page.on('response',on_response)
            page.goto(TRADING+'/',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(900)
            u=urlsplit(page.url); authenticated=u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() and 'logout' not in u.path.lower()
            if not authenticated:out['auth_status']='BLOCKED_AUTH_SESSION_EXPIRED'
            else:
                out['auth_status']='AUTHENTICATED_TRUSTED_DEVICE'; seen=set(); todo=[(job,route) for job in jobs for route in ROUTES[job] if not (route in seen or seen.add(route))]
                for job,route in todo:
                    active['job'],active['route']=job,route; row={'job':job,'requested':route,'reached':False}
                    try:
                        page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(900); pu=urlsplit(page.url); row['url']=clean_url(page.url); row['title']=page.title()[:180]; row['reached']=pu.netloc=='trading.portfoliopersonal.com' and 'login' not in pu.path.lower()
                    except Exception as exc:row['error']=type(exc).__name__
                    out['routes'].append(row)
            ctx.close()
    except Exception as exc:
        if out['auth_status']=='UNKNOWN':out['auth_status']='BLOCKED_BROWSER_ERROR'
        out['error']=type(exc).__name__+':'+str(exc)[:240]
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
    print(json.dumps({'state':out['auth_status'],'jobs':jobs,'routes':len(out['routes']),'endpoints':len(out['endpoints']),'blocked_nonread':len(out['blocked_nonread']),'real_orders_sent':0,'capture':str(target)},ensure_ascii=False,sort_keys=True))
    return 0 if out['auth_status']=='AUTHENTICATED_TRUSTED_DEVICE' else 4

if __name__=='__main__': raise SystemExit(main())
