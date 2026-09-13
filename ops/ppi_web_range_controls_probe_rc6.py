#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os
from pathlib import Path
from urllib.parse import urlsplit

URL='https://trading.portfoliopersonal.com/Cotizaciones/Item/794828?plazo=2'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar')

def clean_url(u):
 p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u): return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def clean_text(s): return ' '.join(str(s or '').split())[:160]

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 out={'auth':'UNKNOWN','page':'','clicked_range':False,'controls':[],'inputs':[],'json_get':[],'blocked_nonread':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY'}
 target=Path(a.output)
 from playwright.sync_api import sync_playwright
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
  def guard(route,req):
   m=req.method.upper(); u=req.url
   if forbidden(u): out['blocked_forbidden'].append({'method':m,'url':clean_url(u)}); return route.abort()
   if m not in SAFE: out['blocked_nonread'].append({'method':m,'url':clean_url(u)}); return route.abort()
   return route.continue_()
  ctx.route('**/*',guard)
  page=ctx.pages[0] if ctx.pages else ctx.new_page()
  def resp(r):
   try:
    if r.request.method.upper()!='GET' or r.status>=400:return
    if 'json' not in str(r.headers.get('content-type') or '').lower():return
    u=clean_url(r.url); host=urlsplit(u).netloc.lower()
    if host.endswith('portfoliopersonal.com') or host.endswith('ppi.com.ar'): out['json_get'].append({'url':u,'status':r.status})
   except Exception: pass
  page.on('response',resp)
  page.goto(URL,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(7000)
  u=urlsplit(page.url); out['page']=clean_url(page.url); out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
  # click only the read-only range selector visible in the chart toolbar
  candidates=[page.get_by_text('Rango de fechas',exact=True),page.get_by_text('Rango personalizado',exact=True)]
  for loc in candidates:
   try:
    if loc.count() and loc.first.is_visible(): loc.first.click(timeout=3000); out['clicked_range']=True; break
   except Exception: pass
  page.wait_for_timeout(1200)
  # sanitized DOM inventory around date/range controls; no values or hidden data
  seen=set()
  for sel in ('button:visible','a:visible','[role="button"]:visible'):
   try:
    for el in page.locator(sel).all()[:250]:
     text=clean_text(el.inner_text(timeout=300))
     aria=clean_text(el.get_attribute('aria-label'))
     title=clean_text(el.get_attribute('title'))
     blob=' '.join((text,aria,title)).lower()
     if not any(k in blob for k in ('rango','fecha','personal','día','dias','días','semana','mes','año','ir a','1d','5d','1m','3m','6m','1y','ytd','max')): continue
     href=el.get_attribute('href') or ''
     if href: href=urlsplit(href).path
     item=(el.evaluate('(e)=>e.tagName'),text,aria,title,href)
     if item not in seen: seen.add(item); out['controls'].append({'tag':item[0],'text':text,'aria':aria,'title':title,'href':href})
   except Exception: pass
  try:
   for el in page.locator('input:visible').all()[:50]:
    typ=clean_text(el.get_attribute('type')); ph=clean_text(el.get_attribute('placeholder')); aria=clean_text(el.get_attribute('aria-label')); name=clean_text(el.get_attribute('name'))
    # intentionally do not read current values
    out['inputs'].append({'type':typ,'placeholder':ph,'aria':aria,'name':name})
  except Exception: pass
  ctx.close()
 target.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(target,0o600)
 print('AUTH='+out['auth']); print('PAGE='+out['page']); print('CLICKED_RANGE='+str(out['clicked_range']))
 print('CONTROLS='+str(len(out['controls'])))
 for i,x in enumerate(out['controls'][:40]): print('CONTROL_%d=%s' % (i,json.dumps(x,ensure_ascii=False,separators=(',',':'))))
 print('INPUTS='+str(len(out['inputs'])))
 for i,x in enumerate(out['inputs'][:20]): print('INPUT_%d=%s' % (i,json.dumps(x,ensure_ascii=False,separators=(',',':'))))
 print('GET_JSON='+str(len(out['json_get']))); print('BLOCKED_NONREAD='+str(len(out['blocked_nonread']))); print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden']))); print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY')
 return 0 if out['auth']=='AUTHENTICATED_TRUSTED_DEVICE' else 4
if __name__=='__main__': raise SystemExit(main())
