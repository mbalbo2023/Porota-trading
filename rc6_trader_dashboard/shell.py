"""Shared shell; only eight primary links on every trader page."""
from .components import Field, badge, clock, e, unverified, value_html
from .design_system import CSS, SCRIPT
from .navigation import DESTINATIONS, view_path
from .projection import UNKNOWN
from urllib.parse import urlencode


def _real_order_receipt(state):
    """A mutable local SQLite counter never attests an external runtime fact.

    In particular, a locally forged fresh heartbeat does not establish the
    identity of the writer or bind the observation to a deployed image/SHA.
    This projection has no cryptographically bound runtime receipt yet.
    """
    value = state.get("real_orders_sent")
    if type(value) is not int or value < 0:
        # SQLite can expose malformed or unavailable facts. False and "0"
        # must not silently pass as a verified integer counter.
        return ("real_orders_sent=NO_VERIFICADO · estado actual NO_VERIFICADO · falta un contador numérico válido en la base observada.",
                "Alerta de integridad: contador de órdenes reales no verificable." if value is not None else "")
    if value > 0:
        return (f"real_orders_sent={value} · ALERTA: valor registrado en BD local",
                "Alerta de seguridad: contador de órdenes reales distinto de cero.")
    return (f"real_orders_sent=0 (último valor almacenado) · observación histórica"
            f" · BD local (heartbeat {clock(state.get('heartbeat_at'))})"
            " · origen/runtime no acreditado · estado actual NO_VERIFICADO", "")


