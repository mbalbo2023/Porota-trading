#!/usr/bin/env python3
"""Localhost-only RC6 private operator Site for SSH port forwarding.

This surface is intentionally read-only and binds only to 127.0.0.1. It renders
preopen, live Decision Cockpit and postclose evidence without calling broker
APIs or modifying the trading engine.
"""
from __future__ import annotations

import html
import json
import os
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
HOST = "127.0.0.1"
PORT = int(os.getenv("POROTA_PRIVATE_SITE_PORT", "8766"))
ROOT = Path(os.getenv("POROTA_ROOT", "/opt/porota-trading"))
DATA = ROOT / "data"
SNAP = DATA / "paper_v17" / "snapshots"
LATEST = SNAP / "latest.json"
PREOPEN = SNAP / "preopen_latest.json"
LIVE = SNAP / "live_latest.json"
POSTCLOSE = SNAP / "postclose_latest.json"
POST_REVIEW = DATA / "paper_v17" / "reports" / "postclose_review_latest.json"
MAX_ROWS = 10


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def money(value) -> str:
    x = number(value)
    if x is None:
        return "N/D"
    return f"$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value) -> str:
    x = number(value)
    return "N/D" if x is None else f"{x:.1f}%".replace(".", ",")


def parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)
    except (TypeError, ValueError):
        return None


def fmt_dt(value) -> str:
    parsed = parse_dt(value)
    if not parsed:
        return "N/D" if not value else esc(value)
    return parsed.astimezone(TZ).strftime("%d/%m/%Y · %H:%M:%S")


def age_seconds(value) -> float | None:
    parsed = parse_dt(value)
    if not parsed:
        return None
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())


def badge(text: str, kind: str = "neutral") -> str:
    return f'<span class="badge {kind}">{esc(text)}</span>'


def stat(label: str, value, detail: str = "") -> str:
    return (
        '<div class="stat">'
        f'<div class="sl">{esc(label)}</div>'
        f'<div class="sv">{esc(value)}</div>'
        f'<div class="sd">{esc(detail)}</div>'
        '</div>'
    )


def state_bundle() -> dict:
    latest = read_json(LATEST)
    pre = read_json(PREOPEN)
    live = read_json(LIVE)
    post = read_json(POSTCLOSE)
    review = read_json(POST_REVIEW)
    if latest.get("phase") == "preopen" and not pre:
        pre = latest
    if latest.get("phase") == "postclose" and not post:
        post = latest
    live_age = age_seconds(live.get("generated_at")) if live else None
    live_fresh = bool(live and live.get("phase") == "live" and live_age is not None and live_age <= 130)
    current = live if live_fresh else (post or latest or pre)
    return {
        "latest": latest, "pre": pre, "live": live, "post": post, "review": review,
        "live_age": live_age, "live_fresh": live_fresh, "current": current,
    }


def opportunity_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="5" class="muted">Sin oportunidades verificables en la lectura actual.</td></tr>'
    out = []
    for row in rows[:MAX_ROWS]:
        action = str(row.get("action") or "N/D")
        cls = "pos" if "BUY" in action.upper() else ""
        out.append(
            "<tr>"
            f"<td><b>{esc(row.get('symbol') or 'N/D')}</b></td>"
            f"<td class='{cls}'>{esc(action)}</td>"
            f"<td>{esc(row.get('score') if row.get('score') is not None else 'N/D')}</td>"
            f"<td>{esc(row.get('reason') or 'N/D')}</td>"
            f"<td>{fmt_dt(row.get('decided_at'))}</td>"
            "</tr>"
        )
    return "".join(out)


def why_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="4" class="muted">Sin abstenciones/HOLD verificables.</td></tr>'
    return "".join(
        "<tr>"
        f"<td><b>{esc(row.get('symbol') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('action') or 'N/D')}</td>"
        f"<td>{esc(row.get('reason') or 'N/D')}</td>"
        f"<td>{fmt_dt(row.get('decided_at'))}</td>"
        "</tr>"
        for row in rows[:MAX_ROWS]
    )


def operation_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="7" class="muted">Sin operaciones PAPER verificables.</td></tr>'
    out = []
    for row in rows[:MAX_ROWS]:
        pnl = number(row.get("net_pnl_ars"))
        cls = "pos" if pnl is not None and pnl > 0 else ("neg" if pnl is not None and pnl < 0 else "")
        out.append(
            "<tr>"
            f"<td><b>{esc(row.get('symbol') or 'N/D')}</b></td>"
            f"<td>{fmt_dt(row.get('opened_at'))}</td>"
            f"<td>{fmt_dt(row.get('closed_at'))}</td>"
            f"<td class='{cls}'>{money(pnl)}</td>"
            f"<td>{esc(row.get('decision_reason') or 'N/D')}</td>"
            f"<td>{esc(row.get('counterfactual') or 'N/D')}</td>"
            f"<td>{esc(', '.join(map(str, row.get('external_sources') or [])) or 'N/D')}</td>"
            "</tr>"
        )
    return "".join(out)



def fmt_number(value, decimals: int = 2) -> str:
    x = number(value)
    if x is None:
        return "N/D"
    return f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def position_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="10" class="muted">Sin posiciones PAPER abiertas verificables.</td></tr>'
    out = []
    for row in rows[:MAX_ROWS]:
        pnl = number(row.get("unrealized_pnl"))
        cls = "pos" if pnl is not None and pnl > 0 else ("neg" if pnl is not None and pnl < 0 else "")
        out.append(
            "<tr>"
            f"<td><b>{esc(row.get('symbol') or 'N/D')}</b></td>"
            f"<td>{esc(row.get('asset_class') or 'N/D')}</td>"
            f"<td>{esc(row.get('currency') or 'N/D')}</td>"
            f"<td>{fmt_number(row.get('quantity'), 4)}</td>"
            f"<td>{money(row.get('entry_price'))}</td>"
            f"<td>{money(row.get('current_price'))}</td>"
            f"<td>{money(row.get('stop_price'))}</td>"
            f"<td>{money(row.get('target_price'))}</td>"
            f"<td class='{cls}'>{money(pnl)}</td>"
            f"<td>{fmt_dt(row.get('opened_at'))}</td>"
            "</tr>"
        )
    return "".join(out)


