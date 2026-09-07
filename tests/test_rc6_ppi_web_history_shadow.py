import inspect
import rc6_ppi_web_history_shadow as m


def test_full_ohlcv_candidate_is_detected_without_declaring_canonical():
    payload=[{'date':'2026-09-04','open':100,'high':110,'low':95,'close':108,'volume':12345}]
    out=m.historical_candidate(payload)
    assert out is not None
    assert out['full_ohlcv_shape'] is True
    assert out['rows_detected']==1


def test_partial_shape_is_candidate_but_not_full_ohlcv():
    payload=[{'fecha':'2026-09-04','apertura':100,'cierre':108}]
    out=m.historical_candidate(payload)
    assert out is not None
    assert out['full_ohlcv_shape'] is False


def test_nonhistorical_payload_is_not_promoted():
    assert m.historical_candidate({'ticker':'GGAL','price':100}) is None


def test_sensitive_keys_are_excluded_from_schema():
    payload={'date':'2026-09-04','open':1,'close':2,'password':'x','token':'y','accountNumber':'z'}
    shape=m.schema_shape(payload)
    joined=' '.join(shape['keys']).lower()
    assert 'password' not in joined
    assert 'token' not in joined
    assert 'account' not in joined


def test_query_string_is_removed_from_persisted_url():
    assert m.clean_url('https://trading.portfoliopersonal.com/api/history?ticker=GGAL&token=secret') == 'https://trading.portfoliopersonal.com/api/history'


def test_operation_routes_are_blocked():
    assert m.path_forbidden('https://trading.portfoliopersonal.com/Orden/123')
    assert m.path_forbidden('https://trading.portfoliopersonal.com/Operar/Confirmar')
    assert m.path_forbidden('https://trading.portfoliopersonal.com/Transferir')
    assert not m.path_forbidden('https://trading.portfoliopersonal.com/Cotizaciones/Bonos')


def test_source_has_no_db_or_order_writer_capability():
    src=inspect.getsource(m)
    assert 'sqlite3' not in src
    assert 'history_canonical_v2' not in src
    assert 'history_versions_v2' not in src
    assert 'send_order' not in src
    assert 'place_order' not in src
    assert 'order_budget' not in src
    assert 'canonical_write": "DENY"' in src


def test_representative_routes_cover_core_families():
    families={x[0] for x in m.REPRESENTATIVE_ROUTES}
    assert {'ACCIONES','CEDEARS','BONOS','BONOS_USD','ON','OPCIONES','FUTUROS','LETRAS','ETF'} <= families
