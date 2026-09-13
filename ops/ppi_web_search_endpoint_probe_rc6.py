#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,re
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl

TRADING='https://trading.portfoliopersonal.com'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
TARGETS=[
 ('ACCIONES','CRESD','/Cotizaciones/Acciones'),
 ('CEDEARS','AVYC','/Cotizaciones/Cedears'),
 ('BONOS','TX28D','/Cotizaciones/Bonos'),
 ('LETRAS','S13N6','/Cotizaciones/Letras'),
 ('ON','MRCTO','/Cotizaciones/Ons'),
 ('OPCIONES','YPFV6100OC','/Cotizaciones/Opciones'),
 ('FUTUROS','DLR/AGO27M','/Cotizaciones/Futuros'),
]

def clean(u):
 p=urlsplit(str(u));return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u):return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def short(v,n=180):return re.sub(r'\s+',' ',str(v or '')).strip()[:n]

def safe_hits(obj,symbol,path='root',depth=0):
 if depth>8:return []
 out=[]
 if isinstance(obj,dict):
  vals=[str(v) for v in obj.values() if isinstance(v,(str,int,float))]
  norm=lambda s:re.sub(r'[^A-Z0-9]','',s.upper())
  symn=norm(symbol)
  if any(symn and symn in norm(v) for v in vals):
   fields={}
   for k,v in obj.items():
    kl=str(k).lower()
    if isinstance(v,(str,int,float,bool,type(None))) and (kl in ('id','item','itemid','instrumentid','instrument_id','ticker','symbol','simbolo','codigo','code','name','nombre','market','mercado','settlement','plazo','tipo','type') or kl.endswith('id')):
     fields[str(k)]=short(v,140)
   out.append({'path':path,'fields':fields})
  for k,v in obj.items():out.extend(safe_hits(v,symbol,f'{path}.{k}',depth+1))
 elif isinstance(obj,list):
  for i,v in enumerate(obj[:2000]):out.extend(safe_hits(v,symbol,f'{path}[{i}]',depth+1))
 return out[:40]

def item_links(page,symbol):
 out=[]
 try:
  links=page.locator('a[href*="/Cotizaciones/Item/"]')
  for i in range(min(links.count(),1800)):
   el=links.nth(i)
   try:
    txt=short(el.inner_text(),180);href=el.get_attribute('href') or ''
    if re.sub(r'[^A-Z0-9]','',symbol.upper()) in re.sub(r'[^A-Z0-9]','',txt.upper()):
     m=re.search(r'/Cotizaciones/Item/(\d+)',href)
     if m:out.append({'id':m.group(1),'text':txt,'href':href.split('?')[0]})
   except Exception:pass
 except Exception:pass
 return out[:20]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--profile',required=True);ap.add_argument('--output',required=True);ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable');a=ap.parse_args()
 out={'auth':'UNKNOWN','navigation_candidates':[],'families':[],'blocked_nonread':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO'}
 from playwright.sync_api import sync_playwright
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
  blocked=set()
  def guard(route,request):
   m=request.method.upper();u=request.url;p=urlsplit(u)
   if forbidden(u):out['blocked_forbidden'].append({'method':m,'path':p.path});return route.abort()
   if m not in SAFE:blocked.add((m,p.netloc,p.path));return route.abort()
   return route.continue_()
  ctx.route('**/*',guard)
  page=ctx.pages[0] if ctx.pages else ctx.new_page()
  page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(3500)
  u=urlsplit(page.url);out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
  if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE':
   ctx.close();Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');return 4

  # Discover FCI/fund navigation routes from current app DOM rather than guessing.
  try:
   links=page.locator('a[href]')
   seen=set()
   for i in range(min(links.count(),1200)):
    el=links.nth(i)
    try:
     href=el.get_attribute('href') or '';txt=short(el.inner_text(),120)
     blob=(txt+' '+href).lower()
     if any(w in blob for w in ('fci','fondo','fondos','inversion')) and href.startswith('/'):
      key=(txt,href.split('?')[0])
      if key not in seen:seen.add(key);out['navigation_candidates'].append({'text':txt,'href':href.split('?')[0]})
    except Exception:pass
  except Exception:pass

  for family,symbol,route in TARGETS:
   rec={'family':family,'symbol':symbol,'route':route,'search_requests':[],'search_responses':[],'dom_hits_before':[],'dom_hits_after':[],'search_input_found':False}
   pending={}
   def on_request(req):
    try:
     p=urlsplit(req.url)
     if req.method.upper()=='GET' and p.netloc=='api.portfoliopersonal.com' and '/api/Cotizaciones/Item/Search' in p.path:
      keys=sorted(set(k for k,v in parse_qsl(p.query,keep_blank_values=True)))
      rec['search_requests'].append({'path':p.path,'query_keys':keys})
      pending[req.url]=True
    except Exception:pass
   def on_response(resp):
    try:
     p=urlsplit(resp.url)
     if resp.request.method.upper()=='GET' and p.netloc=='api.portfoliopersonal.com' and '/api/Cotizaciones/Item/Search' in p.path:
      rr={'path':p.path,'status':resp.status,'query_keys':sorted(set(k for k,v in parse_qsl(p.query,keep_blank_values=True)))}
      try:
       data=resp.json();rr['root_type']=type(data).__name__
       if isinstance(data,dict):rr['root_keys']=sorted(map(str,data.keys()))[:60]
       rr['symbol_hits']=safe_hits(data,symbol)
      except Exception:rr['json']='UNPARSED'
      rec['search_responses'].append(rr)
    except Exception:pass
   page.on('request',on_request);page.on('response',on_response)
   page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(4000)
   rec['dom_hits_before']=item_links(page,symbol)
   try:
    inp=page.locator('input[placeholder="Buscar en la lista"]')
    if inp.count() and inp.first.is_visible():
     rec['search_input_found']=True
     # Try literal and normalized variants without logging request values.
     variants=[symbol]
     normalized=symbol.replace('/',' / ')
     if normalized!=symbol:variants.append(normalized)
     compact=re.sub(r'[^A-Za-z0-9.]','',symbol)
     if compact not in variants:variants.append(compact)
     for v in variants[:3]:
      inp.first.fill(v,timeout=3000);page.wait_for_timeout(2200)
      hits=item_links(page,symbol)
      if hits:
       rec['dom_hits_after']=hits;rec['successful_variant_shape']={'length':len(v),'contains_slash':'/' in v,'contains_spaces':' ' in v};break
      inp.first.fill('',timeout=2000);page.wait_for_timeout(500)
   except Exception as e:rec['input_error']=type(e).__name__
   try:page.remove_listener('request',on_request);page.remove_listener('response',on_response)
   except Exception:pass
   out['families'].append(rec)
  out['blocked_nonread']=[{'method':m,'host':h,'path':p} for m,h,p in sorted(blocked)[:200]]
  ctx.close()
 p=Path(a.output);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(p,0o600)
 print('AUTH='+out['auth']);print('NAV='+json.dumps(out['navigation_candidates'],ensure_ascii=False,separators=(',',':')))
 for i,x in enumerate(out['families']):print(f'FAMILY_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
 print('BLOCKED_NONREAD='+str(len(out['blocked_nonread'])));print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden'])));print('REAL_ORDERS=0');print('CANONICAL_WRITE=DENY');print('MASS_SCRAPING_STARTED=NO')
 return 0
if __name__=='__main__':raise SystemExit(main())
