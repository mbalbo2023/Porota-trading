#!/usr/bin/env python3
"""POROTA RC6 W12 — deep authenticated PPI contract evidence scraper V2.

This collector is read-only by construction:
- trusted existing Chrome profile only;
- only GET/HEAD/OPTIONS network methods are allowed;
- order/trade/confirm/cancel routes are never navigated;
- no clicks are required to collect the base evidence;
- request bodies, cookies, tokens, account information and raw HTML are never stored;
- contract fields are observations only: no field is inferred.

It collects four complementary evidence layers:
1. all rows/headers rendered in each /Cotizaciones family;
2. safe row links and data-* attributes;
3. sanitized first-party JSON/XHR emitted while each family renders;
4. safe linked detail pages when the table exposes them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BASE = "https://trading.portfoliopersonal.com"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROUTES = [
    "/Cotizaciones/FCIs", "/Cotizaciones/FCIsExterior", "/Cotizaciones/Acciones",
    "/Cotizaciones/AccionesUSA", "/Cotizaciones/Bonos", "/Cotizaciones/Cauciones",
    "/Cotizaciones/Cedears", "/Cotizaciones/ETFs", "/Cotizaciones/Futuros",
    "/Cotizaciones/Letras", "/Cotizaciones/Licitaciones", "/Cotizaciones/Ons",
    "/Cotizaciones/Opciones", "/Cotizaciones/Indices", "/Cotizaciones/Monedas",
    "/Cotizaciones/Tasas",
]
DETAIL_PRIORITY = {
    "/Cotizaciones/FCIs", "/Cotizaciones/FCIsExterior", "/Cotizaciones/AccionesUSA",
    "/Cotizaciones/Bonos", "/Cotizaciones/Cauciones", "/Cotizaciones/ETFs",
    "/Cotizaciones/Futuros", "/Cotizaciones/Letras", "/Cotizaciones/Licitaciones",
    "/Cotizaciones/Ons", "/Cotizaciones/Opciones",
}
DROP_KEY_PARTS = (
    "token", "cookie", "password", "passwd", "secret", "authorization", "bearer",
    "account", "cuenta", "comitente", "saldo", "tenencia", "portfolio", "usuario",
    "username", "email", "mail", "phone", "telefono", "dni", "cuit", "session",
    "refresh", "clientkey", "apikey",
)
CONTRACT_KEY_PARTS = (
    "ticker", "simbolo", "símbolo", "especie", "instrument", "nombre", "descripcion",
    "descripción", "codigo", "código", "isin", "id", "mercado", "market", "moneda",
    "currency", "plazo", "settlement", "liquidacion", "liquidación", "venc", "expiry",
    "fecha", "strike", "ejercicio", "call", "put", "subyacente", "underlying", "ratio",
    "nominal", "lamina", "lámina", "minimo", "mínimo", "minimum", "multiplo", "múltiplo",
    "step", "tick", "multiplier", "multiplicador", "margen", "margin", "tasa", "tna",
    "comision", "comisión", "fee", "derecho", "cutoff", "rescate", "horario", "categoria",
    "categoría", "clase", "tipo", "cantidaddecimales", "precio", "duration", "tir",
    "paridad", "emisor", "garantia", "garantía", "lot", "lote",
)


def clean(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def clean_url(url: str) -> str:
    u = urlsplit(str(url))
    return f"{u.scheme}://{u.netloc}{u.path}"


def safe_page_url(url: str) -> bool:
    u = urlsplit(str(url))
    p = u.path.lower()
    forbidden = ("/operar", "/orden", "/trade", "/confirm", "/cancel", "/suscribir", "/rescatar")
    return (
        u.scheme == "https"
        and u.netloc == "trading.portfoliopersonal.com"
        and "login" not in p
        and not any(x in p for x in forbidden)
    )


def first_party_json(url: str, content_type: str) -> bool:
    u = urlsplit(str(url))
    host = u.netloc.lower().split(":", 1)[0]
    return (
        (host == "portfoliopersonal.com" or host.endswith(".portfoliopersonal.com"))
        and (u.path.lower().startswith("/api/") or "json" in str(content_type or "").lower())
    )


def allowed_key(key) -> bool:
    s = str(key).lower().replace("-", "_")
    if any(x in s for x in DROP_KEY_PARTS):
        return False
    return any(x in s for x in CONTRACT_KEY_PARTS)


def sanitize(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, dict):
        out = {}
        for key, nested in value.items():
            low = str(key).lower()
            if any(x in low for x in DROP_KEY_PARTS):
                continue
            if allowed_key(key):
                sv = sanitize(nested, depth + 1)
                if sv not in (None, "", [], {}):
                    out[str(key)] = sv
            elif isinstance(nested, (dict, list)):
                sv = sanitize(nested, depth + 1)
                if sv not in (None, "", [], {}):
                    out[str(key)] = sv
        return out
    if isinstance(value, list):
        out = []
        for item in value[:1000]:
            sv = sanitize(item, depth + 1)
            if sv not in (None, "", [], {}):
                out.append(sv)
        return out
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return clean(value, 300)


def extract_tables(page, max_rows=250):
    raw = page.evaluate(
        """(maxRows) => [...document.querySelectorAll('table')].slice(0,20).map(t => ({
          headers:[...t.querySelectorAll('th')].slice(0,60).map(x=>x.innerText),
          rows:[...t.querySelectorAll('tbody tr')].slice(0,maxRows).map(r=>({
            cells:[...r.querySelectorAll('td')].slice(0,60).map(x=>x.innerText),
            links:[...r.querySelectorAll('a[href]')].slice(0,15).map(a=>({text:a.innerText,href:a.href})),
            attrs:Object.fromEntries([...r.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value]))
          }))
        }))""",
        max_rows,
    )
    return raw or []


def normalize_tables(raw_tables, route, hrefs):
    tables = []
    for table in raw_tables:
        rows = []
        for row in table.get("rows") or []:
            links = []
            for item in row.get("links") or []:
                href = urljoin(BASE, item.get("href") or "")
                if safe_page_url(href):
                    href = clean_url(href)
                    hrefs[route].add(href)
                    links.append({"text": clean(item.get("text"), 220), "href": href})
            attrs = {
                str(k): clean(v, 300)
                for k, v in (row.get("attrs") or {}).items()
                if not any(x in str(k).lower() for x in DROP_KEY_PARTS)
            }
            rows.append({
                "cells": [clean(x, 500) for x in (row.get("cells") or [])],
                "links": links,
                "data_attrs": attrs,
            })
        tables.append({
            "headers": [clean(x, 220) for x in (table.get("headers") or [])],
            "rows": rows,
        })
    return tables


def extract_detail(page):
    raw = page.evaluate(
        """() => ({
          title:document.title,
          headings:[...document.querySelectorAll('h1,h2,h3,h4')].slice(0,50).map(x=>x.innerText),
          definitionPairs:[...document.querySelectorAll('dt')].slice(0,120).map(dt=>({k:dt.innerText,v:dt.nextElementSibling?dt.nextElementSibling.innerText:''})),
          labels:[...document.querySelectorAll('label')].slice(0,120).map(x=>x.innerText),
          namedText:[...document.querySelectorAll('[data-testid],[data-field],[data-name]')].slice(0,120).map(x=>({
            testid:x.getAttribute('data-testid'),field:x.getAttribute('data-field'),name:x.getAttribute('data-name'),text:x.innerText
          })),
          tables:[...document.querySelectorAll('table')].slice(0,15).map(t=>({
            headers:[...t.querySelectorAll('th')].slice(0,60).map(x=>x.innerText),
            rows:[...t.querySelectorAll('tbody tr')].slice(0,80).map(r=>[...r.querySelectorAll('td')].slice(0,60).map(x=>x.innerText))
          }))
        })"""
    ) or {}
    return {
        "title": clean(raw.get("title"), 240),
        "headings": [clean(x, 300) for x in raw.get("headings") or []],
        "definition_pairs": [
            {"key": clean(x.get("k"), 220), "value": clean(x.get("v"), 600)}
            for x in raw.get("definitionPairs") or []
        ],
        "labels": [clean(x, 220) for x in raw.get("labels") or []],
        "named_text": [
            {
                "testid": clean(x.get("testid"), 180),
                "field": clean(x.get("field"), 180),
                "name": clean(x.get("name"), 180),
                "text": clean(x.get("text"), 600),
            }
            for x in raw.get("namedText") or []
        ],
        "tables": [
            {
                "headers": [clean(x, 220) for x in t.get("headers") or []],
                "rows": [[clean(v, 600) for v in row] for row in t.get("rows") or []],
            }
            for t in raw.get("tables") or []
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chrome", default="/usr/bin/google-chrome-stable")
    ap.add_argument("--detail-limit", type=int, default=20)
    ap.add_argument("--max-rows", type=int, default=250)
    args = ap.parse_args()

    result = {
        "schema": "POROTA_RC6_W12_FULL_CONTRACT_DEEP_V2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auth_status": "UNKNOWN",
        "routes": {},
        "details": {},
        "xhr": {},
        "blocked_nonread": [],
        "real_orders_sent": 0,
        "no_inference": True,
        "contract_semantics": "OBSERVED_FIELDS_ONLY",
    }
    hrefs = defaultdict(set)
    active = {"route": "", "detail": ""}

    from playwright.sync_api import sync_playwright

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
            if method not in SAFE_METHODS:
                result["blocked_nonread"].append({"method": method, "url": clean_url(request.url)})
                return route.abort()
            return route.continue_()

        ctx.route("**/*", guard)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp):
            try:
                if resp.request.method.upper() != "GET" or resp.status >= 400:
                    return
                ct = resp.headers.get("content-type") or ""
                if not first_party_json(resp.url, ct):
                    return
                payload = resp.json()
                safe = sanitize(payload)
                if safe in (None, "", [], {}):
                    return
                key = clean_url(resp.url) + "|" + active["route"] + "|" + active["detail"]
                result["xhr"][key] = {
                    "url": clean_url(resp.url),
                    "route": active["route"],
                    "detail": active["detail"],
                    "payload": safe,
                }
            except Exception:
                pass

        page.on("response", on_response)

        page.goto(BASE + "/Cotizaciones/Acciones", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        if not safe_page_url(page.url):
            result["auth_status"] = "BLOCKED_AUTH_OR_UNSAFE_LANDING"
            raise SystemExit(4)
        result["auth_status"] = "AUTHENTICATED_TRUSTED_DEVICE"

        for route in ROUTES:
            active["route"] = route
            active["detail"] = ""
            page.goto(BASE + route, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1500)
            if not safe_page_url(page.url):
                raise RuntimeError("UNSAFE_OR_AUTH_ROUTE:" + route)
            raw_tables = extract_tables(page, args.max_rows)
            tables = normalize_tables(raw_tables, route, hrefs)
            result["routes"][route] = {
                "url": clean_url(page.url),
                "title": clean(page.title(), 240),
                "tables": tables,
                "detail_hrefs": sorted(hrefs[route])[:200],
            }

        for route in sorted(DETAIL_PRIORITY):
            for href in sorted(hrefs[route])[: max(0, args.detail_limit)]:
                if href.rstrip("/") == clean_url(BASE + route).rstrip("/"):
                    continue
                active["route"] = route
                active["detail"] = href
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1200)
                    if not safe_page_url(page.url):
                        continue
                    result["details"][href] = {"source_route": route, **extract_detail(page)}
                except Exception as exc:
                    result["details"][href] = {"source_route": route, "error": type(exc).__name__}

        ctx.close()

    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(target, 0o600)

    summary = {
        route: {
            "rows": sum(len(t.get("rows") or []) for t in item.get("tables") or []),
            "hrefs": len(item.get("detail_hrefs") or []),
        }
        for route, item in result["routes"].items()
    }
    print("AUTH_STATUS=" + result["auth_status"])
    print("ROUTE_SUMMARY=" + json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    print("DETAIL_PAGES_CAPTURED=" + str(len(result["details"])))
    print("FIRSTPARTY_XHR_EVIDENCE=" + str(len(result["xhr"])))
    print("BLOCKED_NONREAD=" + str(len(result["blocked_nonread"])))
    print("REAL_ORDERS_SENT=0")
    print("NO_INFERENCE=YES")
    print("CAPTURE=" + str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
