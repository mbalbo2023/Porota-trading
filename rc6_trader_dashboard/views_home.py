"""Home is state and attention, with five-item previews instead of inventories."""
from .components import PAGE_REASONS, Field, badge, clock, e, metric, notice, number, fields, definition_list, unverified, value_html
from .projection import UNKNOWN, freshness


def preview(page, title, path, *, key="symbol"):
    def short_reason(row):
        reason = row.get("reason_summary") or row.get("reason") or row.get("exit_reason") or row.get("close_reason")
        if not reason or reason == UNKNOWN:
            return unverified("El registro no publica el motivo de esta observación.")
        value = str(reason)
        return e(value if len(value) <= 130 else value[:127] + "… (ver detalle)")
    def item(row):
        identity = value_html(row, Field(key, "Identidad de la vista previa"))
        state = badge(row.get("state") or row.get("status") or row.get("final"), reason=row.get("reason"))
        return f"<li><b>{identity}</b>{state}<span>{e(row.get('currency') or '')}</span><small>{short_reason(row)}</small></li>"
    rows = "".join(item(row) for row in page.rows[:5])
    empty = ("<li>Sin registros en este scope (0).</li>" if page.state == "AVAILABLE" else
             "<li>" + unverified(PAGE_REASONS.get(page.reason, page.reason or "La fuente no publicó registros para este alcance.")) + "</li>")
    return f"<section class='panel'><h2>{title}</h2><ul class='preview-list'>{rows or empty}</ul><a class='text-link' href='{path}'>Ver {title.lower()}</a></section>"


def attention(p, positions, daily_risk=None):
    alerts = []
    # Put safety and primary-source uncertainty before per-position detail so
    # the five-item preview cannot hide a current operational incident.
    if not p.shadow_fresh:
        alerts.append(("SHADOW sin corte comprometido fresco", "Evidencia SHADOW", "/sistema/evidencia"))
    iol_profile = getattr(p, "market_provider", "PPI") == "IOL_REST"
    market = p.store.query("SELECT state,checked_at FROM api_health WHERE component=? LIMIT 1",
                           ("IOL_REST_MARKETDATA" if iol_profile else "PPI_PRODUCTION_MARKETDATA",))
    if not market or market[0].get("state") != "VERDE" or freshness(market[0].get("checked_at"), p.now, 180) != "FRESH":
        alerts.append(("IOL REST sin observación de mercado vigente" if iol_profile else "PPI market data sin verificación fresca",
                       "IOL REST" if iol_profile else "PPI", "/sistema/integraciones"))
    if iol_profile and p.iol_authentication.get("state") != "VERDE":
        alerts.append((p.iol_authentication.get("reason") or "Autenticación IOL REST sin vigencia acreditada",
                       "IOL REST", "/sistema/integraciones"))
    if not iol_profile and p.runtime.get("ppi_auth") != "OK":
        alerts.append(("Autenticación PPI sin estado OK", "PPI", "/sistema/integraciones"))
    if p.runtime["heartbeat_freshness"] != "FRESH":
        alerts.append(("Heartbeat runtime sin verificación fresca", "Workers", "/sistema/workers"))
    # Attention must always see the first currency page. A user-controlled
    # table offset must never hide ARS admission failures from the home view.
    daily_risk = daily_risk if daily_risk is not None else p.risk(offset=0)
    if not daily_risk.rows:
        alerts.append(("Riesgo diario sin corte verificable", "Riesgo", "/riesgo/limites"))
    for row in daily_risk.rows:
        currency = row.get("currency") or "Moneda sin identificar"
        state = row.get("state") or UNKNOWN
        if state != "READY":
            detail = "falta una evaluación publicada" if state == UNKNOWN else str(state)
            alerts.append((f"Riesgo diario {currency} · {detail}", currency, "/riesgo/limites"))
        elif row.get("freshness") != "FRESH":
            alerts.append(("Riesgo diario sin corte fresco", currency, "/riesgo/limites"))
    for row in positions.rows:
        if row.get("freshness") != "FRESH":
            alerts.append(("Book de salida sin frescura verificada", row.get("symbol", UNKNOWN), "/riesgo/liquidez"))
        if row.get("exit_state") in {None, "BLOCKED", "STALE"}:
            alerts.append(("Revisar supervisión de salida", row.get("symbol", UNKNOWN), "/en-vivo/workers"))
    def item(title, scope, path):
        scope = "Identidad de posición" if scope == UNKNOWN else scope
        return f"<li><b>{e(title)}</b><span>{e(scope)}</span><a href='{path}'>Revisar {e(scope)}</a></li>"
    items = "".join(item(*alert) for alert in alerts[:5])
    return f"<section class='panel'><h2>Atención ahora</h2><ul class='preview-list'>{items or '<li>Sin alertas en la evidencia disponible. Falta un recibo de cobertura completa.</li>'}</ul></section>", len(alerts)


