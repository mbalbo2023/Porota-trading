#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re
from pathlib import Path
from urllib.parse import urlsplit

BASE='https://trading.portfoliopersonal.com/Cotizaciones/Item/794828?plazo=2'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
WORDS=('tabla','table','data window','ventana de datos','export','exportar','csv','download','descargar','chart data','datos del gráfico','datos del grafico')

def clean_url(u):
    p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'

def forbidden(u):
    return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)

def short(v,n=180):
    return re.sub(r'\s+',' ',str(v or '')).strip()[:n]

def scan(scope):
    out=[]; seen=set()
    sels=['button','[role="button"]','[role="menuitem"]','[role="option"]','a','[aria-label]','[title]','[data-name]','[data-tooltip]','[tabindex]']
    for sel in sels:
        try:
            loc=scope.locator(sel)
            for i in range(min(loc.count(),350)):
                el=loc.nth(i)
                try:
                    if not el.is_visible(): continue
                    d={'sel':sel,'text':short(el.inner_text(),160),'aria':short(el.get_attribute('aria-label'),160),'title':short(el.get_attribute('title'),160),'data_name':short(el.get_attribute('data-name'),160),'tooltip':short(el.get_attribute('data-tooltip'),160),'href':short(el.get_attribute('href'),220),'role':short(el.get_attribute('role'),60)}
                    blob=' '.join(map(str,d.values())).lower()
                    if any(w in blob for w in WORDS):
                        key=tuple(d.values())
                        if key not in seen: seen.add(key); out.append(d)
                except Exception: pass
        except Exception: pass
    return out[:150]

def click_exact(scope,label):
    for maker in [
        lambda s:s.get_by_role('button',name=label,exact=True),
        lambda s:s.get_by_text(label,exact=True),
        lambda s:s.locator(f'[aria-label*="{label}" i]'),
        lambda s:s.locator(f'[title*="{label}" i]'),
    ]:
        try:
            loc=maker(scope)
            for i in range(min(loc.count(),10)):
                if loc.nth(i).is_visible(): loc.nth(i).click(timeout=3500); return True
        except Exception: pass
    return False

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    out={'auth':'UNKNOWN','page':'','graph':False,'frames':[],'context_candidates':[],'blocked_nonread':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO'}
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
        page.goto(BASE,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(7000)
        u=urlsplit(page.url); out['page']=clean_url(page.url)
        out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
        if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE':
            ctx.close(); target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); return 4
        out['graph']=click_exact(page,'Graficador'); page.wait_for_timeout(5000)
        tv=[]
        for fr in page.frames:
            try:
                info={'url':clean_url(fr.url),'name':short(fr.name,80),'candidates':scan(fr)}
                out['frames'].append(info)
                if 'tradingview' in (fr.name or '').lower() or fr.url.lower().startswith('blob:'): tv.append(fr)
            except Exception: pass
        # Right-click chart area to expose TradingView context menu, read-only.
        for fr in tv:
            try:
                chart=fr.locator('[data-name="pane-widget-chart-gui-wrapper"]')
                if chart.count() and chart.first.is_visible():
                    chart.first.click(button='right',timeout=3500); page.wait_for_timeout(1500)
                    out['context_candidates']=scan(fr)
                    break
            except Exception: pass
        ctx.close()
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
    print('AUTH='+out['auth']); print('PAGE='+out['page']); print('GRAPH='+str(out['graph']))
    print('FRAMES='+str(len(out['frames'])))
    for i,f in enumerate(out['frames'][:10]): print(f'FRAME_{i}='+json.dumps({'url':f['url'],'name':f['name'],'candidates':f['candidates'][:30]},ensure_ascii=False,separators=(',',':')))
    print('CONTEXT_CANDIDATES='+str(len(out['context_candidates'])))
    for i,x in enumerate(out['context_candidates'][:60]): print(f'CTX_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
    print('BLOCKED_NONREAD='+str(len(out['blocked_nonread']))); print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden']))); print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY'); print('MASS_SCRAPING_STARTED=NO')
    return 0
if __name__=='__main__': raise SystemExit(main())
