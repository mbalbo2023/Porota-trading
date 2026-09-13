#!/usr/bin/env python3
"""PPI Web authenticated historical discovery — RC6 SHADOW only.

This module is deliberately NOT a History Store writer. It observes the trusted
PPI browser session, navigates quote/detail pages, and records only sanitized
GET response metadata/schema plus historical-looking rows. Every non-read
request after authentication is aborted locally.

Safety:
- no broker/order imports;
- no DB writes;
- no cookies/tokens/authorization headers/request bodies persisted;
- no query strings persisted;
- no BUY/SELL/order/budget/cancel routes;
- canonical_write is always DENY.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

TRADING = "https://trading.portfoliopersonal.com"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Broad enough to detect provider-specific naming without deciding semantics.
DATE_KEYS = {"date", "fecha", "datetime", "timestamp", "time", "day", "dia", "día"}
OHLC_KEYS = {
    "open", "openingprice", "apertura",
    "high", "max", "maximo", "máximo",
    "low", "min", "minimo", "mínimo",
    "close", "price", "cierre", "ultimo", "último",
}
VOLUME_KEYS = {"volume", "volumen", "quantity", "cantidad", "monto"}
DROP_KEY_PARTS = (
    "account", "cuenta", "saldo", "tenencia", "disponible", "comitente",
    "cliente", "documento", "dni", "cuit", "email", "mail", "telefono",
    "phone", "token", "password", "passwd", "cookie", "authorization",
    "secret", "session", "usuario", "username", "user_id",
)
FORBIDDEN_PATH_PARTS = (
    "/orden", "/order", "/operar/confirm", "/confirmar", "/cancel",
    "/transfer", "/suscribir", "/rescatar",
)

REPRESENTATIVE_ROUTES = (
    ("ACCIONES", "GGAL", "/Cotizaciones/Acciones"),
    ("CEDEARS", "AAPL", "/Cotizaciones/Cedears"),
    ("BONOS", "GD30", "/Cotizaciones/Bonos"),
    ("BONOS_USD", "TX28D", "/Cotizaciones/Bonos"),
    ("ON", "ON", "/Cotizaciones/Ons"),
    ("OPCIONES", "OPCION", "/Cotizaciones/Opciones"),
    ("FUTUROS", "FUTURO", "/Cotizaciones/Futuros"),
    ("LETRAS", "LETRA", "/Cotizaciones/Letras"),
    ("ETF", "ETF", "/Cotizaciones/ETFs"),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def clean_url(value: str) -> str:
    u = urlsplit(str(value))
    return f"{u.scheme}://{u.netloc}{u.path}"


def path_forbidden(url: str) -> bool:
    path = urlsplit(str(url)).path.lower()
    return any(part in path for part in FORBIDDEN_PATH_PARTS)


def key_safe(key: object) -> bool:
    low = str(key).lower().replace("-", "_")
    return not any(part in low for part in DROP_KEY_PARTS)


def _walk(value, depth=0):
    if depth > 5:
        return []
    out = []
    if isinstance(value, dict):
        out.append(value)
        for k, v in value.items():
            if key_safe(k) and isinstance(v, (dict, list)):
                out.extend(_walk(v, depth + 1))
    elif isinstance(value, list):
        for item in value[:2000]:
            out.extend(_walk(item, depth + 1))
    return out


def historical_candidate(payload) -> dict | None:
    """Classify shape only; never infer a financial contract from weak names."""
    rows = []
    observed_keys = set()
    for obj in _walk(payload):
        keys = {str(k).lower() for k in obj if key_safe(k)}
        observed_keys.update(keys)
        has_date = bool(keys & DATE_KEYS)
        ohlc_hits = len(keys & OHLC_KEYS)
        if has_date and ohlc_hits >= 2:
            rows.append(obj)
            if len(rows) >= 1000:
                break
    if not rows:
        return None
    keys = sorted(observed_keys)[:120]
    lower = set(keys)
    return {
        "candidate": True,
        "rows_detected": len(rows),
        "has_date": bool(lower & DATE_KEYS),
        "ohlc_key_hits": sorted(lower & OHLC_KEYS),
        "volume_key_hits": sorted(lower & VOLUME_KEYS),
        "sample_keys": keys,
        "full_ohlcv_shape": len(lower & OHLC_KEYS) >= 4 and bool(lower & VOLUME_KEYS),
    }


def schema_shape(payload) -> dict:
    if isinstance(payload, dict):
        return {"type": "object", "keys": sorted(str(k) for k in payload if key_safe(k))[:120]}
    if isinstance(payload, list):
        first = payload[0] if payload and isinstance(payload[0], dict) else {}
        return {"type": "array", "count": len(payload), "first_keys": sorted(str(k) for k in first if key_safe(k))[:120]}
    return {"type": type(payload).__name__}


def payload_digest(payload) -> str:
    # Digest is evidence-only and does not make the source canonical.
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()

    out = {
        "schema": "POROTA_RC6_PPI_WEB_HISTORY_SHADOW_V1",
        "generated_at": now_iso(),
        "auth_status": "UNKNOWN",
        "canonical_write": "DENY",
        "db_write": "NO",
        "real_orders_sent": 0,
        "routes": [],
        "responses": [],
        "historical_candidates": [],
        "blocked_nonread": [],
        "blocked_forbidden_paths": [],
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    profile = Path(args.profile)
    if not profile.is_dir():
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
                user_data_dir=str(profile), executable_path=args.chrome, headless=True,
                locale="es-AR", timezone_id="America/Argentina/Buenos_Aires",
                viewport={"width": 1440, "height": 1000},
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            def guard(route, request):
                method = request.method.upper()
                if path_forbidden(request.url):
                    out["blocked_forbidden_paths"].append({"method": method, "url": clean_url(request.url)})
                    return route.abort()
                if method not in SAFE_METHODS:
                    out["blocked_nonread"].append({"method": method, "url": clean_url(request.url)})
                    return route.abort()
                return route.continue_()

            ctx.route("**/*", guard)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            active = {"family": "", "symbol": "", "route": ""}
            seen = set()

            def on_response(response):
                try:
                    if response.request.method.upper() != "GET" or response.status >= 400:
                        return
                    c_url = clean_url(response.url)
                    host = urlsplit(c_url).netloc.lower()
                    if not (host.endswith("portfoliopersonal.com") or host.endswith("ppi.com.ar")):
                        return
                    ctype = str(response.headers.get("content-type") or "").lower()
                    if "json" not in ctype:
                        return
                    payload = response.json()
                    key = (c_url, active["family"], active["route"])
                    if key in seen:
                        return
                    seen.add(key)
                    meta = {
                        "url": c_url,
                        "status": response.status,
                        "family_context": active["family"],
                        "symbol_context": active["symbol"],
                        "route_context": active["route"],
                        "schema": schema_shape(payload),
                        "payload_sha256": payload_digest(payload),
                    }
                    out["responses"].append(meta)
                    candidate = historical_candidate(payload)
                    if candidate:
                        out["historical_candidates"].append({**meta, **candidate})
                except Exception:
                    pass

            page.on("response", on_response)
            page.goto(TRADING + "/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1200)
            u = urlsplit(page.url)
            authenticated = u.netloc == "trading.portfoliopersonal.com" and "login" not in u.path.lower()
            if not authenticated:
                out["auth_status"] = "BLOCKED_AUTH_SESSION_EXPIRED"
            else:
                out["auth_status"] = "AUTHENTICATED_TRUSTED_DEVICE"
                for family, symbol, route in REPRESENTATIVE_ROUTES:
                    active.update(family=family, symbol=symbol, route=route)
                    row = {"family": family, "symbol": symbol, "requested": route, "reached": False}
                    try:
                        page.goto(TRADING + route, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(1800)
                        pu = urlsplit(page.url)
                        row["url"] = clean_url(page.url)
                        row["reached"] = pu.netloc == "trading.portfoliopersonal.com" and "login" not in pu.path.lower()
                        # Best-effort search/type only. No submit/click on operation controls.
                        candidates = page.locator('input[type="search"], input[placeholder*="Buscar" i], input[placeholder*="especie" i]')
                        if row["reached"] and candidates.count() > 0 and symbol not in {"ON", "OPCION", "FUTURO", "LETRA", "ETF"}:
                            box = candidates.first
                            try:
                                box.fill(symbol, timeout=2000)
                                page.wait_for_timeout(1200)
                            except Exception:
                                pass
                    except Exception as exc:
                        row["error"] = type(exc).__name__
                    out["routes"].append(row)
            ctx.close()
    except Exception as exc:
        if out["auth_status"] == "UNKNOWN":
            out["auth_status"] = "BLOCKED_BROWSER_ERROR"
        out["error"] = type(exc).__name__ + ":" + str(exc)[:200]

    out["summary"] = {
        "responses": len(out["responses"]),
        "historical_candidates": len(out["historical_candidates"]),
        "full_ohlcv_candidates": sum(bool(x.get("full_ohlcv_shape")) for x in out["historical_candidates"]),
        "blocked_nonread": len(out["blocked_nonread"]),
        "blocked_forbidden_paths": len(out["blocked_forbidden_paths"]),
    }
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(target, 0o600)
    print(json.dumps({
        "auth_status": out["auth_status"],
        "routes": len(out["routes"]),
        **out["summary"],
        "canonical_write": "DENY",
        "real_orders_sent": 0,
    }, sort_keys=True))
    return 0 if out["auth_status"] == "AUTHENTICATED_TRUSTED_DEVICE" else 4


if __name__ == "__main__":
    raise SystemExit(main())