def balance_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="6" class="muted">Sin balance por moneda verificable.</td></tr>'
    out = []
    for row in rows[:16]:
        out.append(
            "<tr>"
            f"<td><b>{esc(row.get('currency') or 'N/D')}</b></td>"
            f"<td>{money(row.get('cash'))}</td>"
            f"<td>{money(row.get('exposure'))}</td>"
            f"<td>{money(row.get('unrealized_pnl'))}</td>"
            f"<td>{money(row.get('realized_pnl'))}</td>"
            f"<td>{money(row.get('equity'))}</td>"
            "</tr>"
        )
    return "".join(out)


def daily_risk_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="7" class="muted">Sin corte diario de riesgo verificable.</td></tr>'
    out = []
    for row in rows[:16]:
        out.append(
            "<tr>"
            f"<td>{esc(row.get('day') or 'N/D')}</td>"
            f"<td><b>{esc(row.get('currency') or 'N/D')}</b></td>"
            f"<td>{esc(row.get('state') or 'N/D')}</td>"
            f"<td>{money(row.get('baseline_equity'))}</td>"
            f"<td>{money(row.get('daily_pnl'))}</td>"
            f"<td>{money(row.get('loss_budget'))}</td>"
            f"<td>{esc(row.get('detail') or 'N/D')}</td>"
            "</tr>"
        )
    return "".join(out)


def family_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="5" class="muted">Sin desglose observable por familia.</td></tr>'
    return "".join(
        "<tr>"
        f"<td><b>{esc(row.get('family') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('quotes', 0))}</td>"
        f"<td>{esc(row.get('decisions', 0))}</td>"
        f"<td>{esc(row.get('open_positions', 0))}</td>"
        f"<td>{esc(row.get('closed_positions', 0))}</td>"
        "</tr>"
        for row in rows[:20]
    )


def mapping_rows(mapping: dict, empty: str = "Sin datos verificables.") -> str:
    if not isinstance(mapping, dict) or not mapping:
        return f'<tr><td colspan="2" class="muted">{esc(empty)}</td></tr>'
    return "".join(
        f"<tr><td><b>{esc(k)}</b></td><td>{esc(v)}</td></tr>"
        for k, v in list(mapping.items())[:10]
    )


def performance_rows(by_currency: dict) -> str:
    if not isinstance(by_currency, dict) or not by_currency:
        return '<tr><td colspan="10" class="muted">Sin operaciones cerradas con PnL comparable.</td></tr>'
    out = []
    for currency, row in list(by_currency.items())[:12]:
        out.append(
            "<tr>"
            f"<td><b>{esc(currency)}</b></td>"
            f"<td>{esc(row.get('trades', 0))}</td>"
            f"<td>{esc(row.get('wins', 0))}</td>"
            f"<td>{esc(row.get('losses', 0))}</td>"
            f"<td>{pct(row.get('win_rate_pct'))}</td>"
            f"<td>{money(row.get('net_pnl'))}</td>"
            f"<td>{fmt_number(row.get('profit_factor'), 3)}</td>"
            f"<td>{money(row.get('expectancy'))}</td>"
            f"<td>{money(row.get('largest_win'))}</td>"
            f"<td>{money(row.get('largest_loss'))}</td>"
            "</tr>"
        )
    return "".join(out)



def execution_currency_rows(by_currency: dict) -> str:
    if not isinstance(by_currency, dict) or not by_currency:
        return '<tr><td colspan="4" class="muted">Sin fills verificables.</td></tr>'
    return "".join(
        "<tr>"
        f"<td><b>{esc(currency)}</b></td>"
        f"<td>{esc(row.get('fills', 0))}</td>"
        f"<td>{fmt_number(row.get('avg_slippage'), 6)}</td>"
        f"<td>{money(row.get('total_costs'))}</td>"
        "</tr>"
        for currency, row in list(by_currency.items())[:12]
    )


def fill_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="9" class="muted">Sin fills recientes verificables.</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{fmt_dt(row.get('filled_at'))}</td>"
        f"<td><b>{esc(row.get('symbol') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('asset_class') or 'N/D')}</td>"
        f"<td>{esc(row.get('currency') or 'N/D')}</td>"
        f"<td>{esc(row.get('side') or 'N/D')}</td>"
        f"<td>{fmt_number(row.get('quantity'), 4)}</td>"
        f"<td>{money(row.get('price'))}</td>"
        f"<td>{fmt_number(row.get('slippage'), 6)}</td>"
        f"<td>{money(row.get('costs'))}</td>"
        "</tr>"
        for row in rows[:MAX_ROWS]
    )


def readiness_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="7" class="muted">Sin matriz canónica de readiness disponible.</td></tr>'
    return "".join(
        "<tr>"
        f"<td><b>{esc(row.get('instrument_type') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('declared', 'N/D'))}</td>"
        f"<td>{esc(row.get('queries', 'N/D'))}</td>"
        f"<td>{esc(row.get('observed_count', 'N/D'))}</td>"
        f"<td>{esc(row.get('ready_paper_count', 'N/D'))}</td>"
        f"<td>{esc(row.get('discovery_status') or 'N/D')}</td>"
        f"<td>{fmt_dt(row.get('checked_at'))}</td>"
        "</tr>"
        for row in rows[:30]
    )


def source_health_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="6" class="muted">Sin health de APIs persistido.</td></tr>'
    return "".join(
        "<tr>"
        f"<td><b>{esc(row.get('component') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('state') or 'N/D')}</td>"
        f"<td>{esc(row.get('source') or 'N/D')}</td>"
        f"<td>{fmt_dt(row.get('checked_at'))}</td>"
        f"<td>{fmt_dt(row.get('last_success_at'))}</td>"
        f"<td>{esc(row.get('detail') or 'N/D')}</td>"
        "</tr>"
        for row in rows[:30]
    )


