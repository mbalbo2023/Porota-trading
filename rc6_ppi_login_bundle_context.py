#!/usr/bin/env python3
"""GET-only focused inspection of PPI public login JavaScript.

Resolves the public login API base plus the structural/custom header helper used
by the login client. No credentials and no POSTs are used.
"""
from __future__ import annotations
import argparse,json,re
from urllib.parse import urljoin,urlsplit
LOGIN='https://cuenta.portfoliopersonal.com/login'
MARKERS=['/api/Seguridad/Auth/Login','https://api.portfoliopersonal.com/','14734:function','cp:','fm:','baseURL']
ABSURL=re.compile(r'https://[A-Za-z0-9._:-]+(?:/[A-Za-z0-9_./?=&%:-]*)?')
def clean_url(u:str)->str:
 p=urlsplit(u); return f'{p.scheme}://{p.netloc}{p.path}' if p.scheme else p.path
def squash(s:str)->str:
 s=re.sub(r'\s+',' ',s); s=re.sub(r'([A-Za-z0-9_-]{80,})','<OPAQUE>',s); return s[:3000]
def main()->int:
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 from playwright.sync_api import sync_playwright
 out={'status':'OK_GET_ONLY','credentials_used':False,'post_sent':False,'real_orders_sent':0,'scripts':[],'absolute_urls':[],'contexts':[]}
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.goto(LOGIN,wait_until='domcontentloaded',timeout=20000); page.wait_for_timeout(700)
  scripts=[]; loc=page.locator('script[src]')
  for i in range(min(loc.count(),100)):
   src=loc.nth(i).get_attribute('src') or ''
   if src and '/_next/static/' in urljoin(page.url,src): scripts.append(urljoin(page.url,src))
  for src in scripts:
   try:
    r=ctx.request.get(src,timeout=20000)
    if not r.ok: continue
    text=r.text(); hit=False
    for marker in MARKERS:
     start=0
     while True:
      idx=text.find(marker,start)
      if idx<0: break
      hit=True; lo=max(0,idx-1600); hi=min(len(text),idx+2200)
      out['contexts'].append({'script':clean_url(src),'marker':marker,'context':squash(text[lo:hi])}); start=idx+len(marker)
      if len(out['contexts'])>=32: break
     if len(out['contexts'])>=32: break
    if hit: out['scripts'].append(clean_url(src))
    for u in ABSURL.findall(text):
     cu=clean_url(u)
     if cu not in out['absolute_urls'] and ('portfolio' in cu.lower() or 'api' in cu.lower()): out['absolute_urls'].append(cu[:240])
   except Exception: pass
  ctx.close()
 out['scripts']=out['scripts'][:20]; out['absolute_urls']=out['absolute_urls'][:40]; out['contexts']=out['contexts'][:32]
 print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