def render(p, destination, tab):
    from .datasets import shadow_rows
    counts = p.counts()
    positions = p.positions(limit=5)
    daily_risk = p.risk(offset=0)
    alerts, count = attention(p, positions, daily_risk)
    balances = p.balances()
    cards = []
    for row in balances.rows:
        currency = row["currency"]
        measured_at = clock(row.get("measured_at"))
        reserved = number(row.get("reserved"))
        cards.extend((metric("Patrimonio PAPER · " + currency, row.get("equity"), "Medición " + measured_at if measured_at != UNKNOWN else "La fila no incluye una hora de medición válida.", kind="money", currency=currency),
                      metric("Caja disponible · " + currency, row.get("cash"), "Reservada · " + reserved if reserved != UNKNOWN else "La fila no acredita el importe reservado.", kind="money", currency=currency),
                      metric("Exposición · " + currency, row.get("exposure"), "Consultá el corte de límites en Riesgo.", kind="money", currency=currency)))
    if not cards:
        cards.append(metric("Patrimonio / caja por moneda", None, balances.reason or "Ledger por moneda no publicado"))
    cards.extend((metric("Operaciones abiertas", counts.get("open_positions"), "paper_positions · OPEN", kind="number"),
                  metric("HOT observados", counts.get("HOT"), "Alta prioridad de análisis · HOT ≠ BUY", kind="number"),
                  metric("Señales candidatas", counts.get("SIGNAL"), "Signal no implica economics/risk PASS", kind="number"),
                  metric("Alertas para revisar", count, "Hasta cinco alertas priorizadas", kind="number"),
                  metric("Tiempo a cierre / EOD", None, reason="Falta un reloj de cierre publicado para la rueda.")))
    performance = p.performance(today=True)
    result_cards = "".join(metric("PnL de cierres PAPER hoy · " + r["currency"] + " · " + r["strategy"], r.get("net_pnl"),
                                f"Cerradas: {r.get('sample_size')} · Costos: {r.get('costs')}", kind="money", currency=r["currency"], result=True) for r in performance.rows)
    risk = "".join(definition_list(r, fields("currency|Moneda;state|Estado|status;daily_pnl|PnL diario|money;loss_budget|Presupuesto|money;evaluated_at|Evaluado|time")) for r in daily_risk.rows)
    return ("<div class='metric-grid'>" + "".join(cards) + "</div>" + alerts +
            "<div class='split-panels'>" + preview(positions, "Posiciones abiertas", "/en-vivo/posiciones") +
            preview(shadow_rows(p, "opportunities"), "Oportunidades HOT / WARM", "/en-vivo/oportunidades") + "</div>" +
            "<div class='metric-grid'>" + (result_cards or metric("PnL de cierres PAPER hoy por moneda/estrategia", None, performance.reason or "Sin cierres medidos; las posiciones abiertas se muestran por separado")) + "</div>" +
            "<section class='panel'><h2>Riesgo resumido</h2>" + notice("PnL diario de riesgo = cambio del patrimonio PAPER desde la base del día; incluye valuación no realizada cuando hay libros vigentes. PnL de cierres muestra solo posiciones cerradas.") +
            (risk or "<div class='notice' role='note'>" + unverified("DailyRisk no publicó un corte de hoy.") + "</div>") +
            "<a class='text-link' href='/riesgo'>Abrir riesgo</a></section>")
