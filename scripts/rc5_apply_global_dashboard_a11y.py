from pathlib import Path

p=Path('bg_paper_dashboard.py')
s=p.read_text(encoding='utf-8')


def replace_once(old,new,label):
    global s
    n=s.count(old)
    if n != 1:
        raise SystemExit(f'{label}: esperado 1 match, encontrado {n}')
    s=s.replace(old,new,1)


replace_once(
    'from dh_dashboard_compact_lists_hf6 import COMPACT_CSS, pager_html\nimport eb_dashboard_live_policy_hf2 as live_policy\n',
    'from dh_dashboard_compact_lists_hf6 import COMPACT_CSS, pager_html\nfrom ei_dashboard_table_accessibility_rc5 import TABLE_A11Y_CSS, TABLE_A11Y_SCRIPT\nimport eb_dashboard_live_policy_hf2 as live_policy\n',
    'import helper global')

replace_once(
    """        async function refreshPorota(){{
          if(document.hidden || document.querySelector('dialog[open]') ||
             document.activeElement?.closest('.trade-body')) return;
          const opened=[...document.querySelectorAll('details.paper-trade[open]')]
            .map(x=>x.querySelector('[data-trade-id]')?.dataset.tradeId).filter(Boolean);
          const y=window.scrollY;
""",
    """        async function refreshPorota(){{
          const active=document.activeElement;
          if(document.hidden || document.querySelector('dialog[open]') ||
             active?.matches('a,button,input,select,textarea,summary') ||
             active?.closest('.trade-body,.porota-progressive-controls,.compact-pager')) return;
          const opened=[...document.querySelectorAll('details.paper-trade[open]')]
            .map(x=>x.querySelector('[data-trade-id]')?.dataset.tradeId).filter(Boolean);
          const y=window.scrollY;
          const activeId=active?.id || '';
""",
    'refresh espera foco interactivo')

replace_once(
    """            window.scrollTo(0,y);
          }}catch(_error){{}}
""",
    """            window.scrollTo(0,y);
            if(activeId){{
              const replacement=document.getElementById(activeId);
              if(replacement) replacement.focus({{preventScroll:true}});
            }}
          }}catch(_error){{}}
""",
    'refresh restaura foco identificable')

replace_once(
    'f"<title>{_e(title)}</title>{THEME}{RESPONSIVE_CSS}{COMPACT_CSS}</head><body id=\'top\'>{_nav()}{mode_banner()}"\n',
    'f"<title>{_e(title)}</title>{THEME}{RESPONSIVE_CSS}{COMPACT_CSS}{TABLE_A11Y_CSS}</head><body id=\'top\'>{_nav()}{mode_banner()}"\n',
    'css global en document')

replace_once(
    'f"<main class=\'paper-page\'>{controls}{body}</main><footer class=\'paper-footer\'><a class=\'up-link\' href=\'#top\'>↑ Ir al principio</a></footer>{script}</body></html>")\n',
    'f"<main class=\'paper-page\'>{controls}{body}</main><footer class=\'paper-footer\'><a class=\'up-link\' href=\'#top\'>↑ Ir al principio</a></footer>{script}{TABLE_A11Y_SCRIPT}</body></html>")\n',
    'script global en document')

p.write_text(s,encoding='utf-8')

Path('tests/test_dashboard_global_a11y_rc5.py').write_text(r'''from pathlib import Path

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
    assert "replacement.focus({preventScroll:true})" in s


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
''',encoding='utf-8')
