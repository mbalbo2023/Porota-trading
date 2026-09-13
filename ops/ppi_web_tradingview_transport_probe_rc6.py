#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re
from pathlib import Path
from urllib.parse import urlsplit

BASE='https://trading.portfoliopersonal.com/Cotizaciones/Item/794828?plazo=2'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
HISTORY_WORDS=('hist','history','chart','graf','serie','timeseries','candle','ohlc','bar','quote','cotiz','price','symbol','market','range')
UI_WORDS=('rango','fecha','personalizado','desde','hasta','ir a','go to','date range','custom range','1d','5d','1m','3m','6m','1a','1y','ytd','all','todo')

def clean_url(u):
    p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'

def forbidden(u):
    return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)

def short(v,n=180):
    return re.sub(r'\s+',' ',str(v or '')).strip()[:n]

def candidate_attrs(scope, limit=250):
    out=[]
    sels=['button','[role="button"]','[role="menuitem"]','[role="option"]','input','[aria-label]','[title]','[data-name]','[data-tooltip]','[tabindex]']
    seen=set()
    for sel in sels:
        try:
            loc=scope.locator(sel)
            for i in range(min(loc.count(),limit)):
                el=loc.nth(i)
                try:
                    if not el.is_visible(): continue
                    d={
                        'sel':sel,
                        'text':short(el.inner_text(),140),
                        'aria':short(el.get_attribute('aria-label'),140),
                        'title':short(el.get_attribute('title'),140),
                        'data_name':short(el.get_attribute('data-name'),140),
                        'tooltip':short(el.get_attribute('data-tooltip'),140),
                        'type':short(el.get_attribute('type'),60),
                        'role':short(el.get_attribute('role'),60),
                    }
                    key=tuple(d.values())
                    if key in seen: continue
                    seen.add(key)
                    blob=' '.join(str(x) for x in d.values()).lower()
                    if any(w in blob for w in UI_WORDS): out.append(d)
                except Exception: pass
        except Exception: pass
    return out[:120]

