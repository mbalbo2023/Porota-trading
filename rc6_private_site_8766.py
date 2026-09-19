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
.nav{{display:flex;gap:8px;overflow:auto;margin-bottom:14px}} .nav button{{background:var(--p);color:var(--muted);border:1px solid var(--line);border-radius:10px;padding:10px 13px;font-weight:800;white-space:nowrap}}
.nav button.active{{background:#222938;color:white;border-color:#485365}} .panel{{display:none}} .panel.active{{display:block}}
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

<div class="nav">
<button class="active" onclick="tab('live',this)">Decision Cockpit</button>
<button onclick="tab('pre',this)">Bloqueos pre-rueda</button>
<button onclick="tab('fam',this)">Explorar familias</button>
<button onclick="tab('evi',this)">Evidencia y servicios</button>
<button onclick="tab('cie',this)">Cierre de rueda</button>
<button onclick="tab('sem',this)">Semáforo ejecutivo</button>
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

<section id="pre" class="panel">{pre_notice}<div class="grid">
{stat("SNAPSHOT PREOPEN","VERIFICADO" if pre.get("status")=="VERIFIED" else "N/D",fmt_dt(pre.get("generated_at")))}
{stat("MODO",pre.get("mode","N/D"),"PRODUCTION_PAPER esperado")}
{stat("ÓRDENES REALES",pre.get("real_orders_sent","N/D"),"debe permanecer 0")}
{stat("DECISIONES",pre.get("decision_count","N/D"),"snapshot")}
</div></section>

<section id="fam" class="panel"><div class="card"><h2>Explorar familias</h2>
<div class="notice warnbox"><b>N/D si la fuente no lo publica.</b> Este Site no inventa desglose por familia ni readiness contractual.</div></div></section>

<section id="evi" class="panel"><div class="grid">
{stat("PREOPEN",fmt_dt(pre.get("generated_at")),"preservado" if pre else "no disponible")}
{stat("LIVE",fmt_dt(live.get("generated_at")),"fresco" if b["live_fresh"] else "no fresco / fuera de rueda")}
{stat("IOL CACHE",fmt_dt(iol.get("refreshed_at")),"SHADOW / OBSERVE_ONLY")}
{stat("VALIDACIÓN",fmt_dt((live.get("validation") or {}).get("generated_at") if isinstance(live.get("validation"),dict) else None),"read-only")}
{stat("POSTCLOSE",fmt_dt(post.get("generated_at")),"preservado" if post else "no disponible")}
{stat("SERVICIO","GREEN","127.0.0.1:8766")}
</div><div class="card section"><h2>Fuentes</h2>
<p><code>{esc(PREOPEN)}</code></p><p><code>{esc(LIVE)}</code></p><p><code>{esc(POSTCLOSE)}</code></p>
<p class="muted">El collector LIVE lee estado local y cachés existentes. No consulta PPI/IOL directamente.</p></div></section>

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
</div></section>

<div class="foot">Privado · localhost-only · SSH Port Forwarding · auto-refresh 30 s · sin controles de ejecución.</div>
</div><script>
function tab(id,b){{document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.nav button').forEach(x=>x.classList.remove('active'));document.getElementById(id).classList.add('active');b.classList.add('active')}}
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
