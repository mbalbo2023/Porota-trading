#!/usr/bin/env python3
import argparse, json, re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

TRADING='https://trading.portfoliopersonal.com'
API='https://api.portfoliopersonal.com'
TARGETS=[('CEDEARS','AVYC'),('BONOS','TX28D'),('ON','MRCTO'),('OPCIONES','YPFV6100OC'),('FUTUROS','DLR/AGO27M'),('FCI','PI.RENT.B')]
PENDING={'MRCTO','PI.RENT.B'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')

def pth(url): return urlsplit(url).path

def forbidden(url): return any(x in pth(url).lower() for x in FORBIDDEN)

def unwrap(data):
    if isinstance(data,dict):
        for k in ('payload','data','result','results','items'):
            v=data.get(k)
            if isinstance(v,(list,dict)): return v
    return data

def rows(data):
    x=unwrap(data)
    return x if isinstance(x,list) else []

def ticker_of(o):
    if not isinstance(o,dict): return ''
    return str(o.get('ticker') or o.get('simbolo') or o.get('symbol') or o.get('codigo') or o.get('name') or '')

def desc_of(o):
    if not isinstance(o,dict): return ''
    return str(o.get('descripcion') or o.get('description') or o.get('nombre') or o.get('fundName') or '')

def item_match(o,symbol):
    if not isinstance(o,dict): return None
    ticker=ticker_of(o); desc=desc_of(o)
    iid=o.get('id') or o.get('itemId') or o.get('instrumentId') or o.get('fciId')
    if iid is None: return None
    if ticker.upper()==symbol.upper() or symbol.upper() in (ticker+' '+desc).upper():
        tipo=o.get('tipoItem') or o.get('instrumentType') or o.get('type')
        tid=None; tdesc=''
        if isinstance(tipo,dict):
            tid=tipo.get('id'); tdesc=str(tipo.get('descripcion') or tipo.get('description') or '')
        tid=tid or o.get('typeId') or o.get('tipoItemId') or o.get('instrumentTypeId')
        return {'id':str(iid),'ticker':ticker or symbol,'desc':desc,'type_id':None if tid is None else str(tid),'type_desc':tdesc}
    return None

def extract_types(cfg):
    x=unwrap(cfg)
    if isinstance(x,dict):
        for k in ('tipoItems','tiposItem','instrumentTypes'):
            if isinstance(x.get(k),list): return x[k]
    if isinstance(cfg,dict) and isinstance(cfg.get('tipoItems'),list): return cfg['tipoItems']
    return []

def parse_date(v):
    if not v: return None
    s=str(v).strip().replace('Z','+00:00')
    try: return datetime.fromisoformat(s)
    except Exception: pass
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%Y-%m-%dT%H:%M:%S'):
        try: return datetime.strptime(s[:19],fmt)
        except Exception: pass
    return None

def history_stats(rr):
    ds=[]
    for r in rr:
        if not isinstance(r,dict): continue
        v=r.get('fechaCotizacion') or r.get('fecha') or r.get('date') or r.get('datetime')
        d=parse_date(v)
        if d:
            if d.tzinfo is not None: d=d.astimezone(timezone.utc).replace(tzinfo=None)
            ds.append(d)
    now=datetime.utcnow()
    cutoff=now-timedelta(days=365)
    kept=[d for d in ds if d>=cutoff]
    return len(rr),len(kept),max(0,len(ds)-len(kept)),(min(kept).isoformat() if kept else None),(max(kept).isoformat() if kept else None)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); args=ap.parse_args()
    blocked_nonread=blocked_forbidden=0
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=args.profile,executable_path='/usr/bin/google-chrome-stable',headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
        def guard(route,request):
            nonlocal blocked_nonread,blocked_forbidden
            if forbidden(request.url): blocked_forbidden+=1; return route.abort()
            if request.method.upper() not in ('GET','HEAD','OPTIONS'): blocked_nonread+=1; return route.abort()
            return route.continue_()
        ctx.route('**/*',guard)
        page=ctx.pages[0] if ctx.pages else ctx.new_page()
        captured={}
        def capture(req):
            try:
                if req.method.upper()=='GET' and urlsplit(req.url).netloc=='api.portfoliopersonal.com' and not captured:
                    captured.update(req.headers)
            except Exception: pass
        page.on('request',capture)
        page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(3500)
        auth=urlsplit(page.url).netloc=='trading.portfoliopersonal.com' and 'login' not in pth(page.url).lower()
        print('PPI_PENDING_CLOSURE_CANARY')
        print('AUTH='+('AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH_SESSION_EXPIRED'))
        if not auth: ctx.close(); return 20
        keep={k:v for k,v in captured.items() if k.lower() in ('accept','authorizedclient','clientkey','content-type','origin','referer','user-agent','authorization')}
        print('CAPTURED_AUTH_CONTEXT='+('YES' if keep else 'NO'))
        rq=ctx.request
        def get_json(path,params=None):
            try:
                r=rq.get(API+path,headers=keep,params=params or {},timeout=20000)
                ct=(r.headers.get('content-type') or '').lower()
                return r.status,(r.json() if 'json' in ct else None)
            except Exception: return 0,None

        cs,cfg=get_json('/api/Cotizaciones/Alertas/GetConfig')
        types=extract_types(cfg)
        print(f'ALERT_CONFIG_HTTP={cs}|TYPE_COUNT={len(types)}')
        type_pairs=[]
        for t in types:
            if not isinstance(t,dict): continue
            tid=t.get('id') if t.get('id') is not None else t.get('value')
            lab=str(t.get('descripcion') or t.get('description') or t.get('label') or '')
            type_pairs.append((tid,lab))
            print(f'TYPE|ID={tid}|LABEL={lab[:100]}')

        # Broad authenticated enumeration over every alert instrument type; this avoids fragile family-name guessing.
        discovered={}
        for tid,lab in type_pairs:
            if tid is None: continue
            hs,data=get_json('/api/Cotizaciones/Item/Tradeable',{'tipo':tid})
            rr=rows(data)
            for fam,sym in TARGETS:
                if sym in discovered: continue
                for o in rr:
                    m=item_match(o,sym)
                    if m:
                        discovered[sym]=(fam,m,str(tid),lab,'TRADEABLE_ALL_TYPES',hs,'/api/Cotizaciones/Item/Tradeable')
                        break

        # Exact global-search wrapper candidates, bounded GET only.
        for fam,sym in TARGETS:
            if sym in discovered: continue
            for key in ('search','text','query','value','ticker','filter'):
                hs,data=get_json('/api/Cotizaciones/Item/Search',{key:sym})
                for o in rows(data):
                    m=item_match(o,sym)
                    if m:
                        discovered[sym]=(fam,m,m.get('type_id'),'','GLOBAL_SEARCH',hs,'/api/Cotizaciones/Item/Search')
                        break
                if sym in discovered: break

        # Trigger the real global search component on Mercado for the two pending symbols and capture only path/status/body matches.
        ui_hits={}
        def response_handler(resp):
            try:
                if resp.request.method.upper()!='GET': return
                u=urlsplit(resp.url)
                if u.netloc!='api.portfoliopersonal.com': return
                ct=(resp.headers.get('content-type') or '').lower()
                if 'json' not in ct: return
                data=resp.json()
                for sym in PENDING:
                    for o in rows(data):
                        m=item_match(o,sym)
                        if m: ui_hits[sym]=(m,resp.status,u.path)
            except Exception: pass
        page.on('response',response_handler)
        for sym in sorted(PENDING):
            try:
                page.goto(TRADING+'/Mercado',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(1800)
                loc=page.locator('input[placeholder="Buscar instrumento"]')
                if loc.count():
                    for i in range(min(loc.count(),6)):
                        try:
                            if loc.nth(i).is_visible():
                                loc.nth(i).fill(sym); page.wait_for_timeout(2500); break
                        except Exception: pass
            except Exception: pass
        try: page.remove_listener('response',response_handler)
        except Exception: pass
        for sym,(m,hs,pathx) in ui_hits.items():
            if sym not in discovered:
                fam=next(f for f,s in TARGETS if s==sym)
                discovered[sym]=(fam,m,m.get('type_id'),'','UI_GLOBAL_SEARCH',hs,pathx)

        # FCI-specific public/authenticated pages: observe read-only API responses while loading list page.
        fci_hit=None
        def fci_resp(resp):
            nonlocal fci_hit
            try:
                if resp.request.method.upper()!='GET': return
                u=urlsplit(resp.url)
                if u.netloc!='api.portfoliopersonal.com': return
                if 'json' not in (resp.headers.get('content-type') or '').lower(): return
                data=resp.json()
                def walk(x,depth=0):
                    nonlocal fci_hit
                    if depth>8 or fci_hit: return
                    if isinstance(x,dict):
                        m=item_match(x,'PI.RENT.B')
                        if m: fci_hit=(m,resp.status,u.path); return
                        for v in x.values(): walk(v,depth+1)
                    elif isinstance(x,list):
                        for v in x[:5000]: walk(v,depth+1)
                walk(data)
            except Exception: pass
        if 'PI.RENT.B' not in discovered:
            page.on('response',fci_resp)
            for route in ('/Cotizaciones/FCIs','/Cotizaciones/FCIsExterior'):
                try: page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(3500)
                except Exception: pass
                if fci_hit: break
            try: page.remove_listener('response',fci_resp)
            except Exception: pass
            if fci_hit:
                m,hs,pathx=fci_hit
                discovered['PI.RENT.B']=('FCI',m,m.get('type_id'),'','FCI_PAGE_API',hs,pathx)

        for fam,sym in TARGETS:
            if sym in discovered:
                _,m,tid,lab,src,hs,ep=discovered[sym]
                print(f"{fam}|{sym}|RESOLVE=YES|SOURCE={src}|ITEM={m['id']}|TYPE_ID={m.get('type_id') or tid}|TYPE_LABEL={lab[:80]}|HTTP={hs}|ENDPOINT={ep}")
            else:
                print(f'{fam}|{sym}|RESOLVE=NO')

        hist_ok=0; hist_total=0
        for fam,sym in TARGETS:
            if sym not in discovered: continue
            _,m,tid,lab,src,hs,ep=discovered[sym]; item=m['id']
            ds,detail=get_json('/api/Cotizaciones/Item/'+item)
            ps,plazos=get_json('/api/Cotizaciones/Item/PlazosOperables',{'itemId':item})
            terms=[]; pu=unwrap(plazos)
            if isinstance(pu,dict):
                vv=pu.get('plazosOperables') or pu.get('terms') or []
                if isinstance(vv,list): terms=[str(x.get('id') if isinstance(x,dict) else x) for x in vv[:8]]
            if not terms: terms=['1','2']
            best=None
            for term in terms:
                hh,hd=get_json(f'/api/Cotizaciones/Item/{item}/Historico/{term}')
                rr=rows(hd); stats=history_stats(rr)
                cand=(1 if hh==200 and stats[0]>0 else 0,stats[0],hh,term,stats)
                if best is None or cand[:2]>best[:2]: best=cand
            hist_total+=1
            ok=best and best[0]==1
            if ok: hist_ok+=1
            st=best[4] if best else (0,0,0,None,None)
            print(f'{fam}|{sym}|DETAIL_HTTP={ds}|PLAZOS_HTTP={ps}|TERM={best[3] if best else None}|HIST_HTTP={best[2] if best else 0}|ROWS_TOTAL={st[0]}|ROWS_365D={st[1]}|DROPPED_OLDER_THAN_365D={st[2]}|FROM_365D={st[3]}|TO_365D={st[4]}')

        print(f'RESOLVED={len(discovered)}/6')
        print(f'HISTORY_OK={hist_ok}/{hist_total}')
        print('PENDING_ON_RESOLVED='+('YES' if 'MRCTO' in discovered else 'NO'))
        print('PENDING_FCI_RESOLVED='+('YES' if 'PI.RENT.B' in discovered else 'NO'))
        print('SCRAPE_READY='+('YES' if len(discovered)==6 and hist_ok==6 else 'NO'))
        print(f'BLOCKED_NONREAD={blocked_nonread}')
        print(f'BLOCKED_FORBIDDEN={blocked_forbidden}')
        print('CREDENTIAL_VALUES_LOGGED=False')
        print('QUERY_STRINGS_LOGGED=False')
        print('CANONICAL_WRITE=DENY')
        print('REAL_ORDERS=0')
        print('MASS_SCRAPING_STARTED=NO')
        print('HISTORY_HORIZON_POLICY=PREVIOUS_365D')
        ctx.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
