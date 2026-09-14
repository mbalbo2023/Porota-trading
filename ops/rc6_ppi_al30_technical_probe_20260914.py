#!/usr/bin/env python3
"""Read-only AL30 technical-evidence interaction probe for PPI Web.

The probe may type only the literal ticker AL30 into an instrument/search selector and
select an AL30 suggestion. It never fills quantity or price and never clicks submit,
confirm, buy, sell or order controls. Every non-GET/HEAD/OPTIONS request is aborted.
Only sanitized technical provider fields are persisted; query strings, headers,
cookies, credentials, raw response bodies and account data are never logged.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from rc6_ppi_contract_normalizer import bond_technical

TRADING = "https://trading.portfoliopersonal.com"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TECH_KEY_HINT = re.compile(r"(venc|amort|interes|coupon|cupon|isin|lamina|residual|duration|tir|paridad|tecnico|technical)", re.I)
INPUT_GOOD = re.compile(r"(instrument|especie|ticker|bono|buscar|search)", re.I)
INPUT_BAD = re.compile(r"(precio|price|cantidad|quantity|amount|monto|importe|qty)", re.I)
DROP_KEY_PARTS = (
    "cuenta", "account", "saldo", "tenencia", "disponible", "comitente", "cliente",
    "documento", "dni", "cuit", "email", "mail", "telefono", "phone", "token",
    "password", "passwd", "cookie", "authorization", "secret", "session", "usuario",
    "username", "user_id", "nombrecliente", "razonsocial",
)
SAFE_KEY_PARTS = (
    "ticker", "simbolo", "especie", "descripcion", "instrumento", "moneda", "currency",
    "plazo", "venc", "fecha", "interes", "coupon", "cupon", "amort", "tir", "duration",
    "paridad", "residual", "tecnico", "technical", "isin", "lamina", "nominal", "emisor",
    "legislacion", "pagosporanio", "itemid", "instrumentoid", "cantidaddecimales",
    "cantidaddecimalesprecio", "multiplo", "minimo",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_host_path(url: str) -> str:
    u = urlsplit(str(url))
    return f"{u.netloc}{u.path}"


def key_ok(key: object) -> bool:
    s = str(key).lower().replace("-", "_")
    if any(x in s for x in DROP_KEY_PARTS):
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
        return [x for x in (safe_value(x, depth + 1) for x in v[:500]) if x not in (None, "", [], {})][:500]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)[:300]


def all_keys(value, depth=0):
    if depth > 5:
        return set()
    out = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if not any(x in str(k).lower() for x in DROP_KEY_PARTS):
                out.add(str(k))
            if isinstance(v, (dict, list)):
                out |= all_keys(v, depth + 1)
    elif isinstance(value, list):
        for v in value[:20]:
            out |= all_keys(v, depth + 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--chrome", default=os.getenv("POROTA_CHROME_EXECUTABLE", "/usr/bin/google-chrome-stable"))
    args = ap.parse_args()

    out = {
        "schema": "POROTA_RC6_PPI_AL30_TECHNICAL_PROBE_V1",
        "generated_at": now_iso(),
        "auth_status": "UNKNOWN",
        "route_reached": False,
        "input_candidates": [],
        "selected_input": None,
        "al30_typed": False,
        "al30_option_clicked": False,
        "keyboard_selection_used": False,
        "technical_responses": [],
        "blocked_nonread": 0,
        "quantity_filled": False,
        "price_filled": False,
        "order_control_clicked": False,
        "query_strings_logged": False,
        "request_headers_logged": False,
        "raw_response_bodies_logged": False,
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
                if req.method.upper() != "GET" or u.netloc != "api.portfoliopersonal.com" or response.status >= 400:
                    return
                ct = (response.headers.get("content-type") or "").lower()
                if "json" not in ct:
                    return
                data = response.json()
                keys = sorted(all_keys(data))
                is_named = "datostecnicos" in u.path.lower() or "technical" in u.path.lower()
                is_shape = any(TECH_KEY_HINT.search(k) for k in keys)
                if not (is_named or is_shape):
                    return
                hp = clean_host_path(response.url)
                if hp in seen:
                    return
                rec = {"host_path": hp, "http_status": int(response.status), "keys": keys[:120]}
                if "datostecnicos" in u.path.lower():
                    rec["kind"] = "DatosTecnicos"
                    rec["normalized"] = bond_technical(data)
                else:
                    rec["kind"] = "TECHNICAL_EQUIVALENT_CANDIDATE"
                    rec["sanitized"] = safe_value(data)
                out["technical_responses"].append(rec)
                seen.add(hp)
            except Exception:
                pass

        ctx.route("**/*", guard)
        page.on("response", on_response)
        page.goto(TRADING + "/Operar/Bonos", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        u = urlsplit(page.url)
        out["route_reached"] = u.netloc == "trading.portfoliopersonal.com" and "login" not in u.path.lower() and "logout" not in u.path.lower()
        out["auth_status"] = "AUTHENTICATED_TRUSTED_DEVICE" if out["route_reached"] else "BLOCKED_AUTH_SESSION_EXPIRED"

        if out["route_reached"]:
            inputs = page.locator("input")
            preferred = []
            fallback = []
            for i in range(min(inputs.count(), 40)):
                node = inputs.nth(i)
                try:
                    if not node.is_visible():
                        continue
                    meta = {}
                    for attr in ("id", "name", "placeholder", "aria-label", "type", "autocomplete", "role"):
                        val = node.get_attribute(attr)
                        if val:
                            meta[attr] = str(val)[:160]
                    joined = " ".join(meta.values())
                    safe_meta = {k: v for k, v in meta.items() if k != "value"}
                    out["input_candidates"].append({"index": i, **safe_meta})
                    if INPUT_BAD.search(joined):
                        continue
                    typ = (meta.get("type") or "text").lower()
                    if typ not in ("text", "search", "", "email"):
                        continue
                    if INPUT_GOOD.search(joined):
                        preferred.append((i, node, safe_meta))
                    else:
                        fallback.append((i, node, safe_meta))
                except Exception:
                    pass

            chosen = preferred[0] if preferred else (fallback[0] if len(fallback) == 1 else None)
            if chosen:
                idx, node, meta = chosen
                out["selected_input"] = {"index": idx, **meta}
                node.fill("AL30", timeout=5000)
                out["al30_typed"] = True
                page.wait_for_timeout(1800)

                selectors = [
                    "[role='option']",
                    ".mat-option",
                    "mat-option",
                    ".dropdown-item",
                    ".autocomplete-item",
                    "li",
                ]
                clicked = False
                for sel in selectors:
                    if clicked:
                        break
                    try:
                        opts = page.locator(sel)
                        for j in range(min(opts.count(), 80)):
                            opt = opts.nth(j)
                            if not opt.is_visible():
                                continue
                            txt = " ".join((opt.inner_text(timeout=1000) or "").split())[:180]
                            if re.search(r"(^|\b)AL30(\b|$)", txt, re.I):
                                opt.click(timeout=3000)
                                out["al30_option_clicked"] = True
                                clicked = True
                                break
                    except Exception:
                        pass
                if not clicked:
                    try:
                        node.press("ArrowDown")
                        node.press("Enter")
                        out["keyboard_selection_used"] = True
                    except Exception:
                        pass
                page.wait_for_timeout(5000)

        ctx.close()

    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(target, 0o600)

    dt = [x for x in out["technical_responses"] if x.get("kind") == "DatosTecnicos"]
    normalized = [x.get("normalized") or {} for x in dt]
    print(json.dumps({
        "state": out["auth_status"],
        "route_reached": out["route_reached"],
        "input_candidate_count": len(out["input_candidates"]),
        "selected_input": out["selected_input"],
        "al30_typed": out["al30_typed"],
        "al30_option_clicked": out["al30_option_clicked"],
        "keyboard_selection_used": out["keyboard_selection_used"],
        "technical_response_count": len(out["technical_responses"]),
        "technical_host_paths": [x.get("host_path") for x in out["technical_responses"]],
        "datos_tecnicos_explicit": bool(dt),
        "datos_tecnicos_fieldsets": [sorted(x.keys()) for x in normalized if isinstance(x, dict)],
        "blocked_nonread": out["blocked_nonread"],
        "quantity_filled": False,
        "price_filled": False,
        "order_control_clicked": False,
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
