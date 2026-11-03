"""Extensiones seguras del dashboard v16.3.

Se instala sobre el dashboard existente para conservar su autenticación y sus
rutas. No realiza llamadas externas: muestra solamente estado persistido.
"""

import html
import io
import json
import os
import re
import zipfile
from typing import Optional

from fastapi import Header, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

import aa_env_guard as env_guard
import bb_runtime_status as runtime_status
from v_config_metadata import CONFIG_METADATA
from _version import VERSION


LOG_DIR = os.getenv("LOG_DIR", "data/logs")
_installed = False

MODERN_CSS = """
<style id="porota-theme-v1633">
:root{--bg:#f4f7fb;--surface:#fff;--surface2:#eef3f9;--text:#172033;--muted:#5d6b82;
--line:#dce4ef;--brand:#2457d6;--brand2:#173b99;--ok:#177447;--warn:#966400;--bad:#b52b35;
--shadow:0 8px 24px rgba(23,32,51,.08);color-scheme:light}
*{box-sizing:border-box}html{font-size:16px}body{margin:0!important;padding:0!important;max-width:none!important;
font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif!important;background:var(--bg)!important;
color:var(--text)!important;line-height:1.5}#porota-page-shell{width:min(1180px,calc(100% - 28px));margin:22px auto 48px}
#porota-top-nav{position:sticky;top:0;z-index:9999;display:flex;gap:6px;align-items:center;overflow-x:auto;
padding:10px max(14px,calc((100vw - 1180px)/2));background:rgba(255,255,255,.96);border-bottom:1px solid var(--line);
box-shadow:0 2px 12px rgba(23,32,51,.07);backdrop-filter:blur(10px)}#porota-top-nav a{display:inline-flex;
align-items:center;min-height:44px;padding:8px 12px;border-radius:10px;color:var(--brand2);font-weight:650;text-decoration:none;
white-space:nowrap}#porota-top-nav a:hover{background:var(--surface2)}a{color:var(--brand)}a:focus-visible,button:focus-visible,
input:focus-visible{outline:3px solid #70a2ff;outline-offset:2px}.cont{max-width:none!important;margin:0!important;padding:0!important}
h1{font-size:clamp(1.55rem,3vw,2.15rem)!important;letter-spacing:-.025em;margin:0 0 6px!important}h2{font-size:1.08rem!important;
border:0!important;color:var(--text)!important}.sub,.chico{color:var(--muted)!important}.tarjeta,section,.card{background:var(--surface);
border:1px solid var(--line)!important;border-radius:16px!important;padding:18px!important;box-shadow:var(--shadow);margin-bottom:16px}
.banner{border-radius:14px!important;padding:14px 16px!important}.b-verde{background:#eaf8f0!important;border-color:#a9ddc0!important}
.b-amarillo{background:#fff7df!important;border-color:#ead18a!important}.b-rojo{background:#fff0f1!important;border-color:#e7a8ad!important}
table{display:block;width:100%!important;overflow-x:auto;border-collapse:separate!important;border-spacing:0;font-size:.91rem}
th{background:var(--surface2)!important;color:var(--text);font-weight:700}th,td{padding:11px 12px!important;border-bottom:1px solid var(--line)!important;
text-align:left}button,input,select{min-height:44px;border-radius:10px;border:1px solid var(--line);font:inherit}button{padding:9px 15px;cursor:pointer}
code{background:var(--surface2);padding:2px 6px;border-radius:6px} @media(max-width:700px){#porota-page-shell{width:min(100% - 18px,1180px);
margin-top:14px}.tarjeta,section,.card{padding:14px!important}th,td{padding:9px!important;font-size:.84rem}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
"""


def _sanitize(value: str) -> str:
    try:
        from c_ppi_client import obfuscate_secret
        return obfuscate_secret(str(value))
    except Exception:
        return "[contenido omitido porque no pudo sanearse]"


def _configured(var: str):
    manifest = runtime_status.read_config_presence()
    value = manifest.get("configured", {}).get(var)
    if isinstance(value, bool):
        return value
    if str(os.getenv(var, "")).strip():
        return True
    return None


def _safe_presence_text(var: str) -> str:
    present = _configured(var)
    if present is True:
        return "******** (configurada)"
    if present is False:
        return "NO CONFIGURADA"
    return "ESTADO NO DISPONIBLE"


def _top_nav() -> str:
    return (
        "<nav id='porota-top-nav' aria-label='Navegación principal'>"
        "<a href='/'>⌂ Inicio</a><a href='/vivo'>Actividad</a>"
        "<a href='/telegram'>Telegram</a><a href='/salud'>Salud</a>"
        "<a href='/testing'>Testing</a><a href='/historicos'>Históricos</a>"
        "<a href='/sre'>SRE</a><a href='/dashboard/logs'>Logs</a>"
        "<a href='/api/diagnostics/download'>Diagnóstico</a>"
        "<a href='/config'>Configuración</a></nav>"
    )


