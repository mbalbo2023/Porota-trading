"""Regresión a partir de evidencia del operador; no abre PPI ni órdenes reales."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import c_ppi_client as client
import bu_instrument_catalog as catalog
import bf_production_paper_observer as observer
import bg_paper_dashboard as dashboard
from be_paper_engine import PaperStore

EVIDENCE = json.loads((ROOT / 'tests/fixtures/ppi_public_observation_20260828.json').read_text())


@pytest.fixture
def wrapper(monkeypatch):
    calls = []
    def capture(request):
        calls.append(asdict(request))
        return {'status': 'FAKE_RESPONSE'}
    w = client.ResilientPPIClient.__new__(client.ResilientPPIClient)
    w.client = SimpleNamespace(orders=SimpleNamespace(budget=capture, confirm=capture))
    w._call_with_retry = lambda fn, label: fn()
    monkeypatch.setitem(sys.modules, 'ao_startup_gate', SimpleNamespace(interceptar_orden=lambda *args: None))
    return w, calls


@pytest.mark.parametrize('term,expected', [
    (None, 'POR-EL-DIA'), ('POR-EL-DIA', 'POR-EL-DIA'),
    ('POR-EL-DÍA', 'POR-EL-DIA'), (' por-el-día ', 'POR-EL-DIA'),
    ('HASTA-SU-EJECUCION', 'HASTA-SU-EJECUCION'),
    ('HASTA-SU-EJECUCIÓN', 'HASTA-SU-EJECUCION'),
    ('VALIDA-HASTA-EL', 'VALIDA-HASTA-EL'),
    ('VÁLIDA-HASTA-EL', 'VALIDA-HASTA-EL'), ('72-HS', '72-HS')])
def test_terminos_transmitidos_coinciden_con_configuracion_observada(wrapper, term, expected):
    w, calls = wrapper
    args = {} if term is None else {'term': term}
    expiry = datetime.now(timezone.utc) + timedelta(days=1) if expected == 'VALIDA-HASTA-EL' else None
    if expiry:
        args['operation_max_date'] = expiry.isoformat()
    w.budget_order('TEST', 1, 100, 'ALUA', instrument_type='ACCIONES', **args)
    w.confirm_order('TEST', 1, 100, 'ALUA', [], instrument_type='ACCIONES', **args)
    assert len(calls) == 2
    assert all(r['operationTerm'] == expected for r in calls)
    assert all(r['operationTerm'] in EVIDENCE['configuration']['operation_terms'] for r in calls)
    assert all(r['operationMaxDate'] == expiry for r in calls)


@pytest.mark.parametrize('term', ['VALIDA-HASTA-EL', 'VÁLIDA-HASTA-EL'])
@pytest.mark.parametrize('expiry', [None, '2020-01-01T00:00:00Z', '2030-01-01T00:00:00'])
def test_alias_no_elude_validacion_de_vigencia(wrapper, term, expiry):
    w, calls = wrapper
    with pytest.raises(ValueError):
        w.budget_order('TEST', 1, 100, 'ALUA', term=term, operation_max_date=expiry)
    assert calls == []


@pytest.mark.parametrize('operation', [o for o in EVIDENCE['configuration']['operations'] if o not in {'COMPRA', 'VENTA'}])
def test_operacion_enumerada_no_habilita_ruta_especializada(wrapper, operation):
    w, calls = wrapper
    with pytest.raises(ValueError):
        w.confirm_order('TEST', 1, 100, 'TEST', [], operation=operation)
    assert calls == []


@pytest.fixture
def observed_store(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / 'paper.db'))
    observer._support_schema(store)
    monkeypatch.setattr(observer, 'CATALOG_QUERY_SLEEP_SECONDS', 0)
    monkeypatch.setattr(observer, '_candidate_universe', lambda: [
        (r['filter'], r['family'], 'A-24HS', r['market'], False) for r in EVIDENCE['searches']])
    class Reader:
        def market_configuration(self):
            return EVIDENCE['configuration']
        def search_instruments(self, ticker, kind, **kwargs):
            return next(r['sample'] for r in EVIDENCE['searches'] if r['filter'] == ticker)
    assert observer._download_catalog(Reader(), store) == 3
    return store


def coverage(store):
    with store.connect() as c:
        return {r['instrument_type']: dict(r) for r in c.execute('SELECT * FROM catalog_family_coverage')}


def test_todas_las_familias_declaradas_visibles_sin_inventar_instrumentos(observed_store):
    rows = coverage(observed_store)
    assert set(rows) == set(EVIDENCE['configuration']['instrument_types'])
    assert rows['CAUCIONES']['declared'] == 1
    assert rows['CAUCIONES']['discovery_status'] == 'EMPTY_FILTER_RESULTS'
    assert rows['CAUCIONES']['queries'] == 3
    assert rows['CAUCIONES']['observed_count'] == rows['CAUCIONES']['ready_paper_count'] == 0
    assert rows['ACCIONES']['observed_count'] == rows['ACCIONES']['ready_paper_count'] == 3
    assert rows['ACCIONES']['discovery_status'] == 'INSTRUMENTS_OBSERVED'
    for kind in set(rows) - {'ACCIONES', 'CAUCIONES'}:
        assert rows[kind]['discovery_status'] == 'DECLARED_NO_QUERY'
        assert rows[kind]['queries'] == rows[kind]['observed_count'] == rows[kind]['ready_paper_count'] == 0
    with observed_store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM financial_instrument_catalog').fetchone()[0] == 3
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert set(r[0] for r in c.execute('SELECT status FROM catalog_query_results')) == {'AVAILABLE', 'EMPTY_FILTER_RESULT'}


def test_no_consulta_familias_ni_mercados_ausentes_de_configuracion(observed_store, monkeypatch):
    monkeypatch.setattr(observer, '_candidate_universe', lambda: [
        ('MERVAL', 'INDICES', 'A-24HS', 'BYMA', False),
        ('ALUA', 'ACCIONES', 'A-24HS', 'INVENTADO', False)])
    reader = SimpleNamespace(market_configuration=lambda: EVIDENCE['configuration'],
        search_instruments=lambda *a, **k: pytest.fail('No debe consultar combinaciones no enumeradas'))
    assert observer._download_catalog(reader, observed_store) == 0
    rows = coverage(observed_store)
    assert rows['INDICES']['declared'] == 0
    assert rows['INDICES']['discovery_status'] == 'NOT_ENUMERATED'
    assert rows['ACCIONES']['queries'] == 0
    with observed_store.connect() as c:
        states = set(r[0] for r in c.execute('SELECT status FROM catalog_query_results WHERE run_id=?', (rows['INDICES']['run_id'],)))
    assert states == {'TYPE_NOT_ENUMERATED', 'MARKET_NOT_ENUMERATED'}


@pytest.mark.parametrize('bad', [None, {}, {'instrument_types': ['ACCIONES']},
    dict(EVIDENCE['configuration'], markets=[None]),
    dict(EVIDENCE['configuration'], instrument_types=['ACCIONES', 'ACCIONES'])])
def test_configuracion_invalida_no_recicla_declaracion_anterior(observed_store, bad):
    reader = SimpleNamespace(market_configuration=lambda: bad, search_instruments=lambda *a, **k: [])
    assert observer._download_catalog(reader, observed_store) == 0
    rows = coverage(observed_store)
    assert set(rows) == set(EVIDENCE['configuration']['instrument_types'])
    assert all(r['declared'] == -1 for r in rows.values())
    assert all(r['observed_count'] == 0 for r in rows.values())
    assert rows['LICITACIONES']['discovery_status'] == 'CONFIGURATION_UNAVAILABLE'


@pytest.mark.parametrize('bad', [None, {'message': 'error'}, [None], '', [{}], [{'message': 'error'}]])
def test_payload_invalido_no_es_cero_coincidencias(observed_store, bad):
    reader = SimpleNamespace(market_configuration=lambda: EVIDENCE['configuration'],
        search_instruments=lambda *a, **k: bad)
    assert observer._download_catalog(reader, observed_store) == 0
    assert coverage(observed_store)['CAUCIONES']['discovery_status'] == 'QUERY_ERROR'


def test_dashboard_muestra_cobertura_sin_leer_api_ni_escribir(observed_store, monkeypatch):
    monkeypatch.setattr(dashboard, 'DB_PATH', observed_store.path)
    before = Path(observed_store.path).read_bytes()
    page = dashboard.history_page()
    assert 'Cobertura por familia' in page
    assert all(kind in page for kind in EVIDENCE['configuration']['instrument_types'])
    assert 'Filtros sin coincidencias' in page
    assert 'Declarada; sin consulta' in page
    assert 'No acredita permisos ni habilita operaciones' in page
    assert Path(observed_store.path).read_bytes() == before


def test_familia_nueva_y_retirada_no_desaparecen_del_inventario(observed_store, monkeypatch):
    monkeypatch.setattr(observer, '_candidate_universe', lambda: [])
    config = dict(EVIDENCE['configuration'], instrument_types=['NUEVO-PRODUCTO'])
    observer._download_catalog(SimpleNamespace(market_configuration=lambda: config), observed_store)
    rows = coverage(observed_store)
    assert rows['NUEVO-PRODUCTO']['discovery_status'] == 'DECLARED_NO_QUERY'
    assert rows['CAUCIONES']['declared'] == 0
    assert rows['CAUCIONES']['discovery_status'] == 'NOT_ENUMERATED'


@pytest.mark.parametrize('change,reason', [
    ({'type': 'ETF'}, 'TYPE_NOT_ENUMERATED'),
    ({'market': 'MERCADO-NUEVO'}, 'MARKET_NOT_ENUMERATED')])
def test_registro_devuelto_fuera_del_enum_se_conserva_sin_habilitar_paper(observed_store, monkeypatch, change, reason):
    monkeypatch.setattr(observer, '_candidate_universe', lambda: [('ALUA', 'ACCIONES', 'A-24HS', 'BYMA', True)])
    raw = dict(EVIDENCE['searches'][-1]['sample'][0], **change)
    config = dict(EVIDENCE['configuration'], instrument_types=['ACCIONES'])
    reader = SimpleNamespace(market_configuration=lambda: config, search_instruments=lambda *a, **k: [raw])
    assert observer._download_catalog(reader, observed_store) == 1
    record = catalog.lookup(observed_store, raw['ticker'], raw['type'], 'A-24HS')
    assert record['capability'] == catalog.quote_terms(record)['opening_block_reason'] == reason
    assert coverage(observed_store)[raw['type']]['ready_paper_count'] == 0
    with observed_store.connect() as c:
        assert not c.execute('SELECT 1 FROM candidate_universe WHERE can_simulate=1').fetchone()


def test_errores_parciales_no_afirman_cobertura_completa(observed_store, monkeypatch):
    monkeypatch.setattr(observer, '_candidate_universe', lambda: [
        ('ALUA', 'ACCIONES', 'A-24HS', 'BYMA', True), ('ALU', 'ACCIONES', 'A-24HS', 'BYMA', True)])
    def search(ticker, *a, **k):
        if ticker == 'ALU':
            raise TimeoutError('FIXTURE_ONLY')
        return EVIDENCE['searches'][-1]['sample']
    reader = SimpleNamespace(market_configuration=lambda: EVIDENCE['configuration'], search_instruments=search)
    assert observer._download_catalog(reader, observed_store) == 3
    row = coverage(observed_store)['ACCIONES']
    assert row['discovery_status'] == 'OBSERVED_WITH_ERRORS'
    assert row['queries'] == 2 and row['observed_count'] == 3


def test_configuracion_vacia_es_valida_y_distinta_de_falla(observed_store, monkeypatch):
    reader = SimpleNamespace(market_configuration=lambda: {k: [] for k in EVIDENCE['configuration']},
        search_instruments=lambda *a, **k: pytest.fail('No consultar enums vacíos'))
    assert observer._download_catalog(reader, observed_store) == 0
    assert all(r['declared'] == 0 and r['queries'] == 0 for r in coverage(observed_store).values())


def test_configuracion_no_mutable_y_campos_sin_normalizacion():
    config = catalog.validate_configuration(EVIDENCE['configuration'])
    config['operations'].append('NUEVA')
    assert 'NUEVA' not in EVIDENCE['configuration']['operations']
    assert 'COLOCAR-CAUCION' in config['operations']
    assert 'COLOCAR-CAUCIÓN' not in config['operations']


def test_dashboard_antiguo_no_migra_para_mostrar_inventario(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / 'old.db'))
    monkeypatch.setattr(dashboard, 'DB_PATH', store.path)
    with store.connect() as c:
        c.execute('DROP TABLE IF EXISTS catalog_family_coverage')
    assert 'Sin inventario de familias persistido' in dashboard._family_coverage_panel()
    with store.connect() as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_family_coverage'").fetchone()
