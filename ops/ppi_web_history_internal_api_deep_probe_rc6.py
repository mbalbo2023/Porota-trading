#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re
from pathlib import Path
from urllib.parse import urlsplit

SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
TRADING='https://trading.portfoliopersonal.com'
API='https://api.portfoliopersonal.com'
AUTH_NAMES=('authorization','authorizedclient','clientkey')
SAMPLES=[
 ('ACCIONES','CRESD','794828','/Cotizaciones/Acciones'),
 ('CEDEARS','AVYC',None,'/Cotizaciones/Cedears'),
 ('BONOS','TX28D',None,'/Cotizaciones/Bonos'),
 ('LETRAS','S13N6','926121','/Cotizaciones/Letras'),
 ('ON','MRCTO',None,'/Cotizaciones/Ons'),
 ('OPCIONES','YPFV6100OC',None,'/Cotizaciones/Opciones'),
 ('FUTUROS','DLR/AGO27M',None,'/Cotizaciones/Futuros'),
 ('FCI','PI.RENT.B',None,'/Cotizaciones/FondosComunesDeInversion'),
]

def clean_url(u):
 p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u): return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def short(v,n=160): return re.sub(r'\s+',' ',str(v or '')).strip()[:n]

def summarize_json_bytes(body,ctype=''):
 out={'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'content_type':short(ctype,120)}
 try:
  data=json.loads(body.decode('utf-8'))
  out['root_type']=type(data).__name__
  if isinstance(data,dict):
   out['root_keys']=sorted(map(str,data.keys()))[:60]
   p=data.get('payload')
   out['payload_type']=type(p).__name__
   rows=[]
   if isinstance(p,list): rows=p
   elif isinstance(p,dict):
    out['payload_keys']=sorted(map(str,p.keys()))[:80]
    for k in ('data','items','result','results','historico','history','series','values'):
     if isinstance(p.get(k),list): rows=p[k]; out['row_container']='payload.'+k; break
   if rows:
    out['rows']=len(rows)
    if isinstance(rows[0],dict):
     out['row_keys']=sorted(map(str,rows[0].keys()))[:100]
     dates=[]
     for r in rows:
      if not isinstance(r,dict): continue
      for k,v in r.items():
       if str(k).lower() in ('date','fecha','datetime','time','timestamp') and v not in (None,''): dates.append(str(v))
     if dates: out['date_min']=min(dates); out['date_max']=max(dates)
   else: out['rows']=0
  elif isinstance(data,list):
   out['rows']=len(data)
   if data and isinstance(data[0],dict): out['row_keys']=sorted(map(str,data[0].keys()))[:100]
 except Exception:
  out['json_parse']='FAILED'
 return out

def collect_matching_dicts(obj,symbol,path='root',depth=0):
 hits=[]
 if depth>8: return hits
 if isinstance(obj,dict):
  vals=[str(v) for v in obj.values() if isinstance(v,(str,int,float))]
  if any(symbol.lower()==v.lower() or symbol.lower() in v.lower() for v in vals):
   safe={}
   for k,v in obj.items():
    kl=str(k).lower()
    if isinstance(v,(str,int,float,bool,type(None))) and (kl in ('id','instrumentid','instrument_id','ticker','symbol','simbolo','codigo','code','name','nombre','market','mercado','settlement','plazo') or 'id'==kl[-2:]):
     safe[str(k)]=short(v,120)
   hits.append({'path':path,'fields':safe})
  for k,v in obj.items(): hits.extend(collect_matching_dicts(v,symbol,f'{path}.{k}',depth+1))
 elif isinstance(obj,list):
  for i,v in enumerate(obj[:1500]): hits.extend(collect_matching_dicts(v,symbol,f'{path}[{i}]',depth+1))
 return hits[:30]

def find_dom_item(page,symbol):
 try:
  links=page.locator('a[href*="/Cotizaciones/Item/"]')
  for i in range(min(links.count(),1200)):
   el=links.nth(i)
   try:
    txt=short(el.inner_text(),160); href=el.get_attribute('href') or ''
    if symbol.lower() in txt.lower():
     m=re.search(r'/Cotizaciones/Item/(\d+)',href)
     if m:return m.group(1)
   except Exception:pass
 except Exception:pass
 return None

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 out={'auth':'UNKNOWN','generic_auth_capture':{},'header_ablation':{},'known_history':[],'family_discovery':[],'blocked_nonread':[],'blocked_forbidden':[],'credentials_logged':False,'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO'}
 captured_headers=None
 family_responses=[]
 from playwright.sync_api import sync_playwright
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
  def guard(route,request):
   m=request.method.upper();u=request.url
   if forbidden(u): out['blocked_forbidden'].append({'method':m,'url':clean_url(u)}); return route.abort()
   if m not in SAFE: out['blocked_nonread'].append({'method':m,'url':clean_url(u)}); return route.abort()
   return route.continue_()
  ctx.route('**/*',guard)
  page=ctx.pages[0] if ctx.pages else ctx.new_page()

  def req_handler(req):
   nonlocal captured_headers
   try:
    p=urlsplit(req.url)
    if req.method.upper()=='GET' and p.netloc=='api.portfoliopersonal.com':
     h={str(k).lower():str(v) for k,v in req.headers.items()}
     if all(k in h for k in AUTH_NAMES) and captured_headers is None:
      captured_headers=dict(req.headers)
      out['generic_auth_capture']={'source_path':p.path,'header_names':sorted(h.keys()),'required_header_names_present':[k for k in AUTH_NAMES if k in h]}
   except Exception:pass
  page.on('request',req_handler)

  page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(6000)
  u=urlsplit(page.url); out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
  if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE' or captured_headers is None:
   ctx.close();Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');return 4

  # Generic page API auth headers should be enough for direct history requests if auth is app-wide.
  for symbol,item,plazo in [('CRESD','794828','1'),('CRESD','794828','2'),('S13N6','926121','1'),('S13N6','926121','2')]:
   url=f'{API}/api/Cotizaciones/Item/{item}/Historico/{plazo}'
   try:
    r=ctx.request.get(url,headers=captured_headers,timeout=30000,fail_on_status_code=False);b=r.body()
    out['known_history'].append({'symbol':symbol,'item_id':item,'plazo':plazo,'status':r.status,**summarize_json_bytes(b,r.headers.get('content-type',''))})
   except Exception as e: out['known_history'].append({'symbol':symbol,'item_id':item,'plazo':plazo,'error':type(e).__name__})

  # Header ablation on known-good CRESD history; values stay only in memory.
  base={str(k).lower():str(v) for k,v in captured_headers.items()}
  target=f'{API}/api/Cotizaciones/Item/794828/Historico/2'
  tests={
   'all_captured':set(),
   'minus_authorization':{'authorization'},
   'minus_authorizedclient':{'authorizedclient'},
   'minus_clientkey':{'clientkey'},
   'only_authorization':set(k for k in base if k not in {'authorization'}),
   'only_authorizedclient':set(k for k in base if k not in {'authorizedclient'}),
   'only_clientkey':set(k for k in base if k not in {'clientkey'}),
   'auth_plus_authorizedclient':set(k for k in base if k not in {'authorization','authorizedclient'}),
   'auth_plus_clientkey':set(k for k in base if k not in {'authorization','clientkey'}),
   'authorizedclient_plus_clientkey':set(k for k in base if k not in {'authorizedclient','clientkey'}),
  }
  for name,drop in tests.items():
   h={k:v for k,v in base.items() if k not in drop}
   try:
    r=ctx.request.get(target,headers=h,timeout=30000,fail_on_status_code=False);out['header_ablation'][name]=r.status
   except Exception as e:out['header_ablation'][name]=type(e).__name__

  # Family quote pages: inspect JSON responses and DOM to recover item IDs without chart interaction.
  for family,symbol,known_id,route in SAMPLES:
   family_responses.clear()
   def resp_handler(resp):
    try:
     p=urlsplit(resp.url)
     if resp.request.method.upper()=='GET' and p.netloc=='api.portfoliopersonal.com' and 'json' in (resp.headers.get('content-type','').lower()):
      try:data=resp.json()
      except Exception:return
      hits=collect_matching_dicts(data,symbol)
      if hits:family_responses.append({'path':p.path,'hits':hits[:10]})
    except Exception:pass
   page.on('response',resp_handler)
   page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(6000)
   dom_id=find_dom_item(page,symbol)
   rec={'family':family,'symbol':symbol,'known_id':known_id,'dom_item_id':dom_id,'api_hits':family_responses[:20]}
   # Derive candidate numeric item id from any safe hit field.
   candidate=known_id or dom_id
   if not candidate:
    for rr in family_responses:
     for hit in rr['hits']:
      for k,v in hit.get('fields',{}).items():
       if 'id' in k.lower() and str(v).isdigit() and len(str(v))>=4:
        candidate=str(v);break
      if candidate:break
     if candidate:break
   rec['candidate_item_id']=candidate
   if candidate:
    for plazo in ('1','2'):
     try:
      r=ctx.request.get(f'{API}/api/Cotizaciones/Item/{candidate}/Historico/{plazo}',headers=captured_headers,timeout=30000,fail_on_status_code=False);b=r.body()
      rec.setdefault('history',{})[plazo]={'status':r.status,**summarize_json_bytes(b,r.headers.get('content-type',''))}
     except Exception as e:rec.setdefault('history',{})[plazo]={'error':type(e).__name__}
   out['family_discovery'].append(rec)
   try:page.remove_listener('response',resp_handler)
   except Exception:pass
  ctx.close()

 p=Path(a.output);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(p,0o600)
 print('AUTH='+out['auth']);print('GENERIC_AUTH='+json.dumps(out['generic_auth_capture'],ensure_ascii=False,separators=(',',':')))
 print('HEADER_ABLATION='+json.dumps(out['header_ablation'],ensure_ascii=False,separators=(',',':')))
 for i,x in enumerate(out['known_history']):print(f'KNOWN_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
 for i,x in enumerate(out['family_discovery']):print(f'FAMILY_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
 print('BLOCKED_NONREAD='+str(len(out['blocked_nonread'])));print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden'])));print('CREDENTIALS_LOGGED=False');print('REAL_ORDERS=0');print('CANONICAL_WRITE=DENY');print('MASS_SCRAPING_STARTED=NO')
 return 0
if __name__=='__main__':raise SystemExit(main())
