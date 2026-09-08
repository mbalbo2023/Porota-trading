#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from urllib.parse import urljoin,urlsplit
LOGIN='https://cuenta.portfoliopersonal.com/login'

def clean(url):
    u=urlsplit(url); return f'{u.scheme}://{u.netloc}{u.path}' if u.scheme else u.path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',args=['--no-sandbox','--disable-dev-shm-usage'])
        page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.set_default_timeout(8000); page.set_default_navigation_timeout(15000)
        reqs=[]
        def req(r):
            try:
                u=urlsplit(r.url)
                if len(reqs)<150: reqs.append({'method':r.method.upper(),'host':u.netloc,'path':u.path[:180],'resource':r.resource_type})
            except Exception: pass
        page.on('request',req)
        page.goto(LOGIN,wait_until='domcontentloaded',timeout=15000); page.wait_for_timeout(2500)
        forms=[]
        for i in range(min(page.locator('form').count(),10)):
            f=page.locator('form').nth(i)
            forms.append({'method':(f.get_attribute('method') or 'GET').upper(),'action':clean(urljoin(page.url,f.get_attribute('action') or ''))})
        inputs=[]
        for i in range(min(page.locator('input').count(),30)):
            x=page.locator('input').nth(i)
            inputs.append({'type':x.get_attribute('type') or 'text','name':x.get_attribute('name') or '', 'autocomplete':x.get_attribute('autocomplete') or ''})
        buttons=[]
        for i in range(min(page.locator('button').count(),20)):
            b=page.locator('button').nth(i)
            buttons.append({'type':b.get_attribute('type') or '', 'formaction':clean(urljoin(page.url,b.get_attribute('formaction') or '')) if b.get_attribute('formaction') else '', 'text_class':'INGRESAR' if 'ingresar' in (b.inner_text() or '').lower() else 'OTHER'})
        scripts=[]
        for i in range(min(page.locator('script[src]').count(),80)):
            s=page.locator('script[src]').nth(i).get_attribute('src') or ''
            if s: scripts.append(clean(urljoin(page.url,s)))
        out={'status':'OK_GET_ONLY','page':clean(page.url),'forms':forms,'inputs':inputs,'buttons':buttons,'requests':reqs[-80:],'scripts':scripts[-40:],'credentials_used':False,'post_sent':False,'real_orders_sent':0}
        ctx.close(); print(json.dumps(out,sort_keys=True)); return 0
if __name__=='__main__': raise SystemExit(main())
