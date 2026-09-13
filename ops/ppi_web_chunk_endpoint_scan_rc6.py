#!/usr/bin/env python3
import re
import sys
import urllib.request

URL = "https://trading.portfoliopersonal.com/_next/static/chunks/pages/Cotizaciones/%5BinstrumentName%5D-95207a48fb401c8e.js"

with urllib.request.urlopen(URL, timeout=30) as r:
    data = r.read().decode("utf-8", "ignore")

print(f"CHUNK_URL={URL}")
print(f"BYTES={len(data.encode('utf-8'))}")

patterns = [
    r'[^"\']{0,80}/api/[^"\']{1,220}',
    r'[^"\']{0,80}Cotizaciones[^"\']{1,220}',
    r'[^"\']{0,80}(?:instrument|Instrument|mercado|Mercado|ticker|Ticker|especie|Especie|historico|Historico|socket|Socket)[^"\']{1,220}',
]

seen = set()
results = []
for pat in patterns:
    for m in re.finditer(pat, data):
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if s and s not in seen:
            seen.add(s)
            results.append(s)

keywords = ("api/", "cotiza", "instrument", "mercado", "ticker", "especie", "historico", "socket")
filtered = [s for s in results if any(k in s.lower() for k in keywords)]

print(f"MATCHES={len(filtered)}")
for i, s in enumerate(filtered[:200]):
    print(f"M{i}={s[:320]}")

# Also report quoted string literals likely to be endpoint/event names.
strings = re.findall(r'["\']([^"\']{3,220})["\']', data)
qseen = set()
qout = []
for s in strings:
    sl = s.lower()
    if any(k in sl for k in ("/api/", "cotizacion", "instrument", "mercado", "historico", "realtime", "socket", "search", "buscar")):
        if s not in qseen:
            qseen.add(s)
            qout.append(s)

print(f"QUOTED_MATCHES={len(qout)}")
for i, s in enumerate(qout[:250]):
    print(f"Q{i}={s[:320]}")

sys.exit(0)
