#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,re
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

BASE='https://trading.portfoliopersonal.com/Cotizaciones/Item/794828'
SAFE={'GET','HEAD','OPTIONS'}
FORBIDDEN=('/orden','/order','/operar/confirm','/confirmar','/cancel','/transfer','/suscribir','/rescatar','/licit')
SETTLE_WORDS=('inmediata','inmediato','ci','24','48','plazo','liquid')
CHART_WORDS=('rango','personalizado','grafic','1d','5d','1m','3m','6m','1a','ytd','ir a','desde','hasta')

def clean_url(u):
 p=urlsplit(str(u)); return f'{p.scheme}://{p.netloc}{p.path}'
def forbidden(u): return any(x in urlsplit(str(u)).path.lower() for x in FORBIDDEN)
def norm(s): return re.sub(r'\s+',' ',str(s or '')).strip()
def safe_txt(s): return norm(s)[:180]

def collect(page):
 out=[]; seen=set()
 selectors='button, a, [role="button"], [role="menuitem"], [role="option"], [role="tab"], input, select, option, label'
 for i in range(min(page.locator(selectors).count(),1200)):
  try:
   el=page.locator(selectors).nth(i)
   if not el.is_visible(): continue
   tag=el.evaluate('(e)=>e.tagName')
   txt=safe_txt(el.inner_text(timeout=500) if tag not in ('INPUT','SELECT') else '')
   aria=safe_txt(el.get_attribute('aria-label'))
   title=safe_txt(el.get_attribute('title'))
   name=safe_txt(el.get_attribute('name'))
   typ=safe_txt(el.get_attribute('type'))
   val=safe_txt(el.get_attribute('value'))
   href=''
   h=el.get_attribute('href')
   if h:
    p=urlsplit(h); href=p.path
   hay=' '.join((txt,aria,title,name,typ,val,href)).lower()
   if not any(w in hay for w in SETTLE_WORDS+CHART_WORDS): continue
   item={'tag':tag,'text':txt,'aria':aria,'title':title,'name':name,'type':typ,'value':val,'href':href}
   k=json.dumps(item,sort_keys=True,ensure_ascii=False)
   if k not in seen: seen.add(k); out.append(item)
  except Exception: pass
  if len(out)>=120: break
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--profile',required=True); ap.add_argument('--output',required=True); ap.add_argument('--chrome',default='/usr/bin/google-chrome-stable'); a=ap.parse_args()
 result={'variants':[],'blocked_nonread':[],'blocked_forbidden':[],'real_orders_sent':0,'canonical_write':'DENY'}
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(user_data_dir=a.profile,executable_path=a.chrome,headless=True,locale='es-AR',timezone_id='America/Argentina/Buenos_Aires',viewport={'width':1440,'height':1100},args=['--no-sandbox','--disable-dev-shm-usage'])
  def guard(route,request):
   m=request.method.upper(); u=request.url
   if forbidden(u): result['blocked_forbidden'].append({'method':m,'url':clean_url(u)}); return route.abort()
   if m not in SAFE: result['blocked_nonread'].append({'method':m,'url':clean_url(u)}); return route.abort()
   return route.continue_()
  ctx.route('**/*',guard)
  page=ctx.pages[0] if ctx.pages else ctx.new_page()
  for plazo in (1,2):
   page.goto(f'{BASE}?plazo={plazo}',wait_until='domcontentloaded',timeout=45000); page.wait_for_timeout(9000)
   u=urlsplit(page.url)
   auth=(u.netloc=='trading.portfoliopersonal.com' and 'login' not in u.path.lower())
   controls=collect(page)
   # body snippets only around settlement/chart words; never dump full DOM.
   snippets=[]
   try:
    body=norm(page.locator('body').inner_text(timeout=3000))
    low=body.lower()
    for word in SETTLE_WORDS+CHART_WORDS:
     pos=low.find(word)
     if pos>=0:
      sn=body[max(0,pos-90):pos+180]
      if sn not in snippets: snippets.append(sn)
     if len(snippets)>=20: break
   except Exception: pass
   result['variants'].append({'plazo':plazo,'page':clean_url(page.url),'auth':auth,'controls':controls,'snippets':snippets})
  ctx.close()
 Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(a.output,0o600)
 for v in result['variants']:
  print(f"PLAZO={v['plazo']} AUTH={v['auth']} PAGE={v['page']} CONTROLS={len(v['controls'])} SNIPPETS={len(v['snippets'])}")
  for i,x in enumerate(v['controls'][:40]): print(f"P{v['plazo']}_CONTROL_{i}="+json.dumps(x,ensure_ascii=False,separators=(',',':')))
  for i,x in enumerate(v['snippets'][:15]): print(f"P{v['plazo']}_SNIP_{i}="+json.dumps(x,ensure_ascii=False))
 print('BLOCKED_NONREAD='+str(len(result['blocked_nonread'])))
 print('BLOCKED_FORBIDDEN='+str(len(result['blocked_forbidden'])))
 print('REAL_ORDERS=0'); print('CANONICAL_WRITE=DENY')
 return 0 if all(v['auth'] for v in result['variants']) else 4
if __name__=='__main__': raise SystemExit(main())
