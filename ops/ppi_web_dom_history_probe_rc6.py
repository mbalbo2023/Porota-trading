#!/usr/bin/env python3
import argparse,json,os,re
from pathlib import Path
from urllib.parse import urlsplit

TRADING='https://trading.portfoliopersonal.com'
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar')
KEYWORDS=('CRESD','hist','gráf','graf','cotiz','detalle','precio','evolu','chart','ver')

def clean_url(u):
    p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u): return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def norm(s): return ' '.join(str(s or '').split())[:180]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    out={'auth':'UNKNOWN','symbol':'CRESD','canonical_write':'DENY','real_orders_sent':0,'before_controls':[],'after_controls':[],'get_json':[],'blocked_nonread':[],'clicked':None,'page_before':None,'page_after':None}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
        def guard(route,request):
            m=request.method.upper(); u=clean_url(request.url)
            if forbidden(request.url) or m not in {'GET','HEAD','OPTIONS'}:
                out['blocked_nonread'].append({'method':m,'url':u}); return route.abort()
            route.continue_()
        ctx.route('**/*',guard)
        page=ctx.pages[0] if ctx.pages else ctx.new_page()
        def resp(r):
            try:
                if r.request.method.upper()!='GET' or r.status>=400:return
                u=clean_url(r.url); h=urlsplit(u).netloc.lower()
                if not (h.endswith('portfoliopersonal.com') or h.endswith('ppi.com.ar')):return
                ct=(r.headers.get('content-type') or '').lower()
                if 'json' in ct: out['get_json'].append({'url':u,'status':r.status})
            except Exception: pass
        page.on('response',resp)
        page.goto(TRADING+'/Cotizaciones/Acciones',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(1200)
        if urlsplit(page.url).netloc!='trading.portfoliopersonal.com' or 'login' in urlsplit(page.url).path.lower(): out['auth']='EXPIRED'
        else: out['auth']='AUTHENTICATED_TRUSTED_DEVICE'
        boxes=page.locator('input[type="search"], input[placeholder*="Buscar" i], input[placeholder*="especie" i]')
        if boxes.count():
            try: boxes.first.fill('CRESD',timeout=3000); page.wait_for_timeout(1600)
            except Exception: pass
        out['page_before']=clean_url(page.url)
        def controls():
            vals=[]
            loc=page.locator('a,button,[role="button"],[role="tab"]')
            for i in range(min(loc.count(),500)):
                try:
                    e=loc.nth(i)
                    if not e.is_visible(): continue
                    txt=norm(e.inner_text(timeout=500)); href=e.get_attribute('href') or ''; aria=e.get_attribute('aria-label') or ''; title=e.get_attribute('title') or ''
                    blob=(txt+' '+aria+' '+title).lower()
                    if not any(k.lower() in blob for k in KEYWORDS): continue
                    vals.append({'tag':e.evaluate('(x)=>x.tagName'),'text':txt,'aria':norm(aria),'title':norm(title),'href':clean_url(href) if href.startswith('http') else href.split('?')[0][:180]})
                except Exception: pass
            return vals[:80]
        out['before_controls']=controls()
        # Safe click only on an unambiguous visible CRESD element. Never click operation-like controls.
        candidates=page.get_by_text(re.compile(r'^\s*CRESD\s*$',re.I))
        for i in range(min(candidates.count(),10)):
            try:
                e=candidates.nth(i)
                if not e.is_visible(): continue
                href=e.get_attribute('href') or ''
                if href and forbidden(href): continue
                e.click(timeout=2500); out['clicked']={'text':'CRESD','href':clean_url(href) if href.startswith('http') else href.split('?')[0]}; page.wait_for_timeout(1800); break
            except Exception: continue
        out['page_after']=clean_url(page.url)
        out['after_controls']=controls()
        ctx.close()
    # de-duplicate paths only
    seen=[]
    for x in out['get_json']:
        k=(x['url'],x['status'])
        if k not in [(y['url'],y['status']) for y in seen]: seen.append(x)
    out['get_json']=seen[:100]
    Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(a.output,0o600)
    print('AUTH='+out['auth']); print('PAGE_BEFORE='+str(out['page_before'])); print('CLICKED='+json.dumps(out['clicked'],ensure_ascii=False)); print('PAGE_AFTER='+str(out['page_after']))
    print('BEFORE_CONTROLS='+str(len(out['before_controls']))); [print('BEFORE_'+str(i)+'='+json.dumps(x,ensure_ascii=False)) for i,x in enumerate(out['before_controls'])]
    print('AFTER_CONTROLS='+str(len(out['after_controls']))); [print('AFTER_'+str(i)+'='+json.dumps(x,ensure_ascii=False)) for i,x in enumerate(out['after_controls'])]
    print('GET_JSON='+str(len(out['get_json']))); [print('JSON_'+str(i)+'='+json.dumps(x,ensure_ascii=False)) for i,x in enumerate(out['get_json'])]
    print('BLOCKED_NONREAD='+str(len(out['blocked_nonread']))); print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY')
    return 0
if __name__=='__main__': raise SystemExit(main())