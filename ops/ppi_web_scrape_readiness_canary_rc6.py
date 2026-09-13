#!/usr/bin/env python3
import argparse, json, re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

TRADING='https://trading.portfoliopersonal.com'
API='api.portfoliopersonal.com'
TARGETS=[('CEDEARS','AVYC'),('BONOS','TX28D'),('ON','MRCTO'),('OPCIONES','YPFV6100OC'),('FUTUROS','DLR/AGO27M'),('FCI','PI.RENT.B')]
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')

def path(u): return urlsplit(str(u)).path

def forbidden(u):
    p=path(u).lower(); return any(x in p for x in FORBIDDEN)

def find_candidate(obj,sym):
    out=[]
    def walk(x,d=0):
        if d>10:return
        if isinstance(x,dict):
            tick=x.get('ticker') or x.get('simbolo') or x.get('symbol') or x.get('codigo')
            desc=x.get('descripcion') or x.get('description') or x.get('nombre') or x.get('name') or ''
            iid=x.get('id') or x.get('itemId') or x.get('instrumentId')
            typ=x.get('tipoItem') or x.get('instrumentType') or {}
            tid=(typ.get('id') if isinstance(typ,dict) else None) or x.get('tipoItemId') or x.get('typeId') or x.get('instrumentTypeId')
            tdesc=(typ.get('descripcion') if isinstance(typ,dict) else '') or (typ.get('description') if isinstance(typ,dict) else '')
            if iid is not None and (str(tick or '').upper()==sym.upper() or sym.upper() in f'{tick} {desc}'.upper()):
                out.append({'id':str(iid),'ticker':str(tick or sym),'desc':str(desc),'type_id':None if tid is None else str(tid),'type_desc':str(tdesc),'exact':str(tick or '').upper()==sym.upper()})
            for v in x.values(): walk(v,d+1)
        elif isinstance(x,list):
            for v in x[:5000]: walk(v,d+1)
    walk(obj)
    uniq={ (x['id'],x['type_id'],x['ticker']):x for x in out }
    arr=list(uniq.values()); arr.sort(key=lambda x:(not x['exact'],x['id'])); return arr

def payload_rows(data):
    if isinstance(data,dict):
        for k in ('payload','data','items','results','result','historico','history','series'):
            if isinstance(data.get(k),list): return data[k]
    return data if isinstance(data,list) else []

def rowdate(r):
    if not isinstance(r,dict): return None
    for k in ('fechaCotizacion','fecha','date','datetime'):
        v=r.get(k)
        if v:
            s=str(v).replace('Z','+00:00')
            try:return datetime.fromisoformat(s)
            except: pass
    return None

def click_graph(page):
    for loc in [page.get_by_role('button',name='Graficador',exact=True),page.get_by_text('Graficador',exact=True),page.locator('button:has-text("Graficador")')]:
        try:
            for i in range(min(loc.count(),6)):
                if loc.nth(i).is_visible(): loc.nth(i).click(timeout=4000); return True
        except: pass
    return False

