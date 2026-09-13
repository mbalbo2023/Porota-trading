#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from urllib.parse import urlsplit

ITEM_URL='https://trading.portfoliopersonal.com/Cotizaciones/Item/794828?plazo=2'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar')
DROP=('token','cookie','authorization','session','password','passwd','secret','cuenta','account','dni','cuit','documento','email','telefono','phone','comitente','cliente','usuario','username','user_id')
DATE_KEYS=('date','fecha','datetime','timestamp','time','day','dia','día')
OHLC_KEYS=('openingprice','open','apertura','max','high','maximo','máximo','min','low','minimo','mínimo','price','close','cierre','ultimo','último','volume','volumen')

def clean_url(u):
 p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u): return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def safe_key(k): return not any(x in str(k).lower() for x in DROP)
def walk(v,d=0):
 if d>5:return []
 out=[]
 if isinstance(v,dict):
  out.append(v)
  for k,n in v.items():
   if safe_key(k) and isinstance(n,(dict,list)): out.extend(walk(n,d+1))
 elif isinstance(v,list):
  for n in v[:5000]: out.extend(walk(n,d+1))
 return out
def history_rows(payload):
 rows=[]
 for o in walk(payload):
  low={str(k).lower():v for k,v in o.items() if safe_key(k)}
  if not any(k in low for k in DATE_KEYS): continue
  hits=sum(1 for k in OHLC_KEYS if k in low and low[k] not in (None,''))
  if hits>=2: rows.append(sorted(low.keys())[:30])
  if len(rows)>=20: break
 return rows

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 out={'auth':'UNKNOWN','page':'','json_get':[],'history_candidates':[],'blocked_nonread':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY'}
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
    if r.request.method.upper()!='GET' or r.status>=400:return
    ct=str(r.headers.get('content-type') or '').lower()
    if 'json' not in ct:return
    u=clean_url(r.url); host=urlsplit(u).netloc.lower()
    if not (host.endswith('portfoliopersonal.com') or host.endswith('ppi.com.ar')):return
    p=r.json(); rows=history_rows(p)
    meta={'url':u,'status':r.status,'shape':('list' if isinstance(p,list) else 'object' if isinstance(p,dict) else type(p).__name__)}
    out['json_get'].append(meta)
    if rows: out['history_candidates'].append({'url':u,'status':r.status,'rowlike_count':len(rows),'sample_keys':rows[0]})
   except Exception: pass
  page.on('response',resp)
  page.goto(ITEM_URL,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(12000)
  out['page']=clean_url(page.url); u=urlsplit(page.url)
  out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
  out['title']=page.title()[:120]
  # Sanitize visible range controls only; no click/action.
  txt=[]
  for s in ('text=Rango de fechas','text=Rango personalizado','text=Gráfico','text=Graficador'):
   try:
    if page.locator(s).count(): txt.append(s.replace('text=',''))
   except Exception: pass
  out['visible_markers']=txt
  ctx.close()
 target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
 print('AUTH='+out['auth']); print('PAGE='+out['page']); print('VISIBLE='+','.join(out['visible_markers']))
 print('GET_JSON='+str(len(out['json_get']))); print('HISTORY_CANDIDATES='+str(len(out['history_candidates'])))
 for i,x in enumerate(out['history_candidates'][:10]): print(f"CANDIDATE_{i}={x['url']}|rows={x['rowlike_count']}|keys={','.join(x['sample_keys'])}")
 print('BLOCKED_NONREAD='+str(len(out['blocked_nonread']))); print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden']))); print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY')
 return 0 if out['auth']=='AUTHENTICATED_TRUSTED_DEVICE' else 4
if __name__=='__main__': raise SystemExit(main())
