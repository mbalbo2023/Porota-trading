#!/usr/bin/env python3
import argparse, json, re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

TRADING='https://trading.portfoliopersonal.com'
API='https://api.portfoliopersonal.com'
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
TARGETS={'ON':['MRCTO','MRCT','MRC'], 'FCI':['PI.RENT.B','PI RENT B','PI.RENT','PIRENTB']}

def pth(url): return urlsplit(url).path

def forbidden(url):
    p=pth(url).lower(); return any(x in p for x in FORBIDDEN)

def norm(s): return re.sub(r'[^A-Z0-9]','',str(s or '').upper())

def unwrap(x):
    if isinstance(x,dict):
        for k in ('payload','data','result','results','items'):
            if isinstance(x.get(k),(dict,list)): return x[k]
    return x

def rows(x):
    y=unwrap(x)
    return y if isinstance(y,list) else []

def walk(obj, out=None, depth=0):
    if out is None: out=[]
    if depth>10: return out
    if isinstance(obj,dict):
        out.append(obj)
        for v in obj.values(): walk(v,out,depth+1)
    elif isinstance(obj,list):
        for v in obj[:10000]: walk(v,out,depth+1)
    return out

def identify(d):
    if not isinstance(d,dict): return None
    ticker=d.get('ticker') or d.get('simbolo') or d.get('symbol') or d.get('codigo') or d.get('tickerBase')
    desc=d.get('descripcion') or d.get('description') or d.get('nombre') or d.get('name') or d.get('fundName')
    iid=d.get('id') or d.get('itemId') or d.get('instrumentId') or d.get('fciId') or d.get('item')
    tipo=d.get('tipoItem') or d.get('instrumentType') or d.get('type')
    tid=None; tdesc=''
    if isinstance(tipo,dict):
        tid=tipo.get('id'); tdesc=tipo.get('descripcion') or tipo.get('description') or ''
    tid=tid or d.get('typeId') or d.get('tipoItemId') or d.get('instrumentTypeId')
    if iid is None: return None
    return {'id':str(iid),'ticker':str(ticker or ''),'desc':str(desc or ''),'type_id':None if tid is None else str(tid),'type_desc':str(tdesc or '')}

