#!/usr/bin/env python3
"""Sanitized, GET-only network inventory for PPI contractual evidence.

This is diagnostic-only. It records first-party GET host/path, HTTP status and JSON
schema shape while visiting known quote/order-entry pages. It never records query
strings, request headers, cookies, response bodies, account data or credentials.
All non-read requests are aborted. No DB/service/order capability exists here.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

TRADING = "https://trading.portfoliopersonal.com"
API_HOSTS = {"api.portfoliopersonal.com", "trading.portfoliopersonal.com"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROUTES = [
    "/estadoDeCuenta",
    "/Cotizaciones/Bonos",
    "/Operar/Bonos",
    "/Operar/Ons",
    "/Cotizaciones/Cauciones",
    "/Operar/Cauciones",
]
SENSITIVE_KEY_PARTS = (
    "cuenta", "account", "saldo", "tenencia", "disponible", "comitente", "cliente",
    "documento", "dni", "cuit", "email", "mail", "telefono", "phone", "token",
    "password", "passwd", "cookie", "authorization", "secret", "session", "usuario",
    "username", "user_id", "nombrecliente", "razonsocial",
)
INTERESTING_KEY_PARTS = (
    "ticker", "simbolo", "especie", "instrument", "item", "moneda", "currency",
    "plazo", "settlement", "liquid", "cantidad", "decimal", "precio", "price",
    "tasa", "min", "max", "step", "multip", "lamina", "venc", "amort", "interes",
    "comision", "derecho", "mercado", "market", "operable", "garantia", "aforo",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_host_path(url: str) -> str:
    u = urlsplit(str(url))
    return f"{u.netloc}{u.path}"


def safe_key(k: object) -> bool:
    s = str(k).lower().replace("-", "_")
    return not any(x in s for x in SENSITIVE_KEY_PARTS)


def keyset(obj: object, limit: int = 80) -> list[str]:
    if not isinstance(obj, dict):
        return []
    return sorted(str(k) for k in obj.keys() if safe_key(k))[:limit]


def interesting_keys(keys: list[str]) -> list[str]:
    out = []
    for k in keys:
        s = k.lower()
        if any(x in s for x in INTERESTING_KEY_PARTS):
            out.append(k)
    return out[:80]


def json_shape(data: object) -> dict:
    if isinstance(data, dict):
        keys = keyset(data)
        shape = {
            "type": "object",
            "keys": keys,
            "interesting_keys": interesting_keys(keys),
        }
        for wrapper in ("payload", "data", "result", "results", "items"):
            child = data.get(wrapper)
            if isinstance(child, list):
                shape["wrapper"] = wrapper
                shape["list_count"] = len(child)
                if child and isinstance(child[0], dict):
                    ck = keyset(child[0])
                    shape["first_keys"] = ck
                    shape["first_interesting_keys"] = interesting_keys(ck)
                break
            if isinstance(child, dict):
                ck = keyset(child)
                shape["wrapper"] = wrapper
                shape["nested_keys"] = ck
                shape["nested_interesting_keys"] = interesting_keys(ck)
                nested_lists = [(str(k), v) for k, v in child.items() if isinstance(v, list) and safe_key(k)]
                if nested_lists:
                    name, arr = max(nested_lists, key=lambda x: len(x[1]))
                    shape["nested_list_key"] = name
                    shape["nested_list_count"] = len(arr)
                    if arr and isinstance(arr[0], dict):
                        fk = keyset(arr[0])
                        shape["nested_first_keys"] = fk
                        shape["nested_first_interesting_keys"] = interesting_keys(fk)
                break
        return shape
    if isinstance(data, list):
        shape = {"type": "array", "count": len(data)}
        if data and isinstance(data[0], dict):
            fk = keyset(data[0])
            shape["first_keys"] = fk
            shape["first_interesting_keys"] = interesting_keys(fk)
        return shape
    return {"type": type(data).__name__}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()

    out = {
        "schema": "POROTA_RC6_PPI_CONTRACT_GET_INVENTORY_V1",
        "generated_at": now_iso(),
        "auth_status": "UNKNOWN",
        "routes": [],
        "gets": [],
        "blocked_nonread": Counter(),
        "query_strings_logged": False,
        "headers_logged": False,
        "response_bodies_logged": False,
        "credentials_logged": False,
        "db_import_executed": False,
        "service_restarted": False,
        "real_orders_sent": 0,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)

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
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        active = {"route": ""}
        seen: set[tuple[str, str]] = set()

        def guard(route, request):
            method = request.method.upper()
            if method not in SAFE_METHODS:
                u = urlsplit(request.url)
                out["blocked_nonread"][f"{method}:{u.netloc}{u.path}"] += 1
                return route.abort()
            return route.continue_()

        def response_seen(response):
            try:
                req = response.request
                if req.method.upper() != "GET":
                    return
                u = urlsplit(response.url)
                if u.netloc not in API_HOSTS:
                    return
                hp = f"{u.netloc}{u.path}"
                key = (active["route"], hp)
                if key in seen:
                    return
                ct = (response.headers.get("content-type") or "").lower()
                rec = {
                    "observed_route": active["route"],
                    "host_path": hp,
                    "status": int(response.status),
                    "json": "json" in ct,
                }
                if "json" in ct and response.status < 400:
                    try:
                        rec["shape"] = json_shape(response.json())
                    except Exception:
                        rec["shape"] = {"type": "json_parse_failed"}
                out["gets"].append(rec)
                seen.add(key)
            except Exception:
                pass

        ctx.route("**/*", guard)
        page.on("response", response_seen)

        for route in ROUTES:
            active["route"] = route
            row = {"requested": route, "reached": False}
            try:
                page.goto(TRADING + route, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
                u = urlsplit(page.url)
                row["final_host_path"] = f"{u.netloc}{u.path}"
                row["reached"] = (
                    u.netloc == "trading.portfoliopersonal.com"
                    and "login" not in u.path.lower()
                    and "logout" not in u.path.lower()
                )
            except Exception as exc:
                row["error"] = exc.__class__.__name__
            out["routes"].append(row)

        auth = bool(out["routes"] and out["routes"][0].get("reached"))
        out["auth_status"] = "AUTHENTICATED_TRUSTED_DEVICE" if auth else "BLOCKED_AUTH_SESSION_EXPIRED"
        ctx.close()

    out["blocked_nonread"] = dict(sorted(out["blocked_nonread"].items()))
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(target, 0o600)

    api_paths = sorted({x["host_path"] for x in out["gets"] if x.get("json")})
    interesting = []
    for x in out["gets"]:
        sh = x.get("shape") or {}
        keys = (
            sh.get("interesting_keys", [])
            + sh.get("first_interesting_keys", [])
            + sh.get("nested_interesting_keys", [])
            + sh.get("nested_first_interesting_keys", [])
        )
        if keys:
            interesting.append({"route": x["observed_route"], "host_path": x["host_path"], "interesting_keys": sorted(set(keys))})
    print(json.dumps({
        "state": out["auth_status"],
        "routes_reached": sum(1 for x in out["routes"] if x.get("reached")),
        "routes_total": len(out["routes"]),
        "first_party_get_records": len(out["gets"]),
        "json_host_paths": api_paths,
        "interesting_json": interesting[:80],
        "blocked_nonread_count": sum(out["blocked_nonread"].values()),
        "query_strings_logged": False,
        "headers_logged": False,
        "response_bodies_logged": False,
        "db_import_executed": False,
        "service_restarted": False,
        "real_orders_sent": 0,
        "capture": str(target),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if auth else 4


if __name__ == "__main__":
    raise SystemExit(main())
