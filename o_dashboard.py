"""
o_dashboard.py — Dashboard web de solo lectura (v8.0)

INCORPORA el pedido explícito ("quiero un módulo que sea una página web
[...] dashboard") y la recomendación técnica de las auditorías 7.1/7.3/7.4
de usar FastAPI + HTML — la acepto tal cual porque es un stack liviano,
sin dependencias pesadas, apropiado para un servidor de bajo consumo.

QUÉ SÍ SE IMPLEMENTÓ: un único proceso FastAPI, de SOLO LECTURA (nunca
puede mandar una orden ni tocar la operatoria — ver advertencia de la
auditoría 7.1, "el dashboard jamás debe ejecutar órdenes"), que lee
directo de trading_system.db y arma un resumen cronológico día por día:
noticias del día, señales (aprobadas/rechazadas y por qué), operaciones
cerradas con su lección, comparación contra el día anterior, estado del
kill switch, y las recomendaciones de mejora que genera i_auto_tuner.py.

QUÉ NO SE IMPLEMENTÓ (y por qué): las auditorías proponen migrar todo el
almacenamiento a un esquema con SQLAlchemy/ORM y contenerizar todo con
Docker. Se dejó afuera a propósito: para un solo proceso, un solo
servidor y una sola persona usándolo, un ORM es una capa de abstracción
extra sin beneficio real (las consultas ya son SQL parametrizado, así de
auditable como con un ORM), y Docker suma una curva de aprendizaje
(imágenes, registries, volúmenes) desproporcionada para correr un único
proceso en un único Droplet. Si en el futuro el sistema crece a varios
procesos/entornos, ahí sí se justifica.

SEGURIDAD: el dashboard queda expuesto en el puerto DASHBOARD_PORT del
servidor. Como tiene datos de tu cuenta, se protege con un token simple
por query string — no es autenticación de nivel bancario, pero evita que
cualquiera que escanee el puerto vea tus operaciones. Para más seguridad,
ver la nota sobre restringir el firewall en el Documento Maestro.

Se corre como un servicio separado del bot principal (no comparte proceso
con j_main.py) — ver Documento Maestro v8.0 para el servicio systemd.
Requiere: pip install fastapi uvicorn
"""

import hmac
import hashlib
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from fastapi import FastAPI, HTTPException, Query, Request, Header
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, RedirectResponse
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import uvicorn
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")
DASHBOARD_ACCESS_TOKEN = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8000"))
# NUEVO EN v10.5 (segunda revisión) — antes el servidor escuchaba fijo en
# 0.0.0.0 (todas las interfaces de red), quedando alcanzable desde
# internet apenas el firewall dejara pasar el puerto — con el único
# resguardo siendo el token de aplicación (DASHBOARD_ACCESS_TOKEN). Ahora
# el default es 127.0.0.1 (loopback — solo alcanzable desde DENTRO del
# propio servidor, ej. por un túnel SSH o por Caddy corriendo en la misma
# máquina). Exponerlo a la red (0.0.0.0) sigue siendo posible, pero pasa
# a ser una decisión explícita (DASHBOARD_HOST=0.0.0.0 en el .env) en vez
# del comportamiento por default.
# ===========================================================================
# CAMBIADO EN v16.2 — el default pasa a 0.0.0.0, y no es un relajamiento
# ===========================================================================
# Antes el default era 127.0.0.1. Dentro de un contenedor eso significa
# escuchar en el loopback DEL CONTENEDOR: el mapeo de puertos de Docker no
# puede alcanzarlo, así que el panel no abría ni desde el Droplet ni desde
# ningún lado, y el paso "esperar a que el panel responda" del CI fallaba
# siempre. Es decir que el pipeline que se presentaba como semáforo de calidad
# nunca corrió en verde.
#
# La seguridad la da el MAPEO y el proxy, no el bind interno: el
# docker-compose publica "127.0.0.1:PUERTO:PUERTO", así que el puerto solo
# queda accesible desde el propio host y quien lo publique hacia afuera tiene
# que hacerlo a propósito, por el reverse proxy con TLS.
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")
STARTUP_STATE_PATH = os.getenv("STARTUP_STATE_PATH", "data/startup_state.json")

# Fuente única de la versión. El endpoint /health devolvía "15.0" mientras la
# portada del documento decía otra cosa: la inconsistencia de inventario fue
# un hallazgo de la auditoría anterior y se corrige teniendo un solo lugar.
VERSION = "16.2"
# NUEVO EN v10.5 — auditorías 2 y 3, hallazgo "Exposición de token en
# URL": vigencia del token CSRF de /config, en segundos.
CSRF_TOKEN_MAX_AGE_SECONDS = int(os.getenv("CSRF_TOKEN_MAX_AGE_SECONDS", "900"))

app = FastAPI(title="Dashboard — Bot de Trading (solo lectura)")

SESSION_COOKIE_NAME = "porota_dashboard_session"


@app.middleware("http")
async def token_inicial_a_sesion(request: Request, call_next):
    """Consume el token de la primera URL y continúa con cookie HttpOnly.

    Compatibilidad: los scripts pueden seguir usando Authorization: Bearer.
    Para el navegador, un token válido en query se transforma en una sesión
    opaca y se redirige a la misma ruta sin el secreto. Las páginas internas
    ya no necesitan ni propagan parámetros token.
    """
    import ay_dashboard_auth as auth

    token = request.query_params.get("token", "")
    if token and auth.token_valido(token):
        sesion = auth.crear_sesion_desde_token(token, origen=request.client.host if request.client else "local")
        parametros = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
        destino = request.url.path
        if parametros:
            destino += "?" + urlencode(parametros)
        respuesta = RedirectResponse(destino, status_code=303)
        respuesta.set_cookie(
            SESSION_COOKIE_NAME,
            sesion,
            max_age=auth.COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            secure=auth.ENTORNO == "PRODUCTION",
            samesite="lax",
            path="/",
        )
        return respuesta

    sesion = request.cookies.get(SESSION_COOKIE_NAME)
    sesion_es_valida = auth.sesion_valida(sesion)
    if sesion_es_valida:
        # Las rutas existentes ya aceptan Bearer. Se inyecta sólo dentro del
        # scope ASGI para reutilizar esa validación sin propagar el secreto al
        # navegador, a los enlaces ni a los logs.
        headers = [(k, v) for k, v in request.scope.get("headers", [])
                   if k.lower() != b"authorization"]
        headers.append((b"authorization", ("Bearer " + auth.TOKEN_BEARER).encode("utf-8")))
        request.scope["headers"] = headers
        # request.cookies pudo materializar y cachear Headers antes de esta
        # inyección. Se invalida el cache para que las dependencias FastAPI
        # lean el scope actualizado.
        if hasattr(request, "_headers"):
            delattr(request, "_headers")

    respuesta = await call_next(request)
    if sesion_es_valida and auth.SESION_SIN_VENCIMIENTO:
        # Chrome limita las cookies persistentes a 400 días. Renovarla en
        # cada uso hace que la sesión de Sandbox no venza mientras se utilice.
        respuesta.set_cookie(
            SESSION_COOKIE_NAME,
            sesion,
            max_age=auth.COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            secure=auth.ENTORNO == "PRODUCTION",
            samesite="lax",
            path="/",
        )
    return respuesta


def _query(sql, params=()):
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []  # la tabla puede no existir todavía si el bot nunca la usó
    finally:
        conn.close()


def _check_auth(token: str, authorization: Optional[str], sesion: Optional[str] = None):
    """
    CORRECCIÓN v10.5 — auditorías 2 y 3, hallazgo "Exposición de Token en
    URL": pasar el token por query string (?token=...) queda registrado en
    los logs de Caddy/Nginx, en el historial del navegador y viaja en el
    encabezado Referer si el dashboard llega a linkear a algo externo.
    Ahora se acepta TAMBIÉN (y se recomienda) el encabezado estándar
    "Authorization: Bearer <token>", que no queda en ninguno de esos
    lugares. El query param se mantiene por compatibilidad hacia atrás
    (para no romper accesos ya guardados como favorito) pero deja de ser
    la única forma — ver Documento Maestro para el ejemplo de curl/cliente
    HTTP con el header.
    """
    # v16.2 — tres caminos: cookie de sesión (persona con navegador), token
    # bearer (scripts y healthchecks) o el query param heredado. Ya NO existe
    # el caso "sin token configurado = sin protección": el panel se niega a
    # arrancar sin credenciales, así que llegado acá siempre hay algo que
    # verificar.
    import ay_dashboard_auth as auth
    if auth.autorizado(cookie=sesion, encabezado=authorization, parametro=token):
        return
    raise HTTPException(
        status_code=401,
        detail="Necesitás iniciar sesión en /login, o mandar el encabezado "
               "'Authorization: Bearer TU-TOKEN'. Evitá ?token=... en la URL: "
               "queda en el historial del navegador y en los logs del proxy.",
    )


def _check_token(token: str):
    # Alias retrocompatible — algunas rutas viejas solo tenían query token.
    _check_auth(token, None)