def render(destination, tab, projection, content):
    state = projection.runtime
    policy = projection.shadow["report"].get("capacity_policy", {})
    selector = policy.get("status", UNKNOWN) if isinstance(policy, dict) else UNKNOWN
    if selector not in {"OFF", "OFF_BASELINE", "SHADOW", "SHADOW_BASELINE",
                        "APPROVED_DYNAMIC", "ACTIVATION_BLOCKED_EXIT_CAPACITY",
                        "BASELINE_FAIL_CLOSED"}:
        selector = UNKNOWN
    # The coherent generation has a measured liveness budget; a fixed 30 s
    # clock incorrectly hides a fresh selector during normal native cycles.
    if not projection.shadow_fresh or not projection.shadow["report"].get("as_of"):
        selector = UNKNOWN
    def current(active):
        return "aria-current='page'" if active else ""
    links = "".join(f"<a href='{item.path}' {current(item.key == destination.key)}><span class='nav-icon' aria-hidden='true'>{item.icon}</span>{item.label}</a>" for item in DESTINATIONS)
    retained = urlencode({k: v for k, v in projection.filters.items() if v and k != "offset"})
    tabs = "".join(f"<a href='{view_path(destination, key)}{e('?' + retained) if retained else ''}' {current(key == tab)}>{label}</a>" for key, label in destination.tabs)
    real_order_status, real_order_alert = _real_order_receipt(state)
    iol_profile = getattr(projection, "market_provider", "PPI") == "IOL_REST"
    auth = projection.iol_authentication if iol_profile else {"state": state.get("ppi_auth"),
            "reason": "El registro del observer no aporta estado de autenticación PPI."}
    market_clock = state.get("last_market_data_at")
    market_age = state.get("market_age")
    market_freshness = state["market_freshness"]
    if iol_profile:
        from .projection import freshness
        row = projection.store.one("api_health", "component=?", ("IOL_REST_MARKETDATA",))
        if row.get("state") != "VERDE" or freshness(row.get("checked_at"), projection.now, 180) != "FRESH":
            market_clock, market_age, market_freshness = None, None, UNKNOWN
    def clock_value(label, timestamp, seconds, state, reason):
        if clock(timestamp) == UNKNOWN or seconds is None:
            return unverified(reason)
        return (f"{e(clock(timestamp))} · {value_html({'age': seconds}, Field('age', label, 'number'))} s · " +
                badge(state, reason=reason))
    values = (
        ("Modo runtime", value_html(state, Field("mode", "Modo runtime"))),
        ("Sesión", value_html(state, Field("session_state", "Fase de rueda"))),
        ("Selector / capacidad", e(selector)),
        ("Autenticación IOL REST" if iol_profile else "Autenticación PPI", badge(auth["state"], reason=auth.get("reason"))),
        ("Órdenes reales observadas", e(real_order_status)),
        ("Heartbeat", clock_value("Antigüedad del heartbeat", state.get("heartbeat_at"), state.get("heartbeat_age"),
                                  state["heartbeat_freshness"], "El observer no aporta un heartbeat utilizable para este corte.")),
        ("Último dato de mercado", clock_value("Antigüedad del dato de mercado", market_clock, market_age, market_freshness,
                                               "Falta una observación IOL REST vigente con reloj de mercado." if iol_profile else
                                               "El observer no publica un reloj de mercado utilizable para este corte.")),
        ("Reloj operativo", e(clock(projection.now.isoformat()))),
    )
    strip_parts = []
    for label, value in values:
        aria = " role='status' aria-live='polite'" if label == "Órdenes reales observadas" else ""
        missing_selector = label == "Selector / capacidad" and selector == UNKNOWN
        classes = "strip-item unverified-value" if missing_selector else "strip-item"
        explanation = ("<small class='unverified-reason'>Falta un corte SHADOW vigente que acredite la capacidad.</small>"
                       if missing_selector else "")
        strip_parts.append(f"<div class='{classes}'{aria}><b>{e(label)}</b><span>{value}</span>{explanation}</div>")
    strip = "".join(strip_parts)
    warning = (f"<div class='notice critical' role='alert'>{e(real_order_alert)}</div>"
               if real_order_alert else "")
    title = dict(destination.tabs)[tab]
    mode_label = "MODO SIMULACIÓN PRODUCTIVA" if state.get("mode") == "PRODUCTION_PAPER" else "Modo runtime: " + str(state.get("mode") or UNKNOWN)
    return ("<!doctype html><html lang='es-AR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta name='referrer' content='no-referrer'><title>{e(destination.label)} · {e(title)} — POROTA TRADING RC6</title><style id='trader-terminal-tokens'>{CSS}</style></head><body>"
            "<a class='skip-link' href='#terminal-content'>Ir al contenido</a>"
            f"<aside class='sidebar' id='terminal-sidebar' aria-label='Menú principal'><div class='brand'>POROTA TRADING<small>RC6 · TRADER TERMINAL</small></div>"
            "<button class='button sidebar-close' id='terminal-menu-close' type='button' aria-label='Cerrar menú'>Cerrar menú</button>"
            f"<nav class='primary-nav' id='porota-canonical-nav' aria-label='Destinos principales'>{links}</nav>"
            "<div class='sidebar-footer'>PAPER / SHADOW ONLY<br>PRODUCTION_PAPER / SIMULATION<br>Objetivo: real_orders_sent=0</div></aside>"
            "<div class='workspace'><header class='global-header' id='terminal-header'><div class='title-row'>"
            f"<strong>POROTA TRADING RC6 <span class='muted'>/ {e(destination.label)}</span></strong><span id='porota-paper-mode' aria-label='{e(mode_label)}'>{badge('PAPER / SHADOW ONLY')}<span class='sr-only'>{e(mode_label)}</span></span>"
            "<div class='header-actions'><button class='button menu-button' id='terminal-menu' aria-expanded='false' aria-controls='terminal-sidebar'>Menú</button>"
            "<button class='button' id='refresh-data'>Actualizar datos</button><button class='button' id='auto-refresh' aria-pressed='false'>Activar actualización</button></div></div>"
            f"<div class='status-strip' id='operational-strip'>{strip}</div></header><nav class='subnav' aria-label='Subvistas de {e(destination.label)}'>{tabs}</nav>"
            f"<main class='content' id='terminal-content' tabindex='-1' data-view='{destination.key}/{tab}'><div class='page-heading'><h1>{e(destination.label)} <span class='muted'>/ {e(title)}</span></h1><p>{e(destination.question)}</p></div>{warning}{content}</main>"
            "<footer class='footer'><span id='refresh-status' role='status' aria-live='polite'>Actualización manual · pausa durante interacción.</span><a href='#terminal-content'>Arriba</a></footer></div>"
            f"<script>{SCRIPT}</script></body></html>")
