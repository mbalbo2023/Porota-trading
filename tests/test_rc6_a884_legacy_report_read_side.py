"""A884: no previously generated report without provenance may look certified."""

from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import bg_paper_dashboard as dashboard
from rc6_trader_dashboard import views_analytics


def _report_client(monkeypatch, tmp_path, *, detail, state="VERDE"):
    reports = tmp_path / "reports"
    reports.mkdir()
    pdf = reports / "legacy.pdf"
    pdf.write_bytes(b"%PDF-1.4\nOld orders: 0\n")
    row = {
        "id": 17,
        "period_type": "DIARIO",
        "period_key": "2026-10-30",
        "created_at": "2026-10-30T19:00:00-03:00",
        "state": state,
        "detail": detail,
        "pdf_path": str(pdf),
        "ai_path": None,
    }
    monkeypatch.setattr(dashboard, "_installed", False)
    monkeypatch.setattr(dashboard, "_effective_mode", lambda: "PRODUCTION_PAPER")
    monkeypatch.setattr(dashboard, "artifact_root", lambda *_: tmp_path)
    monkeypatch.setattr(dashboard, "_rows", lambda *_a, **_kw: [dict(row)])
    app = FastAPI()
    dashboard.install(app, lambda *_a, **_kw: None)
    return TestClient(app), row, pdf


def test_legacy_report_direct_download_denied_without_deleting_original(monkeypatch, tmp_path):
    client, row, pdf = _report_client(
        monkeypatch, tmp_path, detail="3 operaciones; 0 noticias")
    before = pdf.read_bytes()
    response = client.get("/api/reports/17/pdf")
    assert response.status_code == 409
    assert "LEGACY_CONTENIDO_SIN_PROVENIENCIA" in response.text
    assert pdf.read_bytes() == before
    assert row["state"] == "VERDE"  # Read-only projection must not edit source.


def test_new_a877_unknown_report_download_remains_available(monkeypatch, tmp_path):
    client, _row, pdf = _report_client(
        monkeypatch, tmp_path,
        detail="2 operaciones; CONTENIDO_NO_VERIFICADO: ordenes_reales/FX",
        state="AMARILLO")
    response = client.get("/api/reports/17/pdf")
    assert response.status_code == 200
    assert response.content == pdf.read_bytes()


def test_analytics_report_registry_masks_legacy_green_and_direct_download(monkeypatch, tmp_path):
    row = {
        "id": 211, "period_type": "DIARIO", "state": "VERDE",
        "detail": "3 operaciones; 0 noticias",
        "pdf_path": "/old/reports/legacy.pdf", "ai_path": None,
    }
    page = SimpleNamespace(rows=[dict(row)])
    store = SimpleNamespace(
        path=str(tmp_path / "observer.db"),
        columns=lambda *_a: ["id", "period_type", "state", "pdf_path"],
        page=lambda *_a, **_kw: page,
    )
    projection = SimpleNamespace(
        store=store, offset=0,
        bounded_file=lambda *_a, **_kw: ({}, "UNAVAILABLE"),
    )
    monkeypatch.setattr(views_analytics, "render_table",
                        lambda *_a, **_kw: repr(page.rows))
    shown = views_analytics.render(projection, "analitica", "reportes")
    assert "NO_VERIFICADO" in shown
    assert "LEGACY_CONTENIDO_SIN_PROVENIENCIA" in shown
    assert "/api/reports/211/pdf" not in shown


def test_financial_monthly_view_never_sums_around_unknown_closed_pnl(monkeypatch, tmp_path):
    database = tmp_path / "monthly.db"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE paper_positions(
            closed_at TEXT,status TEXT,currency TEXT,net_pnl TEXT)""")
        connection.executemany("INSERT INTO paper_positions VALUES(?,?,?,?)", [
            ("2026-10-30T18:00:00Z", "CLOSED", "ARS", "3.00"),
            ("2026-10-30T19:00:00Z", "CLOSED", "ARS", None),
            ("2026-09-30T18:00:00Z", "CLOSED", "ARS", "2.00"),
        ])

    def actual_sql(query, *args, **kwargs):
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(r) for r in connection.execute(query).fetchall()]

    monkeypatch.setattr(dashboard, "_table", lambda name: name == "paper_positions")
    monkeypatch.setattr(dashboard, "_rows", actual_sql)
    monkeypatch.setattr(dashboard, "_porota_leaders_proxy", lambda: {
        "returns": [], "average": None, "median": None,
        "breadth": "s/d", "as_of": None,
    })
    html = dashboard.financial_page()
    assert "2026-10</td>" in html
    assert re.search(r"2026-10</td>.*?NO_VERIFICADO ARS", html)
    assert re.search(r"2026-09</td>.*?2,00 ARS", html)
