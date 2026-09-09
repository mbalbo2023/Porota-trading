import rc6_trusted_browser_contract_collector_v2 as c
import rc6_contract_capture_importer_v2 as i


def test_routes_remain_quote_only_and_methods_readonly():
    routes = [r for group in c.ROUTES.values() for r in group]
    assert routes
    assert all(r.startswith('/Cotizaciones/') for r in routes)
    assert not any('/Operar' in r or '/Orden' in r for r in routes)
    assert c.SAFE_METHODS == {'GET', 'HEAD', 'OPTIONS'}


def test_current_first_party_api_is_not_tied_to_legacy_names():
    assert c.is_first_party_json_candidate('https://api.portfoliopersonal.com/api/Panel/Panel?tipo=1', 'application/json')
    assert c.is_first_party_json_candidate('https://trading.portfoliopersonal.com/api/Widget/ResumenMercado', 'application/json')
    assert not c.is_first_party_json_candidate('https://api.example.com/api/Panel/Panel', 'application/json')


def test_generic_payload_is_sanitized_and_semantically_guarded():
    payload = {
        'payload': [
            {
                'ticker': 'AL30',
                'descripcion': 'Bono',
                'precio': 123.45,
                'accountNumber': 'SECRET',
                'accessToken': 'SECRET_TOKEN',
                'saldoDisponible': 999,
            }
        ]
    }
    got = c.sanitize_endpoint('https://api.portfoliopersonal.com/api/Panel/Panel', payload)
    assert got['kind'] == 'GENERIC_FIRSTPARTY_GET'
    assert got['semantic_guard'] == 'NO_CONTRACT_COMPLETENESS_INFERENCE'
    assert got['rows'][0]['ticker'] == 'AL30'
    text = repr(got).lower()
    assert 'secret' not in text
    assert 'accountnumber' not in text
    assert 'accesstoken' not in text
    assert 'saldodisponible' not in text


def test_every_full_browser_route_has_explicit_family_mapping():
    assert set(c.ROUTES['CONTRACT_EVIDENCE_FULL_BROWSER']) == set(i.ROUTE_FAMILY)


def test_schema_filters_sensitive_top_level_keys():
    shape = c.schema_shape({'payload': [], 'access_token': 'x', 'account': {'id': 1}})
    keys = {x.lower() for x in shape['keys']}
    assert 'access_token' not in keys
    assert 'account' not in keys
