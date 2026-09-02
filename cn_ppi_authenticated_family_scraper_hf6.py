"""Authenticated PPI web/API evidence collector for HF6 PRODUCTION_PAPER.

Safety contract:
- exactly one credential login POST at most;
- no order/account mutation endpoints are ever called;
- tokens, refresh tokens, passwords, cookies and raw HTML are never persisted;
- all family evidence is observational and NEVER promotes an instrument to
  READY_PAPER by itself;
- a 2FA challenge is recorded and the collector stops authenticated work
  fail-closed instead of attempting to bypass it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

SCHEMA = "porota-ppi-authenticated-family-evidence-v1"
API_BASE = "https://api.portfoliopersonal.com"
CUENTA_LOGIN = "https://cuenta.portfoliopersonal.com/login"
TRADING_BASE = "https://trading.portfoliopersonal.com"
AUTHORIZED_CLIENT = "191206"
CLIENT_KEY = "pp123456"

FAMILIES = (
    "CAUCIONES", "BONOS", "LETRAS", "ON", "OBLIGACIONES_NEGOCIABLES",
    "OPCIONES", "FUTUROS", "ETF", "FCI", "FCI_EXTERIOR",
    "ACCIONES_USA", "LEBAC", "NOBAC", "LICITACIONES", "INDICES",
)

FAMILY_TERMS = {
    "CAUCIONES": ("caucion", "cauciones"),
    "BONOS": ("bono", "bonos", "renta fija"),
    "LETRAS": ("letra", "letras"),
    "ON": ("obligacion", "obligaciones", "on"),
    "OBLIGACIONES_NEGOCIABLES": ("obligaciones negociables", "obligacion negociable"),
    "OPCIONES": ("opcion", "opciones", "call", "put"),
    "FUTUROS": ("futuro", "futuros", "rofex", "matba"),
    "ETF": ("etf", "etfs"),
    "FCI": ("fci", "fondo comun", "fondos comunes"),
    "FCI_EXTERIOR": ("fci exterior", "fondo exterior"),
    "ACCIONES_USA": ("acciones usa", "acciones-us", "nyse", "nasdaq"),
    "LEBAC": ("lebac",),
    "NOBAC": ("nobac",),
    "LICITACIONES": ("licitacion", "licitaciones"),
    "INDICES": ("indice", "indices"),
}

INTERESTING_HEADERS = {
    "plazo", "cant dias", "cantidad dias", "ultimo operado", "variacion",
    "monto tom", "tna tom", "tna col", "monto col", "volumen", "fecha vto",
    "tasa aper", "ticker", "especie", "moneda", "mercado", "vencimiento",
    "strike", "ejercicio", "call", "put", "subyacente", "multiplicador",
    "margen", "margen inicial", "margen mantenimiento", "nominal", "lamina",
    "cantidad", "precio", "tir", "duration", "paridad", "issuer", "emisor",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _safe_url(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}{p.path}"


def _clean_text(value: object, limit: int = 500) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _secret_free_error(exc: Exception) -> str:
    return f"{type(exc).__name__}:{_clean_text(str(exc), 240)}"


def _decode_credentials(raw: str) -> tuple[str, str]:
    obj = json.loads(raw)
    user = str(obj.get("username") or obj.get("usuario") or "").strip()
    password = str(obj.get("password") or obj.get("clave") or "")
    if not user or not password:
        raise ValueError("CREDENTIAL_FIELDS_MISSING")
    return user, password


def _module(text: str, module_id: int) -> str:
    start = re.search(rf"(?<!\d){module_id}:function\(", text)
    if not start:
        return ""
    tail = text[start.start():]
    nxt = re.search(r"\},\d+:function\(", tail[20:])
    return tail[: (20 + nxt.start() + 1) if nxt else 30000]


def _fingerprint_payload(session: requests.Session) -> tuple[str, dict]:
    """Build one stable opaque fp value without logging host/user secrets.

    V10 proved the official frontend uses header/cookie name `fp` and creates
    it from {key,browser,os}. The backend treats it as an opaque device token.
    We inspect the current utility module to choose the closest encoding shape
    but never execute remote JavaScript.
    """
    meta = {"source": "PPI_CURRENT_FRONTEND", "algorithm": "BASE64_JSON_FALLBACK"}
    browser = "Chrome"
    operating_system = "Linux"
    seed = hashlib.sha256((os.uname().nodename + "|porota-hf6-readonly").encode()).hexdigest()
    obj = {"key": seed, "br": browser, "os": operating_system}
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    try:
        page = session.get(CUENTA_LOGIN, timeout=(8, 20))
        soup = BeautifulSoup(page.text, "html.parser")
        app_url = ""
        for tag in soup.find_all("script"):
            src = tag.get("src") or ""
            if "/pages/_app-" in src:
                app_url = urljoin(page.url, src)
                break
        if app_url:
            js = session.get(app_url, timeout=(8, 25)).text
            util = _module(js, 45561)
            low = util.lower()
            meta["utility_module_found"] = bool(util)
            meta["utility_has_btoa"] = "btoa" in low
            meta["utility_has_json_stringify"] = "json.stringify" in low
            meta["utility_has_rot94"] = "%94" in low or "% 94" in low
            if meta["utility_has_rot94"]:
                rotated = []
                for ch in body:
                    code = ord(ch)
                    if 33 <= code <= 126:
                        rotated.append(chr(33 + ((code + 14) % 94)))
                    else:
                        rotated.append(ch)
                value = base64.b64encode("".join(rotated).encode()).decode().rstrip("=")
                meta["algorithm"] = "ROT94_BASE64_JSON"
                return value, meta
            if meta["utility_has_btoa"] and meta["utility_has_json_stringify"]:
                value = base64.b64encode(body.encode()).decode().rstrip("=")
                meta["algorithm"] = "BASE64_JSON"
                return value, meta
    except Exception as exc:
        meta["frontend_probe"] = _secret_free_error(exc)
    return base64.b64encode(body.encode()).decode().rstrip("="), meta


def _extract_payload(response: requests.Response):
    try:
        data = response.json()
    except Exception:
        return None, "NON_JSON_RESPONSE"
    if isinstance(data, dict) and "payload" in data:
        return data.get("payload"), _clean_text(data.get("message") or data.get("error") or "")
    return data, _clean_text(data.get("message") if isinstance(data, dict) else "")


def _token_fields(payload: object):
    if not isinstance(payload, dict):
        return None
    token = payload.get("token")
    if not isinstance(token, dict):
        return None
    ttype = str(token.get("tokenType") or "Bearer")
    access = str(token.get("accessToken") or "")
    refresh = str(token.get("refreshToken") or "")
    if not access:
        return None
    return ttype, access, refresh


def _find_account_id(payload: object) -> tuple[str, int]:
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        for key in ("cuentas", "accounts", "items", "results"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    count = len(rows)
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("id", "cuentaId", "cuentaID", "accountId", "accountID"):
            val = row.get(key)
            if val is not None and str(val).strip():
                return str(val), count
    return "", count


def _route_family(route: str) -> list[str]:
    low = route.lower()
    hits = []
    for family, terms in FAMILY_TERMS.items():
        if any(term in low for term in terms):
            hits.append(family)
    return hits


def _manifest_routes(session: requests.Session) -> tuple[list[str], dict]:
    meta = {}
    try:
        r = session.get(TRADING_BASE + "/", allow_redirects=True, timeout=(8, 20))
        meta["home_http"] = r.status_code
        meta["home_final"] = _safe_url(r.url)
        soup = BeautifulSoup(r.text, "html.parser")
        manifest = ""
        for tag in soup.find_all("script"):
            src = tag.get("src") or ""
            if "_buildManifest.js" in src:
                manifest = urljoin(r.url, src)
                break
        if not manifest:
            return [], meta
        js = session.get(manifest, timeout=(8, 20)).text
        meta["manifest"] = _safe_url(manifest)
        routes = sorted(set(re.findall(r'"(/[^"\\]{1,160})"', js)))
        routes = [x for x in routes if x.startswith("/Cotizaciones/") or x in {"/Cotizaciones", "/Research/Calendario"}]
        return routes, meta
    except Exception as exc:
        meta["manifest_error"] = _secret_free_error(exc)
        return [], meta


def _table_evidence(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "", 250)
    tables = []
    for table in soup.find_all("table")[:20]:
        headers = [_clean_text(x.get_text(" ", strip=True), 120) for x in table.find_all("th")]
        rows = []
        for tr in table.find_all("tr")[:251]:
            cells = [_clean_text(x.get_text(" ", strip=True), 160) for x in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        tables.append({"headers": headers, "rows": rows[:250], "row_count_sampled": len(rows)})
    visible = " ".join(_clean_text(x.get_text(" ", strip=True), 1000) for x in soup.find_all(["h1", "h2", "h3", "label", "th"]))
    detected = sorted(h for h in INTERESTING_HEADERS if h in visible.lower())
    return {"title": title, "table_count": len(tables), "tables": tables, "detected_fields": detected}


def _public_fallback_routes() -> dict[str, list[str]]:
    return {
        "CAUCIONES": ["/Cotizaciones/Cauciones"],
        "BONOS": ["/Cotizaciones/Bonos"],
        "LETRAS": ["/Cotizaciones/Letras"],
        "ON": ["/Cotizaciones/ObligacionesNegociables", "/Cotizaciones/ON"],
        "OPCIONES": ["/Cotizaciones/Opciones"],
        "FUTUROS": ["/Cotizaciones/Futuros"],
        "ETF": ["/Cotizaciones/ETFs", "/Cotizaciones/ETF"],
        "FCI": ["/Cotizaciones/FCIs", "/Cotizaciones/FCI"],
        "INDICES": ["/Cotizaciones/Indices"],
        "LICITACIONES": ["/Research/Calendario"],
    }


def collect(credentials_json: str) -> dict:
    user, password = _decode_credentials(credentials_json)
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
        "Accept-Language": "es-AR,es;q=0.9,en;q=0.7",
        "AuthorizedClient": AUTHORIZED_CLIENT,
        "ClientKey": CLIENT_KEY,
    })
    fp, fp_meta = _fingerprint_payload(s)
    s.headers["fp"] = fp

    result = {
        "schema": SCHEMA,
        "observed_at": now_iso(),
        "safety": {
            "credential_login_posts": 0,
            "order_posts": 0,
            "mutation_requests": 0,
            "tokens_persisted": False,
            "cookies_persisted": False,
            "raw_html_persisted": False,
            "password_persisted": False,
        },
        "fingerprint": fp_meta,
        "auth": {},
        "families": {f: {"status": "NOT_OBSERVED", "sources": []} for f in FAMILIES},
        "route_discovery": {},
    }

    # Exactly one credential-bearing POST.
    try:
        result["safety"]["credential_login_posts"] = 1
        lr = s.post(
            API_BASE + "/api/Seguridad/Auth/Login",
            json={"usuario": user, "clave": password},
            headers={"Origin": "https://cuenta.portfoliopersonal.com", "Referer": CUENTA_LOGIN},
            timeout=(8, 25),
        )
        payload, message = _extract_payload(lr)
        result["auth"]["http"] = lr.status_code
        result["auth"]["message"] = message[:180]
        if isinstance(payload, dict) and payload.get("twoFAInfo"):
            info = payload.get("twoFAInfo") or {}
            result["auth"].update({
                "status": "TWO_FACTOR_REQUIRED_FAIL_CLOSED",
                "two_factor_type": _clean_text(info.get("twoFactType") or info.get("type") or "", 80),
                "challenge_token_present": bool(info.get("token")),
            })
            # No attempt to bypass or guess 2FA.
            token_fields = None
        else:
            token_fields = _token_fields(payload)
            result["auth"]["status"] = "AUTHENTICATED" if token_fields else "LOGIN_NOT_AUTHENTICATED"
    except Exception as exc:
        token_fields = None
        result["auth"] = {"status": "LOGIN_ERROR", "error": _secret_free_error(exc)}

    # Public/read-only route discovery happens regardless of auth outcome.
    routes, route_meta = _manifest_routes(s)
    result["route_discovery"] = route_meta
    route_set = set(routes)
    for family, candidates in _public_fallback_routes().items():
        route_set.update(candidates)

    if token_fields:
        token_type, access_token, refresh_token = token_fields
        auth_header = f"{token_type} {access_token}"
        s.headers["Authorization"] = auth_header
        # Mirror only in-memory browser cookies. Never serialize cookie values.
        for name, value in (("tk_ob", auth_header), ("rtk_ob", refresh_token), ("fp", fp)):
            if value:
                s.cookies.set(name, value, domain=".portfoliopersonal.com", path="/")

        # Read account list only to establish the same account context the UI uses.
        try:
            ar = s.get(API_BASE + "/cuentas", timeout=(8, 20))
            ap, _ = _extract_payload(ar)
            account_id, account_count = _find_account_id(ap)
            result["auth"]["account_list_http"] = ar.status_code
            result["auth"]["account_count"] = account_count
            result["auth"]["account_context_set"] = bool(account_id)
            if account_id:
                s.cookies.set("cuentaId", account_id, domain=".portfoliopersonal.com", path="/")
        except Exception as exc:
            result["auth"]["account_list_error"] = _secret_free_error(exc)

    # Read-only HTML/Next route scraping for every known/missing family.
    for route in sorted(route_set):
        families = _route_family(route)
        if not families:
            # Keep generic Cotizaciones routes discoverable but do not assign them.
            continue
        url = urljoin(TRADING_BASE, route)
        try:
            rr = s.get(url, allow_redirects=True, timeout=(8, 25))
            ev = _table_evidence(rr.text)
            ev.update({
                "route": route,
                "http": rr.status_code,
                "final_url": _safe_url(rr.url),
                "authenticated_target_reached": "/logOut" not in rr.url,
                "bytes": len(rr.content),
            })
            for family in families:
                result["families"][family]["sources"].append(ev)
        except Exception as exc:
            ev = {"route": route, "error": _secret_free_error(exc)}
            for family in families:
                result["families"][family]["sources"].append(ev)

    # Family-level decision: evidence only; NEVER automatic operability promotion.
    for family, info in result["families"].items():
        sources = info["sources"]
        if any(x.get("authenticated_target_reached") and (x.get("table_count", 0) > 0 or x.get("detected_fields")) for x in sources):
            info["status"] = "AUTHENTICATED_WEB_EVIDENCE_COLLECTED_REVIEW_REQUIRED"
        elif sources:
            info["status"] = "ROUTE_OBSERVED_NO_CONTRACT_EVIDENCE"
        else:
            info["status"] = "NO_ROUTE_OR_EVIDENCE_FOUND"
        info["automatic_ready_paper"] = False

    return result


def main() -> int:
    raw = os.sys.stdin.read()
    try:
        result = collect(raw)
    except Exception as exc:
        result = {
            "schema": SCHEMA,
            "observed_at": now_iso(),
            "auth": {"status": "COLLECTOR_ABORTED", "error": _secret_free_error(exc)},
            "safety": {"credential_login_posts": 0, "order_posts": 0, "mutation_requests": 0},
            "families": {},
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
