#!/usr/bin/env python3
"""Authenticated trusted-device PPI contract collector for RC4.

Safety properties:
- never accepts username/password/OTP arguments;
- uses an already-authenticated persistent Chrome profile only;
- aborts every non-GET/HEAD/OPTIONS request;
- never fills quantity/price and never clicks Continue/Confirm;
- persists only sanitized endpoint payloads and non-sensitive route metadata.
"""
from __future__ import annotations
import argparse, json, os, re, stat
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from rc4_ppi_contract_normalizer import instrumentos_operables, option_underlyings, bond_technical

TRADING='https://trading.portfoliopersonal.com'
SAFE_METHODS={'GET','HEAD','OPTIONS'}
TARGETS=('InstrumentosOperables','CaucionesOperables','ConfiguracionOperatoriaSimplificada','SubyacenteOpciones','DatosTecnicos')
ROUTES={
 'CONTRACT_EVIDENCE_DYNAMIC':['/Cotizaciones/Bonos'],
 'CONTRACT_EVIDENCE_CAUCIONES':['/Cotizaciones/Cauciones'],
 'CONTRACT_EVIDENCE_AUCTIONS':['/Cotizaciones/Licitaciones'],
 'CONTRACT_EVIDENCE_DERIVATIVES':['/Cotizaciones/Opciones','/Cotizaciones/Futuros'],
 'CONTRACT_EVIDENCE_STATIC':['/Cotizaciones/Acciones','/Cotizaciones/Cedears','/Cotizaciones/Bonos','/Cotizaciones/Letras','/Cotizaciones/Ons','/Cotizaciones/FCIs'],
 'CONTRACT_EVIDENCE_FULL_BROWSER':['/Cotizaciones/FCIs','/Cotizaciones/FCIsExterior','/Cotizaciones/Acciones','/Cotizaciones/AccionesUSA','/Cotizaciones/Bonos','/Cotizaciones/Cauciones','/Cotizaciones/Cedears','/Cotizaciones/ETFs','/Cotizaciones/Futuros','/Cotizaciones/Letras','/Cotizaciones/Licitaciones','/Cotizaciones/Ons','/Cotizaciones/Opciones','/Cotizaciones/Indices','/Cotizaciones/Monedas','/Cotizaciones/Tasas'],
}

def clean_url(url):
    u=urlsplit(str(url)); return f'{u.scheme}://{u.netloc}{u.path}'

def safe_controls(page):
    rows=[]
    try:
        for i in range(min(page.locator('input,select,button,a').count(),500)):
            e=page.locator('input,select,button,a').nth(i)
            try:
                if not e.is_visible(): continue
                tag=e.evaluate('(el)=>el.tagName.toLowerCase()')
                typ=e.get_attribute('type') or ''
                name=e.get_attribute('name') or e.get_attribute('id') or ''
                text=' '.join((e.inner_text() or '').split())[:120] if tag in {'button','a'} else ''
                # Never persist values/placeholders/autocomplete/account fields.
                rows.append({'tag':tag,'type':typ,'name':name[:120],'text':text})
            except Exception: pass
    except Exception: pass
    return rows[:200]

def sanitize_endpoint(url,payload):
    if 'InstrumentosOperables' in url:
        return {'kind':'InstrumentosOperables','rows':instrumentos_operables(payload)}
    if 'SubyacenteOpciones' in url:
        return {'kind':'SubyacenteOpciones','rows':option_underlyings(payload)}
    if 'DatosTecnicos' in url:
        return {'kind':'DatosTecnicos','row':bond_technical(payload)}
    # Unknown/account-sensitive schemas are retained only as shape, not values.
    if 'CaucionesOperables' in url or 'ConfiguracionOperatoriaSimplificada' in url:
        shape={}
        if isinstance(payload,dict): shape={'type':'object','keys':sorted(str(k) for k in payload.keys())[:80]}
        elif isinstance(payload,list): shape={'type':'array','count':len(payload),'first_keys':sorted(str(k) for k in payload[0].keys())[:80] if payload and isinstance(payload[0],dict) else []}
        else: shape={'type':type(payload).__name__}
        return {'kind':'SCHEMA_ONLY','schema':shape}
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--profile',required=True)
    ap.add_argument('--jobs',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--chrome',default=os.getenv('POROTA_CHROME_EXECUTABLE','/usr/bin/google-chrome-stable'))
    args=ap.parse_args()
    jobs=[j for j in args.jobs.split(',') if j in ROUTES]
    out={'schema':'POROTA_RC4_PPI_TRUSTED_CONTRACT_1','generated_at':datetime.now(timezone.utc).isoformat(),
         'auth_status':'UNKNOWN','jobs':jobs,'routes':[],'endpoints':{},'blocked_nonread':[],
         'continue_clicked':False,'amount_filled':False,'price_filled':False,'real_orders_sent':0}
    target=Path(args.output); target.parent.mkdir(parents=True,exist_ok=True)
    if not Path(args.profile).is_dir():
        out['auth_status']='BLOCKED_AUTH_PROFILE_MISSING'; target.write_text(json.dumps(out,ensure_ascii=False,indent=2)); os.chmod(target,0o600); return 0
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        out['auth_status']='BLOCKED_PLAYWRIGHT_UNAVAILABLE'; out['error']=type(exc).__name__; target.write_text(json.dumps(out,ensure_ascii=False,indent=2)); os.chmod(target,0o600); return 0
    try:
        with sync_playwright() as pw:
            ctx=pw.chromium.launch_persistent_context(user_data_dir=args.profile,executable_path=args.chrome,headless=True,
                locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
            def guard(route,request):
                method=request.method.upper()
                if method not in SAFE_METHODS:
                    out['blocked_nonread'].append({'method':method,'url':clean_url(request.url)})
                    return route.abort()
                return route.continue_()
            ctx.route('**/*',guard)
            page=ctx.pages[0] if ctx.pages else ctx.new_page()
            def on_response(response):
                try:
                    if response.request.method.upper()!='GET' or not any(x in response.url for x in TARGETS): return
                    if response.status>=400: return
                    payload=response.json(); sanitized=sanitize_endpoint(response.url,payload)
                    if sanitized is not None: out['endpoints'][clean_url(response.url)]={'status':response.status,**sanitized}
                except Exception: pass
            page.on('response',on_response)
            page.goto(TRADING+'/',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(900)
            u=urlsplit(page.url)
            if u.netloc!='trading.portfoliopersonal.com' or 'login' in u.path.lower() or 'logout' in u.path.lower():
                out['auth_status']='BLOCKED_AUTH_SESSION_EXPIRED'
            else:
                out['auth_status']='AUTHENTICATED_TRUSTED_DEVICE'
                seen=set()
                for job in jobs:
                    for route in ROUTES[job]:
                        if route in seen: continue
                        seen.add(route)
                        row={'job':job,'requested':route,'reached':False}
                        try:
                            page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(900)
                            pu=urlsplit(page.url); row['url']=clean_url(page.url); row['title']=page.title()[:180]
                            row['reached']=pu.netloc=='trading.portfoliopersonal.com' and 'login' not in pu.path.lower()
                            row['controls']=safe_controls(page)
                        except Exception as exc: row['error']=type(exc).__name__
                        out['routes'].append(row)
            ctx.close()
    except Exception as exc:
        if out['auth_status']=='UNKNOWN': out['auth_status']='BLOCKED_BROWSER_ERROR'
        out['error']=type(exc).__name__+':'+str(exc)[:240]
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
    return 0
if __name__=='__main__': raise SystemExit(main())
