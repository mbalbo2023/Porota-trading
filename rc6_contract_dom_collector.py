#!/usr/bin/env python3
"""RC6 authenticated PPI DOM Contract Evidence collector.

Read-only/fail-closed contract:
- uses an existing trusted Chrome profile only;
- navigates only /Cotizaciones/* pages;
- permits GET/HEAD/OPTIONS only;
- aborts all mutations; unexpected first-party PPI mutations are recorded and fail closed;
- extracts only visible quote-table headers/cells when headers are explicit;
- never visits /Operar, fills order fields, clicks confirmations, or imports broker-order code;
- never stores cookies, headers, query strings, request bodies, credentials, account data or HTML.
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
PPI_HOSTS = {
    "trading.portfoliopersonal.com",
    "api.portfoliopersonal.com",
    "cuenta.portfoliopersonal.com",
}
ROUTE_FAMILY = {
    "/Cotizaciones/Acciones": "ACCIONES",
    "/Cotizaciones/AccionesUSA": "ACCIONES_USA",
    "/Cotizaciones/Bonos": "BONOS",
    "/Cotizaciones/Cauciones": "CAUCIONES",
    "/Cotizaciones/Cedears": "CEDEARS",
    "/Cotizaciones/ETFs": "ETF",
    "/Cotizaciones/FCIs": "FCI_LOCAL",
    "/Cotizaciones/FCIsExterior": "FCI_EXTERIOR",
    "/Cotizaciones/Futuros": "FUTUROS",
    "/Cotizaciones/Letras": "LETRAS",
    "/Cotizaciones/Licitaciones": "LICITACIONES",
    "/Cotizaciones/Ons": "ON",
    "/Cotizaciones/Opciones": "OPCIONES",
}
DEFAULT_ROUTES = (
    "/Cotizaciones/Cauciones",
    "/Cotizaciones/Licitaciones",
    "/Cotizaciones/Futuros",
    "/Cotizaciones/Bonos",
    "/Cotizaciones/Opciones",
)
RENDER_TIMEOUT_MS = max(1500, int(os.getenv("POROTA_DOM_RENDER_TIMEOUT_MS", "12000")))


def clean_text(value: str, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:limit]


def clean_url(url: str) -> str:
    u = urlsplit(str(url))
    return f"{u.scheme}://{u.netloc}{u.path}"


def safe_routes(raw: str | None) -> list[str]:
    requested = [x.strip() for x in (raw or "").split(",") if x.strip()] or list(DEFAULT_ROUTES)
    out = []
    for route in requested:
        if route not in ROUTE_FAMILY or not route.startswith("/Cotizaciones/"):
            raise ValueError("UNSAFE_OR_UNKNOWN_ROUTE:" + route)
        if route not in out:
            out.append(route)
    return out


def wait_for_explicit_table(page) -> bool:
    """Wait for provider-rendered headers + rows without inferring any semantics.

    PPI renders the row skeleton before its explicit header text is stable.  A fixed
    1.5s sleep therefore produced intermittent false NO_EXPLICIT_HEADER_TABLE results.
    This waits only for already-permitted DOM evidence; it performs no extra mutation.
    """
    try:
        page.wait_for_function(
            """() => Array.from(document.querySelectorAll('table')).some(t => {
                const headers = Array.from(t.querySelectorAll('thead th, th'))
                    .some(h => (h.textContent || '').trim().length > 0);
                const rows = Array.from(t.querySelectorAll('tbody tr, tr'))
                    .some(r => r.querySelectorAll('td,th').length > 0);
                return headers && rows;
            })""",
            timeout=RENDER_TIMEOUT_MS,
        )
        page.wait_for_timeout(350)
        return True
    except Exception:
        return False


def table_snapshot(table) -> dict:
    headers: list[str] = []
    for selector in ("thead th", "th"):
        try:
            q = table.locator(selector)
            headers = [clean_text(q.nth(i).inner_text(timeout=700), 100)
                       for i in range(min(q.count(), 40))]
            headers = [x for x in headers if x]
            if headers:
                break
        except Exception:
            headers = []
    try:
        rows_loc = table.locator("tbody tr")
        if rows_loc.count() == 0:
            rows_loc = table.locator("tr")
        row_count = rows_loc.count()
    except Exception:
        row_count = 0
        rows_loc = None

    # Never infer a column order. Without explicit provider headers only shape is retained.
    if not headers or rows_loc is None:
        return {"headers": [], "row_count": int(row_count), "rows": [], "materializable": False}

    rows: list[list[str]] = []
    for ri in range(min(row_count, 100)):
        try:
            cells = rows_loc.nth(ri).locator("td,th")
            vals = [clean_text(cells.nth(ci).inner_text(timeout=500))
                    for ci in range(min(cells.count(), 40))]
            if vals and any(vals):
                rows.append(vals)
        except Exception:
            continue
    return {
        "headers": headers[:40],
        "row_count": int(row_count),
        "rows": rows,
        "materializable": bool(headers and rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--routes", default="")
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()
    routes = safe_routes(args.routes)
    out = {
        "schema": "POROTA_RC6_PPI_AUTH_DOM_V1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auth_status": "UNKNOWN",
        "routes": [],
        "blocked_nonread": [],
        "real_orders_sent": 0,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not Path(args.profile).is_dir():
        out["auth_status"] = "BLOCKED_AUTH_PROFILE_MISSING"
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(target, 0o600)
        return 4
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        out["auth_status"] = "BLOCKED_PLAYWRIGHT_UNAVAILABLE"
        out["error"] = type(exc).__name__
        target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(target, 0o600)
        return 4

    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=args.profile,
                executable_path=args.chrome,
                headless=True,
                locale="es-AR",
                timezone_id="America/Argentina/Buenos_Aires",
                viewport={"width": 1440, "height": 1000},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            def guard(route, request):
                method = request.method.upper()
                if method in SAFE_METHODS:
                    return route.continue_()
                u = urlsplit(request.url)
                host = u.netloc.lower()
                path = u.path.rstrip("/") or "/"
                if host not in PPI_HOSTS:
                    return route.abort()
                if method == "POST" and host == "trading.portfoliopersonal.com" and path == "/api/logger":
                    return route.abort()
                if method == "POST" and host == "api.portfoliopersonal.com" and path == "/api/v1/zendesk/zendesk-session":
                    return route.abort()
                out["blocked_nonread"].append({"method": method, "url": clean_url(request.url)})
                return route.abort()

            ctx.route("**/*", guard)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(TRADING + "/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(900)
            u = urlsplit(page.url)
            authenticated = (u.netloc == "trading.portfoliopersonal.com"
                             and "login" not in u.path.lower()
                             and "logout" not in u.path.lower())
            if not authenticated:
                out["auth_status"] = "BLOCKED_AUTH_SESSION_EXPIRED"
            else:
                out["auth_status"] = "AUTHENTICATED_TRUSTED_DEVICE"
                for requested in routes:
                    item = {
                        "requested": requested,
                        "family": ROUTE_FAMILY[requested],
                        "reached": False,
                        "url": "",
                        "tables": [],
                        "render_ready": False,
                    }
                    try:
                        page.goto(TRADING + requested, wait_until="domcontentloaded", timeout=45000)
                        pu = urlsplit(page.url)
                        item["url"] = clean_url(page.url)
                        item["reached"] = (pu.netloc == "trading.portfoliopersonal.com"
                                           and "login" not in pu.path.lower())
                        if item["reached"]:
                            item["render_ready"] = wait_for_explicit_table(page)
                            tables = page.locator("table")
                            for ti in range(min(tables.count(), 8)):
                                snap = table_snapshot(tables.nth(ti))
                                snap["table_index"] = ti
                                item["tables"].append(snap)
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
    materializable = sum(
        1 for r in out["routes"] for t in r.get("tables", []) if t.get("materializable")
    )
    summary = {
        "state": out["auth_status"],
        "routes": len(out["routes"]),
        "materializable_tables": materializable,
        "blocked_nonread": len(out["blocked_nonread"]),
        "real_orders_sent": 0,
        "capture": str(target),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if out["auth_status"] != "AUTHENTICATED_TRUSTED_DEVICE":
        return 4
    if out["blocked_nonread"]:
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
