from pathlib import Path

import ei_dashboard_table_accessibility_rc5 as a11y


def test_global_table_contract_is_accessible_and_progressive():
    a11y.assert_table_accessibility_contract()
    assert a11y.TABLE_PAGE_SIZE == 20
    assert "max-width:980px" in a11y.TABLE_A11Y_CSS
    assert "porota-cell-label" in a11y.TABLE_A11Y_CSS
    assert "Mostrar menos" in a11y.TABLE_A11Y_SCRIPT
    assert "sessionStorage" in a11y.TABLE_A11Y_SCRIPT
    assert "observer.disconnect()" in a11y.TABLE_A11Y_SCRIPT


def test_dashboard_wires_global_contract_and_non_disruptive_refresh():
    s=Path('bg_paper_dashboard.py').read_text(encoding='utf-8')
    assert 'TABLE_A11Y_CSS' in s
    assert 'TABLE_A11Y_SCRIPT' in s
    assert "active?.matches('a,button,input,select,textarea,summary')" in s
    assert "active?.closest('.trade-body,.porota-progressive-controls,.compact-pager')" in s
    assert "replacement.focus({{preventScroll:true}})" in s


def test_named_heavy_pages_keep_canonical_tables_for_global_enhancement():
    s=Path('bg_paper_dashboard.py').read_text(encoding='utf-8')
    required=(
        'Históricos y universo',
        'Base objetiva para ampliar el lote por ciclo',
        'Instrumentos y contratos',
        'Scalping intradiario',
        'Operaciones scalping PAPER',
        'Aprendizaje del sistema',
        'Expectativa empírica por moneda',
    )
    for marker in required:
        assert marker in s
    assert s.count("class='paper-table'") >= 10
