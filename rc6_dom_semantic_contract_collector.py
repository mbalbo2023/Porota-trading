#!/usr/bin/env python3
"""Authenticated read-only DOM collector for W12 semantic Contract Evidence.

Captures only rendered table headers/cell text from /Cotizaciones/* pages. It does
not infer contract fields, click controls, submit forms, call order routes, or
persist raw HTML/cookies/tokens/request bodies.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

TRADING = "https://trading.portfoliopersonal.com"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROUTES = [
    "/Cotizaciones/FCIs", "/Cotizaciones/FCIsExterior", "/Cotizaciones/Acciones",
    "/Cotizaciones/AccionesUSA", "/Cotizaciones/Bonos", "/Cotizaciones/Cauciones",
    "/Cotizaciones/Cedears", "/Cotizaciones/ETFs", "/Cotizaciones/Futuros",
    "/Cotizaciones/Letras", "/Cotizaciones/Licitaciones", "/Cotizaciones/Ons",
    "/Cotizaciones/Opciones", "/Cotizaciones/Indices", "/Cotizaciones/Monedas",
    "/Cotizaciones/Tasas",
]


def clean(value, limit=240):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def clean_url(url):
    u = urlsplit(str(url))
    return f"{u.scheme}://{u.netloc}{u.path}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()

    out = {
        "schema": "POROTA_RC6_PPI_DOM_SEMANTIC_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auth_status": "UNKNOWN", "routes": [], "blocked_nonread": [],
        "continue_clicked": False, "amount_filled": False, "price_filled": False,
        "real_orders_sent": 0,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)

    if not Path(args.profile).is_dir():
        out["auth_status"] = "BLOCKED_AUTH_PROFILE_MISSING"
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(target, 0o600)
        return 4

    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=args.profile, executable_path=args.chrome, headless=True,
                locale="es-AR", timezone_id="America/Argentina/Buenos_Aires",
                viewport={"width": 1440, "height": 1000},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            def guard(route, request):
                method = request.method.upper()
                if method not in SAFE_METHODS:
                    u = urlsplit(request.url)
                    out["blocked_nonread"].append({"method": method, "host": u.netloc, "path": u.path[:160]})
                    return route.abort()
                return route.continue_()
            ctx.route("**/*", guard)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(TRADING + "/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(900)
            u = urlsplit(page.url)
            authenticated = u.netloc == "trading.portfoliopersonal.com" and "login" not in u.path.lower()
            if not authenticated:
                out["auth_status"] = "BLOCKED_AUTH_SESSION_EXPIRED"
            else:
                out["auth_status"] = "AUTHENTICATED_TRUSTED_DEVICE"
                for route_name in ROUTES:
                    item = {"route": route_name, "reached": False, "tables": []}
                    try:
                        page.goto(TRADING + route_name, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1400)
                        pu = urlsplit(page.url)
                        item["url"] = clean_url(page.url)
                        item["reached"] = pu.netloc == "trading.portfoliopersonal.com" and "login" not in pu.path.lower()
                        tables = page.evaluate("""() => [...document.querySelectorAll('table')].slice(0,20).map(t => ({
                          headers:[...t.querySelectorAll('th')].slice(0,40).map(x=>x.innerText),
                          rows:[...t.querySelectorAll('tbody tr')].slice(0,1000).map(r=>[...r.querySelectorAll('td')].slice(0,40).map(x=>x.innerText))
                        }))""")
                        for table in tables:
                            headers = [clean(x) for x in (table.get("headers") or [])]
                            rows = []
                            for row in table.get("rows") or []:
                                vals = [clean(x) for x in row]
                                if any(vals):
                                    rows.append(vals)
                            item["tables"].append({"headers": headers, "rows": rows, "row_count": len(rows)})
                    except Exception as exc:
                        item["error"] = type(exc).__name__
                    out["routes"].append(item)
            ctx.close()
    except Exception as exc:
        if out["auth_status"] == "UNKNOWN":
            out["auth_status"] = "BLOCKED_BROWSER_ERROR"
        out["error"] = type(exc).__name__ + ":" + str(exc)[:240]

    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(target, 0o600)
    table_rows = sum(t.get("row_count", 0) for r in out["routes"] for t in r.get("tables", []))
    print(json.dumps({
        "state": out["auth_status"], "routes": len(out["routes"]),
        "reached": sum(bool(r.get("reached")) for r in out["routes"]),
        "table_rows": table_rows, "blocked_nonread": len(out["blocked_nonread"]),
        "real_orders_sent": 0, "capture": str(target),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if out["auth_status"] == "AUTHENTICATED_TRUSTED_DEVICE" else 4


if __name__ == "__main__":
    raise SystemExit(main())
