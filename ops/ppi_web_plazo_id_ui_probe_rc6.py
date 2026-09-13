#!/usr/bin/env python3
"""Read-only UI probe to map PPI Web plazo ids to visible settlement labels."""
import argparse,re
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
TRADING='https://trading.portfoliopersonal.com'
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/caucion','/licit','/tender','/security','/password','/2fa')
def bad(url): return any(x in urlsplit(url).path.lower() for x in FORBIDDEN)
def clean(s): return ' '.join(str(s or '').split())[:160]
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); a=ap.parse_args()
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path='/usr/bin/google-chrome-stable',headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1000},args=['--no-sandbox','--disable-dev-shm-usage'])
  blocked=0
  def guard(route,req):
   nonlocal blocked
   if bad(req.url) or req.method.upper() not in ('GET','HEAD','OPTIONS'): blocked+=1; return route.abort()
   route.continue_()
  ctx.route('**/*',guard); p=ctx.pages[0] if ctx.pages else ctx.new_page(); p.goto(TRADING+'/estadoDeCuenta',wait_until='domcontentloaded',timeout=45000); p.wait_for_timeout(1500)
  auth=urlsplit(p.url).netloc=='trading.portfoliopersonal.com' and 'login' not in urlsplit(p.url).path.lower(); print('AUTH='+('AUTHENTICATED_TRUSTED_DEVICE' if auth else 'BLOCKED_AUTH'))
  if not auth:return 4
  for term in ('1','2','3','4'):
   histories=[]
   def resp(r):
    if r.request.method.upper()=='GET' and '/Historico/' in urlsplit(r.url).path: histories.append((urlsplit(r.url).path,r.status))
   p.on('response',resp)
   p.goto(f'{TRADING}/Cotizaciones/Item/261?plazo={term}',wait_until='domcontentloaded',timeout=45000); p.wait_for_timeout(2200)
   labels=[]
   for sel in ('button','[role="button"]','select','option','input'):
    try:
     loc=p.locator(sel)
     for i in range(min(loc.count(),120)):
      el=loc.nth(i); txt=clean(el.inner_text(timeout=300) if sel not in ('input',) else el.get_attribute('value'))
      aria=clean(el.get_attribute('aria-label')); title=clean(el.get_attribute('title'))
      z=' | '.join(x for x in (txt,aria,title) if x)
      if re.search(r'\b(CI|24|48|72|INMEDIATO|HS|HORAS)\b',z,re.I) and z not in labels: labels.append(z)
    except Exception: pass
   print(f'PLAZO_ID={term}|VISIBLE_SETTLEMENT_LABELS={labels[:12]}|HISTORY={histories[-3:]}')
  print(f'BLOCKED={blocked}'); print('CANONICAL_WRITE=DENY'); print('REAL_ORDERS=0'); print('CREDENTIAL_VALUES_LOGGED=False'); ctx.close(); return 0
if __name__=='__main__': raise SystemExit(main())
