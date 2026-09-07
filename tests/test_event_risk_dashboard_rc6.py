import ast,inspect
import fj_event_risk_dashboard_rc6 as view


def test_render_semantic_table_and_shadow_safety():
    html=view.render_event_risk_page([{
        'event_id':'e1','event_type':'OIL_SUPPLY_SHOCK','region':'MIDDLE_EAST',
        'confirmation':'MULTI_SOURCE','summary':'Disrupción de oferta confirmada por varias fuentes.',
        'exposures':['OIL','ENERGY'],'identities':['YPFD|ACCIONES|BYMA|ARS|A-24HS'],
        'bias':'MIXED','confidence':'0.80','freshness':'FRESH','source':'GDELT+OFFICIAL',
        'source_tier':'TIER_B_MULTI_SOURCE_CONFIRMED','available_to_engine_at':'2026-09-07T15:00:00Z',
        'provenance_url':'https://example.invalid/evidence'
    }],global_risk='HIGH',updated_at='2026-09-07T16:00:00-03:00')
    assert '<caption>Eventos globales observados por el motor SHADOW</caption>' in html
    assert "<th scope='col'>Evento</th>" in html
    assert 'GLOBAL_EVENT_RISK:</strong> HIGH' in html
    assert 'Puede bloquear PAPER:</strong> NO' in html
    assert 'Puede enviar órdenes:</strong> NO' in html
    assert 'No contiene recomendaciones BUY/SELL' in html
    assert 'YPFD|ACCIONES|BYMA|ARS|A-24HS' in html
    assert 'available_to_engine_at' in html


def test_escapes_untrusted_content_and_unknown_enums_fail_safe():
    html=view.render_event_risk_page([{
        'event_type':'<script>x</script>','summary':'<img src=x onerror=1>',
        'risk':'SUPER','bias':'BUY','confirmation':'CERTAIN','provenance_url':'javascript:alert(1)'
    }])
    assert '<script>' not in html and '<img' not in html and 'javascript:alert' not in html
    assert 'UNKNOWN' in html
    assert '>BUY<' not in html


def test_module_has_no_db_network_broker_or_order_imports():
    tree=ast.parse(inspect.getsource(view)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    forbidden={'sqlite3','requests','httpx','urllib','socket'}
    assert not any(x.split('.')[0] in forbidden or 'broker' in x.lower() or 'order' in x.lower() for x in imports)
    view.assert_shadow_only()
