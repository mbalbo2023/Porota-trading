"""Small pure renderers. All untrusted strings are escaped."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime
import hashlib
from html import escape
import json
from urllib.parse import urlencode
import re

from .projection import UNKNOWN, TZ


def e(value):
    return escape(str(value), quote=True)


def lookup(row, key):
    value = row
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def sanitized(value, depth=0):
    if depth > 8:
        return "DETAIL_DEPTH_LIMIT"
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if re.search(r"(?i)(token|secret|password|passwd|cookie|authorization|api.?key|account.?id|account.?number|cbu|cuit)", str(k)) else sanitized(v, depth + 1) for k, v in list(value.items())[:128]}
    if isinstance(value, (list, tuple)):
        return [sanitized(v, depth + 1) for v in value[:100]]
    if isinstance(value, str) and re.search(r"(?i)\b(authorization|bearer|token|api[_-]?key|secret|password|passwd|cookie|account[_-]?(?:id|number)|cbu|cuit)\b", value):
        return "[REDACTED] sensitive detail"
    return value


def number(value, decimals=2):
    if value is None or isinstance(value, bool):
        return UNKNOWN
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            return UNKNOWN
        return f"{result:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except (InvalidOperation, ValueError):
        return UNKNOWN


def clock(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return UNKNOWN
        return stamp.astimezone(TZ).strftime("%d/%m %H:%M:%S ART")
    except (TypeError, ValueError):
        return UNKNOWN


def badge(value):
    text = UNKNOWN if value is None or value == "" else str(value)
    key = text.upper()
    if key == "HOT":
        tone, icon = "hot", "◆"
    elif key == "WARM":
        tone, icon = "warm", "◇"
    elif key == "DISCOVERY":
        tone, icon = "discovery", "⌕"
    elif key in {"PASS", "HEALTHY", "FRESH", "RUNNING", "OK"}:
        tone, icon = "pass", "✓"
    elif key in {"FAIL", "BLOCKED", "STALE", "ERROR", "HARD_STOP", "SOFT_STOP"}:
        tone, icon = "blocked", "!"
    else:
        tone, icon = "unknown", "·"
    return f"<span class='badge badge-{tone}' data-status='{e(key)}'><span aria-hidden='true'>{icon}</span> {e(text)}</span>"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"


def fields(spec):
    return tuple(Field(*item.split("|")) for item in spec.split(";") if item)


RESULT_KEYS = {"net_pnl", "gross_pnl", "unrealized_pnl", "realized_pnl", "daily_pnl",
               "expectancy", "avg_win", "avg_loss"}


def result_html(value, rendered):
    """Color only a verified signed financial result, retaining its visible sign."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return rendered
    except (InvalidOperation, ValueError, TypeError):
        return rendered
    tone = "positive" if amount > 0 else "negative" if amount < 0 else "neutral"
    label = {"positive": "Resultado positivo", "negative": "Resultado negativo",
             "neutral": "Resultado neutro"}[tone]
    return (f"<span class='result-{tone}'><span class='sr-only'>{label}: </span>"
            f"{rendered}</span>")


def value_html(row, field):
    value = lookup(row, field.key)
    if field.kind == "status":
        return badge(value)
    if value is None or value == "":
        return "<span class='muted'>NO_VERIFICADO</span>"
    if field.kind == "time":
        return e(clock(value))
    if field.kind == "money":
        rendered = e(number(value)) + " <span class='currency'>" + e(row.get("currency") or "MONEDA_NO_VERIFICADA") + "</span>"
        return result_html(value, rendered) if field.key in RESULT_KEYS else rendered
    if field.kind in {"number", "percent"}:
        return e(number(value)) + (" %" if field.kind == "percent" else "")
    if field.kind == "score":
        return e(number(value)) + " <small class='muted'>NO PROBABILIDAD</small>"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (list, dict)):
        return e(json.dumps(sanitized(value), ensure_ascii=False, sort_keys=True, default=str)[:4096])
    return e(str(sanitized(value))[:4096])