def _csrf_token() -> str:
    """
    NUEVO EN v10.5 — auditorías 2 y 3, hallazgo "CSRF en POST /config":
    token anti-falsificación con vencimiento, firmado con el propio
    DASHBOARD_ACCESS_TOKEN como clave HMAC. No necesita guardarse en el
    servidor (no hay sesión ni base de datos de por medio) porque
    incluye su propio timestamp firmado — se verifica que la firma sea
    válida Y que no haya vencido en _verify_csrf_token().
    """
    ts = str(int(time.time()))
    sig = hmac.new((DASHBOARD_ACCESS_TOKEN or "sin-token").encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def _verify_csrf_token(value: str) -> bool:
    try:
        ts_str, sig = value.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        return False
    if time.time() - ts > CSRF_TOKEN_MAX_AGE_SECONDS:
        return False
    expected = hmac.new((DASHBOARD_ACCESS_TOKEN or "sin-token").encode(), ts_str.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def _day_summary(day: str) -> dict:
    headlines = _query("SELECT headline FROM daily_headlines WHERE date = ?", (day,))
    signals = _query(
        "SELECT status, reason, COUNT(*) as n FROM signals WHERE date(timestamp) = ? GROUP BY status, reason",
        (day,),
    )
    closed = _query(
        "SELECT ticker, exit_reason, realized_pnl_ars FROM closed_trades WHERE date(closed_at) = ?", (day,)
    )
    outcomes = _query("SELECT * FROM trade_outcomes WHERE date = ?", (day,))
    return {
        "day": day,
        "headlines": [h["headline"] for h in headlines],
        "signals": signals,
        "closed_trades": closed,
        "wins": len([c for c in closed if c["realized_pnl_ars"] and c["realized_pnl_ars"] > 0]),
        "losses": len([c for c in closed if c["realized_pnl_ars"] and c["realized_pnl_ars"] <= 0]),
        "net_pnl": sum(c["realized_pnl_ars"] for c in closed if c["realized_pnl_ars"]) if closed else 0,
        "outcome": outcomes[0] if outcomes else None,
    }


def _render_html(days: list, halts: list, recommendations: list, tuning_history: list,
                  observation_instruments: dict = None) -> str:
    rows = ""
    prev_pnl = None
    for d in days:
        delta_txt = ""
        if prev_pnl is not None:
            delta = d["net_pnl"] - prev_pnl
            delta_txt = f"<span style='color:{'green' if delta >= 0 else 'crimson'}'>({delta:+.2f} vs. día anterior)</span>"
        prev_pnl = d["net_pnl"]

        headlines_html = "".join(f"<li>{h}</li>" for h in d["headlines"][:8]) or "<li><em>sin noticias registradas</em></li>"
        signals_html = "".join(
            f"<li>{s['status']} — {s['reason'] or ''} ({s['n']})</li>" for s in d["signals"]
        ) or "<li><em>sin señales</em></li>"
        trades_html = "".join(
            f"<li>{c['ticker']}: {c['exit_reason']} → {c['realized_pnl_ars']:+.2f} ARS</li>" for c in d["closed_trades"]
        ) or "<li><em>ninguna operación cerrada</em></li>"

        rows += f"""
        <details style="margin-bottom:12px;border:1px solid #ddd;border-radius:8px;padding:10px;">
          <summary style="cursor:pointer;font-weight:bold;">{d['day']} — P&L cerrado: {d['net_pnl']:+.2f} ARS
          ({d['wins']} ganadas / {d['losses']} perdidas) {delta_txt}</summary>
          <div style="display:flex;gap:24px;margin-top:10px;flex-wrap:wrap;">
            <div><b>Noticias del día</b><ul>{headlines_html}</ul></div>
            <div><b>Señales</b><ul>{signals_html}</ul></div>
            <div><b>Operaciones cerradas</b><ul>{trades_html}</ul></div>
          </div>
        </details>
        """

    halts_html = "".join(
        f"<li>{h['triggered_at']}: {h['reason']}</li>" for h in halts
    ) or "<li>Sin cortes de kill switch registrados.</li>"

    reco_html = "".join(
        f"<details style='margin-bottom:8px;'><summary>{r['date']}</summary><pre style='white-space:pre-wrap'>{r['texto']}</pre></details>"
        for r in recommendations
    ) or "<p><em>Todavía no hay recomendaciones generadas (se generan en el auto-tuning mensual).</em></p>"

    tuning_html = "".join(
        f"<li>{t['date']} — muestra: {t['sample_size']} — {t['notas']}</li>" for t in tuning_history
    ) or "<li>Sin ajustes mensuales todavía.</li>"

    obs = observation_instruments or {}
    obs_data = {}
    if obs.get("data_json"):
        import json as _json
        try:
            obs_data = _json.loads(obs["data_json"])
        except Exception:
            obs_data = {}
    if obs_data:
        observation_html = "<ul>" + "".join(
            f"<li><b>{cls}</b>: {len(tickers)} encontrados — {', '.join(tickers[:10])}"
            f"{' ...' if len(tickers) > 10 else ''}</li>"
            for cls, tickers in obs_data.items()
        ) + f"</ul><p style='font-size:0.85em;color:#999;'>Última actualización: {obs.get('date', 'nunca')}</p>"
    else:
        observation_html = "<p><em>Todavía no se corrió el descubrimiento informativo (se actualiza a la apertura de cada rueda).</em></p>"

    return f"""
    <html>
    <head>
      <title>Dashboard — Bot de Trading</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 20px auto; padding: 0 12px; color: #222; }}
        h1 {{ font-size: 1.4em; }}
        h2 {{ font-size: 1.1em; margin-top: 28px; border-bottom: 1px solid #eee; padding-bottom: 4px; }}
      </style>
    </head>
    <body>
      <h1>📊 Bot de Trading — Dashboard (solo lectura)</h1>
      <p><em>Este panel nunca puede ejecutar órdenes — es de consulta únicamente.</em></p>
      <p><a href="/vivo">📡 Actividad en vivo</a> ·
      <a href="/testing">🧪 Testing</a> ·
      <a href="/salud">🚦 Salud de APIs</a> ·
      <a href="/historicos">📚 Datos históricos</a> ·
      <a href="/aprendizaje">🎓 Blog de aprendizaje</a> ·
      <a href="/config">⚙️ Ir al editor de configuración</a> ·
      <a href="/sre">🩺 SRE / Monitoreo</a> ·
      <a href="/infra">🖥️ Infraestructura</a> ·
      <a href="/ai-decisions">🧠 Decisiones de IA</a> ·
      <a href="/api/reports/weekly/download">📄 Descargar informe semanal</a>
      (reemplazá TU-TOKEN por tu token real en la URL).</p>
      <p style="font-size:0.85em;color:#888;">Más seguro: en vez de ?token=... en la URL, mandá el encabezado
      <code>Authorization: Bearer TU-TOKEN</code> (no queda en logs del servidor ni en el historial del navegador).</p>

      <h2>🛑 Kill switch — historial de cortes</h2>
      <ul>{halts_html}</ul>

      <h2>🧠 Recomendaciones de mejora (revisión humana requerida — no se auto-aplican al código)</h2>
      {reco_html}

      <h2>⚙️ Historial de auto-tuning</h2>
      <ul>{tuning_html}</ul>

      <h2>👁️ Instrumentos vistos pero no operados (opciones, futuros, cauciones, FCI)</h2>
      <p style="font-size:0.9em;color:#666;">El bot los descubre para que tengas visibilidad completa del mercado, pero
      NO opera ahí — esos productos necesitan un modelo de riesgo distinto (apalancamiento, vencimientos) que
      todavía no está construido en este sistema.</p>
      {observation_html}

      <h2>📅 Historial cronológico (últimos {len(days)} días)</h2>
      {rows}
    </body>
    </html>
    """


@app.get("/", response_class=HTMLResponse)
def dashboard(token: str = Query(default=""), days: int = Query(default=14, le=60),
              authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    day_list = [(date.today() - timedelta(days=i)).isoformat() for i in range(days)]
    summaries = [_day_summary(d) for d in day_list]
    halts = _query("SELECT * FROM risk_halts ORDER BY triggered_at DESC LIMIT 20")
    recommendations = _query("SELECT * FROM recommendations_log ORDER BY date DESC LIMIT 6")
    tuning_history = _query("SELECT * FROM auto_tune_history ORDER BY date DESC LIMIT 12")
    obs_rows = _query("SELECT * FROM observation_only_instruments ORDER BY date DESC LIMIT 1")
    observation_instruments = obs_rows[0] if obs_rows else None
    return _render_html(summaries, halts, recommendations, tuning_history, observation_instruments)


@app.get("/api/dashboard")
def dashboard_json(token: str = Query(default=""), days: int = Query(default=14, le=60),
                    authorization: Optional[str] = Header(default=None)):
    """Mismo contenido en JSON, por si en algún momento se quiere consumir
    desde otra vista (ej. una app) en vez de la página HTML."""
    _check_auth(token, authorization)
    day_list = [(date.today() - timedelta(days=i)).isoformat() for i in range(days)]
    return {
        "days": [_day_summary(d) for d in day_list],
        "risk_halts": _query("SELECT * FROM risk_halts ORDER BY triggered_at DESC LIMIT 20"),
        "recommendations": _query("SELECT * FROM recommendations_log ORDER BY date DESC LIMIT 6"),
        "tuning_history": _query("SELECT * FROM auto_tune_history ORDER BY date DESC LIMIT 12"),
    }


@app.get("/api/learning-logs")
def learning_logs(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    """
    NUEVO EN v10.0 — pensado para el flujo de trabajo por chat: bajás este
    JSON (o lo abrís en el navegador) y me lo pegás/subís en la
    conversación. Ahí lo revisamos juntos y decidimos qué cambiar — el
    bot nunca genera ni aplica el cambio de código solo (ver
    s_learning_engine.py para la explicación completa)."""
    _check_auth(token, authorization)
    return {
        "diagnosticos": _query("SELECT * FROM learning_diagnostics ORDER BY date DESC LIMIT 10"),
        "recomendaciones_mensuales": _query("SELECT * FROM recommendations_log ORDER BY date DESC LIMIT 6"),
        "historial_ajustes": _query("SELECT * FROM auto_tune_history ORDER BY date DESC LIMIT 12"),
        "cortes_kill_switch": _query("SELECT * FROM risk_halts ORDER BY triggered_at DESC LIMIT 20"),
    }


@app.get("/api/observation-instruments")
def observation_instruments(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    """NUEVO EN v10.4 — instrumentos que el bot descubrió pero NO opera
    (opciones, futuros, cauciones, FCI) — solo para que los veas."""
    _check_auth(token, authorization)
    rows = _query("SELECT * FROM observation_only_instruments ORDER BY date DESC LIMIT 1")
    return rows[0] if rows else {"date": None, "data_json": "{}"}


@app.get("/api/failed-notifications")
def failed_notifications(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    """NUEVO EN v10.5 (segunda revisión) — avisos de Telegram que no se
    pudieron entregar tras 3 reintentos (ver b_notifiers.py). Si esta
    lista tiene contenido reciente, revisá la conectividad del servidor
    hacia api.telegram.org — puede haber operaciones ejecutadas de las
    que todavía no te enteraste."""
    _check_auth(token, authorization)
    return _query("SELECT * FROM failed_notifications ORDER BY failed_at DESC LIMIT 20")


# ============================================================================
# NUEVO EN v13.0 — Health check real (usado por el HEALTHCHECK del
# Dockerfile/entrypoint.py). Sin autenticación a propósito: Docker lo pega
# desde dentro del propio contenedor, no expuesto a internet salvo que
# DASHBOARD_HOST=0.0.0.0 — y aun así, no revela ningún dato de la cuenta.
# ============================================================================
@app.get("/health")
def health():
    """AMPLIADO EN v15.0. Antes devolvía {"status": "ok"} incondicionalmente:
    respondía OK aunque la base estuviera trabada o el kill switch activo, lo
    cual hacía que el healthcheck de Docker y el de CI validaran únicamente
    que el proceso de FastAPI estuviera vivo — no que el bot estuviera sano.

    Ahora responde OK solo si la base contesta. El resto de los subsistemas se
    informa como detalle, SIN tumbar el health: un kill switch activo no es
    una falla de infraestructura (es el sistema funcionando como debe) y no
    tiene que provocar que Docker reinicie el contenedor en bucle.

    REDUCIDO EN v16.2. Este endpoint no está autenticado —tiene que poder
    pegarle el healthcheck de Docker desde dentro del contenedor— y por eso
    ahora devuelve SOLO el estado agregado. Antes exponía si el bot estaba
    operando, si había un corte activo y qué modelo de IA usaba. El comentario
    que lo justificaba asumía que el puerto no estaba expuesto a internet, y
    ese supuesto deja de valer en cuanto alguien publica el panel. No revela
    credenciales, pero es reconocimiento gratuito para cualquiera que
    descubra la dirección.

    El detalle completo se mudó a /api/v16/estado, que sí está autenticado."""
    detalle = {"status": "ok", "version": VERSION}
    try:
        import ac_db
        db = ac_db.healthcheck()
        detalle["db"] = db
        if not db.get("ok"):
            detalle["status"] = "degraded"
    except Exception as e:
        detalle["status"] = "degraded"
        detalle["db"] = {"ok": False, "error": str(e)}
    # Solo el agregado. Ni el estado del kill switch, ni el modelo activo, ni
    # el detalle de la base salen por acá.
    return {"status": detalle["status"], "version": VERSION}


@app.get("/api/v16/estado")
def estado_detallado(token: str = Query(default=""),
                     authorization: Optional[str] = Header(default=None)):
    """El detalle que antes salía por /health, ahora detrás de autenticación."""
    _check_auth(token, authorization)
    detalle = {"status": "ok", "version": VERSION}
    try:
        import ac_db
        db = ac_db.healthcheck()
        detalle["db"] = db
        if not db.get("ok"):
            detalle["status"] = "degraded"
    except Exception as e:
        detalle["status"] = "degraded"
        detalle["db"] = {"ok": False, "error": str(e)}
    for nombre, fn in (
        ("kill_switch", lambda: __import__("ag_kill_switch_supervisor").estado()),
        ("modelo_ia", lambda: __import__("af_model_registry").get_status()),
        ("descubrimiento_modelos", lambda: __import__("at_model_discovery").estado()),
        ("futuros", lambda: __import__("av_rofex_client").estado()),
    ):
        try:
            detalle[nombre] = fn()
        except Exception as e:
            detalle[nombre] = {"error": str(e)}
    return detalle


@app.get("/api/v15/estado")
def estado_v15(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    """NUEVO EN v15.0 — un solo endpoint con el estado de todo lo que se
    agregó en esta versión. Lo consume la sección nueva del panel y sirve
    para diagnosticar desde la tablet sin entrar por SSH."""
    _check_auth(token, authorization)
    resultado = {}
    bloques = {
        "kill_switch": lambda: __import__("ag_kill_switch_supervisor").estado(),
        "modelo_ia": lambda: __import__("af_model_registry").get_status(),
        "historial_modelos": lambda: __import__("af_model_registry").historial(10),
        "macro": lambda: __import__("ad_macro_history").get_macro_context(),
        "macro_fuentes": lambda: __import__("ad_macro_history").estado_fuentes(),
        "api_ppi_cambios": lambda: __import__("ae_ppi_api_watch").cambios_recientes(10),
        "base_datos": lambda: __import__("ac_db").healthcheck(),
    }
    for nombre, fn in bloques.items():
        try:
            resultado[nombre] = fn()
        except Exception as e:
            resultado[nombre] = {"error": str(e)}
    return resultado


# ============================================================================
# NUEVO EN v13.0 — Gestión de Logs (pedido explícito: "Agregar botón
# descargar en la parte de log en el dashboard, hoy teníamos solamente la
# opción de visualizarlo"). Ver j_main.py para el RotatingFileHandler que
# alimenta este archivo — antes del v13, el bot solo logueaba a stdout,
# así que no existía ningún archivo real para poder ofrecer en descarga.
# ============================================================================
LOG_DIR = os.getenv("LOG_DIR", "data/logs")
LOG_FILE_PATH = os.path.join(LOG_DIR, "trading_bot.log")

# Categorías por palabra clave sobre el mismo archivo — ver docstring de
# j_main.py: se prefirió un único archivo con filtro de texto en el
# momento de la descarga antes que escribir 4 archivos separados.
LOG_CATEGORY_KEYWORDS = {
    "critical": ("CRITICAL", "KILL SWITCH", "kill_switch", "risk_guardian"),
    "trading": ("order_confirmation", "position_manager", "ORDEN", "PnL", "pnl"),
    "system": ("main:", "Bot Autónomo", "Apagado", "Docker", "entrypoint", "scheduler"),
    "ia_fallback": ("gemini", "claude", "agent_graph", "Claude fallback", "GEMINI"),
}


@app.get("/dashboard/logs", response_class=HTMLResponse)
def logs_dashboard(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    exists = os.path.exists(LOG_FILE_PATH)
    size_kb = round(os.path.getsize(LOG_FILE_PATH) / 1024, 1) if exists else 0
    rows_html = "".join(
        f"<tr><td>{cat.upper()}</td><td colspan='2'>"
        f"<a href='/api/logs/download/{cat}'>⬇️ Descargar .log</a></td></tr>"
        for cat in LOG_CATEGORY_KEYWORDS
    )
    return f"""
    <html><head><title>Logs — Bot de Trading</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body {{ font-family:-apple-system,sans-serif;max-width:700px;margin:20px auto;padding:0 12px; }}
    table {{ width:100%;border-collapse:collapse; }} td {{ padding:8px;border-bottom:1px solid #eee; }}</style>
    </head><body>
    <h1>📄 Gestión de Logs</h1>
    <p>{'Archivo actual: ' + LOG_FILE_PATH + f' ({size_kb} KB)' if exists else
        '⚠️ Todavía no hay archivo de log (el bot recién arrancó, o LOG_DIR no es escribible).'}</p>
    <table><tr><th>Categoría</th><th colspan="2">Acción</th></tr>{rows_html}</table>
    <p><a href="/api/logs/download/all">⬇️ Descargar log completo (sin filtrar)</a></p>
    <p><a href="/">← Volver al dashboard</a></p>
    </body></html>
    """


@app.get("/api/logs/download/{log_type}")
def download_log(log_type: str, token: str = Query(default=""),
                  authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    if not os.path.exists(LOG_FILE_PATH):
        raise HTTPException(status_code=404, detail="Todavía no existe ningún archivo de log.")
    with open(LOG_FILE_PATH, encoding="utf-8", errors="replace") as f:
        content = f.read()
    if log_type != "all":
        keywords = LOG_CATEGORY_KEYWORDS.get(log_type)
        if not keywords:
            raise HTTPException(status_code=404, detail=f"Categoría de log desconocida: {log_type}")
        content = "\n".join(line for line in content.splitlines()
                             if any(k.lower() in line.lower() for k in keywords))
    filename = f"trading_bot_{log_type}.log"
    return Response(
        content=content or "(sin líneas para esta categoría en el archivo actual)\n",
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================================
# NUEVO EN v13.0 — SRE / Monitoreo (pedido explícito: "Agregar una parte
# nueva al dashboard que muestre todo lo que tenga que ver con monitoreo
# y SRE"). Combina: eventos persistidos de Circuit Breaker/AIOps (ver
# c_ppi_client._persist_system_event), propuestas de fix del motor de
# introspección (archivos proposals/FIX_*.md — ver
# m_introspection_engine.py, que no usa una tabla de base de datos para
# esto) y la evidencia acumulada del grafo multi-agente en modo shadow.
# ============================================================================
PROPOSALS_DIR = os.getenv("PROPOSALS_DIR", "data/proposals")


@app.get("/sre", response_class=HTMLResponse)
def sre_dashboard(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    events = _query("SELECT * FROM system_events ORDER BY timestamp DESC LIMIT 30")
    events_html = "".join(
        f"<li>{e['timestamp']} — <b>{e['component']}</b>: {e['state']} ({e['detail']})</li>" for e in events
    ) or "<li>Sin eventos de Circuit Breaker/AIOps registrados todavía.</li>"

    proposals_html = "<li>Sin diagnósticos del motor SRE todavía (buena señal — no hubo crashes).</li>"
    if os.path.isdir(PROPOSALS_DIR):
        files = sorted(os.listdir(PROPOSALS_DIR), reverse=True)[:15]
        if files:
            proposals_html = "".join(f"<li>{fn}</li>" for fn in files)

    shadow_stats = _query(
        "SELECT gating, COUNT(*) as n, SUM(agreed) as agreed FROM shadow_evaluations GROUP BY gating"
    )
    shadow_html = ""
    for row in shadow_stats:
        mode = "GATING REAL" if row["gating"] else "SHADOW (observación)"
        pct = round((row["agreed"] or 0) / row["n"] * 100, 1) if row["n"] else 0
        shadow_html += f"<li>{mode}: {row['n']} evaluaciones, {pct}% de acuerdo con la lógica real.</li>"
    shadow_html = shadow_html or "<li>Sin evaluaciones del grafo multi-agente todavía.</li>"

    halts = _query("SELECT * FROM risk_halts ORDER BY triggered_at DESC LIMIT 10")
    halts_html = "".join(f"<li>{h['triggered_at']}: {h['reason']}</li>" for h in halts) or "<li>Sin cortes.</li>"

    # NUEVO EN v14.0 — estado de los streams SignalR de PPI (market data y
    # notificaciones de cuenta). LÍMITE CONOCIDO Y DELIBERADO: el dashboard
    # corre en un PROCESO SEPARADO del bot, así que las variables en memoria
    # de x_ppi_websocket dentro de ESTE proceso están vacías. Lo que se
    # muestra acá es el estado reportado por el propio bot en system_events,
    # no una lectura en vivo — decir "conectado" leyendo una memoria que no
    # es la del bot sería directamente falso.
    stream_events = _query(
        "SELECT * FROM system_events WHERE component = 'STREAM' ORDER BY timestamp DESC LIMIT 8"
    )
    stream_html = "".join(
        f"<li>{e['timestamp']} — {e['state']}: {e['detail']}</li>" for e in stream_events
    ) or ("<li>Sin eventos del stream registrados. El estado en vivo llega en el parte diario "
          "de salud por Telegram, que lo consulta desde el propio proceso del bot.</li>")

    return f"""
    <html><head><title>SRE / Monitoreo</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body {{ font-family:-apple-system,sans-serif;max-width:900px;margin:20px auto;padding:0 12px; }}
    h2 {{ font-size:1.1em;margin-top:26px;border-bottom:1px solid #eee;padding-bottom:4px;color:#2563eb; }}</style>
    </head><body>
    <h1>🩺 SRE / Monitoreo</h1>
    <p style="color:#666;font-size:0.9em;">Estado de la última señal CONOCIDA — este panel es un proceso
    separado del bot, no lee memoria en vivo, sino lo que el bot va persistiendo.</p>
    <h2>Circuit Breaker / AIOps — últimos eventos</h2><ul>{events_html}</ul>
    <h2>Kill switch — últimos cortes</h2><ul>{halts_html}</ul>
    <h2>Diagnósticos del motor de introspección SRE</h2><ul>{proposals_html}</ul>
    <h2>Grafo multi-agente (LangGraph)</h2><ul>{shadow_html}</ul>
    <h2>Stream de tiempo real de PPI (NUEVO v14.0)</h2><ul>{stream_html}</ul>
    <p><a href="/">← Volver al dashboard</a></p>
    </body></html>
    """


# ============================================================================
# NUEVO EN v13.0 — Infraestructura (pedido explícito: backups, consumo de
# espacio, sugerencias). Ver y_infra_monitor.py para la lógica.
# ============================================================================
@app.get("/infra", response_class=HTMLResponse)
def infra_dashboard(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    import y_infra_monitor as infra_monitor
    backup = infra_monitor.get_backup_status()
    disk = infra_monitor.get_disk_usage()
    db_size = infra_monitor.get_db_size_mb()
    suggestions = infra_monitor.get_infra_suggestions()

    color = {"red": "#dc2626", "yellow": "#d97706", "green": "#16a34a"}
    icon = {"red": "🔴", "yellow": "🟡", "green": "🟢"}
    suggestions_html = "".join(
        f"<li style='color:{color[s['severity']]};'>{icon[s['severity']]} {s['text']}</li>"
        for s in suggestions
    )

    return f"""
    <html><head><title>Infraestructura</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body {{ font-family:-apple-system,sans-serif;max-width:800px;margin:20px auto;padding:0 12px; }}
    h2 {{ font-size:1.1em;margin-top:26px;border-bottom:1px solid #eee;padding-bottom:4px;color:#2563eb; }}
    table {{ width:100%;border-collapse:collapse; }} td,th {{ padding:6px;text-align:left;border-bottom:1px solid #eee; }}
    </style></head><body>
    <h1>🖥️ Infraestructura</h1>
    <h2>Backups automáticos (diarios, 03:00)</h2>
    <table>
      <tr><th>Estado</th><td>{backup['status'].upper()}</td></tr>
      <tr><th>Último backup</th><td>{backup['last_backup_at'] or 'nunca'}
        {f"({backup['age_hours']}hs)" if backup['age_hours'] is not None else ''}</td></tr>
      <tr><th>Cantidad de backups retenidos</th><td>{backup['count']}</td></tr>
      <tr><th>Espacio ocupado por backups</th><td>{backup['total_size_mb']} MB</td></tr>
    </table>
    <h2>Consumo de disco</h2>
    <table>
      <tr><th>Ruta</th><td>{disk['path']}</td></tr>
      <tr><th>Uso</th><td>{disk['used_pct']}% ({disk['used_gb']} GB de {disk['total_gb']} GB)</td></tr>
      <tr><th>Libre</th><td>{disk['free_gb']} GB</td></tr>
      <tr><th>Tamaño de trading_system.db</th><td>{db_size} MB</td></tr>
    </table>
    <h2>Sugerencias</h2>
    <ul>{suggestions_html}</ul>
    <p><a href="/">← Volver al dashboard</a></p>
    </body></html>
    """


# ============================================================================
# NUEVO EN v13.0 — Monitor de Decisiones de IA (pedido explícito: "mostrar
# cómo funcionó el motor de decisiones de la Inteligencia artificial en
# cada una de las operaciones con enfoque gerencial, estadísticas y
# gráficos"). Ver j_main.log_ai_decision() para el origen de los datos.
# ============================================================================
@app.get("/ai-decisions", response_class=HTMLResponse)
def ai_decisions_dashboard(token: str = Query(default=""), days: int = Query(default=30, le=180),
                            authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    since = (date.today() - timedelta(days=days)).isoformat()
    stats = _query(
        "SELECT COUNT(*) as n, AVG(macro_score) as avg_score, SUM(veto_risk) as vetoed "
        "FROM ai_decisions WHERE date(timestamp) >= ?", (since,),
    )
    stats = stats[0] if stats else {"n": 0, "avg_score": None, "vetoed": 0}
    daily = _query(
        "SELECT date(timestamp) as d, COUNT(*) as n, AVG(macro_score) as avg_score, SUM(veto_risk) as vetoed "
        "FROM ai_decisions WHERE date(timestamp) >= ? GROUP BY date(timestamp) ORDER BY d DESC LIMIT 30",
        (since,),
    )
    recent_vetoes = _query(
        "SELECT * FROM ai_decisions WHERE veto_risk = 1 AND date(timestamp) >= ? "
        "ORDER BY timestamp DESC LIMIT 15", (since,),
    )

    daily_html = "".join(
        f"<tr><td>{d['d']}</td><td>{d['n']}</td><td>{d['avg_score']:.2f}</td>"
        f"<td>{d['vetoed'] or 0}</td></tr>" for d in daily
    ) or "<tr><td colspan='4'><em>Sin datos en el período.</em></td></tr>"

    vetoes_html = "".join(
        f"<li>{v['timestamp']} — {v['ticker']}: {v['reason']}</li>" for v in recent_vetoes
    ) or "<li>Sin vetos por contexto macro/geopolítico en el período.</li>"

    veto_pct = round((stats["vetoed"] or 0) / stats["n"] * 100, 1) if stats.get("n") else 0
    avg_score_txt = f"{stats['avg_score']:.2f}" if stats.get("avg_score") is not None else "s/d"

    return f"""
    <html><head><title>Decisiones de IA</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>body {{ font-family:-apple-system,sans-serif;max-width:800px;margin:20px auto;padding:0 12px; }}
    h2 {{ font-size:1.1em;margin-top:26px;border-bottom:1px solid #eee;padding-bottom:4px;color:#2563eb; }}
    table {{ width:100%;border-collapse:collapse; }} td,th {{ padding:6px;text-align:left;border-bottom:1px solid #eee; }}
    </style></head><body>
    <h1>🧠 Monitor de Decisiones de IA</h1>
    <p style="color:#666;font-size:0.9em;">Últimos {days} días · motor principal Gemini, respaldo Claude si Gemini falla.</p>
    <h2>Resumen</h2>
    <table>
      <tr><th>Evaluaciones macro/geopolíticas</th><td>{stats.get('n', 0)}</td></tr>
      <tr><th>Score macro promedio</th><td>{avg_score_txt}</td></tr>
      <tr><th>Tasa de veto</th><td>{veto_pct}%</td></tr>
    </table>
    <h2>Evolución diaria</h2>
    <table><tr><th>Día</th><th>Evaluaciones</th><th>Score prom.</th><th>Vetos</th></tr>{daily_html}</table>
    <h2>Últimos vetos por contexto macro/geopolítico</h2>
    <ul>{vetoes_html}</ul>
    <p><a href="/">← Volver al dashboard</a></p>
    </body></html>
    """


# ============================================================================
# NUEVO EN v13.0 — Informe semanal de gestión, descargable (pedido
# explícito). Ver z_reports_engine.py.
# ============================================================================
@app.get("/api/reports/weekly/download")
def download_weekly_report(token: str = Query(default=""), days: int = Query(default=7, le=90),
                            authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    import z_reports_engine as reports_engine
    html = reports_engine.generate_weekly_html_report(days_back=days)
    filename = f"informe_semanal_{date.today().isoformat()}.html"
    return Response(
        content=html, media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================================
# EDITOR VISUAL DE CONFIGURACIÓN (nuevo en v10.4)
# ============================================================================
# Pedido explícito: poder configurar las variables del .env desde una
# página web, en vez de editar el archivo a mano en la terminal.
#
# DECISIONES DE SEGURIDAD DE ESTE EDITOR (léelas antes de usarlo):
#   1. Los campos sensibles (API keys, tokens) NUNCA se muestran completos
#      en la página — se ven los últimos 4 caracteres, y el campo queda
#      vacío para escribir: si lo dejás vacío y guardás, ese valor NO se
#      toca (se conserva el que ya estaba). Esto es para que un valor
#      secreto no quede visible en el HTML de la página ni en capturas
#      de pantalla.
#   2. Guardar acá reescribe el archivo .env del servidor, pero NO
#      reinicia el bot solo. Después de guardar, tenés que reiniciarlo a
#      mano (systemctl restart trading_bot) o pedírmelo en el chat. No se
#      automatizó el reinicio a propósito: darle a un proceso web la
#      capacidad de reiniciar servicios del sistema es un salto de
#      privilegios que no se justifica para esta funcionalidad.
#   3. Protegido por el mismo DASHBOARD_ACCESS_TOKEN que el resto del
#      panel — si no lo configuraste, este editor queda tan expuesto
#      como el resto del dashboard.
# ---------------------------------------------------------------------- #
# NUEVO EN v14.0 — CARGA DE CONFIGURACIÓN ANTES QUE CUALQUIER OTRA COSA
# ---------------------------------------------------------------------- #
# Esto tiene que ejecutarse ANTES de importar cualquier módulo del proyecto.
# Motivo (bug latente encontrado al corregir docker-compose.yml en v14.0):
# varios módulos leen os.getenv() a nivel de módulo, es decir en el momento
# en que se los importa. Hasta v13.0 eso funcionaba de casualidad, porque
# docker-compose inyectaba todo el .env en el entorno del contenedor con
# `env_file:`, así que las variables ya estaban ahí antes de que arrancara
# Python. Al sacar `env_file` (ver el comentario largo en docker-compose.yml
# sobre por qué había que sacarlo), esa red desaparece: si no se carga el
# .env acá arriba, los módulos importados leerían sus valores por defecto y
# el bot arrancaría, silenciosamente, con la configuración equivocada.
from dotenv import load_dotenv
load_dotenv()

import aa_env_guard as env_guard

try:
    from v_config_metadata import CONFIG_METADATA
except ImportError:
    CONFIG_METADATA = []

ENV_FILE_PATH = os.getenv("ENV_FILE_PATH", ".env")


def _read_env_raw() -> str:
    if os.path.exists(ENV_FILE_PATH):
        with open(ENV_FILE_PATH, encoding="utf-8") as f:
            return f.read()
    return ""


def _parse_env_values(raw: str) -> dict:
    values = {}
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    return values


def _mask(value: str) -> str:
    if not value:
        return ""
    return f"(termina en ...{value[-4:]})" if len(value) > 4 else "(configurado)"


@app.get("/config", response_class=HTMLResponse)
def config_editor(token: str = Query(default=""), authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    csrf = _csrf_token()
    raw = _read_env_raw()
    current = _parse_env_values(raw)

    sections = {}
    for var, section, desc, example, sensitive in CONFIG_METADATA:
        sections.setdefault(section, []).append((var, desc, example, sensitive))

    body = ""
    for section, rows in sections.items():
        body += f"<h2>{section}</h2><table style='width:100%;border-collapse:collapse;margin-bottom:20px;'>"
        body += ("<tr style='background:#eee;'><th style='text-align:left;padding:6px;'>Variable</th>"
                 "<th style='text-align:left;padding:6px;'>Qué es</th>"
                 "<th style='text-align:left;padding:6px;'>Valor</th></tr>")
        for var, desc, example, sensitive in rows:
            current_val = current.get(var, "")
            if sensitive:
                display_val = ""
                placeholder = _mask(current_val) or f"ej: {example}"
            else:
                display_val = current_val
                placeholder = f"ej: {example}"
            # NUEVO EN v14.0 — marca visual de variable crítica (Instrucción 1):
            # que el propio panel diga cuáles son las que ameritan tratarse
            # como un tema de seguridad, en vez de que haya que saberlo de memoria.
            critica = " ⚠️" if var in env_guard.CRITICAL_VARS else ""
            estilo_fila = " style='background:#fff7ed;'" if critica else ""
            body += (
                f"<tr{estilo_fila}><td style='padding:6px;font-family:monospace;'>{var}{critica}</td>"
                f"<td style='padding:6px;font-size:0.9em;color:#555;'>{desc}</td>"
                f"<td style='padding:6px;'><input type='text' name='{var}' value='{display_val}' "
                f"placeholder='{placeholder}' style='width:95%;padding:4px;'></td></tr>"
            )
        body += "</table>"

    # ------------------------------------------------------------------ #
    # NUEVO EN v14.0 — Instrucción 1: la opción de reinicio SOLO aparece si
    # hubo un cambio real y reciente. No es un botón permanente.
    # ------------------------------------------------------------------ #
    pendiente = env_guard.get_recent_saved_change()
    bloque_reinicio = ""
    if pendiente:
        idle, motivo = env_guard.is_system_idle(
            require_no_open_positions=bool(pendiente.get("env_switch"))
        )
        estado_sistema = (
            "<span style='color:#16a34a;'>✅ El sistema está desocupado: el reinicio se aplicaría "
            "de inmediato tras tu confirmación.</span>"
            if idle else
            f"<span style='color:#d97706;'>⏳ El sistema está ocupado ({motivo}). Podés confirmar "
            "igual: el reinicio queda agendado y se ejecuta solo cuando quede libre.</span>"
        )
        aviso_env = ""
        if pendiente.get("env_switch"):
            aviso_env = (
                "<p style='background:#fef2f2;border-left:4px solid #dc2626;padding:10px;'>"
                "<b>🔐 Cambio de ENVIRONMENT detectado.</b> Estás por cambiar el ambiente "
                "(SANDBOX ↔ PRODUCTION): es la variable que decide si las órdenes se colocan con "
                "dinero real. Por seguridad, este cambio además exige que no haya <b>ninguna "
                "posición abierta</b> antes de aplicarse.</p>"
            )
        bloque_reinicio = f"""
      <div style="border:2px solid #2563eb;border-radius:10px;padding:16px;margin-top:26px;background:#f8fafc;">
        <h2 style="margin-top:0;">♻️ Aplicar los cambios (requiere reinicio)</h2>
        <p>Se guardaron cambios en: <code>{pendiente['changed_vars']}</code></p>
        <p><b>Los valores nuevos todavía NO están en uso.</b> El bot sigue corriendo con la
        configuración con la que arrancó; recién toma los valores nuevos cuando se reinicia.</p>
        {aviso_env}
        <p>{estado_sistema}</p>
        <p style="font-size:0.9em;color:#555;">Al pedir el reinicio te va a llegar un mensaje a
        Telegram con dos botones. El bot <b>no se reinicia hasta que confirmes ahí</b>, y nunca
        mientras haya una operación en curso.</p>
        <form method="post" action="/restart-request">
          <input type="hidden" name="csrf_token" value="{csrf}">
          <input type="hidden" name="request_id" value="{pendiente['id']}">
          <button type="submit" style="padding:10px 20px;background:#2563eb;color:white;border:none;border-radius:6px;cursor:pointer;">
            Pedir reinicio (confirmación por Telegram)
          </button>
        </form>
      </div>"""

    historial = env_guard.get_history(limit=8)
    filas_hist = "".join(
        f"<tr><td>{h['created_at']}</td><td><code>{h['changed_vars']}</code></td><td>{h['status']}</td></tr>"
        for h in historial
    ) or "<tr><td colspan='3'><em>Sin cambios de configuración registrados.</em></td></tr>"

    return f"""
    <html>
    <head><title>Configuración — Bot de Trading</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 20px auto; padding: 0 12px; }}
      h1 {{ font-size: 1.4em; }} h2 {{ font-size: 1.05em; margin-top: 24px; color: #2563eb; }}
      table {{ font-size: 0.95em; }} code {{ background:#f1f5f9; padding:1px 4px; border-radius:3px; }}</style>
    </head>
    <body>
      <h1>⚙️ Configuración del bot</h1>
      <p style="background:#eff6ff;border-left:4px solid #2563eb;padding:10px;">
      <b>Cómo funciona (v14.0):</b> guardar acá escribe el archivo <code>.env</code> del servidor,
      pero <b>no cambia nada en el bot que está corriendo</b>. Después de guardar vas a ver, en esta
      misma página, la opción de pedir el reinicio — que requiere tu confirmación por Telegram y
      espera a que no haya operaciones en curso. Esa opción aparece únicamente después de un cambio
      real y desaparece sola a los {env_guard.ENV_CHANGE_REQUEST_WINDOW_MINUTES} minutos.</p>
      <p>Las variables marcadas con ⚠️ son <b>críticas</b>: tocan credenciales, ambiente, modo de
      ejecución o límites de riesgo.</p>
      <p>Los campos sensibles se muestran vacíos por seguridad; si los dejás vacíos y guardás, no se
      tocan (se conserva el valor que ya estaba). Solo escribí ahí si querés CAMBIAR ese dato.</p>
      {bloque_reinicio}
      <form method="post" action="/config">
        <input type="hidden" name="csrf_token" value="{csrf}">
        {body}
        <button type="submit" style="padding:10px 20px;background:#2563eb;color:white;border:none;border-radius:6px;cursor:pointer;">Guardar cambios</button>
      </form>

      <h2>Historial de cambios de configuración</h2>
      <table style="width:100%;border-collapse:collapse;">
        <tr style="background:#eee;"><th style="text-align:left;padding:6px;">Cuándo</th>
        <th style="text-align:left;padding:6px;">Variables</th>
        <th style="text-align:left;padding:6px;">Estado</th></tr>
        {filas_hist}
      </table>
      <p><a href="/">← Volver al dashboard</a></p>
    </body>
    </html>
    """


@app.post("/config", response_class=HTMLResponse)
async def config_save(request: Request, token: str = Query(default=""),
                       authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    form = await request.form()
    # Se rechaza el guardado si falta o venció el token CSRF (ver
    # _csrf_token()/_verify_csrf_token()). Esto es ADEMÁS de la autenticación
    # por token/header: un atacante que ya tiene el token de acceso no
    # necesita CSRF, pero si el token se filtró vía Referer/logs, esta capa
    # evita que un formulario de otro sitio con un token viejo siga siendo válido.
    submitted_csrf = form.get("csrf_token", "")
    if not _verify_csrf_token(submitted_csrf):
        raise HTTPException(status_code=403, detail="Token CSRF inválido o vencido — volvé a abrir /config y reintentá.")
    raw = _read_env_raw()
    current = _parse_env_values(raw)

    sensitive_vars = {var for var, _, _, _, sensitive in CONFIG_METADATA if sensitive}
    updated = dict(current)
    # NUEVO EN v14.0 — se comparan los valores viejos contra los nuevos, uno
    # por uno. Sólo se considera "hubo un cambio" si algo cambió DE VERDAD:
    # apretar "Guardar" sin tocar nada no puede habilitar un reinicio. Es la
    # llave 2 de la cadena de seguridad de la Instrucción 1.
    changed_vars = []
    for var, _, _, _, _ in CONFIG_METADATA:
        new_val = form.get(var, "")
        if var in sensitive_vars and new_val == "":
            continue  # campo sensible vacío = no tocar el valor existente
        if updated.get(var, "") != new_val:
            changed_vars.append(var)
        updated[var] = new_val

    # Reescribe el .env preservando el orden/comentarios originales línea por
    # línea; si una variable no existía en el archivo, se agrega al final.
    lines = raw.splitlines()
    seen = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updated:
                new_lines.append(f"{k}={updated[k]}")
                seen.add(k)
                continue
        new_lines.append(line)
    for k, v in updated.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")

    env_switch = "ENVIRONMENT" in changed_vars
    request_id = env_guard.register_change(changed_vars, env_switch) if changed_vars else None

    if not changed_vars:
        cuerpo = ("<h2>Sin cambios</h2><p>No se modificó ninguna variable, así que no hace falta "
                  "reiniciar nada.</p>")
    else:
        criticas = [v for v in changed_vars if v in env_guard.CRITICAL_VARS]
        aviso = ""
        if criticas:
            aviso = ("<p style='background:#fef2f2;border-left:4px solid #dc2626;padding:10px;'>"
                     f"<b>⚠️ Cambiaste variables críticas:</b> <code>{', '.join(criticas)}</code>. "
                     "El reinicio va a pedirte confirmación explícita por Telegram.</p>")
        cuerpo = f"""
          <h2>✅ Configuración guardada</h2>
          <p>Variables modificadas: <code>{', '.join(changed_vars)}</code></p>
          {aviso}
          <p><b>Todavía falta un paso:</b> los valores nuevos no están en uso hasta que el bot se
          reinicie. Volvé al editor y vas a ver la opción para pedir el reinicio (te va a llegar
          una confirmación a Telegram).</p>"""

    return f"""
    <html><body style="font-family:-apple-system,sans-serif;max-width:640px;margin:40px auto;">
      {cuerpo}
      <p style="margin-top:20px;"><a href="/config">← Volver al editor</a></p>
    </body></html>
    """


# ============================================================================
# NUEVO EN v14.0 — Instrucción 1: solicitud de reinicio.
#
# ESTE ENDPOINT NO REINICIA NADA. Lo único que hace es mandar la
# confirmación por Telegram y dejar la solicitud grabada. El reinicio real
# lo decide y lo ejecuta el propio proceso del bot (j_main.
# process_restart_requests_job), que es el dueño de su ciclo de vida.
#
# Se mantiene así a propósito, respetando la regla de seguridad que este
# proyecto tiene desde v8.0: el proceso web NUNCA ejecuta comandos del
# sistema operativo ni mata procesos. Un panel web comprometido no puede
# reiniciar el bot por su cuenta — como mucho puede mandarte un Telegram
# pidiéndotelo, y vos decidís.
# ============================================================================
@app.post("/restart-request", response_class=HTMLResponse)
async def restart_request(request: Request, token: str = Query(default=""),
                           authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    form = await request.form()
    if not _verify_csrf_token(form.get("csrf_token", "")):
        raise HTTPException(status_code=403, detail="Token CSRF inválido o vencido.")

    request_id = form.get("request_id", "")
    # Revalidación del lado del servidor: aunque el formulario venga con un
    # request_id, se vuelve a chequear contra la base que ese cambio exista,
    # esté en estado SAVED y siga dentro de la ventana de tiempo. Confiar en
    # el id que manda el navegador sería confiar en el cliente.
    vigente = env_guard.get_recent_saved_change()
    if not vigente or vigente["id"] != request_id:
        mensaje = ("<h2>⛔ No hay ningún cambio pendiente de aplicar</h2>"
                   "<p>La opción de reinicio solo está disponible durante los "
                   f"{env_guard.ENV_CHANGE_REQUEST_WINDOW_MINUTES} minutos posteriores a un cambio "
                   "real de configuración. Volvé a guardar los cambios y reintentá.</p>")
    else:
        from b_notifiers import MultiChannelNotifier
        ok, detalle = env_guard.request_restart_confirmation(request_id, MultiChannelNotifier())
        icono = "📲" if ok else "⛔"
        mensaje = f"<h2>{icono} {detalle}</h2>"
        if ok:
            mensaje += ("<p>Buscá el mensaje en Telegram y tocá <b>Confirmar reinicio</b>. "
                        "Si no confirmás, no pasa nada: el bot sigue corriendo con la configuración "
                        "anterior.</p>")

    return f"""
    <html><body style="font-family:-apple-system,sans-serif;max-width:640px;margin:40px auto;">
      {mensaje}
      <p style="margin-top:20px;"><a href="/config">← Volver al editor</a></p>
    </body></html>
    """


# ============================================================================
# NUEVO EN v14.0 — Instrucción 7: informe mensual descargable.
# ============================================================================
@app.get("/reports/monthly")
def download_monthly_report(token: str = Query(default=""), days: int = Query(default=30, le=365),
                             authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    import z_reports_engine as reports_engine
    html = reports_engine.generate_monthly_html_report(days_back=days)
    return Response(
        content=html, media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="informe_mensual_{date.today().isoformat()}.html"'},
    )



# ============================================================================
# SOLAPAS NUEVAS DE v16.0
# ============================================================================
# Se agregan como rutas propias en vez de meterse dentro del render principal.
# El motivo es práctico: la página de inicio ya carga catorce días de resumen,
# y sumarle sondeos en vivo la volvería lenta justo cuando uno la abre
# apurado para ver qué está pasando.

_ESTILO_V16 = """
<style>
 body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;margin:0;background:#f6f7f9;color:#1a1a1a}
 .cont{max-width:1100px;margin:0 auto;padding:24px}
 h1{font-size:1.4rem;margin:0 0 4px} h2{font-size:1.05rem;margin:24px 0 8px}
 .sub{color:#666;font-size:.85rem;margin-bottom:20px}
 .tarjeta{background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:16px;margin-bottom:14px}
 table{width:100%;border-collapse:collapse;font-size:.9rem}
 th{text-align:left;padding:8px;background:#f0f1f3;font-weight:600}
 td{padding:8px;border-top:1px solid #eee;vertical-align:top}
 .sem{font-size:1.1rem;margin-right:6px}
 .crit{font-weight:600}
 .nav a{margin-right:14px;font-size:.9rem;text-decoration:none;color:#2563eb}
 .chico{font-size:.8rem;color:#666}
 .banner{padding:12px 16px;border-radius:8px;margin-bottom:18px;font-size:.92rem}
 .b-verde{background:#e8f6ec;border:1px solid #a8d8b9}
 .b-amarillo{background:#fdf6e3;border:1px solid #e6d08a}
 .b-rojo{background:#fdecea;border:1px solid #f0b0aa}
</style>
"""


def _nav(token: str) -> str:
    return (f"<div class='nav'>"
            f"<a href='/'>← Inicio</a>"
            f"<a href='/vivo'>📡 Actividad en vivo</a>"
            f"<a href='/testing'>🧪 Testing</a>"
            f"<a href='/salud'>🚦 Salud de APIs</a>"
            f"<a href='/sre'>🩺 SRE</a>"
            f"<a href='/historicos'>📚 Datos históricos</a>"
            f"<a href='/aprendizaje'>🎓 Blog de aprendizaje</a>"
            f"<a href='/config'>⚙️ Configuración</a>"
            f"</div>")


def _ahora_local() -> datetime:
    """Hora visible del panel, siempre en la zona configurada del mercado."""
    return datetime.now(ZoneInfo(SERVER_TIMEZONE))


def _estado_arranque_persistido() -> dict:
    """Lee el estado compartido sin importar el módulo del motor pesado."""
    try:
        with open(STARTUP_STATE_PATH, encoding="utf-8") as f:
            estado = json.load(f)
        return estado if isinstance(estado, dict) else {}
    except (OSError, ValueError):
        return {}


@app.get("/vivo", response_class=HTMLResponse)
def actividad_en_vivo(token: str = Query(default=""),
                      authorization: Optional[str] = Header(default=None)):
    """Qué está haciendo el bot ahora mismo, con enfoque gerencial.

    La diferencia con la vista de logs no es estética. El log cuenta TODO en
    orden de aparición y hay que interpretarlo. Esta vista responde otra
    pregunta, que es la que uno se hace de verdad al abrir el panel: ¿está
    trabajando, qué está mirando, qué decidió y por qué, y cuánta plata tengo
    comprometida en este momento?
    """
    _check_auth(token, authorization)

    posiciones = _query("SELECT * FROM positions WHERE status='OPEN' ORDER BY opened_at DESC")
    # Los eventos globales del portón conservan su auditoría en la base, pero
    # no son evaluaciones de instrumentos y no deben repetirse como 25 filas.
    senales = _query("SELECT * FROM signals WHERE COALESCE(ticker, '') <> '__SISTEMA__' ORDER BY rowid DESC LIMIT 25")
    decisiones = _query("SELECT * FROM ai_decisions ORDER BY rowid DESC LIMIT 15")
    pendientes = _query("SELECT * FROM order_proposals WHERE status='PENDING' ORDER BY rowid DESC")

    expuesto = sum((p.get("entry_price") or 0) * (p.get("quantity") or 0) for p in posiciones)
    estado_arranque = _estado_arranque_persistido()

    try:
        import p_risk_guardian as rg
        detenido = rg.is_halted()
    except Exception:
        detenido = None

    if detenido:
        banner = ("<div class='banner b-rojo'>🔴 <b>Kill switch activo.</b> El bot no está abriendo "
                  "posiciones nuevas. Las abiertas siguen vigiladas.</div>")
    elif estado_arranque.get("estado") == "ESPERANDO_APERTURA":
        motivo = estado_arranque.get("mensaje") or "Fuera de la rueda bursátil"
        banner = (f"<div class='banner b-amarillo'>🌙 <b>Mercado cerrado.</b> "
                  f"Motor de trading hibernado. {motivo}.</div>")
    elif posiciones:
        banner = (f"<div class='banner b-verde'>🟢 <b>Operando.</b> {len(posiciones)} posición(es) "
                  f"abierta(s), ${expuesto:,.0f} comprometidos.</div>")
    else:
        banner = ("<div class='banner b-amarillo'>🟡 <b>Vigilando sin posiciones abiertas.</b> "
                  "El bot está evaluando el mercado; todavía no encontró una oportunidad que "
                  "pase todos los filtros.</div>")

    fila_pos = "".join(
        f"<tr><td><b>{p.get('ticker')}</b></td><td>{p.get('quantity')}</td>"
        f"<td>${p.get('entry_price')}</td><td>${p.get('stop_loss_price')}</td>"
        f"<td>${p.get('take_profit_price')}</td><td class='chico'>{p.get('opened_at','')}</td></tr>"
        for p in posiciones) or "<tr><td colspan='6' class='chico'>Sin posiciones abiertas.</td></tr>"

    fila_sen = "".join(
        f"<tr><td>{s.get('ticker')}</td><td>{s.get('status')}</td>"
        f"<td class='chico'>{(s.get('reason') or '')[:160]}</td></tr>"
        for s in senales) or "<tr><td colspan='3' class='chico'>Sin evaluaciones registradas todavía.</td></tr>"

    fila_ia = "".join(
        f"<tr><td>{d.get('ticker')}</td><td>{d.get('macro_score')}</td>"
        f"<td>{'🔴 veto' if d.get('veto_risk') else '🟢 sin veto'}</td>"
        f"<td class='chico'>{(d.get('reason') or '')[:160]}</td></tr>"
        for d in decisiones) or "<tr><td colspan='4' class='chico'>Sin decisiones de IA registradas.</td></tr>"

    fila_pen = "".join(
        f"<tr><td>{p.get('ticker')}</td><td>{p.get('quantity')}</td><td>${p.get('price')}</td></tr>"
        for p in pendientes) or "<tr><td colspan='3' class='chico'>Ninguna orden esperando confirmación.</td></tr>"

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="refresh" content="30">
    <title>Actividad en vivo</title>{_ESTILO_V16}</head><body><div class="cont">
    {_nav(token)}
    <h1>📡 Actividad en vivo</h1>
    <p class="sub">Se refresca solo cada 30 segundos · {_ahora_local().strftime('%d/%m/%Y %H:%M:%S')} (Buenos Aires)</p>
    {banner}
    <div class="tarjeta"><h2>Posiciones abiertas</h2><table>
      <tr><th>Instrumento</th><th>Cantidad</th><th>Entrada</th><th>Stop-loss</th><th>Take-profit</th><th>Desde</th></tr>
      {fila_pos}</table></div>
    <div class="tarjeta"><h2>Órdenes esperando tu confirmación</h2><table>
      <tr><th>Instrumento</th><th>Cantidad</th><th>Precio</th></tr>{fila_pen}</table></div>
    <div class="tarjeta"><h2>Últimas evaluaciones — qué miró y qué decidió</h2><table>
      <tr><th>Instrumento</th><th>Resultado</th><th>Motivo</th></tr>{fila_sen}</table></div>
    <div class="tarjeta"><h2>Motor de decisión de IA</h2><table>
      <tr><th>Instrumento</th><th>Score macro</th><th>Veto</th><th>Fundamento</th></tr>{fila_ia}</table></div>
    </div></body></html>"""


@app.get("/salud", response_class=HTMLResponse)
def salud_apis(token: str = Query(default=""),
               authorization: Optional[str] = Header(default=None)):
    """Semáforo por API y por módulo. Cada renglón dice qué se probó y cuándo."""
    _check_auth(token, authorization)
    import am_api_health as health

    ppi = globals().get("_ppi_client_ref")
    notificador = globals().get("_notifier_ref")
    tablero = health.tablero(ppi, notificador)

    clase = {"VERDE": "b-verde", "AMARILLO": "b-amarillo", "ROJO": "b-rojo"}.get(
        tablero["estado_general"], "b-amarillo")

    marca_critico = " <span class='crit'>(crítico)</span>"
    filas = "".join(
        f"<tr><td><span class='sem'>{c['circulo']}</span>{c['nombre']}"
        f"{marca_critico if c['critico'] else ''}</td>"
        f"<td>{c['estado']}</td>"
        f"<td>{c['detalle']}</td>"
        f"<td class='chico'>{(str(c['latencia_ms']) + ' ms') if c['latencia_ms'] else '—'}</td></tr>"
        for c in tablero["chequeos"])

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="refresh" content="60">
    <title>Salud de APIs</title>{_ESTILO_V16}</head><body><div class="cont">
    {_nav(token)}
    <h1>🚦 Salud de APIs y módulos</h1>
    <p class="sub">Verificado a las {tablero['verificado']} · los resultados se cachean 60 s
    para no consumir cuota del bróker en cada refresco.</p>
    <div class="banner {clase}">{tablero['circulo_general']} <b>{tablero['resumen']}</b></div>
    <div class="tarjeta"><table>
      <tr><th>Componente</th><th>Estado</th><th>Detalle</th><th>Latencia</th></tr>{filas}
    </table></div>
    <div class="tarjeta"><h2>Cómo leer los colores</h2>
      <p class="chico">🟢 responde bien · 🟡 responde con salvedades, se puede operar pero conviene mirarlo ·
      🔴 no responde o devuelve algo inservible · ⚪ desactivado a propósito, no es una falla.</p>
    </div></div></body></html>"""


@app.get("/historicos", response_class=HTMLResponse)
def datos_historicos(token: str = Query(default=""),
                     authorization: Optional[str] = Header(default=None)):
    """Cuánta historia hay archivada, de qué fuentes, y cuánta conviene traer."""
    _check_auth(token, authorization)
    try:
        import al_historical_ingest as hist
        estado = hist.estado_del_archivo()
    except Exception as e:
        estado = {"semaforo": "ROJO", "instrumentos_archivados": 0, "velas_totales": 0,
                  "atraso_dias": None, "fecha_mas_reciente": None, "ultima_corrida": None,
                  "error": str(e)}

    circulo = {"VERDE": "🟢", "AMARILLO": "🟡", "ROJO": "🔴"}.get(estado["semaforo"], "⚪")

    fuentes = [
        ("IOL — api.invertironline.com", "Acciones, bonos, opciones, cauciones y futuros. "
         "Series ajustadas por splits y dividendos.", "365 días", "Primaria para precios históricos",
         "Requiere cuenta comitente (gratuita)"),
        ("BYMA Open Data — open.bymadata.com.ar", "Paneles oficiales, series de opciones y "
         "contratos de futuros vigentes, índices MERVAL y BURCAP.", "365 días",
         "Fuente de verdad sobre qué instrumentos existen", "Sin credenciales"),
        ("data912.com", "OHLCV histórico y cadenas de opciones del mercado local.", "365 días",
         "Tercera fuente y contraste", "Datos educativos, NO en tiempo real: sirven para archivo, "
         "nunca para decidir una entrada"),
        ("BCRA / INDEC / ArgentinaDatos", "Inflación, reservas, tasa, dólar oficial y financieros.",
         "180 a 730 días", "Ya integrada y funcionando", "Sin credenciales"),
        ("Yahoo Finance", "Acciones y CEDEARs con precio demorado.", "365 días",
         "Último recurso y doble chequeo", "Demorada; no cubre bonos ni opciones"),
    ]
    filas_f = "".join(
        f"<tr><td><b>{n}</b></td><td>{d}</td><td>{a}</td><td>{u}</td><td class='chico'>{o}</td></tr>"
        for n, d, a, u, o in fuentes)

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Datos históricos</title>{_ESTILO_V16}</head><body><div class="cont">
    {_nav(token)}
    <h1>📚 Datos históricos — qué hay y qué conviene importar</h1>
    <p class="sub">El archivo histórico es lo que permite calcular percentiles, volatilidad y
    hacer backtesting sin depender de que una API externa responda.</p>
    <div class="banner {'b-verde' if estado['semaforo']=='VERDE' else 'b-amarillo' if estado['semaforo']=='AMARILLO' else 'b-rojo'}">
      {circulo} <b>{estado['instrumentos_archivados']} instrumentos · {estado['velas_totales']} velas ·
      dato más reciente: {estado.get('fecha_mas_reciente') or 'ninguno'}</b>
      {f"· atraso de {estado['atraso_dias']} días" if estado.get('atraso_dias') is not None else ""}
    </div>
    <div class="tarjeta"><h2>Fuentes disponibles y antigüedad recomendada</h2><table>
      <tr><th>Fuente</th><th>Qué cubre</th><th>Antigüedad</th><th>Rol</th><th>Advertencia</th></tr>
      {filas_f}</table></div>
    <div class="tarjeta"><h2>Por qué 365 días y no más</h2>
      <p class="chico">Las series argentinas atraviesan saltos cambiarios y cambios de régimen que
      rompen la comparabilidad estadística. Una volatilidad calculada sobre cinco años mezcla
      mercados que ya no existen y produce un número que no describe ninguno. Un año cubre un
      ciclo completo y el rango de 52 semanas, que es la referencia que mira todo el mundo.
      El mínimo útil son 90 días: con menos, la volatilidad se calcula sobre tan pocos puntos que
      el margen de error es más ancho que la señal. Los 730 días quedan reservados al backtest del
      filtro macro, donde justamente interesa ver el sistema atravesando un cambio de régimen.</p>
    </div></div></body></html>"""


@app.get("/aprendizaje", response_class=HTMLResponse)
def blog_de_aprendizaje(token: str = Query(default=""),
                        authorization: Optional[str] = Header(default=None)):
    """El lugar único y explícito para bajar el archivo de aprendizaje.

    Existía el endpoint JSON desde hace varias versiones, pero había que
    conocer la URL. Que una función quede escondida detrás de una dirección
    que hay que recordar equivale, en la práctica, a que no exista.
    """
    _check_auth(token, authorization)
    diagnosticos = _query("SELECT * FROM learning_diagnostics ORDER BY date DESC LIMIT 5")
    filas = "".join(
        f"<tr><td>{d.get('date')}</td><td class='chico'>{(d.get('diagnosis') or '')[:300]}</td></tr>"
        for d in diagnosticos) or "<tr><td colspan='2' class='chico'>Todavía no hay diagnósticos.</td></tr>"

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Blog de aprendizaje</title>{_ESTILO_V16}</head><body><div class="cont">
    {_nav(token)}
    <h1>🎓 Blog de aprendizaje</h1>
    <p class="sub">Todo lo que el sistema aprendió de sus propias operaciones, en un solo archivo.</p>

    <div class="tarjeta">
      <h2>Este es el archivo que hay que subir a la conversación</h2>
      <p><a href="/api/learning-logs"
            style="display:inline-block;background:#2563eb;color:#fff;padding:10px 18px;
                   border-radius:6px;text-decoration:none;font-weight:600;">
         ⬇️ Descargar blog de aprendizaje (JSON)</a></p>
      <p class="chico">Contiene los diagnósticos del motor de aprendizaje, las recomendaciones
      mensuales, el historial de ajustes automáticos de umbrales y todos los cortes del kill
      switch. Es el insumo con el que se analiza cómo se está comportando el sistema de verdad
      —no cómo debería comportarse— y de ahí salen las mejoras de la versión siguiente.</p>
      <p class="chico"><b>Junto a este archivo conviene subir también:</b> el log completo
      (<a href="/api/logs/download/all">descargar acá</a>) y el informe mensual
      (<a href="/api/reports/monthly/download">descargar acá</a>). Con esos tres
      alcanza para reconstruir qué pasó sin acceso al servidor.</p>
    </div>

    <div class="tarjeta"><h2>Últimos diagnósticos</h2><table>
      <tr><th>Fecha</th><th>Diagnóstico</th></tr>{filas}</table></div>
    </div></body></html>"""


@app.get("/api/sre/propuestas")
def sre_propuestas(token: str = Query(default=""),
                   authorization: Optional[str] = Header(default=None)):
    """Propuestas de mejora con criticidad, impacto y estado de las condiciones."""
    _check_auth(token, authorization)
    import an_sre_deploy as sre
    return {"propuestas": sre.listar(), "condiciones": sre.condiciones_de_aplicacion()}


@app.post("/api/sre/encolar/{propuesta_id}")
def sre_encolar(propuesta_id: str, token: str = Query(default=""),
                authorization: Optional[str] = Header(default=None)):
    """Lo que hace el botón "Implementar": NO aplica nada, encola y manda el
    pedido de confirmación a Telegram. La aplicación real solo ocurre cuando
    respondés con el código en el chat."""
    _check_auth(token, authorization)
    import an_sre_deploy as sre
    todas = {p["id"]: p for p in sre.listar()}
    if propuesta_id not in todas:
        return {"ok": False, "motivo": "No existe esa propuesta."}
    propuesta = sre.Propuesta(**todas[propuesta_id])
    resultado = sre.encolar_para_confirmacion(propuesta, globals().get("_notifier_ref"))
    return {"ok": True, "mensaje": "Te mandé el pedido de confirmación por Telegram.",
            **resultado}




@app.get("/testing", response_class=HTMLResponse)
def solapa_testing(token: str = Query(default=""),
                   authorization: Optional[str] = Header(default=None)):
    """Estado del arranque y traza paso a paso de la simulación.

    Es la pantalla para mirar mientras el bot corre en el entorno de pruebas:
    dice en qué modo está, quién lo autorizó y qué viene haciendo, en orden.
    """
    _check_auth(token, authorization)
    import ao_startup_gate as gate

    estado = gate.estado_actual()
    pasos = gate.leer_pasos(200)

    modo = estado.get("modo")
    if modo == gate.MODO_SIMULACION:
        banner = ("<div class='banner b-amarillo'>🧪 <b>Modo simulación.</b> El bot recorre "
                  "todo el circuito contra el sandbox de PPI. Ninguna orden sale al mercado real.</div>")
    elif modo == gate.MODO_REAL:
        banner = ("<div class='banner b-rojo'>💰 <b>Modo real.</b> Las órdenes se ejecutan "
                  "con dinero de verdad.</div>")
    elif estado.get("estado") == gate.ESPERANDO:
        banner = (f"<div class='banner b-amarillo'>⏸️ <b>Esperando tu autorización.</b> "
                  f"Te mandé el pedido por Telegram con el código "
                  f"<code>{estado.get('codigo','')}</code>. El bot no está operando.</div>")
    else:
        banner = ("<div class='banner b-verde'>⏸️ <b>Detenido a pedido.</b> El sistema está "
                  "levantado y accesible, sin operar.</div>")

    botones = f"""
      <form method='post' action='/api/testing/autorizar?modo=SIMULACION'
            style='display:inline'><button type='submit'
            style='background:#f59e0b;color:#fff;border:0;padding:9px 16px;border-radius:6px;
                   font-weight:600;cursor:pointer;margin-right:8px;'>🧪 Arrancar en simulación</button></form>
      <form method='post' action='/api/testing/autorizar?modo=DETENIDO'
            style='display:inline'><button type='submit'
            style='background:#6b7280;color:#fff;border:0;padding:9px 16px;border-radius:6px;
                   font-weight:600;cursor:pointer;'>⏸️ Detener</button></form>
    """ if estado.get("estado") in (gate.ESPERANDO, "VENCIDO_SIN_RESPUESTA") else ""

    color_etapa = {"ARRANQUE": "#2563eb", "AUTORIZACION": "#7c3aed",
                   "ORDEN_SIMULADA": "#f59e0b", "EVALUACION": "#0891b2",
                   "DECISION": "#16a34a", "DESCARTE": "#6b7280"}
    filas = "".join(
        f"<tr><td class='chico' style='white-space:nowrap'>{p['momento'][11:]}</td>"
        f"<td><span style='color:{color_etapa.get(p['etapa'], '#374151')};font-weight:600'>"
        f"{p['etapa']}</span></td>"
        f"<td>{p['detalle']}</td><td class='chico'>{p['resultado']}</td></tr>"
        for p in reversed(pasos)) or "<tr><td colspan='4' class='chico'>Todavía no hay pasos registrados.</td></tr>"

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="refresh" content="15">
    <title>Testing</title>{_ESTILO_V16}</head><body><div class="cont">
    {_nav(token)}
    <h1>🧪 Testing — arranque controlado y traza en vivo</h1>
    <p class="sub">Se refresca cada 15 segundos · autorizado por: {estado.get('autorizado_por') or '—'}
    {(' · ' + estado.get('autorizado','')) if estado.get('autorizado') else ''}</p>
    {banner}
    {f"<div class='tarjeta'>{botones}<p class='chico' style='margin-top:10px'>Esta vía manual "
     f"existe para cuando Telegram no está disponible. Lo normal es autorizar desde el "
     f"teléfono.</p></div>" if botones else ""}
    <div class="tarjeta"><h2>Qué viene haciendo el bot, paso a paso</h2><table>
      <tr><th>Hora</th><th>Etapa</th><th>Detalle</th><th>Resultado</th></tr>{filas}</table></div>
    </div></body></html>"""


@app.post("/api/testing/autorizar")
def testing_autorizar(token: str = Query(default=""), modo: str = Query(default="SIMULACION"),
                      authorization: Optional[str] = Header(default=None)):
    """Autorización manual del arranque, para cuando Telegram no responde."""
    _check_auth(token, authorization)
    import ao_startup_gate as gate
    return gate.autorizar(modo, origen="panel web")


@app.get("/api/testing/estado")
def testing_estado(token: str = Query(default=""),
                   authorization: Optional[str] = Header(default=None)):
    _check_auth(token, authorization)
    import ao_startup_gate as gate
    return {"estado": gate.estado_actual(), "pasos": gate.leer_pasos(200)}


if __name__ == "__main__":
    # v16.2 — FALLAR CERRADO. Antes, si el token estaba vacío, el panel
    # arrancaba igual y quedaba sin ninguna protección: solo se imprimía una
    # advertencia que nadie lee en un contenedor. Un panel desprotegido que
    # arranca es peor que uno que no arranca, porque el segundo se nota
    # enseguida y el primero no se nota nunca.
    import ay_dashboard_auth as auth
    problemas = auth.validar_configuracion()
    if problemas:
        print("No se puede levantar el panel. Problemas de configuración:")
        for p in problemas:
            print("  -", p)
        raise SystemExit(2)
    if DASHBOARD_HOST != "127.0.0.1":
        print(f"⚠️  DASHBOARD_HOST={DASHBOARD_HOST} — el dashboard va a quedar alcanzable "
              "desde fuera de este servidor. Asegurate de tener DASHBOARD_ACCESS_TOKEN "
              "configurado y, si es posible, HTTPS con Caddy detrás del contenedor "
              "(ver Documento Maestro v14, sección L).")
    uvicorn.run(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT)
