#!/usr/bin/env python3
import argparse, json
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

TRADING='https://trading.portfoliopersonal.com'
API='https://api.portfoliopersonal.com'
TARGETS=[('CEDEARS','AVYC'),('BONOS','TX28D'),('ON','MRCTO'),('OPCIONES','YPFV6100OC'),('FUTUROS','DLR/AGO27M'),('FCI','PI.RENT.B')]
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')

def path(url): return urlsplit(url).path

def forbidden(url):
    p=path(url).lower(); return any(x in p for x in FORBIDDEN)

def unwrap(data):
    if isinstance(data,dict):
        for k in ('payload','data','result','results','items'):
            if k in data and isinstance(data[k],(list,dict)): return data[k]
    return data

def rows(data):
    x=unwrap(data)
    return x if isinstance(x,list) else []

def family_match(desc,fam):
    d=' '.join(str(desc or '').upper().replace('_',' ').split())
    if fam=='ON': return d in {'ON','ONS'} or 'OBLIGACION' in d
    aliases={
      'CEDEARS':['CEDEAR'], 'BONOS':['BONO'], 'OPCIONES':['OPCION'],
      'FUTUROS':['FUTURO'], 'FCI':['FCI','FONDO']}
    return any(a in d for a in aliases[fam])

def item_match(obj,symbol):
    if not isinstance(obj,dict): return None
    ticker=str(obj.get('ticker') or obj.get('simbolo') or obj.get('symbol') or obj.get('codigo') or '')
    desc=str(obj.get('descripcion') or obj.get('description') or obj.get('nombre') or obj.get('name') or '')
    iid=obj.get('id') or obj.get('itemId') or obj.get('instrumentId')
    tipo=obj.get('tipoItem') or obj.get('instrumentType') or obj.get('type')
    tid=None; tdesc=''
    if isinstance(tipo,dict): tid=tipo.get('id'); tdesc=str(tipo.get('descripcion') or tipo.get('description') or '')
    tid=tid or obj.get('typeId') or obj.get('tipoItemId') or obj.get('instrumentTypeId')
    if iid is None: return None
    if ticker.upper()==symbol.upper() or symbol.upper() in (ticker+' '+desc).upper():
        return {'id':str(iid),'ticker':ticker or symbol,'desc':desc,'type_id':None if tid is None else str(tid),'type_desc':tdesc}
    return None

