from __future__ import annotations

import importlib


def mod():
    import es_dashboard_go_live_ux_rc6 as m
    return importlib.reload(m)


def test_remove_workers_card_only():
    m=mod()
    html=("<main><div class='paper-card'><h2>4. X</h2><p>x</p></div>"
          "<div class='paper-card'><h2>5. Motores / workers</h2><table><tr><td>secret</td></tr></table></div>"
          "<div class='paper-card'><h2>6. Y</h2><p>y</p></div></main>")
    out=m._remove_card_by_heading(html,"5. Motores / workers")
    assert "Motores / workers" not in out
    assert "secret" not in out
    assert "4. X" in out and "6. Y" in out


def test_operational_day_calendar():
    m=mod()
    assert m._operational_day("2026-09-05T12:00:00-03:00") is False  # sábado
    assert m._operational_day("2026-09-06T12:00:00-03:00") is False  # domingo
    assert m._operational_day("2026-09-07T12:00:00-03:00") is True   # BYMA opera


def test_health_neutralizes_known_nonblockers(monkeypatch):
    m=mod()
    m._original["health_components"]=lambda:[
        {"key":"PPI_PRODUCTION_HISTORY","state":"AMARILLO","detail":"parcial","applicable":True},
        {"key":"PPI_PRODUCTION_AUTH","state":"VERDE","detail":"ok","applicable":True},
        {"key":"TELEGRAM","state":"ROJO","detail":"bad","applicable":True},
    ]
    rows=m._health_components_truth()
    assert rows[0]["state"]=="GRIS"
    assert rows[1]["state"]=="VERDE"
    assert rows[2]["state"]=="ROJO"


def test_history_operator_page_excludes_requested_noise(monkeypatch):
    m=mod()
    monkeypatch.setattr(m.bg,"_table",lambda name: name in {"source_sync","production_history_attempts"})
    def fake_rows(sql,*args,**kwargs):
        if "FROM source_sync" in sql:
            return [{"source":"PPI_PRODUCTION_HISTORY","status":"AMARILLO","last_attempt_at":"2026-09-06T23:00:00+00:00","last_success_at":"2026-09-06T23:00:00+00:00","items":100,"detail":"parcial"}]
        if "GROUP BY state" in sql:
            return [{"state":"PARTIAL","identities":2,"valid_rows":200}]
        if "ORDER BY julianday" in sql:
            return [{"symbol":"AAPL","instrument_type":"CEDEARS","state":"PARTIAL","valid_rows":100,"attempted_at":"2026-09-06T23:00:00+00:00","detail":"x"}]
        return []
    monkeypatch.setattr(m.bg,"_rows",fake_rows)
    monkeypatch.setattr(m,"_history_query",lambda sql,params=():
        ([{"rows":1000,"identities":10,"first_day":"2025-01-01","latest_day":"2026-09-04"}]
         if "MIN(date)" in sql else
         [{"rows":100,"identities":4,"latest_day":"2026-09-04"}]
         if "history_close_canonical_v1" in sql else
         [{"source":"PPI_PRODUCTION_HISTORY","rows":900,"identities":9,"latest":"2026-09-04"}]))
    html=m.history_page()
    assert "Base objetiva" not in html
    assert "Cobertura" not in html
    assert "coverage" not in html.lower()
    assert "Fuentes del store canónico" in html
    assert "Últimos 20 intentos" in html
    assert "<table" in html


def test_reports_injects_macro_section():
    m=mod()
    m._original["reports_page"]=lambda:"<html><main><h1>Reportes</h1></main></html>"
    m._original["financial_page"]=lambda:"<html><main><h1>Información financiera</h1><table><tr><td>INDEC</td></tr></table></main></html>"
    html=m.reports_page()
    assert "BCRA / INDEC / macro y performance vs inflación" in html
    assert "INDEC" in html
    assert html.count("<main>")==1


def test_latest_five_filters_non_operational_days(monkeypatch):
    m=mod()
    monkeypatch.setattr(m.bg,"_table",lambda name: name=="paper_positions")
    rows=[]
    for i,stamp in enumerate([
        "2026-09-06T12:00:00-03:00", # domingo, fuera
        "2026-09-05T12:00:00-03:00", # sábado, fuera
        "2026-09-04T16:00:00-03:00",
        "2026-09-03T16:00:00-03:00",
        "2026-09-02T16:00:00-03:00",
        "2026-09-01T16:00:00-03:00",
        "2026-08-31T16:00:00-03:00",
        "2026-08-28T16:00:00-03:00",
    ]):
        rows.append({"paper_id":i,"symbol":f"S{i}","asset_class":"ACCIONES","status":"CLOSED","currency":"ARS","quantity":1,"entry_price":100,"exit_price":101,"net_pnl":1,"close_reason":"TEST","opened_at":stamp,"closed_at":stamp})
    monkeypatch.setattr(m.bg,"_rows",lambda *a,**k: rows)
    html=m._latest_five_valid_operations()
    assert "S0" not in html and "S1" not in html
    assert html.count("<tr>")==6  # header + exactly five records
    assert "Últimas 5 operaciones PAPER válidas" in html
