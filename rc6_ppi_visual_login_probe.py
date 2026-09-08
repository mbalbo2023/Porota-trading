#!/usr/bin/env python3
"""Faithful one-shot PPI web login through the site's own JavaScript.

Safety:
- credentials come only from the restricted host secret and are never printed;
- exactly one approved mutation is allowed: POST
  https://api.portfoliopersonal.com/api/Seguridad/Auth/Login;
- all other POST/PUT/PATCH/DELETE requests are aborted;
- order/trade/confirm/cancel page paths are blocked;
- response values, tokens, cookies and fingerprint values are never printed;
- if 2FA is requested, fail closed; no OTP automation.
"""
from __future__ import annotations
import argparse,json,os,re
from pathlib import Path
from urllib.parse import urlsplit

LOGIN='https://cuenta.portfoliopersonal.com/login'
TRADING='https://trading.portfoliopersonal.com/'
API_HOST='api.portfoliopersonal.com'
API_PATH='/api/Seguridad/Auth/Login'
MUTATING={'POST','PUT','PATCH','DELETE'}
ORDER=re.compile(r'(^|/)(operar|orden|orders?|trade|confirm|cancel|transfer|suscribir|rescatar)(/|$)',re.I)

def secret(path:Path):
 d={}
 for raw in path.read_text(encoding='utf-8').splitlines():
  s=raw.strip()
  if s and not s.startswith('#') and '=' in s:
   k,v=s.split('=',1); d[k.strip()]=v.strip().strip('"').strip("'")
 return d.get('PPI_WEB_USERNAME',''),d.get('PPI_WEB_PASSWORD','')

