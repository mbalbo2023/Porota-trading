from pathlib import Path


def test_live_page_is_day_only_sql_paginated_and_manual_refresh():
    s=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "def _live_session_snapshot" in s
    assert "ORDER BY closed_at DESC LIMIT ? OFFSET ?" in s
    assert "ORDER BY d.decided_at DESC,d.id DESC LIMIT ? OFFSET ?" in s
    assert "closed_total" in s and "decision_total" in s
    assert "closed_all=list(" not in s
    assert "all_live_decisions=list(" not in s
    assert "decision_pager=_live_pager" in s
    assert "return _document(\"En vivo\",body,refresh=0)" in s
    assert "return _document(\"En vivo\",body,refresh=15)" not in s
    assert "overflow:auto}.paper-card" not in s
    assert "table-layout:fixed" in s


def test_live_route_exposes_bounded_pagination():
    s=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "offset:int=Query(default=0,ge=0)" in s
    assert "limit:int=Query(default=10,ge=1,le=10)" in s
    assert "closed_offset:int=Query(default=0,ge=0)" in s
    assert "decision_offset:int|None=Query(default=None,ge=0)" in s
    assert "live_page(offset=offset,limit=limit,closed_offset=closed_offset" in s


def test_table_layout_installs_single_ten_row_pager():
    s=Path("eq_dashboard_table_layout_rc6.py").read_text(encoding="utf-8")
    assert "ownerOfPager" in s
    assert "data-porota-table-pager" in s
    assert "Mostrar más" in s