def visible_searches(page):
    sels=['input[placeholder="Buscar instrumento"]','input[placeholder*="Buscar instrumento" i]','input[placeholder*="Buscar" i]','input[aria-label*="Buscar" i]']
    out=[]
    for s in sels:
        try:
            loc=page.locator(s)
            for i in range(min(loc.count(),20)):
                el=loc.nth(i)
                if el.is_visible(): out.append(el)
        except: pass
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); a=ap.parse_args()
    blocked_nonread=blocked_forbidden=0
    cutoff=datetime.now(timezone.utc)-timedelta(days=365)
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path='/usr/bin/google-chrome-stable',headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
        def guard(route,req):
            nonlocal blocked_nonread,blocked_forbidden
            if forbidden(req.url): blocked_forbidden+=1; return route.abort()
            if req.method.upper() not in ('GET','HEAD','OPTIONS'): blocked_nonread+=1; return route.abort()
            return route.continue_()
        ctx.route('**/*',guard)
        p=ctx.pages[0] if ctx.pages else ctx.new_page()
        p.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); p.wait_for_timeout(2500)
        auth=urlsplit(p.url).netloc=='trading.portfoliopersonal.com' and 'login' not in path(p.url).lower()
        print('PPI_WEB_SCRAPE_READINESS_CANARY'); print('AUTH='+('AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH_SESSION_EXPIRED'))
        if not auth: return 20
        print('HISTORY_HORIZON_POLICY=PREVIOUS_365D')
        resolved=[]
        for fam,sym in TARGETS:
            hits=[]; eps=[]
            def onresp(resp):
                try:
                    if resp.request.method.upper()!='GET': return
                    u=urlsplit(resp.url)
                    if u.netloc!=API: return
                    ct=(resp.headers.get('content-type') or '').lower()
                    if 'json' not in ct: return
                    body=resp.body()
                    if len(body)>2500000:return
                    data=json.loads(body.decode('utf-8'))
                    cand=find_candidate(data,sym)
                    if cand:
                        eps.append((resp.status,u.path)); hits.extend(cand)
                except: pass
            p.on('response',onresp)
            for route in ('/Cotizaciones/WatchList','/Mercado','/estadoDeCuenta'):
                try:
                    p.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000); p.wait_for_timeout(1800)
                    for inp in visible_searches(p)[:4]:
                        try:
                            inp.click(timeout=2000); inp.fill(''); inp.type(sym,delay=80); p.wait_for_timeout(2500)
                            try: inp.press('ArrowDown'); inp.press('Enter')
                            except: pass
                            p.wait_for_timeout(1200)
                        except: pass
                    if hits: break
                except: pass
            try:p.remove_listener('response',onresp)
            except:pass
            uniq={(x['id'],x['type_id'],x['ticker']):x for x in hits}; arr=list(uniq.values()); arr.sort(key=lambda x:(not x['exact'],x['id']))
            if not arr:
                print(f'{fam}|{sym}|RESOLVE=NO|ENDPOINTS={";".join(f"{s}:{q}" for s,q in eps[:8]) or "-"}')
                continue
            b=arr[0]; resolved.append((fam,sym,b))
            print(f'{fam}|{sym}|RESOLVE=YES|ITEM={b["id"]}|TYPE_ID={b["type_id"]}|TYPE={b["type_desc"][:60]}|DESC={b["desc"][:100]}|ENDPOINTS={";".join(f"{s}:{q}" for s,q in eps[:8]) or "-"}')
        for fam,sym,b in resolved:
            item=b['id']; urls=[TRADING+f'/Cotizaciones/FCIs/{item}'] if fam=='FCI' else [TRADING+f'/Cotizaciones/Item/{item}?plazo=1',TRADING+f'/Cotizaciones/Item/{item}?plazo=2']
            for u in urls:
                caps=[]
                def hist(resp):
                    try:
                        if resp.request.method.upper()!='GET':return
                        z=urlsplit(resp.url)
                        if z.netloc!=API or 'histor' not in z.path.lower():return
                        if 'json' not in (resp.headers.get('content-type') or '').lower():return
                        data=json.loads(resp.body().decode('utf-8')); rows=payload_rows(data); ds=[rowdate(r) for r in rows]; ds=[d for d in ds if d]
                        aware=[]
                        for d in ds:
                            if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
                            aware.append(d.astimezone(timezone.utc))
                        in365=sum(1 for d in aware if d>=cutoff)
                        caps.append({'status':resp.status,'path':z.path,'rows_total':len(rows),'rows_365':in365,'drop_old':max(0,len(rows)-in365),'first':min(aware).isoformat() if aware else None,'last':max(aware).isoformat() if aware else None})
                    except: pass
                p.on('response',hist)
                detail=None; graph=False
                try:
                    r=p.goto(u,wait_until='domcontentloaded',timeout=45000); detail=r.status if r else None; p.wait_for_timeout(2500); graph=click_graph(p); p.wait_for_timeout(4500)
                except: pass
                try:p.remove_listener('response',hist)
                except:pass
                h=max(caps,key=lambda x:(x['status']==200,x['rows_365'])) if caps else None
                if h: print(f'{fam}|{sym}|DETAIL={detail}|GRAFICADOR={graph}|HIST_STATUS={h["status"]}|HIST_PATH={h["path"]}|ROWS_TOTAL={h["rows_total"]}|ROWS_365D={h["rows_365"]}|DROPPED_OLDER={h["drop_old"]}|FROM={h["first"]}|TO={h["last"]}')
                else: print(f'{fam}|{sym}|DETAIL={detail}|GRAFICADOR={graph}|HIST_STATUS=NO_HISTORY_XHR|ROWS_365D=0')
        print(f'RESOLVED={len(resolved)}/{len(TARGETS)}')
        print(f'BLOCKED_NONREAD={blocked_nonread}'); print(f'BLOCKED_FORBIDDEN={blocked_forbidden}')
        print('CREDENTIAL_VALUES_LOGGED=False'); print('QUERY_STRINGS_LOGGED=False'); print('CANONICAL_WRITE=DENY'); print('REAL_ORDERS=0'); print('MASS_SCRAPING_STARTED=NO')
        ctx.close()
    return 0
if __name__=='__main__': raise SystemExit(main())
