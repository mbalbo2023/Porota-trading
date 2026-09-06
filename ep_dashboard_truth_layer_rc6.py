"""RC6 dashboard truth layer.

Purpose: make every dashboard page distinguish three different concepts that
must never be conflated:

1. process/service liveness (a watchdog may be RUNNING 24x7),
2. market session activity (MARKET_OPEN vs closed/waiting), and
3. trading policy (for example the economic gate may be BINDING).

This module is read-only.  It does not import an order client, does not write the
PAPER database and does not alter the trading engine.  It only changes visual
semantics and exposes an authenticated read-only truth endpoint.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import Header, Query, Request
from fastapi.responses import JSONResponse

import bg_paper_dashboard as bg

_installed = False
_original_mode_banner = None
_original_exit_panel = None
_original_economic_panel = None
_original_card = None

MARKET_OPEN = "MARKET_OPEN"
ERROR_STATES = {"ERROR", "FAILED", "ROJO", "DEGRADED", "STALE", "UNKNOWN"}


def runtime_truth() -> dict:
    """Return one authoritative, compact dashboard context from observer_state."""
    row = (bg._rows("SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at,detail "
                    "FROM observer_state WHERE id=1") or [{}])[0]
    open_count = 0
    if bg._table("paper_positions"):
        open_count = int((bg._rows("SELECT COUNT(*) n FROM paper_positions WHERE status='OPEN'") or [{"n": 0}])[0]["n"] or 0)
    mode = str(row.get("mode") or bg._effective_mode() or "UNKNOWN").upper()
    process = str(row.get("process_state") or "UNKNOWN").upper()
    session = str(row.get("session_state") or "UNKNOWN").upper()
    ppi_auth = str(row.get("ppi_auth") or "UNKNOWN").upper()
    real_orders = int(row.get("real_orders_sent") or 0)
    return {
        "source": "observer_state",
        "mode": mode,
        "execution": "SIMULATED" if mode == "PRODUCTION_PAPER" else "UNKNOWN",
        "process_state": process,
        "session_state": session,
        "market_open": session == MARKET_OPEN,
        "ppi_auth": ppi_auth,
        "real_orders_sent": real_orders,
        "open_positions": open_count,
        "heartbeat_at": row.get("heartbeat_at"),
        "detail": row.get("detail"),
    }


def _fresh_internal_state(row: dict, default="NOT_STARTED", max_age_seconds=20) -> str:
    state = str(row.get("state") or default).upper()
    try:
        stamp = bg.aware_datetime(row.get("heartbeat_at")).astimezone(bg.TZ)
        age = (datetime.now(bg.TZ) - stamp).total_seconds()
        if not 0 <= age <= max_age_seconds:
            return "STALE"
    except (ValueError, TypeError, AttributeError):
        return "UNKNOWN" if row else default
    return state


def market_sensitive_display_state(raw_state: str, *, session_state: str,
                                   open_positions: int = 0) -> str:
    """Derive visual state without hiding real faults.

    RUNNING outside market is process liveness, not market activity.  Errors and
    stale heartbeats remain visible and are never cosmetically downgraded.
    """
    raw = str(raw_state or "UNKNOWN").upper()
    session = str(session_state or "UNKNOWN").upper()
    if raw in ERROR_STATES:
        return raw
    if session != MARKET_OPEN:
        return "MONITOREO_PASIVO" if open_positions else "EN_ESPERA_MERCADO_CERRADO"
    return raw


def policy_description(policy: str) -> str:
    key = str(policy or "UNKNOWN").upper()
    if key == "BINDING":
        return "BINDING = una señal PAPER que falla la economía queda bloqueada; no es el modo global del sistema."
    if key in {"SHADOW", "OBSERVATION_ONLY", "OBSERVE"}:
        return f"{key} = sólo observación; no bloquea por esta política."
    return f"{key} = política no reconocida; requiere revisión."


def _truth_banner() -> str:
    truth = runtime_truth()
    session_label = "MERCADO ABIERTO" if truth["market_open"] else "MERCADO CERRADO / SIN EJECUCIÓN DE MERCADO"
    css = "paper-notice" if truth["real_orders_sent"] == 0 else "paper-warning"
    return (
        f"<div id='porota-runtime-truth' class='{css}'>"
        f"<b>Verdad runtime:</b> modo {bg._e(truth['mode'])} · ejecución {bg._e(truth['execution'])} · "
        f"proceso {bg._e(truth['process_state'])} · sesión {bg._e(truth['session_state'])} ({session_label}) · "
        f"PPI auth {bg._e(truth['ppi_auth'])} · posiciones abiertas {truth['open_positions']} · "
        f"órdenes reales observadas <b>{truth['real_orders_sent']}</b>. "
        f"<span class='paper-muted'>Fuente: observer_state · pulso {bg._local_time(truth['heartbeat_at'])}.</span>"
        "</div>"
    )


def _patched_mode_banner():
    return _original_mode_banner() + _truth_banner()


def _patched_exit_supervision_panel():
    data = bg.snapshot()
    truth = runtime_truth()
    health = data["exit_supervisor"]
    reader = data["exit_reader"]
    raw_supervisor = _fresh_internal_state(health)
    raw_reader = _fresh_internal_state(reader)
    open_count = len(data["open"])
    supervisor_view = market_sensitive_display_state(
        raw_supervisor, session_state=truth["session_state"], open_positions=open_count)
    reader_view = market_sensitive_display_state(
        raw_reader, session_state=truth["session_state"], open_positions=open_count)

    intents = {r["paper_id"]: r for r in data["exit_intents"]}
    rows = []
    for p in data["open"]:
        r = intents.get(p["paper_id"], {})
        rows.append(
            f"<tr><td>{bg._e(p['symbol'])} · {bg._e(p.get('currency','ARS'))}</td>"
            f"<td>{bg._e(r.get('state','AWAITING_SUPERVISION'))}</td>"
            f"<td>{bg._e(r.get('cause') or '—')}</td>"
            f"<td>{bg._local_time(r.get('due_at'))}</td>"
            f"<td>{bg._e(r.get('blocked_reason','Pendiente de revisión'))}</td>"
            f"<td>{bg._local_time(r.get('supervised_at'))}</td></tr>"
        )

    if truth["market_open"]:
        explanation = (
            "La rueda está abierta. El supervisor puede evaluar salidas PAPER, pero un fill simulado "
            "sigue exigiendo libro fresco, identidad, liquidez y sesión válida."
        )
    elif open_count:
        explanation = (
            "La rueda está cerrada. Hay posiciones PAPER abiertas: el proceso conserva heartbeat y estado, "
            "pero toda ejecución de salida queda bloqueada por sesión hasta una ventana habilitada."
        )
    else:
        explanation = (
            "La rueda está cerrada y no hay posiciones de compraventa abiertas. El watchdog puede seguir vivo "
            "internamente, pero el estado operativo mostrado es EN_ESPERA_MERCADO_CERRADO: no evalúa ni ejecuta ventas."
        )

    return (
        "<div class='paper-card'><h2>Supervisión de salidas</h2>"
        f"<p><b>Estado operativo:</b> {bg._status(supervisor_view)} · sesión {bg._status(truth['session_state'])}.</p>"
        f"<p>Supervisor: <b>{bg._e(supervisor_view)}</b> "
        f"<span class='paper-muted'>(estado interno {bg._e(raw_supervisor)} · pulso {bg._local_time(health.get('heartbeat_at'))})</span></p>"
        f"<p>Lector de salidas: <b>{bg._e(reader_view)}</b> "
        f"<span class='paper-muted'>(estado interno {bg._e(raw_reader)} · pulso {bg._local_time(reader.get('heartbeat_at'))})</span></p>"
        f"<div class='paper-notice'>{bg._e(explanation)}</div>"
        "<p>Una salida decidida no equivale a una venta. Sin libro fresco, liquidez o sesión habilitada, sigue pendiente. "
        "No se ejecuta con precios viejos ni fuera de la ventana PAPER. Un heartbeat STALE/ERROR nunca se oculta.</p>"
        "<table class='paper-table'><tr><th>Instrumento</th><th>Estado de salida</th><th>Causa</th>"
        "<th>Decidida</th><th>Detalle</th><th>Supervisada</th></tr>"
        + ("".join(rows) or "<tr><td colspan='6'>Sin posiciones de compraventa abiertas.</td></tr>")
        + "</table></div>"
    )


def _patched_economic_panel():
    metrics = bg._economic_shadow_metrics()
    truth = runtime_truth()
    policy = str(bg.PAPER_ECONOMIC_GATE_MODE or "UNKNOWN").upper()
    policy_ok = policy == "BINDING"
    cards = "".join((
        bg._card("Evaluaciones económicas", metrics["evaluated"], "Señales BUY PAPER evaluadas hoy", "gray"),
        bg._card("Aprueban economía", metrics["passed"], "Superan el portón matemático vigente", "green"),
        bg._card("Fallan economía", metrics["failed"], "Con BINDING deben quedar bloqueadas", "red" if policy_ok and metrics["failed"] else "gray"),
        bg._card("Abren pese al fallo", metrics["opened_with_failure"], "Invariante: cero cuando la política es BINDING", "red" if metrics["opened_with_failure"] else "green"),
    ))
    closed_note = (" La sesión está cerrada: esta política permanece configurada, pero no significa que el motor esté operando ahora."
                   if not truth["market_open"] else "")
    return (
        "<div class='paper-card'><h2>Portón económico de aperturas PAPER</h2>"
        f"<div class='paper-notice'><b>Política del portón: {bg._e(policy)}.</b> "
        f"{bg._e(policy_description(policy))}{bg._e(closed_note)}</div>"
        "<div class='paper-warning'><b>No es un “modo BINDING” del sistema.</b> "
        f"El modo global observado es <b>{bg._e(truth['mode'])}</b>, ejecución <b>{bg._e(truth['execution'])}</b>, "
        f"sesión <b>{bg._e(truth['session_state'])}</b>. El portón sólo decide si una señal simulada puede abrir "
        "cuando no cubre comisión, derechos, spread, deslizamiento y reward/risk neto.</div>"
        f"<div class='paper-grid'>{cards}</div></div>"
    )


def _patched_card(title, value, detail, state="gray", value_class=""):
    """Make market-sensitive worker cards session-aware without hiding faults."""
    if str(title) == "Scanner":
        truth = runtime_truth()
        raw = str(value or "UNKNOWN").upper()
        view = market_sensitive_display_state(raw, session_state=truth["session_state"],
                                              open_positions=truth["open_positions"])
        if view != raw:
            value = view
            detail = f"{detail} · estado interno {raw}; sin evaluación/apertura de mercado durante sesión cerrada"
            state = "gray"
    return _original_card(title, value, detail, state, value_class)


def install(app, check_auth):
    global _installed, _original_mode_banner, _original_exit_panel, _original_economic_panel, _original_card
    if _installed:
        return
    _installed = True

    _original_mode_banner = bg.mode_banner
    _original_exit_panel = bg._exit_supervision_panel
    _original_economic_panel = bg._economic_shadow_panel
    _original_card = bg._card

    bg.mode_banner = _patched_mode_banner
    bg._exit_supervision_panel = _patched_exit_supervision_panel
    bg._economic_shadow_panel = _patched_economic_panel
    bg._card = _patched_card

    @app.get("/api/dashboard/truth")
    def dashboard_truth(request: Request, token: str = Query(default=""),
                        authorization: str | None = Header(default=None)):
        bg._authorize(check_auth, request, token, authorization)
        truth = runtime_truth()
        truth["invariants"] = {
            "real_orders_zero": truth["real_orders_sent"] == 0,
            "market_actions_currently_possible": truth["market_open"],
            "policy_binding_is_global_mode": False,
        }
        return JSONResponse(truth)