def source_sync_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="6" class="muted">Sin sincronizaciones persistidas.</td></tr>'
    return "".join(
        "<tr>"
        f"<td><b>{esc(row.get('source') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('status') or 'N/D')}</td>"
        f"<td>{esc(row.get('items', 'N/D'))}</td>"
        f"<td>{fmt_dt(row.get('last_attempt_at'))}</td>"
        f"<td>{fmt_dt(row.get('last_success_at'))}</td>"
        f"<td>{esc(row.get('detail') or 'N/D')}</td>"
        "</tr>"
        for row in rows[:30]
    )


def gate_recent_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="8" class="muted">Sin evaluaciones de gates persistidas.</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{fmt_dt(row.get('evaluated_at'))}</td>"
        f"<td><b>{esc(row.get('symbol') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('technical_gate') or 'N/D')}</td>"
        f"<td>{esc(row.get('ai_gate') or 'N/D')}</td>"
        f"<td>{esc(row.get('patrimonial_gate') or 'N/D')}</td>"
        f"<td>{esc(row.get('final_result') or 'N/D')}</td>"
        f"<td>{esc(row.get('reason') or 'N/D')}</td>"
        f"<td>{esc(row.get('paper_id') or '—')}</td>"
        "</tr>"
        for row in rows[:MAX_ROWS]
    )



def pct_fraction(value) -> str:
    x = number(value)
    return "N/D" if x is None else f"{x * 100.0:.2f}%".replace(".", ",")


def instrument_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="16" class="muted">Sin histórico canónico suficiente para los instrumentos visibles.</td></tr>'
    out = []
    for row in rows[:MAX_ROWS]:
        out.append(
            "<tr>"
            f"<td><b>{esc(row.get('symbol') or 'N/D')}</b></td>"
            f"<td>{esc(row.get('family') or 'N/D')}</td>"
            f"<td>{esc(row.get('market') or 'N/D')}</td>"
            f"<td>{esc(row.get('settlement') or 'N/D')}</td>"
            f"<td>{esc(row.get('asof') or 'N/D')}</td>"
            f"<td>{fmt_number(row.get('close'), 4)}</td>"
            f"<td>{pct_fraction(row.get('momentum_20'))}</td>"
            f"<td>{pct_fraction(row.get('momentum_60'))}</td>"
            f"<td>{fmt_number(row.get('rsi14'), 1)}</td>"
            f"<td>{fmt_number(row.get('atr14'), 4)}</td>"
            f"<td>{pct_fraction(row.get('realized_vol20'))}</td>"
            f"<td>{pct_fraction(row.get('max_drawdown_252'))}</td>"
            f"<td>{fmt_number(row.get('high_252'), 4)}</td>"
            f"<td>{fmt_number(row.get('low_252'), 4)}</td>"
            f"<td>{fmt_number(row.get('volume_ratio_20'), 2)}×</td>"
            f"<td>{esc(row.get('source') or 'N/D')}</td>"
            "</tr>"
        )
    return "".join(out)


def equity_curve_rows(by_currency: dict) -> str:
    if not isinstance(by_currency, dict) or not by_currency:
        return '<tr><td colspan="10" class="muted">Sin curva de patrimonio verificable.</td></tr>'
    out = []
    for currency, row in list(by_currency.items())[:12]:
        latest = row.get("latest") if isinstance(row.get("latest"), dict) else {}
        out.append(
            "<tr>"
            f"<td><b>{esc(currency)}</b></td>"
            f"<td>{money(latest.get('equity'))}</td>"
            f"<td>{money(row.get('peak_equity'))}</td>"
            f"<td>{pct(row.get('current_drawdown_pct'))}</td>"
            f"<td>{pct(row.get('max_drawdown_pct'))}</td>"
            f"<td>{money(latest.get('cash'))}</td>"
            f"<td>{money(latest.get('exposure'))}</td>"
            f"<td>{money(latest.get('unrealized_pnl'))}</td>"
            f"<td>{money(latest.get('realized_pnl'))}</td>"
            f"<td>{esc(row.get('samples', 0))}</td>"
            "</tr>"
        )
    return "".join(out)


def event_rows(rows: list[dict]) -> str:
    if not rows:
        return '<tr><td colspan="6" class="muted">Sin eventos estructurados disponibles.</td></tr>'
    return "".join(
        "<tr>"
        f"<td>{fmt_dt(row.get('available_to_engine_at'))}</td>"
        f"<td><b>{esc(row.get('event_type') or 'N/D')}</b></td>"
        f"<td>{esc(row.get('region') or 'N/D')}</td>"
        f"<td>{esc(row.get('title') or 'N/D')}</td>"
        f"<td>{esc(row.get('source_domain') or 'N/D')}</td>"
        f"<td>{esc(row.get('authority') or 'SHADOW_ONLY')}</td>"
        "</tr>"
        for row in rows[:20]
    )


