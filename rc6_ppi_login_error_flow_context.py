#!/usr/bin/env python3
"""GET-only public PPI login error-flow inspection.

Reads only the public login JavaScript bundle to map how HTTP/login errors are
classified by the official web client. No credentials are read and no POSTs are
sent. Output is restricted to bounded public-code context and literal UI/error
strings useful for classifying the already-observed 400 response.
"""
from __future__ import annotations
import argparse,json,re
from urllib.parse import urljoin,urlsplit
LOGIN='https://cuenta.portfoliopersonal.com/login'
MARKERS=['ForgotPasswordBlockedUser','setErrors','login(','.login(', 'blocked','bloque','message','isBlocked','usuario y/o contraseña','contraseña incorrecta','usuario incorrecto']
KEY=re.compile(r'block|bloque|usuario|contrase|credencial|incorrect|inv[aá]lid|error|intento|recuper',re.I)

def clean(u:str)->str:
 p=urlsplit(u); return f'{p.scheme}://{p.netloc}{p.path}'
def compact(s:str)->str:
 s=re.sub(r'\s+',' ',s)
 s=re.sub(r'[A-Za-z0-9_-]{90,}','<OPAQUE>',s)
 return s[:3600]

def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 from playwright.sync_api import sync_playwright
 out={'status':'OK_GET_ONLY','credentials_used':False,'post_sent':False,'real_orders_sent':0,'bundle':'','contexts':[],'literals':[]}
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.goto(LOGIN,wait_until='domcontentloaded',timeout=20000); page.wait_for_timeout(600)
  bundle=''
  loc=page.locator('script[src]')
  for i in range(min(loc.count(),100)):
   src=loc.nth(i).get_attribute('src') or ''
   full=urljoin(page.url,src)
   if '/_next/static/chunks/pages/login-' in full: bundle=full; break
  if not bundle:
   ctx.close(); print(json.dumps({**out,'status':'LOGIN_BUNDLE_NOT_FOUND'},sort_keys=True)); return 4
  r=ctx.request.get(bundle,timeout=20000)
  if not r.ok:
   ctx.close(); print(json.dumps({**out,'status':'LOGIN_BUNDLE_FETCH_FAILED','bundle':clean(bundle)},sort_keys=True)); return 4
  text=r.text(); out['bundle']=clean(bundle)
  seen=set()
  for marker in MARKERS:
   start=0
   while True:
    idx=text.lower().find(marker.lower(),start)
    if idx<0: break
    lo=max(0,idx-1800); hi=min(len(text),idx+2600); c=compact(text[lo:hi]); key=(marker,c[:300])
    if key not in seen:
     seen.add(key); out['contexts'].append({'marker':marker,'context':c})
    start=idx+max(1,len(marker))
    if len(out['contexts'])>=30: break
   if len(out['contexts'])>=30: break
  lits=[]
  for token in re.findall(r'["\']([^"\']{4,220})["\']',text):
   if KEY.search(token):
    t=compact(token)
    if t not in lits: lits.append(t)
  out['literals']=lits[:80]; out['contexts']=out['contexts'][:30]
  ctx.close()
 print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
