#!/usr/bin/env python3
"""GET-only focused inspection of PPI public login JavaScript.

Goal: resolve the actual API base host/prefix used by the public login bundle
for /api/Seguridad/Auth/Login without using credentials or sending POSTs.
Only public JS context, sanitized URLs and structural hints are printed.
"""
from __future__ import annotations
import argparse,json,re
from urllib.parse import urljoin,urlsplit

LOGIN='https://cuenta.portfoliopersonal.com/login'
MARKERS=['/api/Seguridad/Auth/Login','baseURL','axios.create','NEXT_PUBLIC','API_URL','apiUrl','baseUrl']
ABSURL=re.compile(r'https://[A-Za-z0-9._:-]+(?:/[A-Za-z0-9_./?=&%:-]*)?')

def clean_url(u:str)->str:
    p=urlsplit(u)
    return f'{p.scheme}://{p.netloc}{p.path}' if p.scheme else p.path

def squash(s:str)->str:
    s=re.sub(r'\s+',' ',s)
    # avoid accidentally surfacing long opaque values from bundles
    s=re.sub(r'([A-Za-z0-9_-]{80,})','<OPAQUE>',s)
    return s[:2600]

def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    from playwright.sync_api import sync_playwright
    out={'status':'OK_GET_ONLY','credentials_used':False,'post_sent':False,'real_orders_sent':0,'scripts':[],'absolute_urls':[],'contexts':[]}
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.goto(LOGIN,wait_until='domcontentloaded',timeout=20000); page.wait_for_timeout(800)
        scripts=[]
        loc=page.locator('script[src]')
        for i in range(min(loc.count(),100)):
            src=loc.nth(i).get_attribute('src') or ''
            if src:
                full=urljoin(page.url,src)
                if '/_next/static/' in full: scripts.append(full)
        for src in scripts:
            try:
                r=ctx.request.get(src,timeout=20000)
                if not r.ok: continue
                text=r.text()
                hit=False
                for marker in MARKERS:
                    start=0
                    while True:
                        idx=text.find(marker,start)
                        if idx<0: break
                        hit=True
                        lo=max(0,idx-1400); hi=min(len(text),idx+1800)
                        out['contexts'].append({'script':clean_url(src),'marker':marker,'context':squash(text[lo:hi])})
                        start=idx+len(marker)
                        if len(out['contexts'])>=24: break
                    if len(out['contexts'])>=24: break
                if hit: out['scripts'].append(clean_url(src))
                for u in ABSURL.findall(text):
                    cu=clean_url(u)
                    if cu not in out['absolute_urls'] and ('portfolio' in cu.lower() or 'api' in cu.lower()): out['absolute_urls'].append(cu[:240])
                if len(out['contexts'])>=24: break
            except Exception:
                pass
        ctx.close()
    out['scripts']=out['scripts'][:20]; out['absolute_urls']=out['absolute_urls'][:40]; out['contexts']=out['contexts'][:24]
    print(json.dumps(out,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
