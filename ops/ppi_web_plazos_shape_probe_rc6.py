#!/usr/bin/env python3
"""Read-only sanitized probe for PPI Web settlement-term metadata."""
import argparse
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

TRADING='https://trading.portfoliopersonal.com'
API='https://api.portfoliopersonal.com'
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
SAFE_KEYS=('id','value','descripcion','description','label','nombre','name','plazo','term','codigo','code','abreviatura')
DROP=('account','cuenta','saldo','tenencia','comitente','cliente','dni','cuit','mail','email','telefono','token','password','cookie','authorization','secret','session','usuario','user')

def forbidden(url): return any(x in urlsplit(url).path.lower() for x in FORBIDDEN)
def sanitized(node,path='',depth=0,out=None):
    if out is None: out=[]
    if depth>6 or len(out)>=120: return out
    if isinstance(node,dict):
        vals={}
        for k,v in node.items():
            kl=str(k).lower()
            if any(d in kl for d in DROP): continue
            if any(s==kl or s in kl for s in SAFE_KEYS) and isinstance(v,(str,int,float,bool)):
                vals[str(k)]=v
        if vals: out.append((path or '$',vals))
        for k,v in node.items():
            kl=str(k).lower()
            if any(d in kl for d in DROP): continue
            if isinstance(v,(dict,list)): sanitized(v,f'{path}.{k}' if path else str(k),depth+1,out)
    elif isinstance(node,list):
        for i,v in enumerate(node[:50]): sanitized(v,f'{path}[{i}]',depth+1,out)
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); args=ap.parse_args()
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=args.profile,executable_path='/usr/bin/google-chrome-stable',headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',args=['--no-sandbox','--disable-dev-shm-usage'])
        blocked=0
        def guard(route,req):
            nonlocal blocked
            if forbidden(req.url) or req.method.upper() not in ('GET','HEAD','OPTIONS'): blocked+=1; return route.abort()
            route.continue_()
        ctx.route('**/*',guard); page=ctx.pages[0] if ctx.pages else ctx.new_page(); headers={}
        def cap(req):
            if req.method.upper()=='GET' and urlsplit(req.url).netloc=='api.portfoliopersonal.com':
                cand={k:v for k,v in req.headers.items() if k.lower() in ('accept','authorizedclient','clientkey','content-type','origin','referer','user-agent','authorization')}
                if len(cand)>len(headers): headers.clear(); headers.update(cand)
        page.on('request',cap); page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(2500)
        auth=urlsplit(page.url).netloc=='trading.portfoliopersonal.com' and 'login' not in urlsplit(page.url).path.lower()
        print('AUTH='+('AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH'))
        if not auth or not headers: return 4
        rq=ctx.request
        for item,label in [('261','GGAL'),('788105','AVYC'),('804489','TX28D')]:
            r=rq.get(API+'/api/Cotizaciones/Item/PlazosOperables',headers=headers,params={'itemId':item},timeout=20000)
            print(f'ITEM={label}|HTTP={r.status}')
            if r.status==200 and 'json' in (r.headers.get('content-type') or '').lower():
                for p,v in sanitized(r.json()): print(f'PLAZO_META|{label}|PATH={p}|FIELDS={v}')
        print(f'BLOCKED={blocked}'); print('CREDENTIAL_VALUES_LOGGED=False'); print('QUERY_STRINGS_LOGGED=False'); print('CANONICAL_WRITE=DENY'); print('REAL_ORDERS=0')
        ctx.close(); return 0
if __name__=='__main__': raise SystemExit(main())
