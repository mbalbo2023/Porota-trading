#!/usr/bin/env python3
"""Targeted authenticated GET-only capture of current PPI contractual responses.

Captures sanitized response data only for the three first-party endpoints proven by
run 34865060794. Query strings, request headers, cookies, auth tokens and account
information are never logged or persisted. Every non-read request is aborted.
No DB/service/order capability exists.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from rc6_ppi_contract_normalizer import instrumentos_operables

TRADING = "https://trading.portfoliopersonal.com"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TARGET_PATHS = {
    "/api/Ordenes/InstrumentosOperables": "InstrumentosOperables",
    "/api/Ordenes/CaucionesOperables": "CaucionesOperables",
    "/api/Ordenes/ConfiguracionOperatoriaSimplificada": "ConfiguracionOperatoriaSimplificada",
}
ROUTES = ["/Operar/Bonos", "/Operar/Ons", "/Operar/Cauciones"]

DROP_KEY_PARTS = (
    "cuenta", "account", "saldo", "tenencia", "disponible", "comitente", "cliente",
    "documento", "dni", "cuit", "email", "mail", "telefono", "phone", "token",
    "password", "passwd", "cookie", "authorization", "secret", "session", "usuario",
    "username", "user_id", "nombrecliente", "razonsocial",
)
SAFE_KEY_PARTS = (
    "ticker", "simbolo", "especie", "descripcion", "instrumento", "moneda", "currency",
    "plazo", "dia", "venc", "tasa", "tna", "minimo", "minimum", "maximo", "maximum",
    "multiplo", "step", "comision", "porcentaje", "derecho", "mercado", "market",
    "settlement", "liquidacion", "fecha", "nominal", "lamina", "cutoff", "rescate",
    "itemid", "instrumentoid", "plazoid", "monedaid", "cantidaddecimales",
    "cantidaddecimalesprecio", "operablesubasta", "garantia", "aforo", "tipo",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_host_path(url: str) -> str:
    u = urlsplit(str(url))
    return f"{u.netloc}{u.path}"


def key_ok(key: object) -> bool:
    s = str(key).lower().replace("-", "_")
    if any(x in s for x in DROP_KEY_PARTS) or s == "id":
        return False
    return any(x in s for x in SAFE_KEY_PARTS)


def safe_value(v, depth=0):
    if depth > 5:
        return None
    if isinstance(v, dict):
        out = {}
        for k, nested in v.items():
            if key_ok(k):
                sv = safe_value(nested, depth + 1)
                if sv not in (None, "", [], {}):
                    out[str(k)] = sv
            elif isinstance(nested, (dict, list)):
                sv = safe_value(nested, depth + 1)
                if sv not in (None, "", [], {}):
                    out[str(k)] = sv
        return out
    if isinstance(v, list):
        return [x for x in (safe_value(x, depth + 1) for x in v[:1500]) if x not in (None, "", [], {})][:1500]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)[:300]


def payload_rows(payload):
    x = payload.get("payload") if isinstance(payload, dict) else payload
    for _ in range(5):
        if isinstance(x, dict) and "payload" in x:
            x = x.get("payload")
        else:
            break
    if isinstance(x, list):
        raw = x
    elif isinstance(x, dict):
        lists = [v for v in x.values() if isinstance(v, list)]
        raw = max(lists, key=len) if lists else [x]
    else:
        raw = []
    out = []
    for row in raw[:1500]:
        if not isinstance(row, dict):
            continue
        s = safe_value(row)
        if isinstance(s, dict) and s:
            out.append(s)
    return out


def schema_shape(payload):
    if isinstance(payload, dict):
        keys = sorted(str(k) for k in payload.keys() if not any(x in str(k).lower() for x in DROP_KEY_PARTS))[:100]
        return {"type": "object", "keys": keys}
    if isinstance(payload, list):
        return {
            "type": "array",
            "count": len(payload),
            "first_keys": sorted(str(k) for k in payload[0].keys())[:100] if payload and isinstance(payload[0], dict) else [],
        }
    return {"type": type(payload).__name__}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()

    out = {
        "schema": "POROTA_RC6_PPI_CONTRACT_TARGETED_CAPTURE_V1",
        "generated_at": now_iso(),
        "auth_status": "UNKNOWN",
        "routes": [],
        "captures": [],
        "blocked_nonread": 0,
        "query_strings_logged": False,
        "request_headers_logged": False,
        "response_bodies_raw_logged": False,
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
        seen = set()

        def guard(route, request):
            if request.method.upper() not in SAFE_METHODS:
                out["blocked_nonread"] += 1
                return route.abort()
            return route.continue_()

        def on_response(response):
            try:
                req = response.request
                u = urlsplit(response.url)
                if req.method.upper() != "GET" or u.netloc != "api.portfoliopersonal.com":
                    return
                kind = TARGET_PATHS.get(u.path)
                if not kind or response.status >= 400:
                    return
                key = (active["route"], kind)
                if key in seen:
                    return
                data = response.json()
                rec = {
                    "kind": kind,
                    "observed_route": active["route"],
                    "host_path": clean_host_path(response.url),
                    "http_status": int(response.status),
                    "schema": schema_shape(data),
                }
                if kind == "InstrumentosOperables":
                    rows = instrumentos_operables(data)
                    rec["row_count"] = len(rows)
                    rec["al30_rows"] = [r for r in rows if str(r.get("ticker") or "").upper() == "AL30"][:10]
                    rec["sample_fieldsets"] = [sorted(r.keys()) for r in rows[:5]]
                elif kind == "CaucionesOperables":
                    rows = payload_rows(data)
                    rec["row_count"] = len(rows)
                    rec["rows"] = rows[:500]
                    rec["sample_fieldsets"] = [sorted(r.keys()) for r in rows[:10]]
                else:
                    rec["sanitized"] = safe_value(data)
                out["captures"].append(rec)
                seen.add(key)
            except Exception:
                pass

        ctx.route("**/*", guard)
        page.on("response", on_response)

        for route in ROUTES:
            active["route"] = route
            rr = {"requested": route, "reached": False}
            try:
                page.goto(TRADING + route, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3500)
                u = urlsplit(page.url)
                rr["final_host_path"] = f"{u.netloc}{u.path}"
                rr["reached"] = u.netloc == "trading.portfoliopersonal.com" and "login" not in u.path.lower() and "logout" not in u.path.lower()
            except Exception as exc:
                rr["error"] = exc.__class__.__name__
            out["routes"].append(rr)

        out["auth_status"] = "AUTHENTICATED_TRUSTED_DEVICE" if out["routes"] and all(r.get("reached") for r in out["routes"]) else "BLOCKED_AUTH_SESSION_EXPIRED"
        ctx.close()

    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(target, 0o600)

    kinds = sorted({x.get("kind") for x in out["captures"]})
    al30 = [x for x in out["captures"] if x.get("kind") == "InstrumentosOperables" and x.get("al30_rows")]
    cauc = [x for x in out["captures"] if x.get("kind") == "CaucionesOperables"]
    print(json.dumps({
        "state": out["auth_status"],
        "routes_reached": sum(1 for x in out["routes"] if x.get("reached")),
        "routes_total": len(out["routes"]),
        "capture_kinds": kinds,
        "capture_records": len(out["captures"]),
        "al30_explicit": bool(al30),
        "al30_rows": sum(len(x.get("al30_rows") or []) for x in al30),
        "al30_fieldsets": [sorted(r.keys()) for x in al30 for r in (x.get("al30_rows") or [])][:10],
        "cauciones_records": len(cauc),
        "cauciones_rows": sum(int(x.get("row_count") or 0) for x in cauc),
        "cauciones_fieldsets": [fs for x in cauc for fs in (x.get("sample_fieldsets") or [])][:20],
        "blocked_nonread": out["blocked_nonread"],
        "query_strings_logged": False,
        "request_headers_logged": False,
        "raw_response_bodies_logged": False,
        "db_import_executed": False,
        "service_restarted": False,
        "real_orders_sent": 0,
        "capture": str(target),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if out["auth_status"] == "AUTHENTICATED_TRUSTED_DEVICE" else 4


if __name__ == "__main__":
    raise SystemExit(main())
