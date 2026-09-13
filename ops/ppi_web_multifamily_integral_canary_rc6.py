#!/usr/bin/env python3
import json
import re
import argparse
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

TRADING = "https://trading.portfoliopersonal.com"
API_HOST = "api.portfoliopersonal.com"
TARGETS = [
    ("CEDEARS", "AVYC"),
    ("BONOS", "TX28D"),
    ("ON", "MRCTO"),
    ("OPCIONES", "YPFV6100OC"),
    ("FUTUROS", "DLR/AGO27M"),
    ("FCI", "PI.RENT.B"),
]
FORBIDDEN = (
    "/orden", "/order", "/operar/confirm", "/confirmar", "/cancel",
    "/transfer", "/suscribir", "/rescatar", "/caucion", "/licit",
    "/tender", "/security", "/password", "/2fa",
)


def clean_path(url):
    p = urlsplit(str(url))
    return p.path


def is_forbidden(url):
    path = clean_path(url).lower()
    return any(x in path for x in FORBIDDEN)


def walk_matches(obj, symbol, path="$", out=None, depth=0):
    if out is None:
        out = []
    if depth > 12:
        return out
    if isinstance(obj, dict):
        scalar = {str(k): v for k, v in obj.items() if isinstance(v, (str, int, float, bool)) or v is None}
        joined = " ".join(str(v) for v in scalar.values()).upper()
        if symbol.upper() in joined:
            out.append((path, obj))
        for k, v in obj.items():
            walk_matches(v, symbol, f"{path}.{k}", out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5000]):
            walk_matches(v, symbol, f"{path}[{i}]", out, depth + 1)
    return out


def candidate_from_dict(d, symbol):
    aliases = {
        "id": ("id", "itemId", "item_id", "idItem", "instrumentId", "instrument_id"),
        "ticker": ("ticker", "simbolo", "symbol", "codigo"),
        "description": ("descripcion", "description", "nombre", "name"),
    }
    def first(keys):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return None
    ticker = first(aliases["ticker"])
    desc = first(aliases["description"])
    iid = first(aliases["id"])
    tipo = d.get("tipoItem") or d.get("instrumentType") or d.get("type")
    type_id = None
    type_desc = None
    if isinstance(tipo, dict):
        type_id = tipo.get("id")
        type_desc = tipo.get("descripcion") or tipo.get("description") or tipo.get("name")
    for k in ("typeId", "tipoItemId", "instrumentTypeId"):
        if type_id is None and k in d:
            type_id = d.get(k)
    text = " ".join(str(x) for x in (ticker, desc) if x is not None).upper()
    exact = str(ticker or "").upper() == symbol.upper()
    contains = symbol.upper() in text
    if not (exact or contains):
        return None
    if iid is None:
        return None
    return {
        "item_id": str(iid),
        "ticker": str(ticker or symbol),
        "description": str(desc or ""),
        "type_id": None if type_id is None else str(type_id),
        "type_desc": str(type_desc or ""),
        "exact": exact,
    }


def summarize_history(data):
    rows = []
    if isinstance(data, dict):
        payload = data.get("payload")
        if isinstance(payload, list):
            rows = payload
        else:
            for k in ("data", "items", "result", "results", "historico", "history", "series"):
                v = data.get(k)
                if isinstance(v, list):
                    rows = v
                    break
    elif isinstance(data, list):
        rows = data
    dates = []
    keys = []
    if rows and isinstance(rows[0], dict):
        keys = sorted(map(str, rows[0].keys()))[:20]
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in ("fechaCotizacion", "fecha", "date", "datetime"):
            if r.get(k):
                dates.append(str(r[k]))
                break
    return {
        "rows": len(rows),
        "first_date": min(dates) if dates else None,
        "last_date": max(dates) if dates else None,
        "keys": keys,
    }


def click_graph(page):
    attempts = [
        lambda: page.get_by_role("button", name="Graficador", exact=True),
        lambda: page.get_by_text("Graficador", exact=True),
        lambda: page.locator('button:has-text("Graficador")'),
    ]
    for fn in attempts:
        try:
            loc = fn()
            for i in range(min(loc.count(), 8)):
                if loc.nth(i).is_visible():
                    loc.nth(i).click(timeout=4000)
                    return True
        except Exception:
            pass
    return False