def first(page,selectors):
 for sel in selectors:
  try:
   q=page.locator(sel)
   for i in range(min(q.count(),10)):
    n=q.nth(i)
    if n.is_visible(): return n
  except Exception: pass
 return None

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--secret',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 p=Path(a.profile); s=Path(a.secret)
 base={'credentials_exposed':False,'tokens_exposed':False,'cookie_values_exposed':False,'orders_visited':False,'real_orders_sent':0,'attempts':0}
 if not p.is_dir() or not s.is_file(): print(json.dumps({**base,'status':'BLOCKED_LOCAL_PREREQUISITE'},sort_keys=True)); return 4
 u,pw=secret(s)
 if not u or not pw: print(json.dumps({**base,'status':'BLOCKED_AUTH_LOCAL_SECRET_INCOMPLETE'},sort_keys=True)); return 4
 from playwright.sync_api import sync_playwright
 obs={}; blocked=''; api_requests=0
 with sync_playwright() as P:
  ctx=P.chromium.launch_persistent_context(user_data_dir=str(p),executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',args=['--no-sandbox','--disable-dev-shm-usage'])
  page=ctx.pages[0] if ctx.pages else ctx.new_page(); page.set_default_timeout(9000)
  def guard(route,req):
   nonlocal blocked,api_requests
   q=urlsplit(req.url); m=req.method.upper(); path=q.path.rstrip('/') or '/'
   if q.scheme!='https': return route.abort()
   if m in {'GET','HEAD','OPTIONS'}:
    if ORDER.search(q.path): return route.abort()
    return route.continue_()
   if m=='POST' and q.netloc==API_HOST and path==API_PATH:
    api_requests+=1
    if api_requests>1:
     blocked='SECOND_AUTH_POST'; return route.abort()
    return route.continue_()
   if m in MUTATING:
    # third-party telemetry is simply blocked; first-party unexpected mutation is identified sanely
    if q.netloc.endswith('portfoliopersonal.com'): blocked=f'{q.netloc}{q.path}'[:220]
    return route.abort()
   return route.abort()
  def response(r):
   nonlocal obs
   try:
    q=urlsplit(r.url)
    if r.request.method.upper()!='POST' or q.netloc!=API_HOST or (q.path.rstrip('/') or '/')!=API_PATH: return
    o={'http_status':int(r.status),'json_object':False,'envelope_success':False,'envelope_status':None,'payload_object':False,'twofa':False,'change_password':False,'has_token':False,'error_credentials':False,'error_2fa':False,'error_fp':False,'error_blocked':False,'error_captcha':False,'safe_top_keys':[],'safe_payload_keys':[]}
    try:
     d=r.json()
     if isinstance(d,dict):
      o['json_object']=True; o['safe_top_keys']=sorted(k for k in d if k in {'success','status','payload','error','errors','message'})
      o['envelope_success']=bool(d.get('success'))
      if isinstance(d.get('status'),(str,int,float)): o['envelope_status']=str(d.get('status'))[:24]
      pl=d.get('payload') if isinstance(d.get('payload'),dict) else d
      o['payload_object']=isinstance(pl,dict)
      if isinstance(pl,dict):
       o['safe_payload_keys']=sorted(k for k in pl if k in {'twoFAInfo','changePassword','token','usuario','user','status','error','errors','message'})
       o['twofa']=bool(pl.get('twoFAInfo')); o['change_password']=bool(pl.get('changePassword')); o['has_token']=bool(pl.get('token') or pl.get('accessToken'))
      # classify textual error signals locally without returning the text
      vals=[]
      def collect(x):
       if isinstance(x,str): vals.append(x[:500])
       elif isinstance(x,dict):
        for k,v in x.items():
         if str(k).lower() in {'message','mensaje','error','errors','description','descripcion','detail','title'}: collect(v)
       elif isinstance(x,list):
        for v in x[:10]: collect(v)
      collect(d); txt=' '.join(vals).lower()
      o['error_credentials']=bool(re.search(r'usuario|contrase|credencial|clave|incorrect|inv[aá]lid',txt))
      o['error_2fa']=bool(re.search(r'2fa|doble factor|segundo factor|otp|token|c[oó]digo',txt))
      o['error_fp']=bool(re.search(r'finger|disposit|\bfp\b',txt))
      o['error_blocked']=bool(re.search(r'bloque|blocked|lock',txt))
      o['error_captcha']=bool(re.search(r'captcha|incode|challenge',txt))
    except Exception: pass
    obs=o
   except Exception: pass
  ctx.route('**/*',guard); page.on('response',response)
  page.goto(LOGIN,wait_until='domcontentloaded',timeout=20000); page.wait_for_timeout(800)
  user=first(page,["input[name='username']","input[autocomplete='username']","input[placeholder*='usuario' i]"])
  password=first(page,["input[name='password']","input[type='password']"])
  submit=first(page,["#loginAction","button:has-text('Ingresar')","button[type='submit']"])
  if not user or not password or not submit:
   ctx.close(); print(json.dumps({**base,'status':'BLOCKED_LOGIN_FORM_NOT_FOUND'},sort_keys=True)); return 4
  user.fill(u); password.fill(pw); base['attempts']=1; submit.click(no_wait_after=True); page.wait_for_timeout(4500)
  cookies=ctx.cookies(); names={c.get('name') for c in cookies}; tk_present='tk_ob' in names; rtk_present='rtk_ob' in names; fp_present='fp' in names
  final_page=f'{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}{urlsplit(page.url).path}'
  # if frontend produced auth cookies, test only the safe trading landing via GET
  trading_landing=False
  if tk_present and rtk_present and not obs.get('twofa'):
   try:
    page.goto(TRADING,wait_until='commit',timeout=15000); page.wait_for_timeout(1500)
    q=urlsplit(page.url); final_page=f'{q.scheme}://{q.netloc}{q.path}'; trading_landing=(q.netloc=='trading.portfoliopersonal.com' and 'login' not in q.path.lower() and not ORDER.search(q.path))
   except Exception: pass
  ctx.close()
 out={**base,'api_request_count':api_requests,'blocked_first_party_mutation':blocked or None,'page_url':final_page,'fp_cookie_present':fp_present,'auth_cookie_present':tk_present,'refresh_cookie_present':rtk_present,'trading_landing_authenticated':trading_landing}
 if obs: out.update({f'auth_{k}':v for k,v in obs.items()})
 if blocked: status='BLOCKED_UNAPPROVED_FIRST_PARTY_MUTATION'
 elif api_requests!=1: status='BLOCKED_AUTH_API_NOT_CALLED'
 elif obs.get('twofa') or obs.get('error_2fa'): status='BLOCKED_AUTH_2FA_REQUIRED'
 elif obs.get('change_password'): status='BLOCKED_AUTH_PASSWORD_CHANGE_REQUIRED'
 elif trading_landing: status='AUTHENTICATED_TRUSTED_DEVICE'
 elif tk_present and rtk_present: status='AUTHENTICATED_COOKIES_PRESENT_LANDING_UNCONFIRMED'
 elif obs.get('http_status') in (400,401,403): status='BLOCKED_AUTH_REJECTED_OR_CHALLENGE'
 elif obs.get('http_status',0)>=500: status='BLOCKED_AUTH_SERVER_ERROR'
 else: status='BLOCKED_AUTH_UNCLASSIFIED'
 out['status']=status; print(json.dumps(out,sort_keys=True)); return 0 if status.startswith('AUTHENTICATED') else 4
if __name__=='__main__': raise SystemExit(main())
