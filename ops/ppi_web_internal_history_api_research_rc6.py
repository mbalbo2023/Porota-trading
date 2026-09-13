#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re
from pathlib import Path
from urllib.parse import urlsplit

SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
API='https://api.portfoliopersonal.com'
SAMPLES=[
 ('ACCIONES','CRESD','/Cotizaciones/Acciones'),
 ('CEDEARS','AVYC','/Cotizaciones/Cedears'),
 ('BONOS','TX28D','/Cotizaciones/Bonos'),
 ('LETRAS','S13N6','/Cotizaciones/Letras'),
 ('ON','MRCTO','/Cotizaciones/Ons'),
 ('OPCIONES','YPFV6100OC','/Cotizaciones/Opciones'),
 ('FUTUROS','DLR/AGO27M','/Cotizaciones/Futuros'),
 ('FCI','PI.RENT.B','/Cotizaciones/FondosComunesDeInversion'),
]

def clean_url(u):
 p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u):
 return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def short(v,n=160): return re.sub(r'\s+',' ',str(v or '')).strip()[:n]

def summarize_json(data):
 out={'type':type(data).__name__}
 rows=[]
 if isinstance(data,list): rows=data
 elif isinstance(data,dict):
  out['top_keys']=sorted(map(str,data.keys()))[:60]
  for k in ('data','items','result','results','historico','history','series'):
   v=data.get(k)
   if isinstance(v,list): rows=v; out['row_container']=k; break
 out['rows']=len(rows)
 if rows:
  sample=rows[0]
  if isinstance(sample,dict):
   out['row_keys']=sorted(map(str,sample.keys()))[:80]
   date_keys=[k for k in sample.keys() if str(k).lower() in ('date','fecha','datetime','time','timestamp')]
   vals=[]
   for r in rows:
    if not isinstance(r,dict): continue
    for k in date_keys:
     if r.get(k) not in (None,''): vals.append(str(r.get(k)))
   if vals: out['date_min']=min(vals); out['date_max']=max(vals)
 return out

def find_item_id(page, route, symbol):
 page.goto('https://trading.portfoliopersonal.com'+route,wait_until='domcontentloaded',timeout=45000)
 page.wait_for_timeout(5000)
 # Exact symbol via anchor/button first, then any link containing /Cotizaciones/Item/.
 for loc in [page.get_by_role('link',name=symbol,exact=True), page.get_by_text(symbol,exact=True), page.locator(f'a:has-text("{symbol}")')]:
  try:
   for i in range(min(loc.count(),20)):
    el=loc.nth(i)
    href=el.get_attribute('href')
    if href and '/Cotizaciones/Item/' in href:
     m=re.search(r'/Cotizaciones/Item/(\d+)',href)
     if m: return m.group(1), href
  except Exception: pass
 # Search links whose visible text contains symbol.
 try:
  links=page.locator('a[href*="/Cotizaciones/Item/"]')
  for i in range(min(links.count(),600)):
   el=links.nth(i)
   try:
    txt=short(el.inner_text(),120)
    href=el.get_attribute('href') or ''
    if symbol.lower() in txt.lower():
     m=re.search(r'/Cotizaciones/Item/(\d+)',href)
     if m: return m.group(1), href
   except Exception: pass
 except Exception: pass
 return None,None

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 out={'auth':'UNKNOWN','creds_exposed':False,'orders_visited':False,'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO','samples':[],'blocked_nonread':[],'blocked_forbidden':[]}
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
  page.goto('https://trading.portfoliopersonal.com/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(3000)
  u=urlsplit(page.url)
  out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
  if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE':
   ctx.close(); Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); return 4

  # First prove exact CRESD endpoint semantics for both settlement codes.
  targets=[('ACCIONES','CRESD','794828')]
  # Then discover item ids for representative residual families via read-only quote pages.
  for family,symbol,route in SAMPLES[1:]:
   item,href=find_item_id(page,route,symbol)
   targets.append((family,symbol,item))

  for family,symbol,item in targets:
   rec={'family':family,'symbol':symbol,'item_id':item,'plazos':{}}
   if not item:
    rec['discovery']='ITEM_ID_NOT_FOUND'
    out['samples'].append(rec); continue
   for plazo in ('1','2'):
    path=f'/api/Cotizaciones/Item/{item}/Historico/{plazo}'
    url=API+path
    try:
     resp=ctx.request.get(url,timeout=30000,fail_on_status_code=False)
     body=resp.body()
     info={'status':resp.status,'content_type':short(resp.headers.get('content-type'),100),'bytes':len(body),'path':path,'sha256':hashlib.sha256(body).hexdigest()}
     try:
      data=resp.json(); info.update(summarize_json(data))
     except Exception:
      info['json_parse']='FAILED'
     rec['plazos'][plazo]=info
    except Exception as e:
     rec['plazos'][plazo]={'error':type(e).__name__,'path':path}
   out['samples'].append(rec)
  ctx.close()

 p=Path(a.output); p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(p,0o600)
 print('AUTH='+out['auth'])
 for i,r in enumerate(out['samples']): print(f'SAMPLE_{i}='+json.dumps(r,ensure_ascii=False,separators=(',',':')))
 print('BLOCKED_NONREAD='+str(len(out['blocked_nonread'])))
 print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden'])))
 print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY'); print('MASS_SCRAPING_STARTED=NO')
 return 0
if __name__=='__main__': raise SystemExit(main())
