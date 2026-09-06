import eq_dashboard_table_layout_rc6 as layout


def test_many_columns_force_compact_even_on_wide_viewport():
    assert layout.should_force_compact(10, 1200) is True


def test_narrow_content_column_forces_compact():
    assert layout.should_force_compact(6, 700) is True


def test_small_readable_table_stays_tabular():
    assert layout.should_force_compact(4, 900, scroll_width=850) is False


def test_actual_overflow_forces_compact():
    assert layout.should_force_compact(4, 900, scroll_width=1100) is True


def test_rc6_css_and_script_preserve_textual_labels():
    assert "porota-cell-label" in layout.FORCE_COMPACT_CSS
    assert "porotaForceCompact" in layout.FORCE_COMPACT_SCRIPT
    assert "MutationObserver" in layout.FORCE_COMPACT_SCRIPT