def click_ui(scope, labels):
    attempts=[]
    for label in labels:
        strategies=[
            ('role_exact', lambda s,l=label:s.get_by_role('button',name=l,exact=True)),
            ('role_regex', lambda s,l=label:s.get_by_role('button',name=re.compile(re.escape(l),re.I))),
            ('text_exact', lambda s,l=label:s.get_by_text(l,exact=True)),
            ('button_text', lambda s,l=label:s.locator(f'button:has-text("{l}")')),
            ('aria', lambda s,l=label:s.locator(f'[aria-label*="{l}" i]')),
            ('title', lambda s,l=label:s.locator(f'[title*="{l}" i]')),
            ('data_name', lambda s,l=label:s.locator(f'[data-name*="{l}" i]')),
        ]
        for name,maker in strategies:
            try:
                loc=maker(scope); cnt=loc.count()
                for i in range(min(cnt,10)):
                    try:
                        if loc.nth(i).is_visible():
                            loc.nth(i).click(timeout=3500)
                            attempts.append({'label':label,'strategy':name,'clicked':True})
                            return {'label':label,'strategy':name,'clicked':True}, attempts
                    except Exception: pass
                attempts.append({'label':label,'strategy':name,'count':cnt,'clicked':False})
            except Exception as e:
                attempts.append({'label':label,'strategy':name,'error':type(e).__name__,'clicked':False})
    return None, attempts

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    out={
        'auth':'UNKNOWN','page':'','graph_click':None,'range_click':None,'range_attempts':[],
        'frames':[],'network':[],'ws':[],'blocked_nonread':[],'blocked_forbidden':[],
        'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO'
    }
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

        def record_response(r):
            try:
                req=r.request
                if req.method.upper()!='GET': return
                rt=req.resource_type
                p=urlsplit(r.url); path=p.path.lower(); host=p.netloc.lower()
                ct=short(r.headers.get('content-type'),100).lower()
                interesting=(rt in ('xhr','fetch','websocket') or 'json' in ct or any(w in path for w in HISTORY_WORDS))
                if not interesting: return
                rec={'host':host,'path':p.path[:220],'status':r.status,'resource_type':rt,'content_type':ct}
                if rec not in out['network'] and len(out['network'])<250: out['network'].append(rec)
            except Exception: pass
        page.on('response',record_response)

        def on_ws(ws):
            rec={'url':clean_url(ws.url),'received_frames':0,'sent_frames':0,'received_bytes':0,'sent_bytes':0,'keyword_hits':[]}
            out['ws'].append(rec)
            def recv(payload):
                try:
                    if isinstance(payload,bytes):
                        n=len(payload); text=''
                    else:
                        text=str(payload); n=len(text.encode('utf-8','ignore'))
                    rec['received_frames']+=1; rec['received_bytes']+=n
                    low=text.lower()
                    hits=[w for w in HISTORY_WORDS if w in low]
                    for h in hits:
                        if h not in rec['keyword_hits'] and len(rec['keyword_hits'])<20: rec['keyword_hits'].append(h)
                except Exception: pass
            def sent(payload):
                try:
                    if isinstance(payload,bytes): n=len(payload)
                    else: n=len(str(payload).encode('utf-8','ignore'))
                    rec['sent_frames']+=1; rec['sent_bytes']+=n
                except Exception: pass
            ws.on('framereceived',recv); ws.on('framesent',sent)
        page.on('websocket',on_ws)

        page.goto(BASE,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(7000)
        u=urlsplit(page.url); out['page']=clean_url(page.url)
        out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
        if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE':
            ctx.close(); target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); return 4

        # Open only the read-only chart tab.
        g,_=click_ui(page,['Graficador'])
        out['graph_click']=g
        page.wait_for_timeout(6000)

        # Inspect every frame. Prefer TradingView/blob frame for range controls.
        tv_frames=[]
        for fr in page.frames:
            try:
                info={'url':clean_url(fr.url),'name':short(fr.name,80),'candidates':candidate_attrs(fr)}
                out['frames'].append(info)
                lu=fr.url.lower(); ln=(fr.name or '').lower()
                if 'tradingview' in ln or lu.startswith('blob:') or 'tradingview' in lu: tv_frames.append(fr)
            except Exception: pass

        labels=['Rango de fechas','Rango personalizado','Date Range','Custom Range','Ir a','Go to']
        scopes=tv_frames + [page]
        for sc in scopes:
            clicked, attempts=click_ui(sc,labels)
            out['range_attempts'].extend(attempts)
            if clicked:
                out['range_click']=clicked
                page.wait_for_timeout(5000)
                break

        # Re-inspect after any click and allow transport activity to settle.
        out['frames_after']=[]
        for fr in page.frames:
            try:
                out['frames_after'].append({'url':clean_url(fr.url),'name':short(fr.name,80),'candidates':candidate_attrs(fr)})
            except Exception: pass
        page.wait_for_timeout(8000)
        ctx.close()

    target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
    print('AUTH='+out['auth'])
    print('PAGE='+out['page'])
    print('GRAPH_CLICK='+json.dumps(out['graph_click'],ensure_ascii=False))
    print('RANGE_CLICK='+json.dumps(out['range_click'],ensure_ascii=False))
    print('FRAMES='+str(len(out['frames'])))
    for i,f in enumerate(out['frames'][:10]):
        print(f'FRAME_{i}='+json.dumps({'url':f['url'],'name':f['name'],'candidates':f['candidates'][:20]},ensure_ascii=False,separators=(',',':')))
    print('NETWORK='+str(len(out['network'])))
    for i,n in enumerate(out['network'][:80]): print(f'NET_{i}='+json.dumps(n,ensure_ascii=False,separators=(',',':')))
    print('WEBSOCKETS='+str(len(out['ws'])))
    for i,w in enumerate(out['ws'][:20]): print(f'WS_{i}='+json.dumps(w,ensure_ascii=False,separators=(',',':')))
    print('BLOCKED_NONREAD='+str(len(out['blocked_nonread'])))
    print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden'])))
    print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY'); print('MASS_SCRAPING_STARTED=NO')
    return 0

if __name__=='__main__': raise SystemExit(main())
