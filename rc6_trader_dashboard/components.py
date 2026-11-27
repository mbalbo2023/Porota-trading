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
    if isinstance(value, str) and re.search(r"(?i)(?:^|[\W_])(authorization|bearer|token|api[_-]?key|secret|password|passwd|cookie|account[_-]?(?:id|number)|cbu|cuit)(?=$|[\W_])", value):
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
    except (TypeError, ValueError, OverflowError):
        # Provider boundary clocks (observed year1) may parse successfully but
        # underflow when historical ART offsets are applied. Display unknown;
        # never wrap, fabricate a date, or crash the complete source page.
        return UNKNOWN


def unverified(reason, *, value=UNKNOWN, status=False, inline=False):
    """Keep uncertainty and the reason visible together in the same field."""
    label = (f"<span class='badge badge-unknown' data-status='{e(value)}'>"
             f"<span aria-hidden='true'>·</span> {e(value)}</span>" if status else e(value))
    message = str(sanitized(reason))[:240]
    if message == "[REDACTED] sensitive detail":
        message = "El recibo contiene un motivo sensible. Consultá la evidencia sanitizada."
    if inline:
        return (f"<span class='unverified-value'>"
                f"<span class='unverified-reason'>{e(value)} · {e(message)}</span></span>")
    return (f"<span class='unverified-value'>{label}"
            f"<small class='unverified-reason'>{e(message)}</small></span>")


def unknown_value(value):
    return isinstance(value, str) and (value.strip().upper().replace(" ", "_") in {UNKNOWN, "NO_VERIFICADA"}
                                       or value.startswith(UNKNOWN + "_"))


def badge(value, *, reason=None):
    text = UNKNOWN if value is None or value == "" else str(value)
    if unknown_value(text):
        if reason is None and text == "NO_VERIFICADO_AWAITING_RECONCILIATION":
            reason = "La generación SHADOW espera una reconciliación comprometida."
        return unverified(reason or "El registro no aporta un estado verificable.", value=text, status=True)
    key = text.upper()
    if key == "HOT":
        tone, icon = "hot", "◆"
    elif key in {"WARM", "AMARILLO"}:
        tone, icon = "warm", "◇"
    elif key == "DISCOVERY":
        tone, icon = "discovery", "⌕"
    elif key in {"PASS", "HEALTHY", "FRESH", "RUNNING", "OK", "VERDE"}:
        tone, icon = "pass", "✓"
    elif key in {"FAIL", "BLOCKED", "STALE", "ERROR", "HARD_STOP", "SOFT_STOP", "SERVICE_FAILED", "ROJO"}:
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
PAGE_REASONS = {"SHADOW_CUT_NOT_FRESH": "El corte SHADOW está vencido. Revisá el worker y su hora de publicación.",
                "COMMITTED_GENERATION_NOT_PUBLISHED": "El worker SHADOW aún no publicó una generación comprometida.",
                "COMMITTED_GENERATION_REJECTED": "La lectura rechazó la generación SHADOW por integridad o publicación incompleta.",
                "DATASET_NOT_PUBLISHED": "La fuente no publicó el conjunto de datos requerido.",
                "DATASET_SCHEMA_OR_READ_ERROR": "El esquema o la lectura de la fuente impidieron verificar los registros."}
EMBEDDED_UNKNOWN = re.compile(r"(?<!\w)NO[_\s-]+VERIFICAD[OA](?!\w)", re.IGNORECASE)


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


def field_reason(row, field, value):
    reason = lookup(row, field.key + "_reason")
    if not reason and field.key in {"state", "status", "freshness", "reason", "readiness"}:
        reason = row.get("reason") or row.get("blocked_reason")
    if isinstance(reason, str) and reason.strip() and not unknown_value(reason):
        return PAGE_REASONS.get(reason, reason)
    if field.kind == "time":
        return f"El registro no acredita una fecha con zona horaria para {field.label}."
    if field.kind in {"money", "count", "number", "percent", "score"}:
        return f"El registro no aporta un valor numérico utilizable para {field.label}."
    if field.kind == "status" and value == "NO_VERIFICADO_AWAITING_RECONCILIATION":
        return "La generación SHADOW espera una reconciliación comprometida."
    return f"El registro no publica evidencia suficiente para {field.label}."


