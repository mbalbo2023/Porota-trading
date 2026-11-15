from decimal import Decimal
from pathlib import Path

from dg_dashboard_daily_result_ux_hf6 import (
    daily_results_html, report_cards_html, assert_responsive_ux_invariants,
)
from dh_dashboard_compact_lists_hf6 import paginate, pager_html, assert_compact_list_invariants


def test_daily_result_keeps_currencies_separate():
    html=daily_results_html([{
        "day":"2026-09-02",
        "state":"MIXTO_POR_MONEDA",
        "currencies":[
            {"currency":"ARS","daily_pnl":"1000","return_pct":"0.1"},
            {"currency":"USD_MEP","daily_pnl":"-5","return_pct":"-0.5"},
        ],
        "actions":{"COMPRAS":2,"VENTAS":1},
        "symbols":["AAA","BBB"],
        "families":["ACCIONES"],
        "fills":3,
        "closed_positions":1,
    }])
    assert "ARS" in html and "USD_MEP" in html
    assert "Resultado mixto" in html
    assert "1000" in html and "-5" in html


def test_daily_results_exclude_non_operational_days():
    html=daily_results_html([
        {"day":"2026-09-21","state":"NEUTRO","currencies":[],"actions":{},"symbols":[],"families":[]},
        {"day":"2026-09-20","state":"NEUTRO","currencies":[],"actions":{},"symbols":[],"families":[]},
    ])
    assert "2026-09-21" in html
    assert "2026-09-20" not in html

def test_home_page_does_not_render_official_source_status_panel():
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    home=source[source.index("def home_page():"):source.index("def paper_page(")]
    assert "_official_source_evidence_panel()" not in home
    assert "_daily_results_panel()" in home


def test_report_registry_is_cards_not_wide_table():
    html=report_cards_html([{
        "id":1,"period_type":"DIARIO","period_key":"2026-09-02",
        "state":"AMARILLO","created_at":"2026-09-02T20:00:00-03:00",
        "detail":"Cierre PAPER; CONTENIDO_NO_VERIFICADO: FX/ordenes",
        "pdf_path":"data/reports/day.pdf","ai_path":None,
    }])
    assert "report-card" in html
    assert "<table" not in html
    assert "Descargar PDF" in html


def test_legacy_report_without_a877_content_provenance_is_not_green_or_downloadable():
    html = report_cards_html([{
        "id": 210, "period_type": "DIARIO", "period_key": "2026-10-30",
        "state": "VERDE", "created_at": "2026-10-30T19:00:00-03:00",
        "detail": "3 operaciones; 0 noticias",
        "pdf_path": "/evidence/reports/legacy.pdf",
        "ai_path": "/evidence/reports/legacy.json",
    }])
    assert "Estado:</b> NO_VERIFICADO" in html
    assert "LEGACY_CONTENIDO_SIN_PROVENIENCIA" in html
    assert "Descargar PDF" not in html
    assert "Descargar datos" not in html


def test_a877_content_labeled_unknown_preserves_download_with_explicit_amber():
    html = report_cards_html([{
        "id": 211, "period_type": "DIARIO", "period_key": "2026-10-31",
        "state": "AMARILLO", "created_at": "2026-10-31T19:00:00-03:00",
        "detail": "2 operaciones; CONTENIDO_NO_VERIFICADO: ordenes_reales/FX",
        "pdf_path": "/evidence/reports/reviewed.pdf", "ai_path": None,
    }])
    assert "Estado:</b> AMARILLO" in html
    assert "CONTENIDO_NO_VERIFICADO" in html
    assert "Descargar PDF" in html


def test_unproven_legacy_generation_error_remains_red_without_download():
    html = report_cards_html([{
        "id": 212, "period_type": "DIARIO", "period_key": "2026-10-30",
        "state": "ROJO", "created_at": "2026-10-30T19:00:00-03:00",
        "detail": "ValueError: source unavailable",
        "pdf_path": "/evidence/reports/legacy.pdf", "ai_path": None,
    }])
    assert "Estado:</b> ROJO" in html
    assert "LEGACY_CONTENIDO_SIN_PROVENIENCIA" in html
    assert "Descargar PDF" not in html


def test_quality_marker_prefix_alone_cannot_unlock_legacy_download():
    html = report_cards_html([{
        "id": 213, "period_type": "DIARIO", "period_key": "2026-10-30",
        "state": "AMARILLO", "created_at": "2026-10-30T19:00:00-03:00",
        "detail": "3 operaciones; CONTENIDO_NO_VERIFICADO_FALSO",
        "pdf_path": "/evidence/reports/unproven.pdf", "ai_path": None,
    }])
    assert "LEGACY_CONTENIDO_SIN_PROVENIENCIA" in html
    assert "Descargar PDF" not in html


def test_unattested_green_text_marker_alone_is_not_positive_runtime_proof():
    html = report_cards_html([{
        "id": 214, "period_type": "DIARIO", "period_key": "2026-10-30",
        "state": "VERDE", "created_at": "2026-10-30T19:00:00-03:00",
        "detail": "3 operaciones; CONTENIDO_ACREDITADO",
        "pdf_path": "/evidence/reports/unsigned.pdf", "ai_path": None,
    }])
    assert "LEGACY_CONTENIDO_SIN_PROVENIENCIA" in html
    assert "Descargar PDF" not in html


def test_pagination_caps_large_lists():
    page=paginate(list(range(500)),offset=0,limit=500)
    assert len(page.items) <= 50
    assert page.total == 500
    assert page.next_offset is not None
    html=pager_html("/test",page)
    assert "Siguiente" in html


def test_responsive_invariants():
    assert_responsive_ux_invariants()
    assert_compact_list_invariants()
