#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re
from pathlib import Path
from urllib.parse import urlsplit

LOGIN='https://cuenta.portfoliopersonal.com/login'
TRADING='https://trading.portfoliopersonal.com/'
PPI_HOSTS={'cuenta.portfoliopersonal.com','trading.portfoliopersonal.com'}
ORDER_HINT=re.compile(r'(^|/)(operar|orden|orders?|trade|confirm|cancel|transfer|suscribir|rescatar)(/|$)',re.I)
BAD_CRED=re.compile(r'(usuario|contrase(?:n|ñ)a|credencial|datos).{0,100}(incorrect|inv[aá]lid|err[oó]ne|no coincide)|incorrect.{0,100}(usuario|contrase(?:n|ñ)a|credencial)',re.I|re.S)
CAPTCHA=re.compile(r'captcha|recaptcha|no soy un robot|verific.{0,50}humano|challenge',re.I|re.S)
LOCKED=re.compile(r'bloquead|demasiados intentos|intentos fallidos|cuenta suspendid',re.I|re.S)
MAINT=re.compile(r'mantenimiento|temporalmente no disponible|intente m[aá]s tarde',re.I|re.S)
TRUST=re.compile(r'dispositivo.{0,80}(confianza|confiable|seguro)|(confiar|recordar|verificar).{0,80}dispositivo',re.I|re.S)
NOW_NOT=re.compile(r'ahora\s+no|no\s+ahora|m[aá]s\s+tarde|omitir',re.I)
OTP=re.compile(r'(otp|pin|token|c[oó]digo).{0,100}(mail|correo|email|verific|seguridad)|doble factor|segundo factor',re.I|re.S)

def secret(path:Path):
    d={}
    for ln in path.read_text(encoding='utf-8').splitlines():
        if '=' in ln and not ln.lstrip().startswith('#'):
            k,v=ln.split('=',1); d[k.strip()]=v.strip().strip('"').strip("'")
    return d.get('PPI_WEB_USERNAME',''),d.get('PPI_WEB_PASSWORD','')

def first_visible(page, sels):
    for s in sels:
        try:
            q=page.locator(s)
            for i in range(min(q.count(),8)):
                n=q.nth(i)
                if n.is_visible(): return n
        except Exception: pass
    return None

def body(page):
    try:return page.locator('body').inner_text(timeout=3000)[:25000]
    except Exception:return ''

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--secret',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
    user,pwd=secret(Path(a.secret))
    if not user or not pwd:
        print(json.dumps({'status':'BLOCKED_LOCAL_SECRET_INCOMPLETE','credentials_exposed':False,'real_orders_sent':0})); return 4
    from playwright.sync_api import sync_playwright
    first_party=[]; blocked_first_party=[]
    with sync_playwright() as pw:
        ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',args=['--no-sandbox','--disable-dev-shm-usage'])
        page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.set_default_timeout(8000); page.set_default_navigation_timeout(15000)
        def guard(route,request):
            u=urlsplit(request.url); m=request.method.upper()
            if u.scheme!='https': return route.abort()
            if m in {'GET','HEAD','OPTIONS'}:
                if u.netloc in PPI_HOSTS and ORDER_HINT.search(u.path.lower()): return route.abort()
                return route.continue_()
            if m=='POST' and u.netloc=='cuenta.portfoliopersonal.com' and (u.path.rstrip('/') or '/')=='/login': return route.continue_()
            if u.netloc in PPI_HOSTS: blocked_first_party.append(f'{m} {u.netloc}{u.path}'[:200])
            return route.abort()
        ctx.route('**/*',guard)
        def onresp(resp):
            try:
                r=resp.request; u=urlsplit(r.url)
                if u.netloc in PPI_HOSTS and len(first_party)<40:
                    first_party.append(f'{r.method.upper()} {u.netloc}{u.path} -> {resp.status}'[:220])
            except Exception: pass
        page.on('response',onresp)
        page.goto(TRADING,wait_until='commit',timeout=15000); page.wait_for_timeout(1200)
        if urlsplit(page.url).netloc=='trading.portfoliopersonal.com' and 'login' not in page.url.lower():
            ctx.close(); print(json.dumps({'status':'AUTHENTICATED_TRUSTED_DEVICE','submit_count':0,'first_party':first_party,'blocked_first_party':blocked_first_party,'credentials_exposed':False,'real_orders_sent':0})); return 0
        page.goto(LOGIN,wait_until='domcontentloaded',timeout=15000); page.wait_for_timeout(800)
        un=first_visible(page,["input[autocomplete='username']","input[placeholder*='usuario' i]","input[aria-label*='usuario' i]","input[name*='user' i]","input[id*='user' i]","input[type='email']"])
        pwf=first_visible(page,["input[type='password']","input[autocomplete='current-password']","input[placeholder*='contraseña' i]","input[aria-label*='contraseña' i]","input[name*='pass' i]","input[id*='pass' i]"])
        sub=first_visible(page,["button:has-text('Ingresar')","button[type='submit']","input[type='submit']"])
        if not (un and pwf and sub):
            ctx.close(); print(json.dumps({'status':'BLOCKED_LOGIN_FORM_SHAPE','username_field':bool(un),'password_field':bool(pwf),'submit':bool(sub),'first_party':first_party,'blocked_first_party':blocked_first_party,'credentials_exposed':False,'real_orders_sent':0})); return 4
        un.fill(user); pwf.fill(pwd); sub.click(no_wait_after=True,timeout=8000); page.wait_for_timeout(5000)
        txt=body(page); url=page.url
        status='AUTH_STAYED_ON_LOGIN_UNCLASSIFIED'
        if urlsplit(url).netloc=='trading.portfoliopersonal.com' and 'login' not in url.lower(): status='AUTHENTICATED_TRUSTED_DEVICE'
        elif TRUST.search(txt) and NOW_NOT.search(txt): status='TRUST_DEVICE_PROMPT_OPTIONAL'
        elif OTP.search(txt): status='AUTH_OTP_REQUIRED'
        elif BAD_CRED.search(txt): status='AUTH_REJECTED_CREDENTIALS'
        elif CAPTCHA.search(txt): status='AUTH_CAPTCHA_OR_CHALLENGE'
        elif LOCKED.search(txt): status='AUTH_ACCOUNT_LOCK_OR_RATE_LIMIT'
        elif MAINT.search(txt): status='AUTH_MAINTENANCE_OR_TEMPORARY'
        out={'status':status,'submit_count':1,'page_host':urlsplit(url).netloc,'page_path':urlsplit(url).path,'first_party':first_party[-20:],'blocked_first_party':blocked_first_party[-10:],'credentials_exposed':False,'real_orders_sent':0}
        ctx.close(); print(json.dumps(out,sort_keys=True)); return 0 if status=='AUTHENTICATED_TRUSTED_DEVICE' else 4
if __name__=='__main__': raise SystemExit(main())