def target_hit(rec, variants):
    text=norm((rec.get('ticker') or '')+' '+(rec.get('desc') or ''))
    return any(norm(v) and norm(v) in text for v in variants)

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
        api_json=[]
        def on_req(req):
            try:
                if req.method.upper()=='GET' and urlsplit(req.url).netloc=='api.portfoliopersonal.com' and not captured:
                    captured.update(req.headers)
            except Exception: pass
        def on_resp(resp):
            try:
                u=urlsplit(resp.url)
                if resp.request.method.upper()!='GET' or u.netloc!='api.portfoliopersonal.com': return
                if 'json' not in (resp.headers.get('content-type') or '').lower(): return
                b=resp.body()
                if len(b)>4000000: return
                data=json.loads(b.decode('utf-8'))
                api_json.append((resp.status,u.path,data))
            except Exception: pass
        page.on('request',on_req); page.on('response',on_resp)
        page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(4000)
        auth=urlsplit(page.url).netloc=='trading.portfoliopersonal.com' and 'login' not in pth(page.url).lower()
        print('PPI_ON_FCI_BLOCKER_RESOLVER')
        print('AUTH=' + ('AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH_SESSION_EXPIRED'))
        if not auth: ctx.close(); return 20
        keep={k:v for k,v in captured.items() if k.lower() in ('accept','authorizedclient','clientkey','content-type','origin','referer','user-agent','authorization')}
        print('CAPTURED_AUTH_CONTEXT=' + ('YES' if keep else 'NO'))
        rq=ctx.request
        def get_json(path, params=None):
            try:
                r=rq.get(API+path,headers=keep,params=params or {},timeout=20000)
                data=r.json() if 'json' in (r.headers.get('content-type') or '').lower() else None
                return r.status,data
            except Exception: return 0,None

        resolved={}
        # 1) ON: authoritative tradeable type 140 + bounded normalized variants/search.
        hs,hd=get_json('/api/Cotizaciones/Item/Tradeable',{'tipo':140})
        all_on=[]
        for d in rows(hd):
            r=identify(d)
            if r: all_on.append(r)
        print(f'ON_TRADEABLE_HTTP={hs}|COUNT={len(all_on)}')
        for r in all_on:
            if target_hit(r,TARGETS['ON']):
                resolved['ON']=r; print(f"ON|MRCTO|RESOLVE=YES|SOURCE=TRADEABLE_140|ITEM={r['id']}|TICKER={r['ticker']}|TYPE_ID={r['type_id'] or 140}"); break
        if 'ON' not in resolved:
            near=[r for r in all_on if 'MRC' in norm(r['ticker']+' '+r['desc'])][:12]
            print('ON_NEAR=' + (';'.join(f"{x['ticker']}:{x['id']}" for x in near) or '-'))

        # 2) Search endpoint, multiple safe parameter names + variants for both families.
        for fam in ('ON','FCI'):
            if fam in resolved: continue
            for variant in TARGETS[fam]:
                for key in ('search','text','query','value','ticker'):
                    st,data=get_json('/api/Cotizaciones/Item/Search',{key:variant})
                    for d in walk(data):
                        r=identify(d)
                        if r and target_hit(r,TARGETS[fam]):
                            resolved[fam]=r
                            print(f"{fam}|{TARGETS[fam][0]}|RESOLVE=YES|SOURCE=ITEM_SEARCH|ITEM={r['id']}|TICKER={r['ticker']}|TYPE_ID={r['type_id']}|PARAM={key}|HTTP={st}")
                            break
                    if fam in resolved: break
                if fam in resolved: break

        # 3) Normal authenticated family pages. Capture real GET JSON traffic and inspect it recursively.
        family_pages={'ON':['/Cotizaciones/Ons'], 'FCI':['/Cotizaciones/FCIs','/Cotizaciones/FCIsExterior']}
        for fam,pages in family_pages.items():
            if fam in resolved: continue
            before=len(api_json)
            for rp in pages:
                try:
                    page.goto(TRADING+rp,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(5000)
                except Exception: pass
            hits=[]
            endpoints=[]
            for st,path,data in api_json[before:]:
                for d in walk(data):
                    r=identify(d)
                    if r and target_hit(r,TARGETS[fam]): hits.append((r,st,path))
                if any(norm(v) in norm(json.dumps(data,ensure_ascii=False)[:500000]) for v in TARGETS[fam]):
                    endpoints.append((st,path))
            if hits:
                r,st,ep=hits[0]; resolved[fam]=r
                print(f"{fam}|{TARGETS[fam][0]}|RESOLVE=YES|SOURCE=FAMILY_PAGE_NETWORK|ITEM={r['id']}|TICKER={r['ticker']}|TYPE_ID={r['type_id']}|HTTP={st}|ENDPOINT={ep}")
            else:
                eps=[]; seen=set()
                for x in endpoints:
                    if x not in seen: seen.add(x); eps.append(x)
                print(f"{fam}|{TARGETS[fam][0]}|RESOLVE=NO|NETWORK_TARGET_ENDPOINTS=" + (';'.join(f'{s}:{p}' for s,p in eps[:12]) or '-'))

        # 4) Validate detail/plazos/history for anything resolved. FCI also probes family detail page and captures history endpoint.
        for fam in ('ON','FCI'):
            r=resolved.get(fam)
            if not r: continue
            item=r['id']
            ds,detail=get_json('/api/Cotizaciones/Item/'+item)
            ps,plazos=get_json('/api/Cotizaciones/Item/PlazosOperables',{'itemId':item})
            best=None
            terms=[]; pu=unwrap(plazos)
            if isinstance(pu,dict):
                vv=pu.get('plazosOperables') or pu.get('terms') or []
                if isinstance(vv,list): terms=[str(x.get('id') if isinstance(x,dict) else x) for x in vv]
            for term in (terms or ['1','2']):
                hs,hd=get_json(f'/api/Cotizaciones/Item/{item}/Historico/{term}')
                rr=rows(hd)
                cand=(hs,len(rr),term)
                if best is None or (hs==200,len(rr))>(best[0]==200,best[1]): best=cand
            print(f"{fam}|DETAIL_HTTP={ds}|PLAZOS_HTTP={ps}|HIST_HTTP={best[0] if best else 0}|ROWS_TOTAL={best[1] if best else 0}|TERM={best[2] if best else '-'}")
            if fam=='FCI':
                before=len(api_json)
                try:
                    page.goto(TRADING+f'/Cotizaciones/FCIs/{item}',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(6000)
                except Exception: pass
                hist=[]
                for st,pa,data in api_json[before:]:
                    if 'histor' in pa.lower() or 'rendimiento' in pa.lower() or 'evolucion' in pa.lower():
                        hist.append((st,pa,len(rows(data))))
                print('FCI_UI_HISTORY_ENDPOINTS=' + (';'.join(f'{s}:{p}:rows={n}' for s,p,n in hist[:12]) or '-'))

        print('ON_RESOLVED=' + ('YES' if 'ON' in resolved else 'NO'))
        print('FCI_RESOLVED=' + ('YES' if 'FCI' in resolved else 'NO'))
        print('BLOCKER_RESOLVED=' + ('YES' if len(resolved)==2 else 'NO'))
        print(f'BLOCKED_NONREAD={blocked_nonread}')
        print(f'BLOCKED_FORBIDDEN={blocked_forbidden}')
        print('CREDENTIAL_VALUES_LOGGED=False')
        print('QUERY_STRINGS_LOGGED=False')
        print('CANONICAL_WRITE=DENY')
        print('REAL_ORDERS=0')
        print('MASS_SCRAPING_STARTED=NO')
        ctx.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
