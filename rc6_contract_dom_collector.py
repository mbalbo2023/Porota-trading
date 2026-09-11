#!/usr/bin/env python3
"""RC6 authenticated PPI DOM Contract Evidence collector.

Read-only/fail-closed contract:
- uses an existing trusted Chrome profile only;
- navigates only /Cotizaciones/* pages;
- permits GET/HEAD/OPTIONS only;
- aborts all mutations; unexpected first-party PPI mutations are recorded and fail closed;
- extracts only visible quote-table headers/cells when headers are explicit;
- supports HTML tables and ARIA grid/table roles without inferring semantics;
- gives dynamic PPI rendering a bounded wait and one bounded retry;
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
RENDER_WAIT_STEPS_MS = (900, 1300, 1800, 2500)
RETRY_WAIT_STEPS_MS = (1000, 1800, 2500)


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


def _texts(locator, limit: int, timeout_ms: int = 700) -> list[str]:
    out: list[str] = []
    try:
        count = min(locator.count(), limit)
    except Exception:
        return out
    for i in range(count):
        try:
            text = clean_text(locator.nth(i).inner_text(timeout=timeout_ms), 100)
            if text:
                out.append(text)
        except Exception:
            continue
    return out


def html_table_snapshot(table) -> dict:
    headers: list[str] = []
    for selector in ("thead th", "th"):
        try:
            headers = _texts(table.locator(selector), 40)
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

    if not headers or rows_loc is None:
        return {
            "kind": "html_table",
            "headers": [],
            "row_count": int(row_count),
            "rows": [],
            "materializable": False,
        }

    rows: list[list[str]] = []
    for ri in range(min(row_count, 100)):
        try:
            cells = rows_loc.nth(ri).locator("td,th")
            vals = [
                clean_text(cells.nth(ci).inner_text(timeout=500))
                for ci in range(min(cells.count(), 40))
            ]
            if vals and any(vals):
                rows.append(vals)
        except Exception:
            continue
    return {
        "kind": "html_table",
        "headers": headers[:40],
        "row_count": int(row_count),
        "rows": rows,
        "materializable": bool(headers and rows),
    }


def aria_grid_snapshot(grid) -> dict:
    headers: list[str] = []
    try:
        headers = _texts(grid.locator('[role="columnheader"]'), 40)
    except Exception:
        headers = []

    try:
        rows_loc = grid.locator('[role="row"]')
        row_count = rows_loc.count()
    except Exception:
        row_count = 0
        rows_loc = None

    if not headers or rows_loc is None:
        return {
            "kind": "aria_grid",
            "headers": [],
            "row_count": int(row_count),
            "rows": [],
            "materializable": False,
        }

    rows: list[list[str]] = []
    for ri in range(min(row_count, 100)):
        try:
            row = rows_loc.nth(ri)
            data_cells = row.locator('[role="gridcell"],[role="cell"],[role="rowheader"]')
            if data_cells.count() == 0:
                continue
            vals = [
                clean_text(data_cells.nth(ci).inner_text(timeout=500))
                for ci in range(min(data_cells.count(), 40))
            ]
            if vals and any(vals):
                rows.append(vals)
        except Exception:
            continue
    return {
        "kind": "aria_grid",
        "headers": headers[:40],
        "row_count": int(row_count),
        "rows": rows,
        "materializable": bool(headers and rows),
    }


def collect_structures(page) -> list[dict]:
    snaps: list[dict] = []
    try:
        tables = page.locator("table")
        for ti in range(min(tables.count(), 8)):
            snap = html_table_snapshot(tables.nth(ti))
            snap["table_index"] = ti
            snaps.append(snap)
    except Exception:
        pass

    try:
        grids = page.locator('[role="grid"],[role="table"],[role="treegrid"]')
        for gi in range(min(grids.count(), 6)):
            snap = aria_grid_snapshot(grids.nth(gi))
            snap["table_index"] = 1000 + gi
            snaps.append(snap)
    except Exception:
        pass
    return snaps


def materializable_count(snaps: list[dict]) -> int:
    return sum(1 for snap in snaps if snap.get("materializable"))


def wait_for_materializable(page, waits_ms: tuple[int, ...]) -> tuple[list[dict], int]:
    latest: list[dict] = []
    attempts = 0
    for wait_ms in waits_ms:
        page.wait_for_timeout(wait_ms)
        attempts += 1
        latest = collect_structures(page)
        if materializable_count(latest):
            break
    return latest, attempts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--routes", default="")
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()
    routes = safe_routes(args.routes)
    out = {
        "schema": "POROTA_RC6_PPI_AUTH_DOM_V2",
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
            authenticated = (
                u.netloc == "trading.portfoliopersonal.com"
                and "login" not in u.path.lower()
                and "logout" not in u.path.lower()
            )
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
                        "render_attempts": 0,
                        "reload_retry": False,
                    }
                    try:
                        page.goto(TRADING + requested, wait_until="domcontentloaded", timeout=45000)
                        pu = urlsplit(page.url)
                        item["url"] = clean_url(page.url)
                        item["reached"] = (
                            pu.netloc == "trading.portfoliopersonal.com"
                            and "login" not in pu.path.lower()
                        )
                        if item["reached"]:
                            snaps, attempts = wait_for_materializable(page, RENDER_WAIT_STEPS_MS)
                            item["render_attempts"] += attempts
                            if materializable_count(snaps) == 0:
                                item["reload_retry"] = True
                                page.reload(wait_until="domcontentloaded", timeout=45000)
                                snaps, attempts = wait_for_materializable(page, RETRY_WAIT_STEPS_MS)
                                item["render_attempts"] += attempts
                            item["tables"] = snaps
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
