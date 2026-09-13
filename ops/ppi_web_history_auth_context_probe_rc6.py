#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re
from pathlib import Path
from urllib.parse import urlsplit

SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
TRADING='https://trading.portfoliopersonal.com'
API='https://api.portfoliopersonal.com'
TARGETS=[('CRESD','794828','2'),('S13N6','926121','2')]
SENSITIVE={'authorization','proxy-authorization','cookie','set-cookie','x-api-key','x-auth-token','x-access-token'}
SAFE_HEADER_NAMES={'accept','accept-language','content-type','origin','referer','sec-fetch-dest','sec-fetch-mode','sec-fetch-site','user-agent','x-requested-with'}

def clean_url(u):
 p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'

def forbidden(u):
 return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)

def short(v,n=180): return re.sub(r'\s+',' ',str(v or '')).strip()[:n]

def summarize_body(body, content_type=''):
 out={'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'content_type':short(content_type,120)}
 try:
  data=json.loads(body.decode('utf-8'))
  out['json_type']=type(data).__name__
  rows=[]
  if isinstance(data,list): rows=data
  elif isinstance(data,dict):
   out['top_keys']=sorted(map(str,data.keys()))[:50]
   for k in ('data','items','result','results','historico','history','series'):
    if isinstance(data.get(k),list): rows=data[k]; out['row_container']=k; break
  out['rows']=len(rows)
  if rows and isinstance(rows[0],dict): out['row_keys']=sorted(map(str,rows[0].keys()))[:60]
 except Exception:
  out['json_parse']='FAILED'
 return out

def click_exact(scope,label):
 makers=[
  lambda s:s.get_by_role('button',name=label,exact=True),
  lambda s:s.get_by_text(label,exact=True),
  lambda s:s.locator(f'button:has-text("{label}")'),
  lambda s:s.locator(f'[data-name*="{label}" i]'),
 ]
 for m in makers:
  try:
   loc=m(scope)
   for i in range(min(loc.count(),10)):
    if loc.nth(i).is_visible(): loc.nth(i).click(timeout=4000); return True
  except Exception: pass
 return False

def safe_header_summary(headers):
 low={str(k).lower():str(v) for k,v in headers.items()}
 return {
  'names':sorted(low.keys()),
  'sensitive_present':sorted([k for k in low if k in SENSITIVE or 'token' in k or 'auth' in k or 'cookie' in k]),
  'safe_values':{k:short(v,180) for k,v in low.items() if k in SAFE_HEADER_NAMES},
 }

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 out={'auth':'UNKNOWN','targets':[],'storage_key_names':{},'blocked_nonread':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY','mass_scraping_started':'NO','credentials_logged':False}
 captured={}
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

  def on_request(req):
   try:
    p=urlsplit(req.url)
    if req.method.upper()=='GET' and p.netloc=='api.portfoliopersonal.com' and '/api/Cotizaciones/Item/' in p.path and '/Historico/' in p.path:
     captured[p.path]={'url':req.url,'headers':dict(req.headers),'method':req.method}
   except Exception: pass
  page.on('request',on_request)

  page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(2500)
  u=urlsplit(page.url)
  out['auth']='AUTHENTICATED_TRUSTED_DEVICE' if u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower() else 'BLOCKED_AUTH_SESSION_EXPIRED'
  if out['auth']!='AUTHENTICATED_TRUSTED_DEVICE':
   ctx.close(); Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); return 4

  # Storage metadata only: keys, never values.
  try:
   out['storage_key_names']['localStorage']=sorted(page.evaluate('() => Object.keys(localStorage)'))[:100]
   out['storage_key_names']['sessionStorage']=sorted(page.evaluate('() => Object.keys(sessionStorage)'))[:100]
  except Exception: pass

  for symbol,item,plazo in TARGETS:
   rec={'symbol':symbol,'item_id':item,'plazo':plazo,'ui_trigger':{},'captured_request':None,'captured_response':None,'replays':{}}
   item_url=f'{TRADING}/Cotizaciones/Item/{item}?plazo={plazo}'
   page.goto(item_url,wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(5500)
   rec['ui_trigger']['graficador']=click_exact(page,'Graficador'); page.wait_for_timeout(4000)
   tv=[]
   for fr in page.frames:
    try:
     if 'tradingview' in (fr.name or '').lower() or fr.url.lower().startswith('blob:'): tv.append(fr)
    except Exception: pass
   range_clicked=False
   for fr in tv+[page]:
    if click_exact(fr,'Rango de fechas'):
     range_clicked=True; break
   rec['ui_trigger']['rango_de_fechas']=range_clicked
   page.wait_for_timeout(4500)

   path=f'/api/Cotizaciones/Item/{item}/Historico/{plazo}'
   cap=captured.get(path)
   if cap:
    hs=safe_header_summary(cap['headers'])
    rec['captured_request']={'path':path,'header_summary':hs}
    # Find the original successful browser response by triggering once more if needed.
    try:
     with page.expect_response(lambda r: urlsplit(r.url).path==path and r.request.method.upper()=='GET',timeout=12000) as ri:
      # safe UI-only retrigger: switch away/back if range popup consumed the first call
      if tv:
       try:
        click_exact(page,'Detalles'); page.wait_for_timeout(500); click_exact(page,'Graficador')
       except Exception: pass
      page.wait_for_timeout(1000)
     rr=ri.value; body=rr.body(); rec['captured_response']={'status':rr.status,**summarize_body(body,rr.headers.get('content-type',''))}
    except Exception:
     # If no second call, response may already have happened; replay matrix below still tests exact headers.
     pass

    exact_headers=dict(cap['headers'])
    safe_headers={k:v for k,v in exact_headers.items() if str(k).lower() in SAFE_HEADER_NAMES}
    # Matrix A: BrowserContext request + exact in-memory captured headers. Values never logged/persisted.
    try:
     r=ctx.request.get(cap['url'],headers=exact_headers,timeout=30000,fail_on_status_code=False); b=r.body()
     rec['replays']['context_exact_headers']={'status':r.status,**summarize_body(b,r.headers.get('content-type',''))}
    except Exception as e: rec['replays']['context_exact_headers']={'error':type(e).__name__}
    # Matrix B: context cookies/session only, no copied headers.
    try:
     r=ctx.request.get(cap['url'],timeout=30000,fail_on_status_code=False); b=r.body()
     rec['replays']['context_no_extra_headers']={'status':r.status,**summarize_body(b,r.headers.get('content-type',''))}
    except Exception as e: rec['replays']['context_no_extra_headers']={'error':type(e).__name__}
    # Matrix C: only explicitly non-sensitive browser headers.
    try:
     r=ctx.request.get(cap['url'],headers=safe_headers,timeout=30000,fail_on_status_code=False); b=r.body()
     rec['replays']['context_safe_headers_only']={'status':r.status,**summarize_body(b,r.headers.get('content-type',''))}
    except Exception as e: rec['replays']['context_safe_headers_only']={'error':type(e).__name__}
    # Matrix D: in-page fetch without copied auth headers; browser handles CORS/cookies naturally.
    try:
     fr=page.evaluate("""async (u) => {try {const r=await fetch(u,{method:'GET',credentials:'include'}); const t=await r.text(); return {status:r.status,bytes:new TextEncoder().encode(t).length,ctype:r.headers.get('content-type')||''};} catch(e){return {error:e.name};}}""",cap['url'])
     rec['replays']['page_fetch_credentials_include']=fr
    except Exception as e: rec['replays']['page_fetch_credentials_include']={'error':type(e).__name__}
   else:
    rec['captured_request']={'path':path,'captured':False}
   out['targets'].append(rec)
  ctx.close()

 p=Path(a.output); p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(p,0o600)
 print('AUTH='+out['auth'])
 print('STORAGE_KEYS='+json.dumps(out['storage_key_names'],ensure_ascii=False,separators=(',',':')))
 for i,r in enumerate(out['targets']): print(f'TARGET_{i}='+json.dumps(r,ensure_ascii=False,separators=(',',':')))
 print('BLOCKED_NONREAD='+str(len(out['blocked_nonread']))); print('BLOCKED_FORBIDDEN='+str(len(out['blocked_forbidden'])))
 print('CREDENTIALS_LOGGED=False'); print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY'); print('MASS_SCRAPING_STARTED=NO')
 return 0
if __name__=='__main__': raise SystemExit(main())
