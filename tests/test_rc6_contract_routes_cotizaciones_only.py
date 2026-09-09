import rc6_trusted_browser_contract_collector as c

def test_all_navigation_routes_are_cotizaciones_only():
    routes=[r for group in c.ROUTES.values() for r in group]
    assert routes
    assert all(r.startswith('/Cotizaciones/') for r in routes), routes
    assert not any('/Operar' in r or '/Orden' in r for r in routes)

def test_collector_network_methods_remain_read_only():
    assert c.SAFE_METHODS == {'GET','HEAD','OPTIONS'}
