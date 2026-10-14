"""Home is state and attention, with five-item previews instead of inventories."""
from .components import badge, e, metric, notice, fields, definition_list
from .projection import UNKNOWN


def preview(page, title, path, *, key="symbol"):
    rows = "".join(f"<li><b>{e(row.get(key) or UNKNOWN)}</b>{badge(row.get('state') or row.get('status') or row.get('final'))}<span>{e(row.get('currency') or '')}</span><small>{e(row.get('reason') or row.get('exit_reason') or row.get('close_reason') or row.get('as_of') or UNKNOWN)}</small></li>" for row in page.rows[:5])
    return f"<section class='panel'><h2>{title}</h2><ul class='preview-list'>{rows or '<li>NO_VERIFICADO · sin registros publicados en este scope.</li>'}</ul><a class='text-link' href='{path}'>Ver {title.lower()}</a></section>"


def attention(p, positions):
    alerts = []
    for row in positions.rows:
        if row.get("freshness") != "FRESH":
            alerts.append(("Book de salida sin frescura verificada", row.get("symbol", UNKNOWN), "/riesgo/liquidez"))
        if row.get("exit_state") in {None, "BLOCKED", "STALE"}:
            alerts.append(("Revisar supervisión de salida", row.get("symbol", UNKNOWN), "/en-vivo/workers"))
    for row in p.risk().rows:
        if row.get("state") in {"HARD_STOP", "SOFT_STOP", "STALE", "BLOCKED"}:
            alerts.append(("Revisar límite de riesgo diario", row.get("currency", UNKNOWN), "/riesgo/limites"))
    if p.runtime["heartbeat_freshness"] != "FRESH":
        alerts.append(("Heartbeat runtime sin verificación fresca", p.runtime["heartbeat_freshness"], "/sistema/workers"))
    items = "".join(f"<li><b>{e(title)}</b><span>{e(scope)}</span><a href='{path}'>Revisar {e(scope)}</a></li>" for title, scope, path in alerts[:5])
    return f"<section class='panel'><h2>Atención ahora</h2><ul class='preview-list'>{items or '<li>Sin alertas derivadas de la evidencia disponible; coverage global NO_VERIFICADO.</li>'}</ul></section>", len(alerts)


def render(p, destination, tab):
    from .datasets import shadow_rows
    counts = p.counts()
    positions = p.positions(limit=5)
    alerts, count = attention(p, positions)
    balances = p.balances()
    cards = []
    for row in balances.rows:
        currency = row["currency"]
        cards.extend((metric("Patrimonio PAPER · " + currency, row.get("equity"), "as_of " + str(row.get("measured_at") or UNKNOWN), kind="money", currency=currency),
                      metric("Caja disponible · " + currency, row.get("cash"), "Reservada: " + (str(row["reserved"]) if row.get("reserved") is not None else UNKNOWN), kind="money", currency=currency),
                      metric("Exposición · " + currency, row.get("exposure"), "Límite: NO_VERIFICADO", kind="money", currency=currency)))
    if not cards:
        cards.append(metric("Patrimonio / caja por moneda", None, "Ledger por moneda no publicado"))
    cards.extend((metric("Operaciones abiertas", counts.get("open_positions"), "paper_positions · OPEN", kind="number"),
                  metric("HOT observados", counts.get("HOT"), "Alta prioridad de análisis · HOT ≠ BUY", kind="number"),
                  metric("Señales candidatas", counts.get("SIGNAL"), "Signal no implica economics/risk PASS", kind="number"),
                  metric("Alertas para revisar", count, "Hasta cinco alertas priorizadas", kind="number"),
                  metric("Tiempo a cierre / EOD", None, "Requiere reloj de cierre publicado")))
    performance = p.performance(today=True)
    result_cards = "".join(metric("PnL neto hoy · " + r["currency"] + " · " + r["strategy"], r.get("net_pnl"),
                                f"Cerradas: {r.get('sample_size')} · Costos: {r.get('costs')}", kind="money", currency=r["currency"]) for r in performance.rows)
    risk = "".join(definition_list(r, fields("currency|Moneda;state|Estado|status;daily_pnl|PnL diario|money;loss_budget|Presupuesto|money;evaluated_at|Evaluado|time")) for r in p.risk().rows)
    return ("<div class='metric-grid'>" + "".join(cards) + "</div>" + alerts +
            "<div class='split-panels'>" + preview(positions, "Posiciones abiertas", "/en-vivo/posiciones") +
            preview(shadow_rows(p, "opportunities"), "Oportunidades HOT / WARM", "/en-vivo/oportunidades") + "</div>" +
            "<div class='metric-grid'>" + (result_cards or metric("PnL neto hoy por moneda/estrategia", None, "Sin cierres medidos en este scope")) + "</div>" +
            "<section class='panel'><h2>Riesgo resumido</h2>" + (risk or notice("NO_VERIFICADO · DailyRisk no publicó un corte de hoy.")) + "<a class='text-link' href='/riesgo'>Abrir riesgo</a></section>")
