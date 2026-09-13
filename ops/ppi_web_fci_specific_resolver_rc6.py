#!/usr/bin/env python3
import argparse, json
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

TRADING='https://trading.portfoliopersonal.com'
API_HOST='api.portfoliopersonal.com'
TARGETS=['PI.RENT.B','PI RENT B','PI.RENT','PIRENTB']
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')


def pth(url): return urlsplit(url).path

def forbidden(url):
    p=pth(url).lower(); return any(x in p for x in FORBIDDEN)

def flatten(obj, out=None, depth=0):
    if out is None: out=[]
    if depth>12: return out
    if isinstance(obj,dict):
        out.append(obj)
        for v in obj.values(): flatten(v,out,depth+1)
    elif isinstance(obj,list):
        for v in obj[:10000]: flatten(v,out,depth+1)
    return out

def norm(s): return ''.join(ch for ch in str(s or '').upper() if ch.isalnum())

def candidate(d):
    if not isinstance(d,dict): return None
    vals=[]
    for k in ('ticker','simbolo','symbol','codigo','descripcion','description','nombre','name'):
        if d.get(k) is not None: vals.append(str(d.get(k)))
    text=' '.join(vals)
    if not any(norm(t) and norm(t) in norm(text) for t in TARGETS): return None
    iid=d.get('id') or d.get('itemId') or d.get('instrumentId') or d.get('idItem')
    clase=d.get('claseFCIId')
    car=d.get('caracteristicas')
    if isinstance(car,dict) and clase is None: clase=car.get('claseFCIId') or car.get('claseId')
    tipo=d.get('tipoItem')
    tid=tipo.get('id') if isinstance(tipo,dict) else d.get('tipoItemId') or d.get('typeId')
    return {'id':iid,'clase':clase,'type_id':tid,'text':text[:160]}


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); args=ap.parse_args()
    blocked_nonread=blocked_forbidden=0
    seen=[]; found=[]
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=args.profile,executable_path='/usr/bin/google-chrome-stable',headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
        def guard(route,request):
            nonlocal blocked_nonread,blocked_forbidden
            if forbidden(request.url): blocked_forbidden+=1; return route.abort()
            if request.method.upper() not in ('GET','HEAD','OPTIONS'): blocked_nonread+=1; return route.abort()
            return route.continue_()
        ctx.route('**/*',guard)
        page=ctx.pages[0] if ctx.pages else ctx.new_page()
        def onresp(resp):
            try:
                if resp.request.method.upper()!='GET': return
                u=urlsplit(resp.url)
                if u.netloc!=API_HOST: return
                ct=(resp.headers.get('content-type') or '').lower()
                if 'json' not in ct: return
                body=resp.body()
                if len(body)>5000000: return
                data=json.loads(body.decode('utf-8'))
                rec={'status':resp.status,'path':u.path,'count':0}
                hits=[]
                for d in flatten(data):
                    c=candidate(d)
                    if c: hits.append(c)
                rec['count']=len(hits)
                if hits:
                    for c in hits[:20]: found.append((u.path,resp.status,c))
                seen.append(rec)
            except Exception:
                pass
        page.on('response',onresp)
        page.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(3500)
        auth=urlsplit(page.url).netloc=='trading.portfoliopersonal.com' and 'login' not in pth(page.url).lower()
        print('PPI_FCI_SPECIFIC_RESOLVER')
        print('AUTH=' + ('AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH_SESSION_EXPIRED'))
        if not auth: ctx.close(); return 20
        for route in ('/Cotizaciones/FCIs','/Cotizaciones/FCIsExterior'):
            try:
                r=page.goto(TRADING+route,wait_until='domcontentloaded',timeout=45000)
                print(f'PAGE|{route}|HTTP={r.status if r else 0}')
                page.wait_for_timeout(7000)
                # Use only visible/read-only search controls if present.
                for sel in ('input[placeholder*="Buscar" i]','input[type="search"]'):
                    loc=page.locator(sel)
                    for i in range(min(loc.count(),6)):
                        if loc.nth(i).is_visible():
                            for t in TARGETS[:2]:
                                try:
                                    loc.nth(i).fill(t,timeout=2500); page.wait_for_timeout(3500)
                                except Exception: pass
                            break
            except Exception:
                pass
        # summarize API paths, without query strings or bodies
        ded=[]; s=set()
        for x in seen:
            key=(x['status'],x['path'],x['count'])
            if key not in s:
                s.add(key); ded.append(x)
        for x in ded[:80]: print(f"API|HTTP={x['status']}|PATH={x['path']}|TARGET_HITS={x['count']}")
        # dedupe candidates
        out=[]; s=set()
        for ep,st,c in found:
            key=(ep,str(c.get('id')),str(c.get('clase')),str(c.get('type_id')),c.get('text'))
            if key not in s:
                s.add(key); out.append((ep,st,c))
        for ep,st,c in out[:30]:
            print(f"FCI_CANDIDATE|HTTP={st}|ENDPOINT={ep}|ITEM={c.get('id')}|CLASE_FCI_ID={c.get('clase')}|TYPE_ID={c.get('type_id')}|TEXT={c.get('text')}")
        print('FCI_RESOLVED=' + ('YES' if out else 'NO'))
        print(f'BLOCKED_NONREAD={blocked_nonread}')
        print(f'BLOCKED_FORBIDDEN={blocked_forbidden}')
        print('CREDENTIAL_VALUES_LOGGED=False')
        print('QUERY_STRINGS_LOGGED=False')
        print('CANONICAL_WRITE=DENY')
        print('REAL_ORDERS=0')
        print('MASS_SCRAPING_STARTED=NO')
        ctx.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