def value_html(row, field):
    value = lookup(row, field.key)
    if field.kind == "status":
        return badge(value, reason=field_reason(row, field, value))
    if value is None or value == "" or unknown_value(value):
        return unverified(field_reason(row, field, value))
    if field.kind == "time":
        formatted = clock(value)
        return unverified(field_reason(row, field, value)) if formatted == UNKNOWN else e(formatted)
    if field.kind in {"money", "count", "number", "percent", "score"}:
        formatted = number(value, 0 if field.kind == "count" else 2)
        if formatted == UNKNOWN:
            return unverified(field_reason(row, field, value))
        rendered = e(formatted)
        if field.kind == "money":
            currency = row.get("currency")
            rendered += (f" <span class='currency'>{e(currency)}</span>" if currency and not unknown_value(currency) else
                         " " + unverified("El registro no acredita la moneda de este importe.", value="MONEDA_NO_VERIFICADA"))
            return result_html(value, rendered) if field.key in RESULT_KEYS else rendered
        if field.kind == "percent":
            return rendered + " %"
        if field.kind == "score":
            return rendered + " <small class='muted'>NO PROBABILIDAD</small>"
        return rendered
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, (list, dict)):
        text = json.dumps(sanitized(value), ensure_ascii=False, sort_keys=True, default=str)[:4096]
        if EMBEDDED_UNKNOWN.search(text):
            reason = ("Los lectores o supervisores marcados no aportan un estado o reloj acreditado al corte."
                      if field.key == "workers" else f"El registro no acredita los valores marcados de {field.label}.")
            return unverified(reason, value=text)
        return e(text)
    return e(str(sanitized(value))[:4096])


IDENTITY = fields("symbol|Ticker;family|Familia;market|Mercado;currency|Moneda;settlement|Settlement")
EVIDENCE = fields("source|Fuente;as_of|Reloj de la fuente|time;freshness|Freshness|status;reason|Motivo;entry_authority|Autoridad de entrada")
AGGREGATE_EVIDENCE = fields("source|Fuente;as_of|Reloj del agregado|time;scope|Granularidad;reason|Alcance")


def definition_list(row, columns):
    return "<dl class='facts'>" + "".join(f"<div><dt>{e(f.label)}</dt><dd>{value_html(row, f)}</dd></div>" for f in columns) + "</dl>"


def detail(row, columns, row_id, index, *, label=None):
    if label is None:
        keys = ("symbol", "worker", "component", "unit", "store", "setting", "engine",
                "strategy", "family", "currency", "label", "date_ar", "session_date", "id")
        name = next((row[key] for key in keys if row.get(key) not in (None, "", UNKNOWN)), None)
        label = f"Detalle fila {index}" + (f" · {sanitized(name)}" if name is not None else "")
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
    total = value_html({"total": page.total, "total_reason": "La lectura no acredita el total de filas para este alcance."},
                       Field("total", "Total de filas", "count"))
    range_text = f"{page.offset + 1}–{page.offset + visible}" if visible else "0"
    more = link(page.offset + 10, "Mostrar 10 más") if page.total is not None and page.offset + visible < page.total else ""
    less = link(max(0, page.offset - 10), "Mostrar menos") if page.offset else ""
    return f"<nav class='pager' aria-label='Paginación de {e(table_id)}'><span>{range_text} de {total} · {visible} filas</span>{less}{more}</nav>"


