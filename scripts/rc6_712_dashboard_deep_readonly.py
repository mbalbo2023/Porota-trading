#!/usr/bin/env python3
"""Bounded visual census of the installed PAPER dashboard, GET only.

Artifacts redact all free text before screenshots. The JSON retains only
operational status codes, structural counts, safe static headings and hashes.
No browser action can POST or navigate away from the local SSH tunnel.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_trader_dashboard.navigation import CANONICAL_PATHS
from rc6_685_live_browser_readonly import BASE, PRIORITY, REDACT, STATUS

SAFE_HEADINGS = frozenset((
    'Estado', 'Fuentes', 'Riesgo', 'Instrumentos', 'Capacidad', 'Decisiones',
    'Oportunidades', 'Workers', 'Scheduler', 'Backups', 'Cierres', 'Históricos',
    'Anual', 'Mercado', 'Liquidez', 'Scalping', 'Cauciones', 'PAPER', 'SHADOW',
    'Datos', 'Contratos', 'Evidencia', 'Salud', 'Alertas', 'Aprendizaje',
))

DOM = r"""() => {
  const status = /\b(?:NO_VERIFICADO|STALE|BASELINE_UNAVAILABLE|SOURCE_UNAVAILABLE|PENDING|ERROR|NOT_RUNNING|BLOCKED|OFF_BASELINE|ANNUAL_NO_SELECTABLE_CANONICAL_SERIES)\b/gi;
  const viewport = innerWidth;
  const safe = /^(Estado|Fuentes|Riesgo|Instrumentos|Capacidad|Decisiones|Oportunidades|Workers|Scheduler|Backups|Cierres|Históricos|Anual|Mercado|Liquidez|Scalping|Cauciones|PAPER|SHADOW|Datos|Contratos|Evidencia|Salud|Alertas|Aprendizaje)$/i;
  const sha = str => { let h = 2166136261; for(const c of str) h = Math.imul(h ^ c.charCodeAt(0),16777619); return (h>>>0).toString(16); };
  const label = el => { const raw = (el?.textContent || '').trim().replace(/\s+/g,' ').slice(0,120); return safe.test(raw) ? raw : 'hash:' + sha(raw); };
  const loc = el => { const parent = el.closest('article,section,li,tr,.card,.panel,[role="alert"]') || el.parentElement; const heading = parent?.querySelector('h1,h2,h3,h4,legend,th,[role="heading"]'); return {container:(parent?.tagName||'').toLowerCase(), container_id:(parent?.id||'').slice(0,80), heading:label(heading), class_token:(parent?.className?.toString()||'').split(/\s+/).filter(x=>/^(card|panel|status|source|risk|table|notice|warn|alert)/i.test(x)).slice(0,3)}; };
  const occurrences = [];
  const walker = document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  let node;
  while((node=walker.nextNode())) {
    if(node.parentElement?.closest('script,style,noscript')) continue;
    const value = node.nodeValue || '';
    if(!node.parentElement?.getClientRects().length) continue;
    for(const m of value.matchAll(status)) occurrences.push({state:m[0].toUpperCase(),...loc(node.parentElement)});
  }
  const tables = [...document.querySelectorAll('table')].map((table,index) => {
    const rows=[...table.querySelectorAll('tbody tr')];
    const visible=rows.filter(x=>x.getClientRects().length).length;
    const cells=[...table.querySelectorAll('th,td')];
    return {index, rows:rows.length,visible_rows:visible,columns:table.querySelectorAll('thead th').length,
      scroll_container:!!table.closest('[class*="scroll"],[class*="overflow"]'),
      clipped_cells:cells.filter(x=>x.scrollWidth>x.clientWidth+2).length,
      nonempty:cells.filter(x=>(x.textContent||'').trim()).length};
  });
  const links=[...document.querySelectorAll('nav a,aside a,[role="navigation"] a')].map(x=>x.getAttribute('href')||'');
  const disclosure=[...document.querySelectorAll('details')].map(x=>({open:x.open,children:x.querySelectorAll('a,button').length}));
  const buttons=[...document.querySelectorAll('button,[role="button"]')].map(x=>({expanded:x.getAttribute('aria-expanded'),disabled:x.disabled||x.getAttribute('aria-disabled')==='true'}));
  return {viewport,body:document.body.scrollWidth,html:document.documentElement.scrollWidth,
    cards:document.querySelectorAll('article,.card,.panel').length,h1:document.querySelectorAll('h1').length,
    tables,occurrences,nav_links:links.length,nav_targets:[...new Set(links.filter(x=>x.startsWith('/')).map(x=>x.split('?')[0]))],
    disclosure,buttons,visible_more:[...document.querySelectorAll('a,button,summary')].filter(x=>/ver m[aá]s|mostrar (?:10 )?m[aá]s|expandir/i.test(x.textContent||'')).length};
}"""

def main(output):
    token = os.environ.get('DASHBOARD_ACCESS_TOKEN', '')
    if len(token) < 24:
        raise SystemExit('BEARER_UNAVAILABLE')
    output.mkdir(parents=True, exist_ok=True)
    report = {'schema':'RC6_712_VISUAL_DEEP_READONLY_V1',
              'as_of':datetime.now(timezone.utc).isoformat(),
              'target':'installed-paper-dashboard-via-ssh-tunnel',
              'request_policy':'GET/HEAD localhost only', 'paths':[], 'screenshots':[],
              'findings':[]}
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True,
                                   args=['--no-sandbox','--disable-dev-shm-usage'])
        context=browser.new_context(viewport={'width':1440,'height':900},
                    extra_http_headers={'Authorization':'Bearer '+token},reduced_motion='reduce')
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(type(e).__name__))
        def readonly(route):
            request=route.request
            parsed=urlsplit(request.url)
            if request.method not in ('GET','HEAD') or parsed.netloc!='127.0.0.1:18085':
                report['findings'].append({'kind':'blocked_nonread_request','method':request.method})
                route.abort()
            else:
                route.continue_()
        page.route('**/*',readonly)
        response=page.goto(BASE+'/api/dashboard/truth',wait_until='domcontentloaded',timeout=12000)
        truth=json.loads(page.locator('body').inner_text())
        if response.status!=200 or truth.get('mode')!='PRODUCTION_PAPER' or truth.get('real_orders_sent')!=0:
            raise SystemExit('PAPER_GUARD_NOT_PROVEN')
        report['paper_guard']={'mode':'PRODUCTION_PAPER','real_orders_sent':0,'http':200}
        # The truth endpoint already reads IOL's separate mounted cache. Only
        # aggregate states/clocks enter public evidence, never symbol quotes.
        iol=truth.get('iol') or {}
        quotes=iol.get('quotes') or {}
        families=iol.get('families') or {}
        report['iol_truth']={'quotes':{k:quotes.get(k) for k in ('total','states','source_states','state','as_of')},
            'families':{'state':families.get('state'),'as_of':families.get('as_of'),
                        'last_known_good_at':families.get('last_known_good_at'),
                        'section_states':{str(k):v.get('state') for k,v in (families.get('sections') or {}).items()
                                          if isinstance(v,dict)}}}
        for path in CANONICAL_PATHS:
            try:
                response=page.goto(BASE+path,wait_until='domcontentloaded',timeout=12000)
                metrics=page.evaluate(DOM)
                item={'path':path,'http':response.status if response else None,**metrics}
                if item['http']!=200 or max(item['body'],item['html'])>1442:
                    report['findings'].append({'kind':'route_http_or_overflow','path':path,'http':item['http']})
                if path=='/analitica/anual':
                    item['annual_selector_links']=page.locator('a[href^="/analitica/anual?identity="]').count()
                # Expand each disclosure in place; the browser blocks mutation and
                # all nonlocal requests. Record structural changes, not private text.
                for detail in page.locator('details').all()[:16]:
                    if not detail.evaluate('(x)=>x.open'):
                        detail.locator('summary').first.click(timeout=1000)
                expanded=page.evaluate(DOM)
                item['expanded_disclosures']=sum(x['open'] for x in expanded['disclosure'])
                item['expanded_occurrences']=expanded['occurrences']
                first_dialog=page.locator('button[data-detail-dialog]').first
                if first_dialog.count():
                    first_dialog.click(timeout=1000)
                    dialog=page.locator('dialog[open]')
                    item['dialog_opened']=dialog.count()==1
                    item['dialog_visible_statuses']=dict(Counter(x['state'] for x in
                        page.evaluate(DOM)['occurrences']))
                    dialog.locator('button[type="submit"]').first.click(timeout=1000)
                    item['dialog_closed']=page.locator('dialog[open]').count()==0
                    if not item['dialog_opened'] or not item['dialog_closed']:
                        report['findings'].append({'kind':'detail_dialog_interaction','path':path})
                page.evaluate(REDACT)
                name='home' if path=='/' else path.strip('/').replace('/','_')
                filename=f'{name}-1440.png'
                page.screenshot(path=str(output/filename),full_page=True,animations='disabled')
                report['screenshots'].append({'path':path,'width':1440,'file':filename})
                # A bounded GET on one representative server paginator verifies
                # the next ten rows are reachable without submitting a form.
                if path in ('/en-vivo/decisiones','/instrumentos','/analitica/anual'):
                    more=page.locator('nav.pager a[href*="offset=10"]')
                    if more.count():
                        before=[v for v in page.locator('table tbody tr[data-row]').evaluate_all(
                            '(nodes)=>nodes.map(x=>x.id)')]
                        more.first.click(timeout=5000)
                        after=page.locator('table tbody tr[data-row]').evaluate_all(
                            '(nodes)=>nodes.map(x=>x.id)')
                        item['pagination_probed']={'before':len(before),'after':len(after),
                            'distinct':bool(set(after)-set(before)),
                            'offset_10':'offset=10' in page.url}
                        if not item['pagination_probed']['distinct'] or not item['pagination_probed']['offset_10']:
                            report['findings'].append({'kind':'paginator_not_advancing','path':path})
                report['paths'].append(item)
            except Exception as exc:
                report['findings'].append({'kind':'route_exception','path':path,'error_type':type(exc).__name__})
                report['paths'].append({'path':path,'http':None})
        for width in (768,390):
            page.set_viewport_size({'width':width,'height':900})
            for path in PRIORITY:
                try:
                    response=page.goto(BASE+path,wait_until='domcontentloaded',timeout=12000)
                    metrics=page.evaluate(DOM)
                    if response.status!=200 or max(metrics['body'],metrics['html'])>width+2:
                        report['findings'].append({'kind':'responsive_http_or_overflow','path':path,'width':width})
                    if path=='/' and width==390:
                        menu=page.locator('#terminal-menu')
                        menu.click(timeout=1000)
                        report['mobile_menu_open']=menu.get_attribute('aria-expanded')=='true'
                        page.keyboard.press('Escape')
                        report['mobile_menu_closed']=menu.get_attribute('aria-expanded')=='false'
                        if not (report['mobile_menu_open'] and report['mobile_menu_closed']):
                            report['findings'].append({'kind':'mobile_menu_interaction'})
                    page.evaluate(REDACT)
                    name='home' if path=='/' else path.strip('/').replace('/','_')
                    filename=f'{name}-{width}.png'
                    page.screenshot(path=str(output/filename),full_page=True,animations='disabled')
                    report['screenshots'].append({'path':path,'width':width,'file':filename})
                except Exception as exc:
                    report['findings'].append({'kind':'responsive_exception','path':path,'width':width,'error_type':type(exc).__name__})
        report['canonical_count_expected']=len(CANONICAL_PATHS)
        report['pageerror_count']=len(errors)
        browser.close()
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print('RC6_712_VISUAL_SUMMARY='+json.dumps({'paths':len(report['paths']),
        'screenshots':len(report['screenshots']),'pageerrors':len(errors),
        'findings':report['findings'],
        'statuses':dict(Counter(x['state'] for p in report['paths'] for x in p.get('occurrences',[])))},
        ensure_ascii=False,separators=(',',':')))
    if len(report['paths'])!=len(CANONICAL_PATHS) or report['findings'] or errors:
        raise SystemExit('VISUAL_DEEP_READONLY_FINDINGS')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