def _config_page() -> str:
    sections = {}
    for var, section, description, default, sensitive in CONFIG_METADATA:
        sections.setdefault(section, []).append(
            (var, description, default, sensitive)
        )
    parts = []
    for section, rows in sections.items():
        body = []
        for var, description, default, sensitive in rows:
            critical = var in env_guard.CRITICAL_VARS
            if sensitive or critical:
                shown = _safe_presence_text(var)
            else:
                shown = str(default)
            body.append(
                "<tr><td><code>{}</code>{}</td><td>{}</td><td>"
                "<input value='{}' readonly aria-readonly='true' "
                "style='width:95%;padding:6px;background:#f3f4f6'></td></tr>".format(
                    html.escape(var), " ⚠️" if critical else "",
                    html.escape(description), html.escape(shown, quote=True),
                )
            )
        parts.append(
            f"<h2>{html.escape(section)}</h2><table><tr><th>Variable</th>"
            f"<th>Descripción</th><th>Valor por defecto / estado seguro</th></tr>"
            + "".join(body) + "</table>"
        )
    return """<!doctype html><html lang='es'><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Configuración</title><style>body{font-family:sans-serif;max-width:1100px;
    margin:auto;padding:0 12px}table{width:100%;border-collapse:collapse}th,td{
    text-align:left;padding:7px;border-bottom:1px solid #ddd}h2{margin-top:28px}</style>
    </head><body>""" + _top_nav() + """<h1>⚙️ Configuración — sólo lectura</h1>
    <p><b>Vista segura:</b> no modifica <code>.env</code>, no solicita reinicios y
    nunca incluye credenciales ni valores críticos en el HTML.</p>""" + "".join(parts) + \
        "</body></html>"


def _rewrite_html(path: str, content: str) -> str:
    # Retira textos de inventario obsoletos sin afirmar que un derivado es
    # operable antes de que su modelo de riesgo lo habilite.
    content = content.replace(" (NUEVO v14.0)", "")
    content = content.replace("<b>Cómo funciona (v14.0):</b>", "<b>Cómo funciona:</b>")
    content = content.replace(
        "Instrumentos vistos pero no operados (opciones, futuros, cauciones, FCI)",
        "Instrumentos derivados y complementarios observados",
    )
    old = ("El bot los descubre para que tengas visibilidad completa del mercado, pero\n"
           "      NO opera ahí — esos productos necesitan un modelo de riesgo distinto "
           "(apalancamiento, vencimientos) que\n      todavía no está construido en este sistema.")
    content = content.replace(
        old,
        "Se muestran para contexto. Cada clase sólo se habilita si cuenta con permisos, "
        "datos y un modelo de riesgo específico; observarla no autoriza una orden.",
    )
    if path == "/":
        marker = "</p>"
        extra = ("<p><a href='/telegram'>✈️ Telegram</a> · "
                 "<a href='/dashboard/logs'>📄 Logs</a> · "
                 "<a href='/api/diagnostics/download'>📦 Diagnóstico completo</a></p>")
        pos = content.find(marker)
        if pos >= 0 and "/api/diagnostics/download" not in content[:pos + len(marker)]:
            content = content[:pos + len(marker)] + extra + content[pos + len(marker):]
    if path == "/testing":
        content = content.replace(
            "Autorización manual del arranque, para cuando Telegram no responde.",
            "Control manual de emergencia. El arranque normal lo autoriza el calendario BYMA; Telegram no lo bloquea.",
        )
        content = content.replace("Pedir autorización por Telegram", "Autorizar manualmente (emergencia)")
    if "id='porota-top-nav'" not in content and "id='porota-canonical-nav'" not in content:
        match = re.search(r"<body[^>]*>", content, flags=re.IGNORECASE)
        if match:
            content = content[:match.end()] + _top_nav() + content[match.end():]
    if "porota-theme-v1633" not in content:
        content = re.sub(r"</head>", MODERN_CSS + "</head>", content, count=1, flags=re.IGNORECASE)
    if "id='porota-page-shell'" not in content:
        nav_end = content.find("</nav>")
        body_end = content.lower().rfind("</body>")
        if nav_end >= 0 and body_end > nav_end:
            nav_end += len("</nav>")
            content = content[:nav_end] + "<main id='porota-page-shell'>" + content[nav_end:body_end] + "</main>" + content[body_end:]
    return content