IDENTITY = fields("symbol|Ticker;family|Familia;market|Mercado;currency|Moneda;settlement|Settlement")
EVIDENCE = fields("source|Fuente;as_of|Reloj de la fuente|time;freshness|Freshness|status;reason|Motivo;entry_authority|Autoridad de entrada")


def definition_list(row, columns):
    return "<dl class='facts'>" + "".join(f"<div><dt>{e(f.label)}</dt><dd>{value_html(row, f)}</dd></div>" for f in columns) + "</dl>"


def detail(row, columns, row_id, index, *, label=None):
    label = label or f"Detalle fila {index} · {row.get('symbol') or row.get('worker') or row.get('component') or row.get('currency') or 'registro'}"
    dialog_id = f"{row_id}-dialog"
    return (f"<button class='button detail-trigger' type='button' data-detail-dialog='{dialog_id}' "
            f"aria-haspopup='dialog' aria-controls='{dialog_id}'>{e(label)}</button>"
            f"<dialog id='{dialog_id}' class='row-dialog' aria-labelledby='{dialog_id}-title'>"
            f"<div class='dialog-heading'><h3 id='{dialog_id}-title'>{e(label)}</h3>"
            "<form method='dialog'><button class='button' type='submit'>Cerrar detalle</button></form></div>"
            f"<div class='dialog-body'>{definition_list(row, columns)}"
            + (f"<a class='text-link' href='/instrumentos/ficha?{e(urlencode({'identity': row['identity']}))}'>Abrir ficha de {e(row.get('symbol'))}</a>" if row.get("identity") else "")
            + "</div></dialog>")


def pager(page, path, filters, table_id):
    params = {k: v for k, v in filters.items() if v and k not in {"token", "offset", "limit"}}
    def link(offset, text):
        return f"<a class='button' aria-controls='{table_id}' href='{e(path)}?{e(urlencode({**params, 'offset': offset}))}'>{text}</a>"
    visible = len(page.rows)
    total = number(page.total, 0) if page.total is not None else UNKNOWN
    range_text = f"{page.offset + 1}–{page.offset + visible}" if visible else "0"
    more = link(page.offset + 10, "Mostrar 10 más") if page.total is not None and page.offset + visible < page.total else ""
    less = link(max(0, page.offset - 10), "Mostrar menos") if page.offset else ""
    return f"<nav class='pager' aria-label='Paginación de {e(table_id)}'><span>{range_text} de {total} · {visible} filas</span>{less}{more}</nav>"


def table(page, title, columns, extra=(), *, path="", filters=None, table_id="main-table"):
    column_classes = [
        " ".join(name for name, enabled in (
            ("secondary", i > 2),
            ("readiness-column", f.key == "readiness" and f.kind == "status"),
        ) if enabled)
        for i, f in enumerate(columns)
    ]
    body = []
    for index, row in enumerate(page.rows[:10]):
        key = row.get("paper_id") or row.get("request_id") or row.get("decision_key") or row.get("identity") or json.dumps(
            {name: row[name] for name in ("symbol", "family", "market", "currency", "settlement", "strategy", "worker", "component", "setting", "id", "lifecycle_id", "unit", "store") if name in row},
            sort_keys=True, default=str)
        if key == "{}":
            key = str(index)
        row_id = table_id + "-" + hashlib.sha256(str(key).encode()).hexdigest()[:12]
        cells = "".join(f"<td data-label='{e(f.label)}' class='{column_classes[i]}'>{value_html(row, f)}</td>" for i, f in enumerate(columns))
        drawer_fields = tuple(dict.fromkeys((*IDENTITY, *columns, *extra, *EVIDENCE)))
        body.append(f"<tr id='{row_id}' data-row='true'>{cells}<td data-label='Detalle'>{detail(row, drawer_fields, row_id, index + 1)}</td></tr>")
    if not body:
        message = ("Sin registros en este scope." if page.state == "AVAILABLE" else
                   "NO_VERIFICADO · " + (page.reason or "evidencia aún no publicada o lectura no disponible."))
        body.append(f"<tr><td colspan='{len(columns) + 1}' class='empty'>{e(message)}</td></tr>")
    heads = "".join(f"<th scope='col' class='{column_classes[i]}'>{e(f.label)}</th>" for i, f in enumerate(columns))
    provenance = f"<div class='source-line'>Fuente: {e(page.source)} · {badge(page.state)}"
    if page.as_of:
        provenance += f" · {e(clock(page.as_of))}"
    provenance += "</div>"
    audit = detail({"reason": page.reason or "Consulta read-only; paginación en servidor."},
                   fields("reason|Estado de lectura"), f"{table_id}-reading", 1,
                   label="Estado de lectura")
    return (f"<section class='panel data-panel' id='{table_id}-panel'><div class='panel-heading'><h2>{e(title)}</h2></div>"
            f"<table class='terminal-table' id='{table_id}'><caption class='sr-only'>{e(title)}</caption><thead><tr>{heads}<th scope='col'>Detalle</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
            + (pager(page, path, filters or {}, table_id) if path else "") + provenance + audit + "</section>")


