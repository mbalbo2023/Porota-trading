import er_dashboard_table_semantics_rc6 as layout
import ev_dashboard_table_accessibility_rc6 as a11y


def test_classic_policy_never_converts_tables_to_cards():
    assert layout.table_layout_policy() == "CLASSIC_ROWS_COLUMNS"
    cases = [
        (10, 1200, None),
        (6, 700, None),
        (4, 900, 1100),
        (12, 480, 2200),
        (2, 180, 500),
    ]
    for columns, available, scroll_width in cases:
        assert layout.should_force_compact(columns, available, scroll_width) is False


def test_wide_tables_get_local_readable_minimum_width():
    assert layout.recommended_table_min_width(4) == 0
    assert layout.recommended_table_min_width(5) == 0
    assert layout.recommended_table_min_width(6) >= 700
    assert layout.recommended_table_min_width(10) == 1160
    assert layout.recommended_table_min_width(20) == 1320


def test_accessibility_layer_preserves_native_table_semantics():
    a11y.assert_table_accessibility_contract()
    css = a11y.TABLE_A11Y_CSS
    script = a11y.TABLE_A11Y_SCRIPT
    assert "display:table!important" in css
    assert "display:table-header-group!important" in css
    assert "display:table-row-group!important" in css
    assert "display:table-row!important" in css
    assert "display:table-cell!important" in css
    assert "porota-table-scroll" in css
    assert "data-porota-compact" not in css
    assert "porota-cell-label" not in css
    assert "scope','col'" in script
    assert "role','region'" in script
    assert "porotaInitClassicTables" in script
