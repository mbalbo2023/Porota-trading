#!/usr/bin/env python3
"""Authenticated read-only PPI Web residual history collector for RC6.

Uses the production frontend's own read-only Quotes API flow. Browser auth headers
are kept only in memory and are never logged or persisted. No canonical/history DB
writes happen here; normalized evidence is emitted for the separate server runner.
"""
from __future__ import annotations

import argparse, hashlib, json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

TRADING='https://trading.portfoliopersonal.com'
API='https://api.portfoliopersonal.com'
SAFE_METHODS={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
DEFERRED_FAMILIES={'FCI','FCIS','FCI_EXTERIOR','FCIS_EXTERIOR','FONDOS','FONDO'}


def now_iso(): return datetime.now(timezone.utc).isoformat(timespec='microseconds')
def clean_url(url):
    u=urlsplit(str(url)); return f'{u.scheme}://{u.netloc}{u.path}'
def forbidden(url): return any(x in urlsplit(str(url)).path.lower() for x in FORBIDDEN)
def unwrap(data):
    if isinstance(data,dict):
        for k in ('payload','data','result','results','items'):
            if isinstance(data.get(k),(list,dict)): return data[k]
    return data
def list_rows(data):
    x=unwrap(data); return x if isinstance(x,list) else []
def norm_family(v): return str(v or '').strip().upper()
def family_match(desc,fam):
    d=' '.join(str(desc or '').upper().replace('_',' ').split()); fam=norm_family(fam)
    if fam in {'ON','OBLIGACIONES','OBLIGACIONES_NEGOCIABLES'}: return d in {'ON','ONS'} or 'OBLIGACION' in d
    aliases={
        'ACCIONES':['ACCION'], 'CEDEARS':['CEDEAR'], 'BONOS':['BONO'], 'BONOS_USD':['BONO'],
        'OPCIONES':['OPCION'], 'FUTUROS':['FUTURO'], 'LETRAS':['LETRA'], 'ETF':['ETF'], 'ETFS':['ETF'],
    }
    return any(a in d for a in aliases.get(fam,[fam]))
def item_match(obj,symbol):
    if not isinstance(obj,dict): return None
    ticker=str(obj.get('ticker') or obj.get('simbolo') or obj.get('symbol') or obj.get('codigo') or '')
    desc=str(obj.get('descripcion') or obj.get('description') or obj.get('nombre') or obj.get('name') or '')
    iid=obj.get('id') or obj.get('itemId') or obj.get('instrumentId')
    tipo=obj.get('tipoItem') or obj.get('instrumentType') or obj.get('type'); tid=None; tdesc=''
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
    return []
def first(obj,*names):
    if not isinstance(obj,dict): return None
    low={str(k).lower():v for k,v in obj.items()}
    for name in names:
        v=low.get(name.lower())
        if v not in (None,''): return v
    return None
def normalize_history(payload):
    out=[]
    for obj in list_rows(payload):
        if not isinstance(obj,dict): continue
        date=first(obj,'date','fecha','fechaCotizacion')
        op=first(obj,'openingPrice','open','apertura'); hi=first(obj,'max','high','maxDia')
        lo=first(obj,'min','low','minDia'); close=first(obj,'price','close','ultOperado'); vol=first(obj,'volume','volumen')
        if date in (None,'') or any(v in (None,'') for v in (op,hi,lo,close,vol)): continue
        out.append({'date':date,'openingPrice':op,'max':hi,'min':lo,'price':close,'volume':vol})
    return out
def parse_date(v):
    try: return datetime.fromisoformat(str(v).replace('Z','+00:00')).date()
    except Exception: return None
def _term_candidates(value,depth=0):
    if depth>5: return []
    out=[]
    if isinstance(value,dict):
        iid=value.get('id') if value.get('id') is not None else value.get('value')
        desc=value.get('descripcion') or value.get('description') or value.get('label') or value.get('nombre') or value.get('name')
        if iid is not None and desc not in (None,''): out.append((str(iid),str(desc).upper()))
        for v in value.values():
            if isinstance(v,(dict,list)): out.extend(_term_candidates(v,depth+1))
    elif isinstance(value,list):
        for v in value: out.extend(_term_candidates(v,depth+1))
    return out
def settlement_term(plazos,settlement):
    wanted=str(settlement or '').strip().upper()
    candidates=[]; seen=set()
    for pair in _term_candidates(plazos):
        if pair not in seen: seen.add(pair); candidates.append(pair)
    def matches(desc):
        if wanted in {'CI','CONTADO INMEDIATO','INMEDIATO'}: return 'INMEDIATO' in desc or desc.strip()=='CI'
        for hrs in ('24','48','72'):
            if hrs in wanted: return hrs in desc
        return wanted and wanted in desc
    for iid,desc in candidates:
        if matches(desc): return iid
    # Only the two term IDs already proven by normal PPI Web flow are safe fallbacks.
    if wanted in {'CI','CONTADO INMEDIATO','INMEDIATO'}: return '1'
    if '24' in wanted: return '2'
    return None
def load_targets(path):
    rows=[]; seen=set()
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line.strip(): continue
        x=json.loads(line); key=tuple(str(x.get(k,'')).strip().upper() for k in ('symbol','instrument_type','market','settlement'))
        if not all(key[:2]): raise ValueError('TARGET_IDENTITY_INCOMPLETE')
        if key in seen: raise ValueError('TARGET_DUPLICATE')
        seen.add(key); rows.append({**x,'symbol':key[0],'instrument_type':key[1],'market':key[2],'settlement':key[3]})
    return rows
def digest(rows): return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--targets-jsonl',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); args=ap.parse_args()
    targets=load_targets(args.targets_jsonl); output=Path(args.output); output.parent.mkdir(parents=True,exist_ok=True)
    report={'schema':'POROTA_RC6_PPI_WEB_DIRECT_HISTORY_V1','generated_at':now_iso(),'auth_status':'UNKNOWN','canonical_write':'DENY','db_write':'NO','real_orders_sent':0,'mass_scraping_started':'NO','captures':[],'target_results':[],'blocked_nonread':0,'blocked_forbidden':0,'credentials_logged':False,'query_strings_logged':False}
    if any(norm_family(t['instrument_type']) in DEFERRED_FAMILIES for t in targets):
        report['error']='FCI_TARGET_MUST_BE_DEFERRED'; output.write_text(json.dumps(report,ensure_ascii=False,indent=2)); return 9
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=args.profile,executable_path=args.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
        def guard(route,request):
            if forbidden(request.url): report['blocked_forbidden']+=1; return route.abort()
            if request.method.upper() not in SAFE_METHODS: report['blocked_nonread']+=1; return route.abort()
            return route.continue_()
        ctx.route('**/*',guard); page=ctx.pages[0] if ctx.pages else ctx.new_page(); auth_headers={}
        def capture(req):
            try:
                if req.method.upper()=='GET' and urlsplit(req.url).netloc=='api.portfoliopersonal.com':
                    cand={k:v for k,v in req.headers.items() if k.lower() in ('accept','authorizedclient','clientkey','content-type','origin','referer','user-agent','authorization')}
                    if len(cand)>len(auth_headers): auth_headers.clear(); auth_headers.update(cand)
            except Exception: pass
        page.on('request',capture); page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(3000)
        auth=urlsplit(page.url).netloc=='trading.portfoliopersonal.com' and 'login' not in urlsplit(page.url).path.lower()
        report['auth_status']='AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH_SESSION_EXPIRED'
        if not auth or not auth_headers:
            ctx.close(); output.write_text(json.dumps(report,ensure_ascii=False,indent=2)); os.chmod(output,0o600); print(json.dumps({'auth_status':report['auth_status'],'targets':len(targets),'captures':0,'canonical_write':'DENY','real_orders_sent':0})); return 4
        rq=ctx.request
        def get_json(pth,params=None):
            try:
                r=rq.get(API+pth,headers=auth_headers,params=params or {},timeout=25000); ct=(r.headers.get('content-type') or '').lower(); data=r.json() if 'json' in ct else None; return r.status,data
            except Exception: return 0,None
        cfg_http,cfg=get_json('/api/Cotizaciones/Alertas/GetConfig'); types=extract_type_items(cfg); family_types={}
        for t in targets:
            fam=t['instrument_type']
            if fam in family_types: continue
            for typ in types:
                if isinstance(typ,dict) and family_match(str(typ.get('descripcion') or typ.get('description') or typ.get('label') or ''),fam):
                    family_types[fam]=typ.get('id') if typ.get('id') is not None else typ.get('value'); break
        cutoff=datetime.now(timezone.utc).date()-timedelta(days=365)
        for target in targets:
            fam=target['instrument_type']; sym=target['symbol']; found=None; source=''; tid=family_types.get(fam); last_http=0
            if tid is not None:
                last_http,data=get_json('/api/Cotizaciones/Item/Tradeable',{'tipo':tid})
                for obj in list_rows(data):
                    m=item_match(obj,sym)
                    if m: found=m; source='TRADEABLE_BY_TYPE'; break
            if not found:
                for key in ('search','text','query','value','ticker'):
                    last_http,data=get_json('/api/Cotizaciones/Item/Search',{key:sym})
                    for obj in list_rows(data):
                        m=item_match(obj,sym)
                        if m: found=m; source='GLOBAL_SEARCH'; break
                    if found: break
            tr={**target,'discovery_source':source or None,'discovery_http':last_http,'item_id':found.get('id') if found else None,'type_id':(found.get('type_id') if found else None) or tid,'result':'DISCOVERY_UNRESOLVED'}
            if not found: report['target_results'].append(tr); continue
            item=found['id']; detail_http,_=get_json('/api/Cotizaciones/Item/'+item); plazos_http,plazos=get_json('/api/Cotizaciones/Item/PlazosOperables',{'itemId':item}); term=settlement_term(plazos,target['settlement'])
            tr.update({'detail_http':detail_http,'plazos_http':plazos_http,'term':term})
            if detail_http!=200 or plazos_http!=200 or term is None:
                tr['result']='DETAIL_OR_SETTLEMENT_UNRESOLVED'; report['target_results'].append(tr); continue
            hist_http,hist=get_json(f'/api/Cotizaciones/Item/{item}/Historico/{term}'); provider=list_rows(hist); normalized=normalize_history(hist)
            in_window=[r for r in normalized if (parse_date(r['date']) is not None and parse_date(r['date'])>=cutoff)]
            tr.update({'history_http':hist_http,'provider_rows':len(provider),'normalized_rows':len(normalized),'rows_365d':len(in_window),'dropped_old':max(0,len(normalized)-len(in_window))})
            if hist_http!=200: tr['result']='HISTORY_HTTP_ERROR'
            elif len(provider)==0: tr['result']='PROVIDER_EMPTY'
            elif len(normalized)==0: tr['result']='PROVIDER_INVALID_SHAPE'
            else:
                tr['result']='CAPTURED'; cap={k:target[k] for k in ('symbol','instrument_type','market','settlement')}; cap.update({'residual_class':target.get('residual_class',''),'source':'PPI_WEB_HISTORY','item_id':item,'type_id':tr['type_id'],'term':term,'history_http':hist_http,'rows_sha256':digest(in_window),'rows':in_window}); report['captures'].append(cap)
            report['target_results'].append(tr)
        ctx.close()
    report['summary']={'targets':len(targets),'captured_targets':sum(1 for x in report['target_results'] if x['result']=='CAPTURED'),'provider_empty':sum(1 for x in report['target_results'] if x['result']=='PROVIDER_EMPTY'),'unresolved':sum(1 for x in report['target_results'] if x['result'] not in ('CAPTURED','PROVIDER_EMPTY')),'rows_365d':sum(len(x['rows']) for x in report['captures']),'alert_config_http':cfg_http,'history_horizon_policy':'PREVIOUS_365D'}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(output,0o600)
    print(json.dumps({'auth_status':report['auth_status'],**report['summary'],'blocked_nonread':report['blocked_nonread'],'blocked_forbidden':report['blocked_forbidden'],'credentials_logged':False,'query_strings_logged':False,'canonical_write':'DENY','real_orders_sent':0},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
