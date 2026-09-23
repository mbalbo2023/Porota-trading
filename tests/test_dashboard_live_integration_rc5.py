from pathlib import Path


def test_live_page_is_day_only_paginated_and_manual_refresh():
    s=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "live_policy.decisions_for_live" in s
    assert "live_policy.closed_for_live" in s
    assert "live_policy.page_for_tablet" in s
    assert "decision_pager=\"\"" in s
    assert "dos controles contradictorios" in s
    assert "return _document(\"En vivo\",body,refresh=0)" in s
    assert "return _document(\"En vivo\",body,refresh=15)" not in s
    assert "overflow:auto}.paper-card" not in s
    assert "table-layout:fixed" in s


def test_live_route_exposes_bounded_pagination():
    s=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "offset:int=Query(default=0,ge=0)" in s
    assert "limit:int=Query(default=20,ge=1,le=50)" in s
    assert "live_page(offset=offset,limit=limit)" in s
