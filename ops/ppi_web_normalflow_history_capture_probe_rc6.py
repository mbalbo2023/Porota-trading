#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re, hashlib
from pathlib import Path
from urllib.parse import urlsplit

BASE='https://trading.portfoliopersonal.com/Cotizaciones/Item/794828?plazo=2'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
SENSITIVE_NAMES=('authorization','cookie','set-cookie','x-xsrf-token','x-csrf-token','x-api-key','api-key','token')
DATE_KEYS=('date','fecha','time','timestamp','datetime')

def clean_url(u):
    p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'

def forbidden(u):
    return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)

def safe_header_summary(headers):
    h={str(k).lower():str(v) for k,v in (headers or {}).items()}
    names=sorted(h.keys())
    sensitive_present={n: any((n==k) or (n in k) for k in names) for n in SENSITIVE_NAMES}
    keep={}
    for k in ('accept','content-type','origin','referer','sec-fetch-dest','sec-fetch-mode','sec-fetch-site','user-agent'):
        if k in h:
            v=h[k]
            if k=='referer': v=clean_url(v)
            if k=='user-agent': v=v[:120]
            keep[k]=v[:220]
    return {'header_names':[n for n in names if not any(s in n for s in SENSITIVE_NAMES)][:80], 'safe_values':keep, 'sensitive_present':sensitive_present}

def summarize_json(obj):
    out={'type':type(obj).__name__,'rows':0,'top_keys':[],'row_keys':[],'first_date':None,'last_date':None}
    rows=[]
    if isinstance(obj,list):
        rows=obj
    elif isinstance(obj,dict):
        out['top_keys']=list(obj.keys())[:40]
        for key in ('data','result','results','items','historico','history','series','values'):
            v=obj.get(key)
            if isinstance(v,list): rows=v; break
        if not rows:
            for v in obj.values():
                if isinstance(v,list) and v and isinstance(v[0],dict): rows=v; break
    out['rows']=len(rows)
    if rows and isinstance(rows[0],dict):
        out['row_keys']=list(rows[0].keys())[:40]
        dates=[]
        for r in rows:
            if not isinstance(r,dict): continue
            for k in DATE_KEYS:
                if k in r and r.get(k) not in (None,''):
                    dates.append(str(r.get(k))); break
        if dates:
            out['first_date']=dates[0][:80]; out['last_date']=dates[-1][:80]
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    out={'auth':'UNKNOWN','page':'','normal_history':[],'direct_fetch':None,'blocked_nonread':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO'}
    target=Path(a.output)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
        def guard(route,request):
            m=request.method.upper(); u=request.url
            if forbidden(u): out['blocked_forbidden'].append({'method':m,'url':clean_url(u)}); return route.abort()
            if m not in SAFE: out['blocked_nonread'].append({'method':m,'url':clean_url(u)}); return route.abort()
            return route.continue_()
        ctx.route('**/*',guard)
        page=ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(r):
            try:
                p=urlsplit(r.url)
                if '/api/Cotizaciones/Item/' not in p.path or '/Historico/' not in p.path: return
                req=r.request
                rec={'status':r.status,'method':req.method,'url':clean_url(r.url),'resource_type':req.resource_type,'request':safe_header_summary(req.all_headers()),'response_content_type':str(r.headers.get('content-type',''))[:120]}
                try:
                    body=r.body()
                    rec['bytes']=len(body); rec['sha256']=hashlib.sha256(body).hexdigest()
                    if 'json' in rec['response_content_type'].lower():
                        obj=json.loads(body.decode('utf-8','replace')); rec['json']=summarize_json(obj)
                except Exception as e:
                    rec['body_error']=type(e).__name__
                out['normal_history'].append(rec)
            except Exception: pass
        page.on('response',on_response)

        page.goto(BASE,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(7000)
        u=urlsplit(page.url); out['page']=clean_url(page.url)
        out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
        if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE':
            ctx.close(); target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); return 4

        # Trigger the same read-only application flow that previously produced HTTP 200.
        try:
            page.get_by_role('button',name='Graficador',exact=True).click(timeout=6000)
        except Exception:
            try: page.get_by_text('Graficador',exact=True).click(timeout=6000)
            except Exception: pass
        page.wait_for_timeout(9000)

        # If normal flow succeeded, compare with a plain browser fetch from the same authenticated page.
        hist=[x for x in out['normal_history'] if x.get('status')==200]
        if hist:
            url=hist[-1]['url']
            try:
                direct=page.evaluate("""async (u) => { const r=await fetch(u,{method:'GET',credentials:'include'}); return {status:r.status, contentType:r.headers.get('content-type')||'', text:(await r.text()).slice(0,200)} }""", url)
                out['direct_fetch']={'status':direct.get('status'),'content_type':direct.get('contentType',''),'body_preview_kind':'JSON_OR_TEXT_REDACTED','body_chars':len(direct.get('text',''))}
            except Exception as e:
                out['direct_fetch']={'error':type(e).__name__}
            page.wait_for_timeout(3000)
        ctx.close()

    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
    print('AUTH='+out['auth']); print('PAGE='+out['page']); print('NORMAL_HISTORY_COUNT='+str(len(out['normal_history'])))
    for i,r in enumerate(out['normal_history'][:10]):
        print(f'HIST_{i}='+json.dumps(r,ensure_ascii=False,separators=(',',':')))
    print('DIRECT_FETCH='+json.dumps(out['direct_fetch'],ensure_ascii=False,separators=(',',':')))
    print('BLOCKED_NONREAD='+str(len(out['blocked_nonread']))); print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden'])))
    print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY'); print('MASS_SCRAPING_STARTED=NO')
    return 0 if out['auth']=='AUTHENTICATED_TRUSTED_DEVICE' else 4
if __name__=='__main__': raise SystemExit(main())