def _telegram_page() -> str:
    rows = runtime_status.telegram_activity(150)
    body = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            *(html.escape(str(row.get(key, ""))) for key in
              ("timestamp", "direction", "event_type", "status", "summary"))
        ) for row in rows
    ) or "<tr><td colspan='5'>Sin actividad registrada desde este despliegue.</td></tr>"
    return """<!doctype html><html lang='es'><head><meta charset='utf-8'>
    <meta name='viewport' content='width=device-width,initial-scale=1'>
    <meta http-equiv='refresh' content='10'><title>Telegram</title><style>
    body{font-family:sans-serif;max-width:1100px;margin:auto;padding:0 12px}
    table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px;
    border-bottom:1px solid #ddd}</style></head><body>""" + _top_nav() + \
        f"<h1>✈️ Actividad de Telegram</h1><p>Se refresca cada 10 segundos. " \
        f"No muestra token ni chat ID.</p><table><tr><th>Hora</th><th>Dirección</th>" \
        f"<th>Tipo</th><th>Estado</th><th>Resumen saneado</th></tr>{body}</table></body></html>"


def _diagnostic_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        summary = {
            "generated_at": runtime_status.now_iso(), "version": VERSION,
            "timezone": runtime_status.SERVER_TIMEZONE,
            "ppi_auth": runtime_status.latest_event("PPI_AUTH"),
            "ppi_rest": runtime_status.latest_event("PPI_REST"),
            "stream": runtime_status.latest_event("STREAM"),
            "trading_gate": runtime_status.latest_event("TRADING_GATE"),
            "telegram_recent": runtime_status.telegram_activity(100),
        }
        try:
            import al_historical_ingest as history
            summary["historical"] = history.estado_del_archivo()
        except Exception as exc:
            summary["historical"] = {"error": str(exc)}
        archive.writestr("resumen.json", _sanitize(json.dumps(
            summary, ensure_ascii=False, indent=2, default=str)))
        safe_config = {}
        for var, section, _description, default, sensitive in CONFIG_METADATA:
            safe_config[var] = {
                "section": section, "default": default, "configured": _configured(var),
                "protected": bool(sensitive or var in env_guard.CRITICAL_VARS),
            }
        archive.writestr("configuracion_saneada.json", json.dumps(
            safe_config, ensure_ascii=False, indent=2))
        paths = []
        if os.path.isdir(LOG_DIR):
            paths.extend(os.path.join(LOG_DIR, name) for name in os.listdir(LOG_DIR))
        if os.path.isdir("data"):
            paths.extend(os.path.join("data", name) for name in os.listdir("data")
                         if name.endswith((".json", ".jsonl", ".log", ".txt")))
        used = set()
        for path in paths:
            try:
                if (not os.path.isfile(path) or os.path.getsize(path) > 25 * 1024 * 1024
                        or os.path.abspath(path) in used):
                    continue
                used.add(os.path.abspath(path))
                content = open(path, encoding="utf-8", errors="replace").read()
                archive.writestr("archivos/" + os.path.basename(path), _sanitize(content))
            except Exception:
                continue
    return buffer.getvalue()


def install(app, check_auth) -> None:
    global _installed
    if _installed:
        return
    _installed = True

    @app.middleware("http")
    async def secure_dashboard_extension(request, call_next):
        path = request.url.path
        if path == "/config" and request.method == "POST":
            return PlainTextResponse("Configuración de sólo lectura.", status_code=405)
        response = await call_next(request)
        content_type = response.headers.get("content-type", "")
        if path == "/config" and request.method == "GET" and response.status_code < 400:
            return HTMLResponse(_rewrite_html(path, _config_page()), status_code=response.status_code)
        if path.startswith("/api/logs/download/") and response.status_code < 400:
            body = b"".join([chunk async for chunk in response.body_iterator])
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(_sanitize(body.decode("utf-8", "replace")),
                            status_code=response.status_code, headers=headers,
                            media_type="text/plain")
        if "text/html" in content_type and response.status_code < 400:
            body = b"".join([chunk async for chunk in response.body_iterator])
            rewritten = _rewrite_html(path, body.decode("utf-8", "replace"))
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return HTMLResponse(rewritten, status_code=response.status_code, headers=headers)
        return response

    paths = {getattr(route, "path", "") for route in app.routes}
    if "/telegram" not in paths:
        def telegram(request: Request, token: str = Query(default=""),
                     authorization: Optional[str] = Header(default=None)):
            try:
                check_auth(token, authorization,
                           request.cookies.get("porota_dashboard_session"))
            except TypeError:
                check_auth(token, authorization)
            return HTMLResponse(_telegram_page())
        app.add_api_route("/telegram", telegram, methods=["GET"],
                          response_class=HTMLResponse)
    if "/api/diagnostics/download" not in paths:
        def diagnostics(request: Request, token: str = Query(default=""),
                        authorization: Optional[str] = Header(default=None)):
            try:
                check_auth(token, authorization,
                           request.cookies.get("porota_dashboard_session"))
            except TypeError:
                check_auth(token, authorization)
            stamp = runtime_status.now_local().strftime("%Y%m%d_%H%M%S")
            return Response(_diagnostic_zip(), media_type="application/zip", headers={
                "Content-Disposition": f"attachment; filename=porota_diagnostico_{stamp}.zip"
            })
        app.add_api_route("/api/diagnostics/download", diagnostics, methods=["GET"])
