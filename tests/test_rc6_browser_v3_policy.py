import ast
from pathlib import Path
import rc6_trusted_browser_contract_collector as c

def test_route_catalog_is_cotizaciones_only():
    c.assert_safe_route_catalog()
    assert c.ROUTES
    assert all(route.startswith('/Cotizaciones/') for routes in c.ROUTES.values() for route in routes)
    assert all('/Operar' not in route for routes in c.ROUTES.values() for route in routes)

def test_v3_mutation_policy_is_present_statically():
    s=Path('rc6_trusted_browser_contract_collector.py').read_text(encoding='utf-8')
    assert 'POROTA_CE_NONREAD_POLICY_V3' in s
    assert '/api/logger' in s
    assert 'zendesk-session' in s
    assert 'blocked_nonread' in s
    assert 'route.abort()' in s

def test_no_broker_order_imports():
    tree=ast.parse(Path('rc6_trusted_browser_contract_collector.py').read_text(encoding='utf-8'))
    imported=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imported.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom): imported.append(n.module or '')
    joined=' '.join(imported).lower()
    assert 'ppi_client' not in joined
    assert 'c_ppi_client' not in joined
