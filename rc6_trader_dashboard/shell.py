"""Shared shell; only eight primary links on every trader page."""
from .components import badge, clock, e, number
from .design_system import CSS, SCRIPT
from .navigation import DESTINATIONS, view_path
from .projection import UNKNOWN, freshness
from .datasets import policy_state
from urllib.parse import urlencode


def render(destination, tab, projection, content):
    state = projection.runtime
    selector = policy_state(projection)
    def current(active):
        return "aria-current='page'" if active else ""
    links = "".join(f"<a href='{item.path}' {current(item.key == destination.key)}><span class='nav-icon' aria-hidden='true'>{item.icon}</span>{item.label}</a>" for item in DESTINATIONS)
    retained = urlencode({k: v for k, v in projection.filters.items() if v and k != "offset"})
    tabs = "".join(f"<a href='{view_path(destination, key)}{e('?' + retained) if retained else ''}' {current(key == tab)}>{label}</a>" for key, label in destination.tabs)
    values = (
        ("Modo runtime", state.get("mode", UNKNOWN)), ("Sesión", state.get("session_state", UNKNOWN)),
        ("Selector / capacidad", selector), ("PPI · autoridad primaria", state.get("ppi_auth", UNKNOWN)),
        ("Órdenes reales observadas", "real_orders_sent=" + str(state.get("real_orders_sent", UNKNOWN)) +
         (" · órdenes reales: NINGUNA" if state.get("real_orders_sent") == 0 else "")),
        ("Heartbeat", f"{clock(state.get('heartbeat_at'))} · age {number(state.get('heartbeat_age'), 1)} s · {state['heartbeat_freshness']}"),
        ("Último dato de mercado", f"{clock(state.get('last_market_data_at'))} · age {number(state.get('market_age'), 1)} s · {state['market_freshness']}"),
        ("Reloj operativo", clock(projection.now.isoformat())),
    )
    strip = "".join(f"<div class='strip-item'><b>{e(label)}</b><span>{e(value)}</span></div>" for label, value in values)
    warning = ""
    if state.get("real_orders_sent") not in (None, 0):
        warning = "<div class='notice critical' role='alert'>Alerta de seguridad: contador de órdenes reales distinto de cero.</div>"
    title = dict(destination.tabs)[tab]
    mode_label = "MODO SIMULACIÓN PRODUCTIVA" if state.get("mode") == "PRODUCTION_PAPER" else "Modo runtime: " + str(state.get("mode") or UNKNOWN)
    return ("<!doctype html><html lang='es-AR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<meta name='referrer' content='no-referrer'><title>{e(destination.label)} · {e(title)} — POROTA TRADING RC6</title><style id='trader-terminal-tokens'>{CSS}</style></head><body>"
            "<a class='skip-link' href='#terminal-content'>Ir al contenido</a>"
            f"<aside class='sidebar' id='terminal-sidebar' aria-label='Menú principal'><div class='brand'>POROTA TRADING<small>RC6 · TRADER TERMINAL</small></div><nav class='primary-nav' id='porota-canonical-nav' aria-label='Destinos principales'>{links}</nav>"
            "<div class='sidebar-footer'>PAPER / SHADOW ONLY<br>PRODUCTION_PAPER / SIMULATION<br>Objetivo: real_orders_sent=0</div></aside>"
            "<div class='workspace'><header class='global-header' id='terminal-header'><div class='title-row'>"
            f"<strong>POROTA TRADING RC6 <span class='muted'>/ {e(destination.label)}</span></strong><span id='porota-paper-mode' aria-label='{e(mode_label)}'>{badge('PAPER / SHADOW ONLY')}<span class='sr-only'>{e(mode_label)}</span></span>"
            "<div class='header-actions'><button class='button menu-button' id='terminal-menu' aria-expanded='false' aria-controls='terminal-sidebar'>Menú</button>"
            "<button class='button' id='refresh-data'>Actualizar datos</button><button class='button' id='auto-refresh' aria-pressed='false'>Activar actualización</button></div></div>"
            f"<div class='status-strip' id='operational-strip'>{strip}</div></header><nav class='subnav' aria-label='Subvistas de {e(destination.label)}'>{tabs}</nav>"
            f"<main class='content' id='terminal-content' tabindex='-1' data-view='{destination.key}/{tab}'><div class='page-heading'><h1>{e(destination.label)} <span class='muted'>/ {e(title)}</span></h1><p>{e(destination.question)}</p></div>{warning}{content}</main>"
            "<footer class='footer'><span id='refresh-status' role='status' aria-live='polite'>Actualización manual · pausa durante interacción.</span><a href='#terminal-content'>Arriba</a></footer></div>"
            f"<script>{SCRIPT}</script></body></html>")