def table(page, title, columns, extra=(), *, path="", filters=None, table_id="main-table", aggregate=False, identity=True, evidence=True):
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
        # Multiple observations can share one instrument identity. A dialog
        # target must still identify this particular row within the page.
        row_id = table_id + "-" + hashlib.sha256(
            f"{key}:{page.offset + index}".encode()).hexdigest()[:12]
        cells = "".join(f"<td data-label='{e(f.label)}' class='{column_classes[i]}'>{value_html(row, f)}</td>" for i, f in enumerate(columns))
        # Family aggregates and service health rows have no instrument identity.
        # Keep their factual check/source fields without adding inapplicable keys.
        evidence_fields = (AGGREGATE_EVIDENCE if aggregate else EVIDENCE) if evidence else ()
        drawer_fields = tuple(dict.fromkeys((*(() if aggregate or not identity else IDENTITY), *columns, *extra, *evidence_fields)))
        if not identity and row.get("grain") == "SERVICE_CHECK":
            # The internal PAPER checker publishes a check and a receipt, not
            # provider/event clocks or market-data quality for an instrument.
            inapplicable = {"data_freshness", "data_quality", "provider_clock",
                            "entry_authority", "reason", "conflicts"}
            drawer_fields = tuple(f for f in drawer_fields if f.key not in inapplicable)
        body.append(f"<tr id='{row_id}' data-row='true'>{cells}<td data-label='Detalle'>{detail(row, drawer_fields, row_id, index + 1)}</td></tr>")
    if not body:
        if page.reason in {"SELECT_EXACT_IDENTITY_FOR_MARKET", "SELECT_EXACT_IDENTITY_FOR_CONTRACT"}:
            message = "REQUIERE_IDENTIDAD · Seleccione ticker, familia, mercado, moneda y settlement en el buscador."
        elif page.state == "AVAILABLE":
            message = "Sin registros en este scope."
        else:
            message = unverified(PAGE_REASONS.get(page.reason,
                page.reason or "La fuente no aporta registros verificables para este alcance."), inline=True)
        body.append(f"<tr><td colspan='{len(columns) + 1}' class='empty'>{message}</td></tr>")
    heads = "".join(f"<th scope='col' class='{column_classes[i]}'>{e(f.label)}</th>" for i, f in enumerate(columns))
    selection_required = page.reason in {"SELECT_EXACT_IDENTITY_FOR_MARKET", "SELECT_EXACT_IDENTITY_FOR_CONTRACT"}
    source_state = badge("REQUIERE_IDENTIDAD" if selection_required else page.state,
                         reason=PAGE_REASONS.get(page.reason, page.reason or "La lectura no acredita el estado de la fuente."))
    source_label = value_html({"source": page.source}, Field("source", "Fuente de la tabla"))
    provenance = f"<div class='source-line'>Fuente: {source_label} · {source_state}"
    if page.as_of:
        provenance += " · " + value_html({"as_of": page.as_of}, Field("as_of", "Hora del corte", "time"))
    provenance += "</div>"
    audit = detail({"reason": page.reason or "Consulta read-only; paginación en servidor."},
                   fields("reason|Estado de lectura"), f"{table_id}-reading", 1,
                   label="Estado de lectura")
    return (f"<section class='panel data-panel' id='{table_id}-panel'><div class='panel-heading'><h2>{e(title)}</h2></div>"
            f"<table class='terminal-table' id='{table_id}'><caption class='sr-only'>{e(title)}</caption><thead><tr>{heads}<th scope='col'>Detalle</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
            + (pager(page, path, filters or {}, table_id) if path else "") + provenance + audit + "</section>")


def metric(label, value, subtitle="", *, kind="text", currency=None, result=False, reason=None):
    shown = value_html({"v": value, "v_reason": reason, "currency": currency}, Field("v", label, kind))
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


def funnel(counts, scope="Falta un corte comprometido para este embudo"):
    stages = ("READY", "ELIGIBLE", "TRADEABLE", "DISCOVERY", "WARM", "HOT", "SIGNAL", "ECONOMICS", "RISK", "PAPER")
    return ("<section class='panel'><h2>Embudo operativo</h2><ol class='funnel'>" +
            "".join(f"<li>{badge(stage)}<b>{value_html({'value': counts.get(stage)}, Field('value', stage, 'count'))}</b></li>" for stage in stages) +
            f"</ol><p class='muted'>{e(scope)} · Etapas independientes · HOT ≠ BUY · DISCOVERY/WARM = observación.</p></section>")


def timeline(events, title="Promociones / demociones / descartes en vivo", *, verified_empty=False):
    def event(row):
        when, symbol, state, reason = (value_html(row, field) for field in
            fields("as_of|Hora del evento|time;symbol|Ticker;state|Estado del evento|status;reason|Motivo del evento"))
        return f"<li><time>{when}</time><b>{symbol}</b>{state}<p>{reason}</p></li>"
    items = "".join(event(row) for row in events[:5])
    empty = ("<li class='muted'>Sin eventos en este scope (0).</li>" if verified_empty else
             "<li class='muted'>NO_VERIFICADO · Sin eventos comprometidos disponibles.</li>")
    return f"<section class='panel'><h2>{e(title)}</h2><ol class='timeline'>{items or empty}</ol></section>"
