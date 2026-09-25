import eq_dashboard_table_layout_rc6 as layout


def test_rc6_table_layout_never_hides_header_row():
    css = layout.FORCE_COMPACT_CSS
    assert "display:table-header-group!important" in css
    assert "visibility:visible!important" in css
    assert "clip:rect(0,0,0,0)" not in css
    assert "position:absolute!important;width:1px" not in css


def test_rc6_table_headers_are_sticky_and_wide_tables_fit_tablet_viewport():
    css = layout.FORCE_COMPACT_CSS
    assert "position:sticky!important" in css
    assert "width:100%!important" in css
    assert "table-layout:fixed!important" in css
    assert "max-width:100%!important" in css
    assert "width:max-content!important" not in css
    assert "display:table!important" in css
    assert ".porota-cell-label{display:none!important}" in css
