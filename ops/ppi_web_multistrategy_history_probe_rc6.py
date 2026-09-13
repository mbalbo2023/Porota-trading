#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re
from pathlib import Path
from urllib.parse import urlsplit

BASE='https://trading.portfoliopersonal.com/Cotizaciones/Item/794828'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
PATTERNS=('rango de fechas','rango personalizado','personalizado','desde','hasta','ir a','1d','5d','1m','3m','6m','1a','1y','graficador','gráfico','grafico')

def clean_url(u):
    p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'

def forbidden(u):
    return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)

def short(s,n=180):
    return re.sub(r'\s+',' ',str(s or '')).strip()[:n]

def controls(scope):
    out=[]
    sels=['button','[role="button"]','[role="menuitem"]','[role="option"]','a','input','select','[tabindex]']
    for sel in sels:
        try:
            loc=scope.locator(sel)
            for i in range(min(loc.count(),250)):
                el=loc.nth(i)
                try:
                    if not el.is_visible(): continue
                    txt=short(el.inner_text())
                    aria=short(el.get_attribute('aria-label'))
                    title=short(el.get_attribute('title'))
                    ph=short(el.get_attribute('placeholder'))
                    typ=short(el.get_attribute('type'))
                    role=short(el.get_attribute('role'))
                    blob=' '.join([txt,aria,title,ph]).lower()
                    if any(p in blob for p in PATTERNS):
                        out.append({'sel':sel,'text':txt,'aria':aria,'title':title,'placeholder':ph,'type':typ,'role':role})
                except Exception: pass
        except Exception: pass
    return out[:120]

def click_first(scope, strategies):
    attempts=[]
    for name, maker in strategies:
        try:
            loc=maker(scope)
            cnt=loc.count()
            vis=False
            if cnt:
                for i in range(min(cnt,20)):
                    try:
                        if loc.nth(i).is_visible():
                            vis=True
                            loc.nth(i).click(timeout=5000)
                            attempts.append({'strategy':name,'count':cnt,'visible':True,'clicked':True})
                            return name, attempts
                    except Exception: pass
            attempts.append({'strategy':name,'count':cnt,'visible':vis,'clicked':False})
        except Exception as e:
            attempts.append({'strategy':name,'error':type(e).__name__,'clicked':False})
    return None, attempts

def extract_plazo_text(page):
    try:
        body=short(page.locator('body').inner_text(),20000)
    except Exception:
        body=''
    hits=[]
    for m in re.finditer(r'(?i)(CRESD.{0,120}?Plazo.{0,60})', body):
        hits.append(short(m.group(1),220))
    if not hits:
        for m in re.finditer(r'(?i)(Plazo.{0,80})', body):
            hits.append(short(m.group(1),180))
    return hits[:8]

def frame_info(page):
    out=[]
    for fr in page.frames:
        try:
            out.append({'url':clean_url(fr.url),'name':short(fr.name,80),'controls':controls(fr)[:40]})
        except Exception: pass
    return out[:20]

