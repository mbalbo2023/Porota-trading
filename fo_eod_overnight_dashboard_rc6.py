"""Read-only dashboard fragment for Trading -> Estrategias -> EOD / Overnight.

This module is deliberately observational. It never changes the EOD policy,
never promotes a strategy, never writes to SQLite and never calls order routes.
The operational policy remains CURRENT_EOD until a separate, explicit paper
promotion is approved.
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from cg_paper_workspace import database_path, checked_path, identity_from_connection

TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
STATE_PATH = Path(os.getenv("EOD_OVERNIGHT_READINESS_PATH", "data/eod_overnight_readiness.json"))


def _e(value) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _dt(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _conn():
    path = str(database_path())
    c = sqlite3.connect(checked_path(path).as_uri() + "?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    identity_from_connection(c)
    c.execute("PRAGMA query_only=ON")
    return c


def _readiness_state() -> dict:
    """Optional evaluator output. Invalid/missing state always fails closed."""
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("readiness state must be an object")
        return payload
    except Exception:
        return {}


def _true_overnight_snapshot() -> dict:
    """Recompute the true-overnight replay from the live PAPER DB, read-only."""
    result = {
        "state": "UNAVAILABLE",
        "total": 0,
        "covered": 0,
        "missing": 0,
        "stop_first": 0,
        "target_first": 0,
        "neither": 0,
    }
    try:
        with closing(_conn()) as c:
            before = c.total_changes
            positions = c.execute(
                """
                SELECT paper_id,symbol,asset_class,settlement,currency,market,
                       stop_price,target_price,closed_at
                  FROM paper_positions
                 WHERE status='CLOSED' AND close_reason='EOD_PAPER'
                   AND closed_at IS NOT NULL
                 ORDER BY julianday(closed_at), paper_id
                """
            ).fetchall()
            result["total"] = len(positions)

            for p in positions:
                closed = _dt(p["closed_at"])
                close_date = closed.astimezone(TZ).date()
                end = closed + timedelta(days=12)
                rows = c.execute(
                    """
                    SELECT observed_at,bid
                      FROM market_snapshots
                     WHERE symbol=? AND asset_class=? AND settlement=?
                       AND currency=? AND market=?
                       AND julianday(observed_at)>julianday(?)
                       AND julianday(observed_at)<=julianday(?)
                     ORDER BY julianday(observed_at),id
                    """,
                    (
                        p["symbol"], p["asset_class"], p["settlement"],
                        p["currency"], p["market"], p["closed_at"], end.isoformat(),
                    ),
                ).fetchall()

                valid = []
                for r in rows:
                    try:
                        at = _dt(r["observed_at"])
                        bid = Decimal(str(r["bid"]))
                        if at.astimezone(TZ).date() > close_date and bid > 0:
                            valid.append((at, bid))
                    except Exception:
                        continue

                if valid:
                    result["covered"] += 1
                else:
                    result["missing"] += 1
                    continue

                try:
                    stop = Decimal(str(p["stop_price"]))
                    target = Decimal(str(p["target_price"]))
                except Exception:
                    result["neither"] += 1
                    continue

                first = None
                for _, bid in valid:
                    if bid <= stop:
                        first = "STOP"
                        break
                    if bid >= target:
                        first = "TARGET"
                        break
                if first == "STOP":
                    result["stop_first"] += 1
                elif first == "TARGET":
                    result["target_first"] += 1
                else:
                    result["neither"] += 1

            if c.total_changes != before:
                raise RuntimeError("read-only evaluator unexpectedly changed DB state")
            result["state"] = "READY" if result["total"] else "NO_SAMPLE"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _pill(label: str, state: str, detail: str) -> str:
    key = str(state or "PENDING").upper()
    css = "s-verde" if key in {"GREEN", "READY", "OK"} else "s-rojo" if key in {"RED", "BLOCKED", "ERROR"} else "s-amarillo"
    return (
        "<tr><td><b>" + _e(label) + "</b></td>"
        "<td><span class='paper-status " + css + "'>" + _e(key) + "</span></td>"
        "<td>" + _e(detail) + "</td></tr>"
    )


def render() -> str:
    replay = _true_overnight_snapshot()
    persisted = _readiness_state()

    # Promotion is intentionally fail-closed. Merely having replay coverage can
    # never activate overnight holding or turn the global gate green.
    all_green = persisted.get("all_gates_green") is True
    persisted_readiness = str(persisted.get("readiness") or "").upper()
    ready_for_promotion = all_green and persisted_readiness == "READY_FOR_PROMOTION"
    readiness = "READY FOR PROMOTION" if ready_for_promotion else "EVALUATING"
    readiness_css = "s-verde" if ready_for_promotion else "s-amarillo"

    total = int(replay.get("total") or 0)
    covered = int(replay.get("covered") or 0)
    missing = int(replay.get("missing") or 0)
    coverage_state = "GREEN" if total > 0 and covered == total else "PENDING"
    replay_state = "GREEN" if replay.get("state") == "READY" and covered > 0 else "PENDING"

    configured_gates = persisted.get("gates") if isinstance(persisted.get("gates"), dict) else {}
    gate_defaults = {
        "intraday_path": ("PENDING", "Cobertura intradiaria todavía no certificada para promoción."),
        "close_next_open_gap": (coverage_state, f"Cobertura next-session {covered}/{total}; faltantes {missing}." if total else "Sin muestra EOD suficiente."),
        "mae_mfe": ("PENDING", "MAE/MFE pendiente de certificación económica por segmento."),
        "stop_target_chronology": (replay_state, f"STOP primero {replay.get('stop_first',0)} · TARGET primero {replay.get('target_first',0)} · ninguno {replay.get('neither',0)}."),
        "full_transaction_costs": ("PENDING", "Costos completos pendientes de gate certificado."),
        "strategy_instrument_segmentation": ("PENDING", "Segmentación estrategia/instrumento pendiente de suficiencia."),
        "sample_sufficiency": ("PENDING", "No se inventa umbral: debe provenir del evaluador certificado."),
        "stability_risk_validation": ("PENDING", "Validación de estabilidad/riesgo todavía requerida."),
    }
    labels = {
        "intraday_path": "Intraday path",
        "close_next_open_gap": "Close → next open gap",
        "mae_mfe": "MAE / MFE",
        "stop_target_chronology": "Cronología STOP / TARGET",
        "full_transaction_costs": "Costos completos",
        "strategy_instrument_segmentation": "Segmentación estrategia / instrumento",
        "sample_sufficiency": "Suficiencia de muestra",
        "stability_risk_validation": "Estabilidad / riesgo",
    }

    rows = []
    for key, (default_state, default_detail) in gate_defaults.items():
        item = configured_gates.get(key) if isinstance(configured_gates.get(key), dict) else {}
        state = str(item.get("state") or default_state).upper()
        detail = str(item.get("detail") or default_detail)
        # A persisted GREEN is only informational until the aggregate signed
        # evaluator state says every gate is green. No auto-promotion exists.
        rows.append(_pill(labels[key], state, detail))

    source_note = "evaluator persistido + replay live" if persisted else "replay live; gates restantes fail-closed"
    error_note = ""
    if replay.get("state") == "UNAVAILABLE":
        error_note = "<div class='paper-warning'>No se pudo leer el replay EOD en este momento. La estrategia permanece en evaluación y CURRENT_EOD no cambia.</div>"

    return f"""
    <div class='paper-card'>
      <h2>🌙 EOD / Overnight</h2>
      <p><span class='paper-status {readiness_css}'>{_e(readiness)}</span>
      <span class='paper-muted'> · evaluador SHADOW · fuente: {_e(source_note)}</span></p>
      <div class='paper-grid'>
        <div class='paper-card card-yellow'><b>Política activa</b><br><b class='metric'>CURRENT_EOD</b><br><span class='paper-muted'>El cierre EOD vigente no se modifica.</span></div>
        <div class='paper-card card-gray'><b>Auto-promoción</b><br><b class='metric'>OFF</b><br><span class='paper-muted'>Nunca se activa overnight por sí solo.</span></div>
        <div class='paper-card card-gray'><b>Objetivo eventual</b><br><b class='metric'>ACTIVE_PAPER</b><br><span class='paper-muted'>Solo después de todos los gates verdes.</span></div>
        <div class='paper-card'><b>Replay true overnight</b><br><b class='metric'>{covered}/{total}</b><br><span class='paper-muted'>posiciones EOD con próxima sesión; faltan {missing}</span></div>
      </div>
      {error_note}
      <div class='paper-notice'><b>Qué significa:</b> esta estrategia está en observación y acumula evidencia con la ingesta histórica. Ver datos o un gate verde no habilita operatoria. La promoción es un paso separado y permanece PAPER.</div>
      <table class='paper-table'>
        <tr><th>Evidencia requerida</th><th>Estado</th><th>Detalle</th></tr>
        {''.join(rows)}
      </table>
    </div>
    """
