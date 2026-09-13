#!/usr/bin/env python3
import json
import re
import urllib.request
import urllib.error
from html import unescape
from urllib.parse import urljoin, urlsplit

BASE = "https://trading.portfoliopersonal.com"
ROUTES = [
    "/Cotizaciones/Acciones", "/Cotizaciones/Cedears", "/Cotizaciones/Bonos",
    "/Cotizaciones/Letras", "/Cotizaciones/Ons", "/Cotizaciones/Opciones",
    "/Cotizaciones/Futuros", "/FCI", "/FCIs", "/Fondos", "/Cotizaciones/FCI",
]
TARGETS = ["AVYC", "TX28D", "MRCTO", "YPFV6100OC", "DLR/AGO27M", "PI.RENT.B"]
TERMS = [
    "instrument", "cotiza", "historico", "watchlist", "sector", "ticker", "especie",
    "mercado", "plazo", "fci", "fondo", "cedear", "bono", "opcion", "futuro",
    "obligacion", "letra", "socket", "realtime", "search", "buscar", "item"
]
MODULE_IDS = ["4176", "37602", "78625", "30197", "38391", "19963", "45297", "47317", "47759"]
MAX_CHUNKS = 140
MAX_BYTES = 3_000_000
UA = "Mozilla/5.0 (compatible; Porota-RC6-readonly-static-recon/1.0)"


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES + 1)
            return r.status, dict(r.headers), body[:MAX_BYTES]
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(200000)
    except Exception as e:
        return 0, {}, ("ERR:" + type(e).__name__).encode()


def txt(b):
    return b.decode("utf-8", "ignore")


def clean_url(u):
    p = urlsplit(u)
    return f"{p.scheme}://{p.netloc}{p.path}"


def compact(s, n=360):
    return re.sub(r"\s+", " ", unescape(s)).strip()[:n]


def extract_scripts(html):
    out=[]
    for m in re.finditer(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I):
        out.append(urljoin(BASE, unescape(m.group(1))))
    return out


def extract_next_data(html):
    m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.I|re.S)
    if not m:
        return None
    try:
        return json.loads(unescape(m.group(1)))
    except Exception:
        return None


def walk(obj, path="$", depth=0):
    if depth > 9:
        return
    if isinstance(obj, dict):
        for k,v in obj.items():
            yield from walk(v, f"{path}.{k}", depth+1)
    elif isinstance(obj, list):
        for i,v in enumerate(obj[:2000]):
            yield from walk(v, f"{path}[{i}]", depth+1)
    elif isinstance(obj, (str,int,float,bool)) or obj is None:
        yield path, obj


def string_candidates(data):
    # quoted literals plus URL/path-like tokens
    out=[]; seen=set()
    pats = [
        r'["\']([^"\']{3,260})["\']',
        r'(/api/[A-Za-z0-9_./${}\[\]-]{2,240})',
        r'(/Cotizaciones/[A-Za-z0-9_./${}\[\]-]{1,220})',
        r'(https?://[A-Za-z0-9._:/?=&%${}\[\]-]{8,260})',
        r'(wss?://[A-Za-z0-9._:/?=&%${}\[\]-]{8,260})',
    ]
    for pat in pats:
        for m in re.finditer(pat, data):
            s=compact(m.group(1))
            sl=s.lower()
            if any(t in sl for t in TERMS) or "/api/" in sl or "cotizaciones/" in sl:
                if s not in seen:
                    seen.add(s); out.append(s)
    return out


def module_snippets(data):
    out=[]
    for mid in MODULE_IDS:
        # webpack module definitions usually look like 4176:(e,a,n)=>{...}
        for pat in (rf'(?<!\d){mid}:\s*[^,]{{0,120}}', rf'(?<!\d){mid}:'):
            m=re.search(pat, data)
            if m:
                start=max(0,m.start()-120); end=min(len(data),m.start()+2200)
                out.append((mid, compact(data[start:end], 2000)))
                break
    return out


print("PPI_BROAD_STATIC_RECON")
print("MODE=PUBLIC_GET_ONLY")
print("AUTH_SESSION_USED=NO")
print("HISTORY_FETCH=NO")
print("CANONICAL_WRITE=DENY")

all_scripts=[]
build_ids=set()
route_rows=[]
next_hits=[]
for route in ROUTES:
    status, headers, body = get(BASE + route)
    html=txt(body)
    final_route=route
    scripts=extract_scripts(html)
    all_scripts.extend(scripts)
    nd=extract_next_data(html)
    build_id=None
    if isinstance(nd, dict):
        build_id=nd.get("buildId")
        if build_id:
            build_ids.add(str(build_id))
        for p,v in walk(nd):
            sv=str(v)
            if any(t.upper() in sv.upper() for t in TARGETS) or any(k in p.lower() for k in ("instrument","cotiza","ticker","item","fci")):
                next_hits.append((route,p,compact(sv,180)))
    route_rows.append((route,status,len(body),build_id,len(scripts)))

