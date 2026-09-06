"""RC6 dashboard truth layer.

Distinguishes process/service liveness, market-session activity and trading
policy.  Read-only presentation: no broker/order client and no DB writes.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import Header, Query, Request
from fastapi.responses import JSONResponse

import bg_paper_dashboard as bg
from eo_dashboard_truth_semantics_rc6 import (
    MARKET_OPEN,
    market_sensitive_display_state,
    policy_description,
    session_permits_market_activity,
)

_installed = False
_original_mode_banner = None
_original_exit_panel = None
_original_economic_panel = None
_original_card = None


def runtime_truth() -> dict:
    """Return one authoritative dashboard context from observer_state."""
    row = (bg._rows(
        "SELECT mode,process_state,session_state,ppi_auth,real_orders_sent,heartbeat_at,detail "
        "FROM observer_state WHERE id=1"
    ) or [{}])[0]
    open_count = 0
    if bg._table("paper_positions"):
        open_count = int((bg._rows(
            "SELECT COUNT(*) n FROM paper_positions WHERE status='OPEN'"
        ) or [{"n": 0}])[0]["n"] or 0)
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
        "market_open": session_permits_market_activity(session),
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


def _truth_banner() -> str:
    truth = runtime_truth()
    session_label = (
        "MERCADO ABIERTO"
        if truth["market_open"]
        else "MERCADO CERRADO / SIN EJECUCIÓN DE MERCADO"
    )
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
        raw_supervisor,
        session_state=truth["session_state"],
        open_positions=open_count,
    )
    reader_view = market_sensitive_display_state(
        raw_reader,
        session_state=truth["session_state"],
        open_positions=open_count,
    )

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
    policy_shadow = policy == "SHADOW"
    cards = "".join((
        bg._card("Evaluaciones económicas", metrics["evaluated"], "Señales BUY PAPER evaluadas hoy", "gray"),
        bg._card("Would allow", metrics["passed"], "SHADOW habría permitido estas señales; no es autorización real", "green"),
        bg._card("Would block", metrics["failed"], "SHADOW habría bloqueado, pero PAPER sigue para aprender", "yellow" if metrics["failed"] else "gray"),
        bg._card("PAPER pese a would-block", metrics["opened_with_failure"], "Esperado en SHADOW: conserva el contrafactual para aprender", "green" if policy_shadow else "yellow"),
    ))
    closed_note = (
        " La sesión está cerrada: esta política permanece configurada, pero no significa que el motor esté operando ahora."
        if not truth["market_open"] else ""
    )
    return (
        "<div class='paper-card'><h2>Portón económico de aperturas PAPER</h2>"
        f"<div class='paper-notice'><b>Política del portón: {bg._e(policy)}.</b> "
        f"{bg._e(policy_description(policy))}{bg._e(closed_note)}</div>"
        "<div class='paper-warning'><b>Etapa de aprendizaje SHADOW.</b> "
        f"El modo global observado es <b>{bg._e(truth['mode'])}</b>, ejecución <b>{bg._e(truth['execution'])}</b>, "
        f"sesión <b>{bg._e(truth['session_state'])}</b>. En SHADOW el portón calcula costos, spread, slippage y reward/risk, "
        "pero NO veta PAPER: registra qué habría bloqueado y luego se contrasta contra el resultado realizado.</div>"
        f"<div class='paper-grid'>{cards}</div></div>"
    )


def _patched_card(title, value, detail, state="gray", value_class=""):
    """Make the market-sensitive Scanner card session-aware without hiding faults."""
    if str(title) == "Scanner":
        truth = runtime_truth()
        raw = str(value or "UNKNOWN").upper()
        view = market_sensitive_display_state(
            raw,
            session_state=truth["session_state"],
            open_positions=truth["open_positions"],
        )
        if view != raw:
            value = view
            detail = (
                f"{detail} · estado interno {raw}; sin evaluación/apertura de mercado "
                "durante sesión cerrada"
            )
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
    def dashboard_truth(
        request: Request,
        token: str = Query(default=""),
        authorization: str | None = Header(default=None),
    ):
        bg._authorize(check_auth, request, token, authorization)
        truth = runtime_truth()
        truth["invariants"] = {
            "real_orders_zero": truth["real_orders_sent"] == 0,
            "session_permits_market_activity": truth["market_open"],
            "session_open_is_trade_authorization": False,
            "policy_binding_is_global_mode": False,
        }
        return JSONResponse(truth)
