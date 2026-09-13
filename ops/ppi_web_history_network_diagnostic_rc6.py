#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

TRADING='https://trading.portfoliopersonal.com'
SAFE_METHODS={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar')

def clean(u:str)->str:
    p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'

def forbidden(u:str)->bool:
    return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); args=ap.parse_args()
    out={'auth':'UNKNOWN','blocked':[],'get_responses':[],'controls':[],'real_orders_sent':0,'canonical_write':'DENY'}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=args.profile,executable_path=args.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
        def guard(route,req):
            m=req.method.upper(); u=clean(req.url)
            if forbidden(req.url): out['blocked'].append({'method':m,'url':u,'reason':'FORBIDDEN_PATH'}); return route.abort()
            if m not in SAFE_METHODS: out['blocked'].append({'method':m,'url':u,'reason':'NONREAD_METHOD'}); return route.abort()
            return route.continue_()
        ctx.route('**/*',guard)
        page=ctx.pages[0] if ctx.pages else ctx.new_page()
        def resp(r):
            try:
                if r.request.method.upper()!='GET': return
                u=clean(r.url); host=urlsplit(u).netloc.lower()
                if not (host.endswith('portfoliopersonal.com') or host.endswith('ppi.com.ar')): return
                out['get_responses'].append({'status':r.status,'url':u,'content_type':str(r.headers.get('content-type') or '').split(';')[0][:80]})
            except Exception: pass
        page.on('response',resp)
        page.goto(TRADING+'/',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(900)
        p=urlsplit(page.url)
        if p.netloc!='trading.portfoliopersonal.com' or 'login' in p.path.lower(): out['auth']='BLOCKED_AUTH_SESSION_EXPIRED'
        else:
            out['auth']='AUTHENTICATED_TRUSTED_DEVICE'
            page.goto(TRADING+'/Cotizaciones/Acciones',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(1000)
            boxes=page.locator('input[type="search"], input[placeholder*="Buscar" i], input[placeholder*="especie" i]')
            if boxes.count():
                try: boxes.first.fill('CRESD',timeout=2500); page.wait_for_timeout(1800)
                except Exception: pass
            # Collect only visible control text/href path; no clicks.
            for sel in ('a','button'):
                loc=page.locator(sel)
                for i in range(min(loc.count(),300)):
                    try:
                        el=loc.nth(i)
                        if not el.is_visible(): continue
                        txt=' '.join((el.inner_text(timeout=500) or '').split())[:120]
                        href=el.get_attribute('href') if sel=='a' else None
                        item={'tag':sel,'text':txt}
                        if href:
                            item['href_path']=urlsplit(href).path if '://' in href else href.split('?',1)[0]
                        out['controls'].append(item)
                    except Exception: pass
        ctx.close()
    # Aggregate to avoid leaking request multiplicity details beyond method/path counts.
    c=Counter((x['method'],x['url'],x['reason']) for x in out['blocked'])
    out['blocked_summary']=[{'method':m,'url':u,'reason':r,'count':n} for (m,u,r),n in c.most_common()]
    g=Counter((x['status'],x['url'],x['content_type']) for x in out['get_responses'])
    out['get_summary']=[{'status':s,'url':u,'content_type':ct,'count':n} for (s,u,ct),n in g.most_common()]
    out['blocked_count']=len(out['blocked']); out['get_count']=len(out['get_responses'])
    out.pop('blocked',None); out.pop('get_responses',None)
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(args.output,0o600)
    print('AUTH='+out['auth']); print('BLOCKED_COUNT='+str(out['blocked_count'])); print('BLOCKED_UNIQUE='+str(len(out['blocked_summary']))); print('GET_UNIQUE='+str(len(out['get_summary'])))
    for i,x in enumerate(out['blocked_summary'][:40]): print(f"BLOCKED_{i}={x['method']}|{x['url']}|count={x['count']}|{x['reason']}")
    for i,x in enumerate(out['get_summary'][:30]): print(f"GET_{i}={x['status']}|{x['url']}|{x['content_type']}|count={x['count']}")
    for i,x in enumerate(out['controls'][:80]): print(f"CONTROL_{i}={x.get('tag')}|{x.get('text','')}|{x.get('href_path','')}")
    print('REAL_ORDERS_SENT=0'); print('CANONICAL_WRITE=DENY')
    return 0 if out['auth']=='AUTHENTICATED_TRUSTED_DEVICE' else 4
if __name__=='__main__': raise SystemExit(main())