def render() -> str:
    b = state_bundle()
    current, pre, live, post, review = b["current"], b["pre"], b["live"], b["post"], b["review"]
    mode = current.get("mode")
    real = current.get("real_orders_sent")
    paper_ok = mode == "PRODUCTION_PAPER" and real == 0
    live_label = "LIVE" if b["live_fresh"] else "NO LIVE"
    live_kind = "ok" if b["live_fresh"] else "warn"

    top = live.get("top_opportunities") if isinstance(live.get("top_opportunities"), list) else []
    why = live.get("why_not_traded") if isinstance(live.get("why_not_traded"), list) else []
    trace = live.get("traceability") if isinstance(live.get("traceability"), list) else []
    iol = live.get("iol") if isinstance(live.get("iol"), dict) else {}
    risk = live.get("risk") if isinstance(live.get("risk"), dict) else {}
    changes = live.get("changes_from_preopen") if isinstance(live.get("changes_from_preopen"), dict) else {}
    runtime = live.get("runtime") if isinstance(live.get("runtime"), dict) else {}
    alerts = live.get("urgent_alerts") if isinstance(live.get("urgent_alerts"), list) else []
    positions = live.get("positions") if isinstance(live.get("positions"), list) else []
    market = live.get("market") if isinstance(live.get("market"), dict) else {}
    performance = live.get("performance") if isinstance(live.get("performance"), dict) else {}
    funnel = live.get("decision_funnel") if isinstance(live.get("decision_funnel"), dict) else {}
    families = live.get("families") if isinstance(live.get("families"), list) else []
    portfolio = live.get("portfolio") if isinstance(live.get("portfolio"), dict) else {}
    balances = portfolio.get("balances_by_currency") if isinstance(portfolio.get("balances_by_currency"), list) else []
    daily_risk = portfolio.get("daily_risk") if isinstance(portfolio.get("daily_risk"), list) else []
    exit_intents = portfolio.get("exit_intents") if isinstance(portfolio.get("exit_intents"), list) else []
    execution = live.get("execution") if isinstance(live.get("execution"), dict) else {}
    gate_matrix = live.get("gate_matrix") if isinstance(live.get("gate_matrix"), dict) else {}
    family_readiness = live.get("family_readiness") if isinstance(live.get("family_readiness"), list) else []
    source_health = live.get("source_health") if isinstance(live.get("source_health"), dict) else {}
    api_health = source_health.get("api_health") if isinstance(source_health.get("api_health"), list) else []
    source_sync = source_health.get("source_sync") if isinstance(source_health.get("source_sync"), list) else []
    equity_curve = live.get("equity_curve") if isinstance(live.get("equity_curve"), dict) else {}
    event_risk = live.get("event_risk") if isinstance(live.get("event_risk"), dict) else {}
    event_run = event_risk.get("latest_run") if isinstance(event_risk.get("latest_run"), dict) else {}
    events = event_risk.get("events") if isinstance(event_risk.get("events"), list) else []
    instrument_analytics = live.get("instrument_analytics") if isinstance(live.get("instrument_analytics"), list) else []

    post_metrics = post.get("metrics") if isinstance(post.get("metrics"), dict) else {}
    post_ops = post.get("operations") if isinstance(post.get("operations"), list) else []
    if review.get("metrics") and isinstance(review.get("metrics"), dict):
        post_metrics = review["metrics"]
    if review.get("operations") and isinstance(review.get("operations"), list):
        post_ops = review["operations"]

    change_items = changes.get("changes") if isinstance(changes.get("changes"), list) else []
    change_html = "".join(
        f"<li><b>{esc(row.get('symbol'))}</b>: {esc(row.get('from'))} → {esc(row.get('to'))}</li>"
        for row in change_items[:MAX_ROWS]
    ) or f"<li>{esc(changes.get('label') or 'INSUFFICIENT_EVIDENCE')}</li>"
    alert_html = "".join(f"<li>{esc(item)}</li>" for item in alerts) or "<li>Sin alertas urgentes del collector LIVE.</li>"

    pre_notice = "" if pre else (
        '<div class="notice warnbox"><b>Pre-rueda no preservado.</b> '
        'No se reconstruye por inferencia. El próximo snapshot preopen quedará separado.</div>'
    )

    return f'''<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Porota Trading RC6 · Decision Cockpit</title>
<style>
:root{{--bg:#0b0d11;--p:#151922;--p2:#1a202b;--line:#2d3440;--text:#f5f7fa;--muted:#9aa5b3;--g:#3ddc97;--y:#ffd166;--r:#ff6b6b}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(180deg,#090b0f,#0f1218);color:var(--text);font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}}
.wrap{{max-width:1240px;margin:auto;padding:22px 16px 48px}} .ey{{font-size:12px;letter-spacing:.14em;color:var(--muted);font-weight:900}}
h1{{font-size:31px;margin:7px 0}} h2{{font-size:20px;margin:0 0 12px}} .sub,.muted{{color:var(--muted)}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}} .badge{{padding:7px 10px;border:1px solid var(--line);border-radius:999px;font-size:12px;font-weight:850}}
.badge.ok{{color:var(--g);background:#10271f;border-color:#245b47}} .badge.warn{{color:var(--y);background:#28220f;border-color:#665629}}
.nav{{display:flex;gap:8px;overflow:auto;margin-bottom:14px;padding-bottom:2px}} .nav button{{background:var(--p);color:var(--muted);border:1px solid var(--line);border-radius:10px;padding:12px 14px;font-weight:800;white-space:nowrap;min-height:44px}}
.nav button:focus-visible{{outline:3px solid #79a8ff;outline-offset:2px}} .nav button.active{{background:#222938;color:white;border-color:#64748b}} .panel{{display:none}} .panel.active{{display:block}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}} @media(max-width:850px){{.grid{{grid-template-columns:repeat(2,1fr)}}}} @media(max-width:520px){{.grid{{grid-template-columns:1fr}}}}
.stat,.card{{background:linear-gradient(180deg,var(--p2),var(--p));border:1px solid var(--line);border-radius:15px;padding:16px;box-shadow:0 8px 24px #0003}}
.sl{{font-size:11px;letter-spacing:.08em;color:var(--muted);font-weight:900}} .sv{{font-size:24px;font-weight:900;margin:7px 0}} .sd{{font-size:13px;color:var(--muted)}}
.section{{margin-top:14px}} .notice{{border:1px solid var(--line);border-radius:13px;padding:14px 16px;margin:12px 0}} .warnbox{{background:#28220f;border-color:#665629;color:#ffe49a}}
.tablewrap{{overflow:auto;border:1px solid var(--line);border-radius:13px}} table{{width:100%;min-width:780px;border-collapse:collapse;background:var(--p)}} th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:left;font-size:13px}} th{{font-size:11px;color:var(--muted)}}
.pos{{color:var(--g);font-weight:900}} .neg{{color:var(--r);font-weight:900}} .foot{{margin-top:20px;color:var(--muted);font-size:12px}} code{{color:#dce7ff;overflow-wrap:anywhere}}
</style></head><body><div class="wrap">
<div class="ey">POROTA TRADING · RC6</div><h1>Decision Cockpit privado</h1>
<div class="sub">LIVE durante rueda + snapshots inmutables pre/post. Sólo lectura; no cambia decisiones, parámetros ni órdenes.</div>
<div class="badges">
{badge("PAPER seguro" if paper_ok else "VERIFICAR","ok" if paper_ok else "warn")}
{badge(live_label,live_kind)}
{badge("Corte: "+fmt_dt(current.get("generated_at")))}
{badge("localhost:8766")}
</div>

<div class="nav" role="tablist" aria-label="Vistas del trader">
<button class="active" role="tab" aria-selected="true" onclick="tab('live',this)">Decision Cockpit</button>
<button role="tab" aria-selected="false" onclick="tab('pos',this)">Posiciones y riesgo</button>
<button role="tab" aria-selected="false" onclick="tab('mkt',this)">Mercado y liquidez</button>
<button role="tab" aria-selected="false" onclick="tab('ins',this)">Instrumentos</button>
<button role="tab" aria-selected="false" onclick="tab('exe',this)">Ejecución y costos</button>
<button role="tab" aria-selected="false" onclick="tab('str',this)">Estrategia y gates</button>
<button role="tab" aria-selected="false" onclick="tab('evt',this)">Eventos y macro</button>
<button role="tab" aria-selected="false" onclick="tab('pre',this)">Bloqueos pre-rueda</button>
<button role="tab" aria-selected="false" onclick="tab('fam',this)">Explorar familias</button>
<button role="tab" aria-selected="false" onclick="tab('perf',this)">Performance</button>
<button role="tab" aria-selected="false" onclick="tab('evi',this)">Evidencia y servicios</button>
<button role="tab" aria-selected="false" onclick="tab('cie',this)">Cierre de rueda</button>
<button role="tab" aria-selected="false" onclick="tab('sem',this)">Semáforo ejecutivo</button>
</div>

<section id="live" class="panel active">
<div class="grid">
{stat("ESTADO","LIVE" if b["live_fresh"] else "FUERA DE LIVE",fmt_dt(live.get("generated_at")))}
{stat("OPORTUNIDADES",len(top),"máximo 10 visibles")}
{stat("POSICIONES ABIERTAS",risk.get("open_positions","N/D"),"PAPER")}
{stat("RÉGIMEN",live.get("market_regime","N/D"),"Sólo si lo declara runtime")}
</div>
<div class="card section"><h2>Top oportunidades actuales</h2><div class="tablewrap"><table><thead><tr><th>Símbolo</th><th>Acción</th><th>Score motor</th><th>Motivo</th><th>Hora</th></tr></thead><tbody>{opportunity_rows(top)}</tbody></table></div><p class="muted">El score es el que publica el motor. Si el runtime no expone componentes internos, no se fabrica una descomposición.</p></div>
<div class="card section"><h2>Por qué NO operó</h2><div class="tablewrap"><table><thead><tr><th>Símbolo</th><th>Acción</th><th>Gate/motivo</th><th>Hora</th></tr></thead><tbody>{why_rows(why)}</tbody></table></div></div>
<div class="card section"><h2>Trazabilidad · últimas 10 decisiones</h2><div class="tablewrap"><table><thead><tr><th>Símbolo</th><th>Acción</th><th>Score motor</th><th>Motivo</th><th>Hora</th></tr></thead><tbody>{opportunity_rows(trace)}</tbody></table></div></div>
<div class="grid section">
{stat("IOL OBSERVADO",iol.get("universe_observed","N/D"),"universo cacheado")}
{stat("IOL FRESCO READY",iol.get("fresh_ready","N/D"),"TTL live")}
{stat("DIVERGENCIAS",iol.get("price_divergence","N/D"),"comparación SHADOW")}
{stat("INFLUENCIA IOL",iol.get("influence_on_live_decision","N/D"),iol.get("decision_effect","OBSERVE_ONLY"))}
</div>
<div class="card section"><h2>Qué cambió desde pre-rueda</h2><ul>{change_html}</ul></div>
<div class="card section"><h2>Contrafáctico LIVE</h2><div class="notice warnbox"><b>INSUFFICIENT_EVIDENCE</b><br>Durante la rueda no se reinterpreta una decisión como si hubiese usado otra fuente o regla. El contrafáctico se publica post-cierre sólo con evidencia comparable.</div></div>
<div class="grid section">
{stat("NOTIONAL EST.",money(risk.get("estimated_notional_ars")),"N/D si faltan precio/cantidad")}
{stat("PNL NO REALIZADO",money(risk.get("unrealized_pnl_ars")),"N/D si runtime no lo publica")}
{stat("DECISIONES VISIBLES",runtime.get("decisions_visible","N/D"),"stream del dashboard")}
{stat("CERRADAS VISIBLES",runtime.get("closed_positions_visible","N/D"),"PAPER")}
</div>
<div class="card section"><h2>Trazabilidad y alarmas</h2><ul>{alert_html}</ul></div>
</section>


<section id="pos" class="panel">
<div class="grid">
{stat("POSICIONES ABIERTAS",len(positions),"PAPER")}
{stat("NOTIONAL EST.",money(risk.get("estimated_notional_ars")),"suma disponible; no mezcla si falta identidad")}
{stat("PNL NO REALIZADO",money(risk.get("unrealized_pnl_ars")),"sólo lo publicado por runtime")}
{stat("SALIDAS PENDIENTES",len(exit_intents),"intenciones registradas")}
</div>
<div class="card section"><h2>Libro de posiciones abierto</h2><div class="tablewrap"><table><thead><tr><th>Símbolo</th><th>Familia</th><th>Moneda</th><th>Cantidad</th><th>Entrada</th><th>Marca</th><th>Stop</th><th>Target</th><th>uPnL</th><th>Desde</th></tr></thead><tbody>{position_rows(positions)}</tbody></table></div></div>
<div class="card section"><h2>Riesgo diario por moneda</h2><div class="tablewrap"><table><thead><tr><th>Día</th><th>Moneda</th><th>Estado</th><th>Equity base</th><th>PnL diario</th><th>Presupuesto pérdida</th><th>Detalle</th></tr></thead><tbody>{daily_risk_rows(daily_risk)}</tbody></table></div></div>
<div class="card section"><h2>Caja, exposición y patrimonio por moneda</h2><div class="tablewrap"><table><thead><tr><th>Moneda</th><th>Caja</th><th>Exposición</th><th>uPnL</th><th>rPnL</th><th>Equity</th></tr></thead><tbody>{balance_rows(balances)}</tbody></table></div><p class="muted">No se consolida ARS/USD/MEP/CCL en un total artificial sin una conversión explícita y contemporánea.</p></div>
</section>

<section id="mkt" class="panel">
<div class="grid">
{stat("COTIZACIONES",market.get("quotes","N/D"),"identidades observadas")}
{stat("LIBROS VÁLIDOS",market.get("valid_books","N/D"),"bid/ask utilizables")}
{stat("SPREAD MEDIANA",fmt_number(market.get("median_spread_bps"),2)+" bps" if market.get("median_spread_bps") is not None else "N/D","microestructura observada")}
{stat("STALE / N/D",market.get("stale_or_unknown","N/D"),"según timestamp publicado")}
</div>
<div class="card section"><h2>Cobertura de mercado por familia</h2><div class="tablewrap"><table><thead><tr><th>Familia</th><th>Cotizaciones</th></tr></thead><tbody>{mapping_rows(market.get("by_asset_class") if isinstance(market.get("by_asset_class"),dict) else {}, "Sin familias cotizadas.")}</tbody></table></div></div>
<div class="grid section">
{stat("BOOKS CRUZADOS",market.get("crossed_books","N/D"),"ask &lt; bid; revisar calidad")}
{stat("BOOK FALTANTE",market.get("missing_book","N/D"),"sin bid/ask válido")}
{stat("FRESHEST",fmt_number(market.get("freshest_age_seconds"),1)+" s" if market.get("freshest_age_seconds") is not None else "N/D","edad mínima")}
{stat("STALEST",fmt_number(market.get("stalest_age_seconds"),1)+" s" if market.get("stalest_age_seconds") is not None else "N/D","edad máxima")}
</div>
<div class="notice warnbox"><b>Disciplina de datos:</b> esta vista no fabrica variaciones, volumen, profundidad ni indicadores que el runtime no publique. Cuando falte una serie, queda N/D.</div>
</section>



<section id="ins" class="panel">
<div class="grid">
{stat("INSTRUMENTOS",len(instrument_analytics),"máximo 10 visibles")}
{stat("AUTORIDAD","CONTEXT_ONLY","no emite BUY/SELL")}
{stat("HISTÓRICO","CANÓNICO v2","identidad exacta mercado/liquidación")}
{stat("ACTUALIZACIÓN",fmt_dt(live.get("generated_at")),"snapshot LIVE")}
</div>
<div class="card section"><h2>Contexto técnico por instrumento</h2>
<div class="tablewrap"><table><thead><tr><th>Símbolo</th><th>Familia</th><th>Mercado</th><th>Liquidación</th><th>Fecha</th><th>Cierre</th><th>Mom 20</th><th>Mom 60</th><th>RSI14</th><th>ATR14</th><th>Vol 20 anual.</th><th>DD máx 252</th><th>Máx 252</th><th>Mín 252</th><th>Volumen / prom20</th><th>Fuente</th></tr></thead><tbody>{instrument_rows(instrument_analytics)}</tbody></table></div>
<p class="muted">Indicadores descriptivos sobre la serie canónica. No se convierten en una señal, no alteran el score del motor y no mezclan identidades distintas.</p></div>
</section>

<section id="exe" class="panel">
<div class="grid">
{stat("FILLS MUESTRA",execution.get("sample_size","N/D"),"últimos fills persistidos")}
{stat("MONEDAS",len(execution.get("by_currency") or {}) if isinstance(execution.get("by_currency"),dict) else "N/D","costos separados")}
{stat("AUTORIDAD","DISPLAY_ONLY","sin controles de ejecución")}
{stat("ÓRDENES REALES",real if real is not None else "N/D","debe permanecer 0")}
</div>
<div class="card section"><h2>Calidad de ejecución por moneda</h2><div class="tablewrap"><table><thead><tr><th>Moneda</th><th>Fills</th><th>Slippage promedio</th><th>Costos acumulados</th></tr></thead><tbody>{execution_currency_rows(execution.get("by_currency") if isinstance(execution.get("by_currency"),dict) else {})}</tbody></table></div>
<p class="muted">Slippage y costos se muestran en las unidades persistidas por el motor; no se reinterpretan ni convierten entre monedas.</p></div>
<div class="card section"><h2>Últimos fills PAPER</h2><div class="tablewrap"><table><thead><tr><th>Hora</th><th>Símbolo</th><th>Familia</th><th>Moneda</th><th>Lado</th><th>Cantidad</th><th>Precio</th><th>Slippage</th><th>Costos</th></tr></thead><tbody>{fill_rows(execution.get("recent") if isinstance(execution.get("recent"),list) else [])}</tbody></table></div></div>
</section>

<section id="str" class="panel">
<div class="grid">
{stat("DECISIONES MUESTRA",funnel.get("sample_size","N/D"),"ventana visible del observer")}
{stat("BUY",((funnel.get("actions") or {}).get("BUY","N/D")) if isinstance(funnel.get("actions"),dict) else "N/D","candidatos/decisiones publicadas")}
{stat("HOLD",((funnel.get("actions") or {}).get("HOLD","N/D")) if isinstance(funnel.get("actions"),dict) else "N/D","abstenciones")}
{stat("RÉGIMEN",live.get("market_regime","N/D"),"declarado por runtime")}
</div>
<div class="card section"><h2>Embudo de decisiones</h2><div class="tablewrap"><table><thead><tr><th>Acción</th><th>Cantidad</th></tr></thead><tbody>{mapping_rows(funnel.get("actions") if isinstance(funnel.get("actions"),dict) else {})}</tbody></table></div></div>
<div class="card section"><h2>Principales gates / motivos</h2><div class="tablewrap"><table><thead><tr><th>Motivo</th><th>Cantidad</th></tr></thead><tbody>{mapping_rows(funnel.get("top_reasons") if isinstance(funnel.get("top_reasons"),dict) else {})}</tbody></table></div></div>
<div class="card section"><h2>Versiones de estrategia observadas</h2><div class="tablewrap"><table><thead><tr><th>Versión</th><th>Decisiones</th></tr></thead><tbody>{mapping_rows(funnel.get("strategies") if isinstance(funnel.get("strategies"),dict) else {}, "Sin versión de estrategia publicada.")}</tbody></table></div></div>
<div class="grid section">
{stat("GATES MUESTRA",gate_matrix.get("sample_size","N/D"),"últimas evaluaciones")}
{stat("RESULTADOS",len(gate_matrix.get("final_results") or {}) if isinstance(gate_matrix.get("final_results"),dict) else "N/D","estados finales")}
{stat("MOTIVOS",len(gate_matrix.get("top_reasons") or {}) if isinstance(gate_matrix.get("top_reasons"),dict) else "N/D","causas principales")}
{stat("AUTORIDAD","READ_ONLY","trazabilidad")}
</div>
<div class="card section"><h2>Resultado final de gates</h2><div class="tablewrap"><table><thead><tr><th>Resultado</th><th>Cantidad</th></tr></thead><tbody>{mapping_rows(gate_matrix.get("final_results") if isinstance(gate_matrix.get("final_results"),dict) else {})}</tbody></table></div></div>
<div class="card section"><h2>Gates recientes</h2><div class="tablewrap"><table><thead><tr><th>Hora</th><th>Símbolo</th><th>Técnico</th><th>IA</th><th>Patrimonial</th><th>Final</th><th>Motivo</th><th>Paper ID</th></tr></thead><tbody>{gate_recent_rows(gate_matrix.get("recent") if isinstance(gate_matrix.get("recent"),list) else [])}</tbody></table></div></div>
</section>


<section id="evt" class="panel">
<div class="grid">
{stat("ESTADO GDELT",event_risk.get("status","N/D"),"evidencia estructurada")}
{stat("ÚLTIMA CORRIDA",event_run.get("state","N/D"),fmt_dt(event_run.get("finished_at")))}
{stat("EVENTOS VISIBLES",len(events),"máximo 20")}
{stat("AUTORIDAD",event_run.get("authority","SHADOW_ONLY"),"OBSERVE_ONLY")}
</div>
<div class="card section"><h2>Riesgo de eventos estructurado</h2><div class="tablewrap"><table><thead><tr><th>Disponible</th><th>Tipo</th><th>Región</th><th>Título</th><th>Dominio</th><th>Autoridad</th></tr></thead><tbody>{event_rows(events)}</tbody></table></div>
<p class="muted">Eventos macro/geopolíticos se muestran como contexto SHADOW. No habilitan, bloquean ni modifican órdenes por sí solos.</p></div>
</section>

<section id="pre" class="panel">{pre_notice}<div class="grid">
{stat("SNAPSHOT PREOPEN","VERIFICADO" if pre.get("status")=="VERIFIED" else "N/D",fmt_dt(pre.get("generated_at")))}
{stat("MODO",pre.get("mode","N/D"),"PRODUCTION_PAPER esperado")}
{stat("ÓRDENES REALES",pre.get("real_orders_sent","N/D"),"debe permanecer 0")}
{stat("DECISIONES",pre.get("decision_count","N/D"),"snapshot")}
</div></section>

<section id="fam" class="panel">
<div class="card"><h2>Readiness canónico por familia</h2><div class="tablewrap"><table><thead><tr><th>Familia</th><th>Declarada</th><th>Queries</th><th>Observados</th><th>READY PAPER</th><th>Discovery</th><th>Corte</th></tr></thead><tbody>{readiness_rows(family_readiness)}</tbody></table></div></div>
<div class="card section"><h2>Actividad observable por familia</h2><div class="tablewrap"><table><thead><tr><th>Familia</th><th>Cotizaciones</th><th>Decisiones</th><th>Abiertas</th><th>Cerradas</th></tr></thead><tbody>{family_rows(families)}</tbody></table></div>
<p class="muted">Readiness viene de la tabla canónica cuando existe. Greeks, duration, TIR, cupón, vencimiento, basis u otras métricas específicas sólo se muestran cuando una fuente canónica las publica; nunca se infieren.</p></div>
</section>

<section id="perf" class="panel">
<div class="card"><h2>Performance PAPER por moneda</h2><div class="tablewrap"><table><thead><tr><th>Moneda</th><th>Trades</th><th>Wins</th><th>Losses</th><th>Win rate</th><th>PnL neto</th><th>Profit factor</th><th>Expectancy</th><th>Mayor win</th><th>Mayor loss</th></tr></thead><tbody>{performance_rows(performance.get("by_currency") if isinstance(performance.get("by_currency"),dict) else {})}</tbody></table></div>
<div class="notice warnbox"><b>Sin total multi-moneda:</b> si hay más de una moneda, el Site mantiene resultados separados para evitar una suma económicamente inválida.</div></div>
<div class="card section"><h2>Motivos de cierre</h2><div class="tablewrap"><table><thead><tr><th>Motivo</th><th>Cantidad</th></tr></thead><tbody>{mapping_rows(performance.get("close_reasons") if isinstance(performance.get("close_reasons"),dict) else {}, "Sin cierres clasificables.")}</tbody></table></div></div>
<div class="card section"><h2>Equity y drawdown por moneda</h2><div class="tablewrap"><table><thead><tr><th>Moneda</th><th>Equity</th><th>Pico</th><th>DD actual</th><th>DD máximo</th><th>Caja</th><th>Exposición</th><th>uPnL</th><th>rPnL</th><th>Muestras</th></tr></thead><tbody>{equity_curve_rows(equity_curve.get("by_currency") if isinstance(equity_curve.get("by_currency"),dict) else {})}</tbody></table></div>
<p class="muted">La curva se calcula por moneda sobre snapshots persistidos; no se genera un equity total mezclando monedas.</p></div>
</section>

<section id="evi" class="panel"><div class="grid">
{stat("PREOPEN",fmt_dt(pre.get("generated_at")),"preservado" if pre else "no disponible")}
{stat("LIVE",fmt_dt(live.get("generated_at")),"fresco" if b["live_fresh"] else "no fresco / fuera de rueda")}
{stat("IOL CACHE",fmt_dt(iol.get("refreshed_at")),"SHADOW / OBSERVE_ONLY")}
{stat("VALIDACIÓN",fmt_dt((live.get("validation") or {}).get("generated_at") if isinstance(live.get("validation"),dict) else None),"read-only")}
{stat("POSTCLOSE",fmt_dt(post.get("generated_at")),"preservado" if post else "no disponible")}
{stat("SERVICIO","GREEN","127.0.0.1:8766")}
</div><div class="card section"><h2>Fuentes</h2>
<p><code>{esc(PREOPEN)}</code></p><p><code>{esc(LIVE)}</code></p><p><code>{esc(POSTCLOSE)}</code></p>
<p class="muted">El collector LIVE lee estado local y cachés existentes. No consulta PPI/IOL directamente.</p></div>
<div class="card section"><h2>Salud de APIs / adaptadores</h2><div class="tablewrap"><table><thead><tr><th>Componente</th><th>Estado</th><th>Fuente</th><th>Chequeado</th><th>Último OK</th><th>Detalle</th></tr></thead><tbody>{source_health_rows(api_health)}</tbody></table></div></div>
<div class="card section"><h2>Sincronización de fuentes</h2><div class="tablewrap"><table><thead><tr><th>Fuente</th><th>Estado</th><th>Items</th><th>Intento</th><th>Último OK</th><th>Detalle</th></tr></thead><tbody>{source_sync_rows(source_sync)}</tbody></table></div></div>
</section>

<section id="cie" class="panel"><div class="grid">
{stat("OPERACIONES PAPER",post_metrics.get("closed_operations",len(post_ops)),"cierre")}
{stat("WINRATE",pct(post_metrics.get("win_rate_pct")),"verificable")}
{stat("PNL NETO",money(post_metrics.get("net_pnl_ars")),"PAPER")}
{stat("DECISIONES",post_metrics.get("decisions_observed",post.get("decision_count","N/D")),"snapshot")}
</div><div class="card section"><h2>Operaciones · máximo 10</h2>
<div class="tablewrap"><table><thead><tr><th>Símbolo</th><th>Apertura</th><th>Cierre</th><th>PnL</th><th>Explicación</th><th>Contrafáctico</th><th>Fuentes</th></tr></thead><tbody>{operation_rows(post_ops)}</tbody></table></div></div></section>

<section id="sem" class="panel"><div class="grid">
{stat("MODO PAPER","VERDE" if mode=="PRODUCTION_PAPER" else "VERIFICAR",mode or "N/D")}
{stat("DINERO REAL","VERDE" if real==0 else "VERIFICAR",f"real_orders_sent={real if real is not None else 'N/D'}")}
{stat("LIVE","VERDE" if b["live_fresh"] else "FUERA DE VENTANA",fmt_dt(live.get("generated_at")))}
{stat("ALERTAS",len(alerts),"collector LIVE")}
{stat("STALE QUOTES",market.get("stale_or_unknown","N/D"),"calidad de mercado")}
{stat("IOL FRESCO",iol.get("fresh_ready","N/D"),"OBSERVE_ONLY")}
{stat("RISK ROWS",len(daily_risk),"por moneda")}
{stat("EVENT RISK",event_run.get("state","N/D"),"SHADOW_ONLY")}
{stat("ANÁLISIS TÉCNICO",len(instrument_analytics),"context-only")}
</div></section>

<div class="foot">Privado · localhost-only · SSH Port Forwarding · auto-refresh 30 s · sin controles de ejecución.</div>
</div><script>
function tab(id,b){{document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.nav button').forEach(x=>{{x.classList.remove('active');x.setAttribute('aria-selected','false')}});document.getElementById(id).classList.add('active');b.classList.add('active');b.setAttribute('aria-selected','true')}}
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_data(self, code: int, data: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            b = state_bundle()
            payload = {
                "status": "ok",
                "service": "porota-private-site-v6",
                "bind": HOST,
                "port": PORT,
                "public_exposure": False,
                "live_fresh": b["live_fresh"],
                "live_generated_at": b["live"].get("generated_at"),
                "preopen_preserved": bool(b["pre"]),
                "postclose_preserved": bool(b["post"]),
                "ts": int(time.time()),
            }
            return self.send_data(200, json.dumps(payload, ensure_ascii=False, indent=2).encode(), "application/json; charset=utf-8")
        if path == "/api/live":
            payload = read_json(LIVE)
            code = 200 if payload else 404
            return self.send_data(code, json.dumps(payload or {"status": "not_found"}, ensure_ascii=False, indent=2).encode(), "application/json; charset=utf-8")
        return self.send_data(200, render().encode())


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"POROTA_PRIVATE_SITE=READY bind={HOST} port={PORT}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