def shadow_info(page):
    try:
        return page.evaluate("""
        () => Array.from(document.querySelectorAll('*')).filter(e=>e.shadowRoot).slice(0,50).map(e=>({
          tag:e.tagName, id:e.id||'', cls:(e.className||'').toString().slice(0,120),
          text:(e.shadowRoot.innerText||'').replace(/\\s+/g,' ').trim().slice(0,240)
        }))
        """)
    except Exception:
        return []

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    out={'auth':'UNKNOWN','settlements':{},'strategies':{},'pages':{},'blocked_nonread':[],'blocked_forbidden':[],'get_hosts':{},'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO'}
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
        def resp(r):
            try:
                if r.request.method.upper()!='GET': return
                p=urlsplit(r.url); host=p.netloc.lower(); path=p.path
                key=host
                d=out['get_hosts'].setdefault(key,{'count':0,'paths':[]})
                d['count']+=1
                if len(d['paths'])<30 and any(x in path.lower() for x in ('hist','chart','graf','serie','cotiz','price','quote','market','symbol')):
                    d['paths'].append(path[:220])
            except Exception: pass
        page.on('response',resp)

        # First: settlement mapping on both variants, no interaction beyond page load.
        for plazo in ('1','2'):
            page.goto(f'{BASE}?plazo={plazo}',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(7000)
            u=urlsplit(page.url)
            auth = u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower()
            out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH_SESSION_EXPIRED'
            out['settlements'][plazo]={'page':clean_url(page.url),'text':extract_plazo_text(page)}

        # Multi-strategy on plazo=2.
        page.goto(f'{BASE}?plazo=2',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(7000)
        pre=controls(page)
        graph_strategies=[
            ('role_button_exact', lambda s:s.get_by_role('button',name='Graficador',exact=True)),
            ('text_exact', lambda s:s.get_by_text('Graficador',exact=True)),
            ('button_has_text', lambda s:s.locator('button:has-text("Graficador")')),
            ('role_has_text', lambda s:s.locator('[role="button"]:has-text("Graficador")')),
            ('xpath_text', lambda s:s.locator('xpath=//*[normalize-space(text())="Graficador"]')),
            ('text_engine', lambda s:s.locator('text=Graficador')),
        ]
        chosen, attempts=click_first(page,graph_strategies)
        out['strategies']['graficador']={'chosen':chosen,'attempts':attempts}
        page.wait_for_timeout(5000)
        post=controls(page)

        range_strategies=[
            ('role_button_range', lambda s:s.get_by_role('button',name=re.compile('Rango de fechas',re.I))),
            ('text_range_exact', lambda s:s.get_by_text('Rango de fechas',exact=True)),
            ('button_range_text', lambda s:s.locator('button:has-text("Rango de fechas")')),
            ('role_range_text', lambda s:s.locator('[role="button"]:has-text("Rango de fechas")')),
            ('xpath_range', lambda s:s.locator('xpath=//*[contains(normalize-space(.),"Rango de fechas")]')),
        ]
        rchosen, rattempts=click_first(page,range_strategies)
        out['strategies']['range']={'chosen':rchosen,'attempts':rattempts}
        page.wait_for_timeout(3000)
        after_range=controls(page)

        # Inspect page, frames and shadow roots as fallbacks. No further click/submit.
        out['pages']['plazo2']={
            'url':clean_url(page.url),
            'controls_before':pre[:80],
            'controls_after_graficador':post[:120],
            'controls_after_range':after_range[:160],
            'frames':frame_info(page),
            'shadow_roots':shadow_info(page),
            'inputs':[{
                'type':short(page.locator('input').nth(i).get_attribute('type')),
                'placeholder':short(page.locator('input').nth(i).get_attribute('placeholder')),
                'aria':short(page.locator('input').nth(i).get_attribute('aria-label')),
                'name':short(page.locator('input').nth(i).get_attribute('name')),
            } for i in range(min(page.locator('input').count(),30))]
        }
        ctx.close()
    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
    print('AUTH='+out['auth'])
    for p in ('1','2'): print('PLAZO_'+p+'='+json.dumps(out['settlements'][p],ensure_ascii=False))
    print('GRAFICADOR_STRATEGY='+str(out['strategies']['graficador']['chosen']))
    print('RANGE_STRATEGY='+str(out['strategies']['range']['chosen']))
    c=out['pages']['plazo2']; print('POST_GRAPH_CONTROLS='+str(len(c['controls_after_graficador']))); print('POST_RANGE_CONTROLS='+str(len(c['controls_after_range']))); print('FRAMES='+str(len(c['frames']))); print('SHADOW_ROOTS='+str(len(c['shadow_roots'])))
    for i,x in enumerate(c['controls_after_range'][:40]): print(f'CONTROL_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
    for i,x in enumerate(c['frames'][:10]): print(f'FRAME_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
    print('BLOCKED_NONREAD='+str(len(out['blocked_nonread']))); print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden']))); print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY'); print('MASS_SCRAPING_STARTED=NO')
    return 0 if out['auth']=='AUTHENTICATED_TRUSTED_DEVICE' else 4
if __name__=='__main__': raise SystemExit(main())
