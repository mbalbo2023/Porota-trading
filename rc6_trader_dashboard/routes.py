"""Authenticated, read-only canonical and legacy surfaces.

Installed last so canonical pages never run an expensive legacy renderer and
discard it. The existing bearer/cookie authorization remains authoritative.
"""
from time import perf_counter
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from . import SCHEMA
from .navigation import CANONICAL_PATHS, LEGACY, resolve
from .projection import Projection, Store
from .shell import render as shell
from . import views_home, views_live, views_trading, views_universe, views_instruments, views_risk, views_analytics, views_system

VIEWS = {"inicio": views_home, "en-vivo": views_live, "trading": views_trading,
         "universo": views_universe, "instrumentos": views_instruments,
         "riesgo": views_risk, "analitica": views_analytics, "sistema": views_system}
FILTERS = {"q", "family", "market", "currency", "settlement", "strategy", "state", "identity", "offset", "funnel_offset", "channel", "session", "cohort", "lab", "price_basis", "adjustment_basis"}


def build_page(path, params, database_path, *, now=None):
    destination, tab = resolve(path, params)
    if destination is None or tab is None:
        raise HTTPException(404, "Subview no disponible")
    filters = {k: str(v)[:(512 if k == "identity" else 80)] for k, v in params.items() if k in FILTERS}
    start = perf_counter()
    with Store(database_path, now=now, shadow_filters=filters) as store:
        projection = Projection(store, filters)
        content = VIEWS[destination.key].render(projection, destination, tab)
        if path in {"/observacion", "/testing"}:
            content = "<p class='source-line'>Panel de simulación productiva · compatibilidad read-only.</p>" + content
        if path == "/trading/cauciones":
            content = "<p class='source-line'>Trading — Cauciones · Historial PAPER y readiness actual se muestran separados.</p>" + content
        document = shell(destination, tab, projection, content)
        count = store.query_count
    if "SOURCE_SNAPSHOT_REJECTED" in store.errors:
        from .components import notice
        store.schema.clear()
        projection = Projection(store, filters)
        projection.shadow = {"state": "NO_VERIFICADO", "reason": "SOURCE_SNAPSHOT_REJECTED", "report": {}}
        document = shell(destination, tab, projection, notice("NO_VERIFICADO · Corte de lectura no disponible dentro del presupuesto."))
    return document, {"X-Porota-Projection": SCHEMA, "X-Porota-Read-Queries": str(count),
                      "Server-Timing": f"projection;dur={(perf_counter() - start) * 1000:.2f}"}


def install(app, check_auth, database_path):
    if getattr(app.state, "trader_terminal_installed", False):
        return
    app.state.trader_terminal_installed = True
    import ay_dashboard_auth as auth

    # Inventory includes every canonical subview even though the middleware
    # handles the request before legacy route handlers/middlewares execute.
    existing = {route.path for route in app.routes}
    def endpoint(request: Request):
        raise HTTPException(503, "Terminal dispatcher unavailable")
    for path in sorted(CANONICAL_PATHS.keys() | LEGACY.keys() | {"/api/trader/logs/download"}):
        if path not in existing:
            app.add_api_route(path, endpoint, methods=["GET"], name="trader_terminal_" + path.replace("/", "_"))

    @app.middleware("http")
    async def trader_terminal(request, call_next):
        path = request.url.path
        destination, tab = resolve(path, request.query_params)
        download = path == "/api/trader/logs/download"
        if request.method != "GET" or (destination is None and not download):
            return await call_next(request)
        base_headers = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache", "Expires": "0",
                        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
        token = request.query_params.get("token", "")
        cookie = request.cookies.get("porota_dashboard_session")
        if token and auth.token_valido(token):
            session = auth.crear_sesion_desde_token(token, origen=request.client.host if request.client else "local")
            values = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            response = RedirectResponse(path + ("?" + urlencode(values) if values else ""), status_code=303, headers=base_headers)
            response.set_cookie("porota_dashboard_session", session, max_age=auth.COOKIE_MAX_AGE_SECONDS,
                                httponly=True, secure=auth.ENTORNO == "PRODUCTION", samesite="lax", path="/")
            return response
        try:
            check_auth(token, request.headers.get("authorization"), cookie)
            if not download and tab is None:
                raise HTTPException(404, "Subview no disponible")
            if download:
                def log_body():
                    from .datasets import logs
                    with Store(database_path()) as store:
                        page = logs(Projection(store))
                        return "\n".join(row["line"] for row in page.rows) or "NO_VERIFICADO"
                content = await run_in_threadpool(log_body)
                response = PlainTextResponse(content + "\n", headers={**base_headers, "Content-Disposition": 'attachment; filename="porota-sanitized-log-tail.txt"'})
            else:
                content, headers = await run_in_threadpool(build_page, path, dict(request.query_params), database_path())
                response = HTMLResponse(content, headers={**base_headers, **headers})
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=base_headers)
        if auth.sesion_valida(cookie) and auth.SESION_SIN_VENCIMIENTO:
            response.set_cookie("porota_dashboard_session", cookie, max_age=auth.COOKIE_MAX_AGE_SECONDS,
                                httponly=True, secure=auth.ENTORNO == "PRODUCTION", samesite="lax", path="/")
        return response