def locate_global_search(page):
    selectors = [
        'input[placeholder="Buscar instrumento"]',
        'input[placeholder*="Buscar instrumento" i]',
        'input[aria-label*="Buscar instrumento" i]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 10)):
                if loc.nth(i).is_visible():
                    return loc.nth(i)
        except Exception:
            pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    args = ap.parse_args()

    blocked_nonread = 0
    blocked_forbidden = 0

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=args.profile,
            executable_path="/usr/bin/google-chrome-stable",
            headless=True,
            locale="es-AR",
            timezone_id="America/Argentina/Buenos_Aires",
            viewport={"width": 1440, "height": 1000},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        def guard(route, request):
            nonlocal blocked_nonread, blocked_forbidden
            method = request.method.upper()
            if is_forbidden(request.url):
                blocked_forbidden += 1
                return route.abort()
            if method not in ("GET", "HEAD", "OPTIONS"):
                blocked_nonread += 1
                return route.abort()
            return route.continue_()

        ctx.route("**/*", guard)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(TRADING + "/estadoDeCuenta", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)

        auth = (
            urlsplit(page.url).netloc == "trading.portfoliopersonal.com"
            and "login" not in urlsplit(page.url).path.lower()
        )
        print("PPI_MULTIFAMILY_INTEGRAL_CANARY")
        print("AUTH=" + ("AUTHENTICATED_TRUSTED_DEVICE" if auth else "BLOCKED_AUTH_SESSION_EXPIRED"))
        if not auth:
            ctx.close()
            return 20

        for family, symbol in TARGETS:
            search_candidates = []
            search_endpoints = []

            def on_search_response(resp):
                try:
                    if resp.request.method.upper() != "GET":
                        return
                    p = urlsplit(resp.url)
                    if p.netloc != API_HOST:
                        return
                    ct = (resp.headers.get("content-type") or "").lower()
                    if "json" not in ct:
                        return
                    body = resp.body()
                    if len(body) > 2_000_000:
                        return
                    data = json.loads(body.decode("utf-8"))
                    mm = walk_matches(data, symbol)
                    if mm:
                        search_endpoints.append((resp.status, p.path))
                    for _, d in mm:
                        if isinstance(d, dict):
                            c = candidate_from_dict(d, symbol)
                            if c:
                                search_candidates.append(c)
                except Exception:
                    pass

            page.on("response", on_search_response)
            try:
                page.goto(TRADING + "/estadoDeCuenta", wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                inp = locate_global_search(page)
                if inp is not None:
                    inp.fill(symbol, timeout=4000)
                    page.wait_for_timeout(4500)
            except Exception:
                pass
            try:
                page.remove_listener("response", on_search_response)
            except Exception:
                pass

            dedup = {}
            for c in search_candidates:
                dedup[(c["item_id"], c.get("type_id"), c.get("ticker"))] = c
            candidates = list(dedup.values())
            candidates.sort(key=lambda x: (not x["exact"], x["item_id"]))
            best = candidates[0] if candidates else None
            eps = []
            seen = set()
            for status, path in search_endpoints:
                if (status, path) not in seen:
                    seen.add((status, path))
                    eps.append((status, path))

            if not best:
                print(f"{family}|{symbol}|FOUND=NO|SEARCH_ENDPOINTS={';'.join(f'{s}:{p}' for s,p in eps[:8]) or '-'}")
                continue

            print(
                f"{family}|{symbol}|FOUND=YES|ITEM={best['item_id']}|TYPE_ID={best['type_id']}|"
                f"TYPE={best['type_desc'][:80]}|DESC={best['description'][:120]}|"
                f"SEARCH_ENDPOINTS={';'.join(f'{s}:{p}' for s,p in eps[:8]) or '-'}"
            )

            item = best["item_id"]
            if family == "FCI":
                detail_urls = [TRADING + f"/Cotizaciones/FCIs/{item}"]
                plazos = [None]
            else:
                detail_urls = [TRADING + f"/Cotizaciones/Item/{item}"]
                plazos = ["1", "2"]

            for plazo in plazos:
                captures = []
                detail_status = None
                graph = False

                def on_hist(resp):
                    try:
                        if resp.request.method.upper() != "GET":
                            return
                        p = urlsplit(resp.url)
                        if p.netloc != API_HOST:
                            return
                        if "histor" not in p.path.lower():
                            return
                        ct = (resp.headers.get("content-type") or "").lower()
                        rec = {"status": resp.status, "path": p.path, "rows": None, "first": None, "last": None}
                        if "json" in ct:
                            try:
                                data = json.loads(resp.body().decode("utf-8"))
                                s = summarize_history(data)
                                rec.update({"rows": s["rows"], "first": s["first_date"], "last": s["last_date"]})
                            except Exception:
                                pass
                        captures.append(rec)
                    except Exception:
                        pass

                page.on("response", on_hist)
                try:
                    url = detail_urls[0]
                    if plazo is not None:
                        url += f"?plazo={plazo}"
                    r = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    detail_status = r.status if r else None
                    page.wait_for_timeout(3500)
                    graph = click_graph(page)
                    page.wait_for_timeout(5000)
                except Exception:
                    pass
                try:
                    page.remove_listener("response", on_hist)
                except Exception:
                    pass

                if captures:
                    besth = max(captures, key=lambda x: (x["status"] == 200, x["rows"] or 0))
                    print(
                        f"{family}|{symbol}|DETAIL={detail_status}|PLAZO={plazo or '-'}|GRAFICADOR={graph}|"
                        f"HIST_STATUS={besth['status']}|HIST_PATH={besth['path']}|ROWS={besth['rows']}|"
                        f"FROM={besth['first']}|TO={besth['last']}"
                    )
                else:
                    print(
                        f"{family}|{symbol}|DETAIL={detail_status}|PLAZO={plazo or '-'}|GRAFICADOR={graph}|"
                        f"HIST_STATUS=NO_HISTORY_XHR|ROWS=0"
                    )

        print(f"BLOCKED_NONREAD={blocked_nonread}")
        print(f"BLOCKED_FORBIDDEN={blocked_forbidden}")
        print("CREDENTIAL_VALUES_LOGGED=False")
        print("QUERY_STRINGS_LOGGED=False")
        print("CANONICAL_WRITE=DENY")
        print("REAL_ORDERS=0")
        print("MASS_SCRAPING_STARTED=NO")
        print("HISTORY_HORIZON_POLICY=PREVIOUS_365D")
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