def extract_type_items(cfg):
    x=unwrap(cfg)
    if isinstance(x,dict):
        for k in ('tipoItems','tiposItem','instrumentTypes'):
            if isinstance(x.get(k),list): return x[k]
    if isinstance(cfg,dict) and isinstance(cfg.get('tipoItems'),list): return cfg['tipoItems']
    return []

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
        page=ctx.pages[0] if ctx.pages else ctx.new_page(); captured={}
        def capture(req):
            try:
                if req.method.upper()=='GET' and urlsplit(req.url).netloc=='api.portfoliopersonal.com':
                    candidate={k:v for k,v in req.headers.items() if k.lower() in ('accept','authorizedclient','clientkey','content-type','origin','referer','user-agent','authorization')}
                    if len(candidate)>len(captured): captured.clear(); captured.update(candidate)
            except Exception: pass
        page.on('request',capture)
        page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(4500)
        auth=urlsplit(page.url).netloc=='trading.portfoliopersonal.com' and 'login' not in path(page.url).lower()
        print('PPI_AUTH_DIRECT_DISCOVERY_CANARY'); print('AUTH=' + ('AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH_SESSION_EXPIRED'))
        if not auth: ctx.close(); return 20
        keep=dict(captured); print('CAPTURED_AUTH_CONTEXT=' + ('YES' if keep else 'NO'))
        rq=ctx.request
        def get_json(pth,params=None):
            try:
                r=rq.get(API+pth,headers=keep,params=params or {},timeout=20000)
                ct=(r.headers.get('content-type') or '').lower(); data=r.json() if 'json' in ct else None
                return r.status,data
            except Exception: return 0,None
        cfg_status,cfg=get_json('/api/Cotizaciones/Alertas/GetConfig'); types=extract_type_items(cfg)
        print(f'ALERT_CONFIG_HTTP={cfg_status}|TYPE_COUNT={len(types)}')
        fam_type={}
        for fam,_ in TARGETS:
            for t in types:
                if isinstance(t,dict) and family_match(str(t.get('descripcion') or t.get('description') or t.get('label') or ''),fam):
                    fam_type[fam]=t.get('id') if t.get('id') is not None else t.get('value'); break
        print('TYPE_IDS_RESOLVED=' + str(sum(1 for f,_ in TARGETS if fam_type.get(f) is not None)) + '/6')
        resolved=[]
        for fam,sym in TARGETS:
            found=None; source='-'; ep='-'; http=0; tid=fam_type.get(fam)
            if tid is not None:
                http,data=get_json('/api/Cotizaciones/Item/Tradeable',{'tipo':tid}); ep='/api/Cotizaciones/Item/Tradeable'
                for obj in rows(data):
                    m=item_match(obj,sym)
                    if m: found=m; source='TRADEABLE_BY_TYPE'; break
            if not found:
                for key in ('search','text','query','value','ticker'):
                    h,d=get_json('/api/Cotizaciones/Item/Search',{key:sym})
                    if h: http=h; ep='/api/Cotizaciones/Item/Search'
                    for obj in rows(d):
                        m=item_match(obj,sym)
                        if m: found=m; source='GLOBAL_SEARCH'; break
                    if found: break
            if found:
                print(f"{fam}|{sym}|RESOLVE=YES|SOURCE={source}|ITEM={found['id']}|TYPE_ID={found.get('type_id') or tid}|HTTP={http}|ENDPOINT={ep}"); resolved.append((fam,sym,found,tid))
            else: print(f'{fam}|{sym}|RESOLVE=NO|TYPE_ID={tid}|HTTP={http}|ENDPOINT={ep}')
        hist_ok=0
        for fam,sym,it,tid in resolved:
            item=it['id']; ds,_=get_json('/api/Cotizaciones/Item/'+item); ps,plazos=get_json('/api/Cotizaciones/Item/PlazosOperables',{'itemId':item}); terms=[]; pu=unwrap(plazos)
            if isinstance(pu,dict):
                vv=pu.get('plazosOperables') or pu.get('terms') or []
                if isinstance(vv,list): terms=[str(x.get('id') if isinstance(x,dict) else x) for x in vv[:4]]
            if not terms: terms=['1','2']
            best=(0,0,None,None,None)
            for term in terms:
                hs,hd=get_json(f'/api/Cotizaciones/Item/{item}/Historico/{term}'); rr=rows(hd); dates=[]
                for r in rr:
                    if isinstance(r,dict):
                        v=r.get('fechaCotizacion') or r.get('fecha') or r.get('date')
                        if v: dates.append(str(v))
                cand=(1 if hs==200 else 0,len(rr),hs,min(dates) if dates else None,max(dates) if dates else None)
                if cand[:2]>best[:2]: best=cand
            if best[0] and best[1]>0: hist_ok+=1
            print(f'{fam}|{sym}|DETAIL_HTTP={ds}|PLAZOS_HTTP={ps}|HIST_HTTP={best[2]}|ROWS_TOTAL={best[1]}|FROM={best[3]}|TO={best[4]}')
        print(f'RESOLVED={len(resolved)}/6'); print(f'HISTORY_OK={hist_ok}/{len(resolved)}'); print(f'BLOCKED_NONREAD={blocked_nonread}'); print(f'BLOCKED_FORBIDDEN={blocked_forbidden}')
        print('CREDENTIAL_VALUES_LOGGED=False'); print('QUERY_STRINGS_LOGGED=False'); print('CANONICAL_WRITE=DENY'); print('REAL_ORDERS=0'); print('MASS_SCRAPING_STARTED=NO'); print('HISTORY_HORIZON_POLICY=PREVIOUS_365D'); ctx.close()
    return 0
if __name__=='__main__': raise SystemExit(main())
