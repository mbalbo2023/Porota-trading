#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,re
from urllib.parse import urljoin,urlsplit
LOGIN='https://cuenta.portfoliopersonal.com/login'
KEY=re.compile(r'login|auth|captcha|incode|password|usuario|contrase|error|bloque|session|device|disposit',re.I)
URLISH=re.compile(r'(?:(?:https?:)?//[A-Za-z0-9._:-]+/[A-Za-z0-9_./?=&%-]*|/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./?=&%-]+)+)')
def clean(u):
 x=urlsplit(u); return f'{x.scheme}://{x.netloc}{x.path}' if x.scheme else x.path

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 from playwright.sync_api import sync_playwright
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.goto(LOGIN,wait_until='domcontentloaded',timeout=15000); page.wait_for_timeout(1000)
  srcs=[]
  for i in range(min(page.locator('script[src]').count(),80)):
   s=page.locator('script[src]').nth(i).get_attribute('src') or ''
   if s and ('/pages/login-' in s or '/chunks/' in s): srcs.append(urljoin(page.url,s))
  findings=[]
  for src in srcs:
   try:
    r=ctx.request.get(src,timeout=15000)
    if not r.ok: continue
    text=r.text()
    if not KEY.search(text): continue
    paths=[]
    for m in URLISH.findall(text):
     c=clean(m)
     if KEY.search(c) and c not in paths: paths.append(c[:220])
    literals=[]
    for token in re.findall(r'["\']([^"\']{3,160})["\']',text):
     if KEY.search(token) and not any(x in token.lower() for x in ['webpack','sourceMappingURL'.lower()]):
      t=re.sub(r'\s+',' ',token)[:160]
      if t not in literals: literals.append(t)
    findings.append({'script':clean(src),'paths':paths[:30],'signals':literals[:60]})
   except Exception: pass
  ctx.close(); print(json.dumps({'status':'OK_GET_ONLY','findings':findings,'credentials_used':False,'post_sent':False,'real_orders_sent':0},sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
