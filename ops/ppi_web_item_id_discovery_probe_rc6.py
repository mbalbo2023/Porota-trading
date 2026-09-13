#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,re
from pathlib import Path
from urllib.parse import urlsplit

TRADING='https://trading.portfoliopersonal.com'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
TARGETS=[
 ('ACCIONES','CRESD',['/Cotizaciones/Acciones']),
 ('CEDEARS','AVYC',['/Cotizaciones/Cedears']),
 ('BONOS','TX28D',['/Cotizaciones/Bonos']),
 ('LETRAS','S13N6',['/Cotizaciones/Letras']),
 ('ON','MRCTO',['/Cotizaciones/Ons','/Cotizaciones/ON']),
 ('OPCIONES','YPFV6100OC',['/Cotizaciones/Opciones']),
 ('FUTUROS','DLR/AGO27M',['/Cotizaciones/Futuros']),
 ('FCI','PI.RENT.B',['/Cotizaciones/FCI','/Cotizaciones/FondosComunesDeInversion','/Cotizaciones/Fondos']),
]

def clean(u):
 p=urlsplit(str(u));return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u):return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def short(v,n=180):return re.sub(r'\s+',' ',str(v or '')).strip()[:n]

def scan_items(page,symbol):
 out=[]; exact=[]
 try:
  links=page.locator('a[href*="/Cotizaciones/Item/"]')
  for i in range(min(links.count(),1500)):
   el=links.nth(i)
   try:
    href=el.get_attribute('href') or ''; txt=short(el.inner_text(),180)
    m=re.search(r'/Cotizaciones/Item/(\d+)',href)
    if not m:continue
    rec={'id':m.group(1),'text':txt,'href':href.split('?')[0]}
    if len(out)<40:out.append(rec)
    if symbol.lower() in txt.lower():exact.append(rec)
   except Exception:pass
 except Exception:pass
 # Visible exact text whose ancestor is an anchor.
 if not exact:
  try:
   loc=page.get_by_text(symbol,exact=True)
   for i in range(min(loc.count(),30)):
    el=loc.nth(i)
    try:
     href=el.evaluate("e=>{let a=e.closest('a');return a?a.getAttribute('href'):null}")
     if href and '/Cotizaciones/Item/' in href:
      m=re.search(r'/Cotizaciones/Item/(\d+)',href)
      if m:exact.append({'id':m.group(1),'text':symbol,'href':href.split('?')[0]})
    except Exception:pass
  except Exception:pass
 return out,exact

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--profile',required=True);ap.add_argument('--output',required=True);ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable');a=ap.parse_args()
 out={'auth':'UNKNOWN','families':[],'blocked_nonread_paths':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO'}
 blocked=set();get_api=set()
 from playwright.sync_api import sync_playwright
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
  def guard(route,request):
   m=request.method.upper();u=request.url;p=urlsplit(u)
   if forbidden(u):out['blocked_forbidden'].append({'method':m,'path':p.path});return route.abort()
   if m not in SAFE:
    blocked.add((m,p.netloc,p.path));return route.abort()
   if p.netloc=='api.portfoliopersonal.com':get_api.add(p.path)
   return route.continue_()
  ctx.route('**/*',guard)
  page=ctx.pages[0] if ctx.pages else ctx.new_page()
  page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(2500)
  u=urlsplit(page.url);out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
  if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE':
   ctx.close();Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');return 4
  for family,symbol,routes in TARGETS:
   rec={'family':family,'symbol':symbol,'attempts':[],'found':None}
   for route in routes:
    before_block=set(blocked);before_get=set(get_api)
    try:
     page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(5000)
    except Exception as e:
     rec['attempts'].append({'route':route,'nav_error':type(e).__name__});continue
    attempt={'route':route,'final_url':clean(page.url)}
    items,exact=scan_items(page,symbol);attempt['items_before']=items[:15];attempt['exact_before']=exact[:10]
    # Inventory visible inputs, then try symbol in each plausible search/text input one at a time.
    inputs=[]
    try:
     loc=page.locator('input')
     for i in range(min(loc.count(),40)):
      el=loc.nth(i)
      try:
       if not el.is_visible():continue
       d={'i':i,'type':short(el.get_attribute('type'),50),'placeholder':short(el.get_attribute('placeholder'),120),'aria':short(el.get_attribute('aria-label'),120),'name':short(el.get_attribute('name'),120)}
       inputs.append(d)
      except Exception:pass
    except Exception:pass
    attempt['inputs']=inputs
    for d in inputs:
     if d['type'].lower() not in ('','text','search'):continue
     try:
      el=page.locator('input').nth(d['i']);el.fill(symbol,timeout=3000);page.wait_for_timeout(1800)
      _,hits=scan_items(page,symbol)
      if hits:
       attempt['search_input']=d;attempt['exact_after']=hits[:10];rec['found']=hits[0];break
      el.fill('',timeout=2000)
     except Exception:pass
    attempt['blocked_new']=[{'method':x[0],'host':x[1],'path':x[2]} for x in sorted(blocked-before_block)[:40]]
    attempt['get_api_new']=sorted(get_api-before_get)[:60]
    rec['attempts'].append(attempt)
    if rec['found']:break
   out['families'].append(rec)
  ctx.close()
 out['blocked_nonread_paths']=[{'method':m,'host':h,'path':p} for m,h,p in sorted(blocked)[:250]]
 p=Path(a.output);p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');os.chmod(p,0o600)
 print('AUTH='+out['auth'])
 for i,x in enumerate(out['families']):print(f'FAMILY_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
 print('BLOCKED_UNIQUE='+str(len(blocked)))
 for i,x in enumerate(out['blocked_nonread_paths'][:120]):print(f'BLOCKED_{i}='+json.dumps(x,ensure_ascii=False,separators=(',',':')))
 print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden'])));print('REAL_ORDERS=0');print('CANONICAL_WRITE=DENY');print('MASS_SCRAPING_STARTED=NO')
 return 0
if __name__=='__main__':raise SystemExit(main())