def metric(label, value, subtitle="", *, kind="text", currency=None, result=False):
    shown = value_html({"v": value, "currency": currency}, Field("v", label, kind))
    if result and kind == "money" and value is not None:
        shown = result_html(value, shown)
    return f"<article class='metric-card'><h2>{e(label)}</h2><div class='metric-value'>{shown}</div><p>{e(subtitle)}</p></article>"


def filters_form(path, values, *, catalog=False, positions=False):
    entries = (("q", "Buscar ticker"), ("family", "Familia"), ("currency", "Moneda"), ("market", "Mercado"),
               ("settlement", "Settlement"), ("strategy", "Estrategia"))
    html = f"<form class='filters' method='get' action='{e(path)}' aria-label='Filtros de la mesa'>"
    for name, label in entries:
        if catalog and name == "strategy":
            continue
        html += f"<label for='filter-{name}'>{label}<input id='filter-{name}' name='{name}' value='{e(values.get(name, ''))}' maxlength='80' autocomplete='off'></label>"
    if not positions:
        states = ("", "RUNTIME_READY", "NO_READY") if catalog else ("", "HOT", "WARM", "DISCOVERY", "BLOCKED")
        html += "<label for='filter-state'>Estado<select id='filter-state' name='state'>"
        for state in states:
            html += f"<option value='{state}' {'selected' if values.get('state', '') == state else ''}>{state or 'Todos'}</option>"
        html += "</select></label>"
    html += f"<button class='button primary' type='submit'>Aplicar filtros</button><a class='button' href='{e(path)}'>Limpiar filtros</a></form>"
    return html


def notice(text, critical=False):
    return f"<div class='notice {'critical' if critical else ''}' role='note'>{e(text)}</div>"


def funnel(counts, scope="NO_VERIFICADO · committed cut unavailable"):
    stages = ("READY", "ELIGIBLE", "TRADEABLE", "DISCOVERY", "WARM", "HOT", "SIGNAL", "ECONOMICS", "RISK", "PAPER")
    return ("<section class='panel'><h2>Embudo operativo</h2><ol class='funnel'>" +
            "".join(f"<li>{badge(stage)}<b>{e(number(counts.get(stage), 0))}</b></li>" for stage in stages) +
            f"</ol><p class='muted'>{e(scope)} · Etapas independientes · HOT ≠ BUY · DISCOVERY/WARM = observación.</p></section>")


def timeline(events, title="Promociones / demociones / descartes en vivo"):
    items = "".join(f"<li><time>{e(clock(row.get('as_of')))}</time><b>{e(row.get('symbol') or UNKNOWN)}</b>{badge(row.get('state'))}<p>{e(row.get('reason') or UNKNOWN)}</p></li>" for row in events[:5])
    empty = "<li class='muted'>NO_VERIFICADO · Sin eventos comprometidos disponibles.</li>"
    return f"<section class='panel'><h2>{e(title)}</h2><ol class='timeline'>{items or empty}</ol></section>"
