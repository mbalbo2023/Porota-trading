from pathlib import Path

import er_dashboard_truth_projection_rc6 as projection
from dg_dashboard_daily_result_ux_hf6 import daily_results_html
from ei_dashboard_table_accessibility_rc5 import TABLE_A11Y_SCRIPT
from eq_dashboard_table_layout_rc6 import FORCE_COMPACT_CSS


def test_oct01_daily_results_are_eight_and_calendar_filtered():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    helper = Path("dg_dashboard_daily_result_ux_hf6.py").read_text(encoding="utf-8")
    assert "limit_days=8" in source
    assert "últimas ocho ruedas BYMA" in source
    assert "últimas ocho ruedas BYMA" in helper
    assert "es_dia_habil_operativo" in helper
    assert "repeat(4,minmax(0,1fr))" in helper


def test_signed_result_decoration_is_global_and_semantic():
    assert "decorateSignedResults" in TABLE_A11Y_SCRIPT
    assert "SIGNED_RESULT_HEADER" in TABLE_A11Y_SCRIPT
    assert "pnl|resultado|retorno" in TABLE_A11Y_SCRIPT.lower()


def test_headers_and_long_statuses_do_not_overlap_rows():
    assert "position:static!important" in FORCE_COMPACT_CSS
    assert ".paper-table .paper-status" in FORCE_COMPACT_CSS
    assert "white-space:normal!important" in FORCE_COMPACT_CSS


def test_instrument_search_is_applied_in_sql():
    seen = {}
    def table(name):
        return name in {"financial_instrument_catalog", "candidate_identity_v2", "contract_evidence_v2_current"}
    def query(sql, params=()):
        seen["sql"] = sql
        seen["params"] = params
        return []
    projection.instrument_rows(query, table, limit=10, offset=0, q="AL30", family="BONOS")
    assert "upper(c.ticker) LIKE ?" in seen["sql"]
    assert "upper(c.instrument_type)=upper(?)" in seen["sql"]
    assert seen["params"][-2:] == (10, 0)
    assert "%AL30%" in seen["params"]


def test_dashboard_exposes_direct_search_for_both_large_catalogs():
    dashboard = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    universe = Path("bh_universe_dashboard_hf6.py").read_text(encoding="utf-8")
    assert "_instrument_filter_form" in dashboard
    assert 'action="/instrumentos"' not in dashboard
    assert '"/instrumentos"' in dashboard
    assert '"/universo-operativo"' in universe
    assert "Use los filtros para ubicar un instrumento" in universe


def test_evidence_v2_family_timestamp_is_explicit():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "observada {_local_time(item.get('evidence_at'))}" in source


def test_pending_confirmation_can_wrap():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "white-space:normal;max-width:100%" in source
    assert "PENDING_CONFIRMATION" in source


def test_pending_health_card_names_pending_components():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "pending_items" in source
    assert "pending_detail" in source