for route,status,n,build_id,sc in route_rows:
    print(f"ROUTE|{route}|status={status}|bytes={n}|build={build_id}|scripts={sc}")

print(f"BUILD_IDS={','.join(sorted(build_ids)) if build_ids else '-'}")
print(f"NEXTDATA_HITS={len(next_hits)}")
for i,(route,p,v) in enumerate(next_hits[:80]):
    print(f"NEXT_{i}|route={route}|path={p}|value={v}")

# Build manifest / route data alternatives.
for bid in sorted(build_ids):
    for suffix in ("/_buildManifest.js", "/_ssgManifest.js"):
        u=f"{BASE}/_next/static/{bid}{suffix}"
        status,h,b=get(u)
        print(f"MANIFEST|status={status}|bytes={len(b)}|url={clean_url(u)}")
        if status==200:
            all_scripts.append(u)
            s=txt(b)
            for cand in string_candidates(s)[:80]:
                print(f"MANIFEST_MATCH|{compact(cand)}")
    # Probe Next data routes. GET-only, no auth context.
    for route in ROUTES:
        path=route.strip("/") or "index"
        u=f"{BASE}/_next/data/{bid}/{path}.json"
        status,h,b=get(u)
        if status not in (404,0):
            print(f"NEXTDATA_ROUTE|route={route}|status={status}|bytes={len(b)}|url={clean_url(u)}")
            if status==200 and "json" in (h.get("Content-Type") or h.get("content-type") or "").lower():
                try:
                    o=json.loads(txt(b)); hits=[]
                    for p,v in walk(o):
                        sv=str(v)
                        if any(t.upper() in sv.upper() for t in TARGETS) or any(k in p.lower() for k in ("instrument","cotiza","ticker","item","fci")):
                            hits.append((p,compact(sv,160)))
                    print(f"NEXTDATA_ROUTE_HITS|route={route}|hits={len(hits)}")
                    for p,v in hits[:30]:
                        print(f"NEXTDATA_ROUTE_MATCH|route={route}|path={p}|value={v}")
                except Exception:
                    pass

# De-dup scripts and recursively include sourceMappingURL targets.
queue=[]; seen_urls=set()
for u in all_scripts:
    if u.startswith(BASE) and u not in seen_urls:
        seen_urls.add(u); queue.append(u)

results=[]
module_results=[]
map_count=0
idx=0
while idx < len(queue) and idx < MAX_CHUNKS:
    u=queue[idx]; idx+=1
    status,h,b=get(u)
    if status!=200:
        continue
    data=txt(b)
    cands=string_candidates(data)
    if cands:
        results.append((u,len(b),cands[:120]))
    mods=module_snippets(data)
    if mods:
        module_results.append((u,mods))
    # Find dynamic chunk filenames and add them.
    for m in re.finditer(r'["\']([^"\']+\.js)["\']', data):
        rel=m.group(1)
        if rel.startswith("http"):
            nu=rel
        elif rel.startswith("/_next/"):
            nu=BASE+rel
        elif "/_next/" in rel:
            nu=urljoin(u,rel)
        elif rel.startswith("static/") or rel.startswith("chunks/"):
            nu=urljoin(BASE+"/_next/",rel)
        else:
            continue
        if nu.startswith(BASE) and nu not in seen_urls and len(queue)<MAX_CHUNKS*2:
            seen_urls.add(nu); queue.append(nu)
    # source maps if declared
    mm=re.search(r'//# sourceMappingURL=([^\s]+)', data)
    if mm and map_count < 12:
        mu=urljoin(u,mm.group(1)); map_count+=1
        ms,mh,mb=get(mu)
        print(f"SOURCEMAP|status={ms}|bytes={len(mb)}|url={clean_url(mu)}")
        if ms==200:
            try:
                mo=json.loads(txt(mb)); contents=mo.get("sourcesContent") or []
                source_hits=[]
                for j,src in enumerate(contents[:500]):
                    if not isinstance(src,str): continue
                    cc=string_candidates(src)
                    if cc:
                        source_hits.append((j,cc[:30]))
                print(f"SOURCEMAP_HITS|count={len(source_hits)}")
                for j,cc in source_hits[:40]:
                    for cand in cc[:20]:
                        print(f"SM_MATCH|source={j}|{compact(cand)}")
            except Exception:
                pass

print(f"SCANNED_CHUNKS={idx}")
print(f"CHUNKS_WITH_MATCHES={len(results)}")
for ci,(u,n,cands) in enumerate(results[:70]):
    print(f"CHUNK_{ci}|bytes={n}|url={clean_url(u)}|matches={len(cands)}")
    for j,s in enumerate(cands[:35]):
        print(f"CHUNK_{ci}_M{j}|{compact(s)}")

print(f"MODULE_LOCATIONS={len(module_results)}")
for u,mods in module_results[:50]:
    for mid,snip in mods:
        print(f"MODULE|id={mid}|url={clean_url(u)}|snippet={snip}")

print("RECON_COMPLETE=YES")
