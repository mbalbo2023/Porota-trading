#!/usr/bin/env python3
import re, sys, urllib.request
BASE='https://trading.portfoliopersonal.com'
TARGETS=['quotesApi','getInstrumentsForAlerts','getInstrument','searchSymbols','search(e)','/api/','Cotizaciones','Instrument','Search']
SEEDS=['/Mercado','/Cotizaciones/Alertas/1','/Cotizaciones/Item/794828','/Cotizaciones/Acciones']
UA={'User-Agent':'Mozilla/5.0'}

def get(url):
    req=urllib.request.Request(url,headers=UA,method='GET')
    with urllib.request.urlopen(req,timeout=30) as r:
        return r.read().decode('utf-8','replace')

def urls_from_html(html):
    return re.findall(r'<script[^>]+src="([^"]+\.js)"',html)

def snippets(txt, needle, span=700):
    out=[]; start=0
    low=txt.lower(); n=needle.lower()
    while True:
        i=low.find(n,start)
        if i<0: break
        out.append(txt[max(0,i-span):min(len(txt),i+span)])
        start=i+len(n)
        if len(out)>=12: break
    return out

print('PPI_API_WRAPPER_RECON')
print('MODE=PUBLIC_GET_ONLY')
print('AUTH_SESSION_USED=NO')
print('CANONICAL_WRITE=DENY')
script_urls=set()
for p in SEEDS:
    try:
        h=get(BASE+p)
        for u in urls_from_html(h):
            script_urls.add(u if u.startswith('http') else BASE+u)
        print(f'PAGE|{p}|status=200|scripts={len(urls_from_html(h))}')
    except Exception as e:
        print(f'PAGE|{p}|error={type(e).__name__}')

# Build manifest to expand candidate chunks
try:
    h=get(BASE+'/Cotizaciones/Acciones')
    m=re.search(r'buildId":"([^"]+)',h)
    if not m:
        m=re.search(r'/_next/static/([^/]+)/_buildManifest\.js',h)
    build=m.group(1) if m else None
    if build:
        man=get(f'{BASE}/_next/static/{build}/_buildManifest.js')
        for s in re.findall(r'"(static/chunks/[^"]+\.js)"',man):
            script_urls.add(BASE+'/_next/'+s)
        print(f'BUILD={build}|manifest_chunks={len(script_urls)}')
except Exception as e:
    print(f'MANIFEST_ERROR={type(e).__name__}')

hits=[]
for idx,u in enumerate(sorted(script_urls)):
    try:
        txt=get(u)
    except Exception:
        continue
    local=[]
    for t in TARGETS:
        if t.lower() in txt.lower():
            local.append(t)
    if not local:
        continue
    score=sum(3 if x in local else 0 for x in ['quotesApi','getInstrumentsForAlerts','getInstrument','searchSymbols']) + len(local)
    hits.append((score,u,txt,local))

hits.sort(reverse=True,key=lambda x:x[0])
print(f'SCANNED={len(script_urls)}|MATCHING={len(hits)}')
for rank,(score,u,txt,local) in enumerate(hits[:30]):
    print(f'CHUNK|rank={rank}|score={score}|url={u}|terms={",".join(local)}')
    # Generic API path inventory
    paths=sorted(set(re.findall(r'[/A-Za-z0-9_.:-]{4,120}',txt)))
    api=[]
    for p in paths:
        lp=p.lower()
        if ('/api/' in lp or 'cotizacion' in lp or 'instrument' in lp or 'search' in lp) and len(p)<110:
            api.append(p)
    for p in api[:35]:
        print('PATH|'+p)
    for needle in ['quotesApi','getInstrumentsForAlerts','getInstrument','searchSymbols']:
        ss=snippets(txt,needle)
        for j,s in enumerate(ss[:4]):
            one=' '.join(s.replace('\n',' ').split())
            # Redact likely secrets/storage values if any; static JS only but keep compact.
            one=re.sub(r'([A-Za-z0-9_-]{80,})','<LONG_TOKEN>',one)
            print(f'SNIP|needle={needle}|n={j}|{one[:1800]}')

print('RECON_COMPLETE=YES')
