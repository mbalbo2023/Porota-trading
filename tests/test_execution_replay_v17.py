"""Replay con fixtures explícitamente sintéticas; no certifican un feed PPI."""
from dataclasses import replace
from decimal import Decimal as D
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import PaperStore
from bl_candle_engine import CandleArchive
from bs_instrument_contracts import InstrumentContract
from bx_execution_replay import BookEvent, ExecutionAssumptions, ExecutionReplay, FeeTerms, ReplayOrder
from test_candle_archive_v17 import series, bar


def at(seconds):
    return (datetime.fromisoformat('2026-08-28T11:01:00-03:00') + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def setup(tmp_path):
    archive = CandleArchive(PaperStore(str(tmp_path/'replay.db')))
    s = series(settlement='INMEDIATA')
    archive.put(s, bar(), known_at=at(0))
    evidence = (archive.read(s, as_of=at(0))[0]['version_id'],)
    contract = InstrumentContract('GGAL', 'ACCIONES', 'ARS', 'BYMA', 'INMEDIATA', D(1), D(1), 'TEST')
    fees = FeeTerms(s.key, 'TEST_ALL_IN_NOT_PPI', at(-1), at(-60), at(3600), D('.01'), D(0), D(0))
    assumptions = ExecutionAssumptions(D(1), D(0), D('.01'), 0, 120, 'TEST')
    return archive, s, contract, fees, assumptions, evidence


def order(setup, name='buy', submitted=1, **changes):
    return ReplayOrder(**(dict(order_id=name, series_id=setup[1].key, strategy_version='TEST_ONLY',
        submitted_at=at(submitted), expires_at=at(60), side='BUY', quantity=D(5),
        evidence_ids=setup[-1]) | changes))


def book(setup, second=2, **changes):
    return BookEvent(**(dict(event_id=f'b{second}', series_id=setup[1].key, source='TEST_BOOK',
        book_at=at(second), received_at=at(second), bid=D(100), ask=D(101), bid_size=D(100),
        ask_size=D(100), settlement_at=at(second), settlement_source='TEST_CI',
        session_open=True, session_source='TEST_SESSION') | changes))


def run(setup, orders, books, **kwargs):
    engine = ExecutionReplay(*setup[:5])
    return engine.run(orders, books, **(dict(start=at(0), end=at(60), initial_cash='10000') | kwargs))


def test_fills_en_evento_posterior_con_costos_de_ambas_puntas(setup):
    result = run(setup, [order(setup), order(setup, 'sell', 3, side='SELL')],
                 [book(setup), book(setup, 4, bid=D(110), ask=D(111))])
    buy, sell = result['fills']
    assert D(buy['notional']) == 505 and D(buy['fee']) == D('5.05')
    assert D(sell['notional']) == 550 and D(sell['fee']) == D('5.50')
    assert D(result['cash']) == D('10034.45') and D(result['realized_pnl']) == D('34.45')
    assert result['open_quantity'] == '0' and result['pending_proceeds'] == '0'
    assert not result['promotion_allowed']
    assert result['observed_drawdown_pct'] is not None


def test_liquidacion_pendiente_no_financia_otra_compra_y_se_acredita_una_vez(setup):
    orders = [order(setup, quantity=D(5)), order(setup, 'sell', 3, side='SELL'),
              order(setup, 'too-soon', 5, quantity=D(1)), order(setup, 'after-due', 11, quantity=D(1))]
    books = [book(setup), book(setup, 4, bid=D(110), ask=D(111), settlement_at=at(10)),
             book(setup, 6), book(setup, 12), book(setup, 13)]
    result = run(setup, orders, books, initial_cash='510.05')
    assert result['orders'][2]['status'] == 'UNFILLED_CASH_HOLDINGS_OR_DEPTH'
    assert result['orders'][3]['status'] == 'FILLED'
    assert D(result['cash']) == D('442.49')
    interim = run(setup, orders[:2], books[:2], initial_cash='510.05', end=at(8))
    assert interim['cash'] == '0' and D(interim['pending_proceeds']) == D('544.5')
    assert D(interim['curve'][-1]['equity']) == D('544.5')


def test_profundidad_compartida_y_remanente_ioc_cancelado(setup):
    assumptions = replace(setup[4], participation=D('.5'))
    setup = (*setup[:4], assumptions, setup[5])
    orders = [order(setup, 'a', quantity=D(4)), order(setup, 'b', quantity=D(4))]
    result = run(setup, orders, [book(setup, ask_size=D(10)), book(setup, 10)])
    assert [f['quantity'] for f in result['fills']] == ['4', '1']
    assert result['orders'][1]['status'] == 'PARTIAL_CANCELLED'
    assert result['open_quantity'] == '5'


def test_caja_incluye_minimo_fijo_slippage_y_redondeo(setup):
    fees = replace(setup[3], rate=D(0), minimum=D(2), fixed=D(1))
    assumptions = replace(setup[4], slippage_bps=D(10), price_tick=D('.05'))
    setup = (*setup[:3], fees, assumptions, setup[5])
    result = run(setup, [order(setup, quantity=D(2))], [book(setup)], initial_cash='205.29')
    assert result['fills'][0]['price'] == '101.15'
    assert result['fills'][0]['quantity'] == '1' and result['fills'][0]['fee'] == '3'
    assert result['orders'][0]['status'] == 'PARTIAL_CANCELLED'
    assert D(result['cash']) == D('101.14')


def test_mismo_libro_repetido_no_repone_liquidez(setup):
    first = book(setup, settlement_at=at(20), ask_size=D(5))
    duplicate = replace(first, event_id='duplicate', received_at=at(4), ask_size=D('5.00'))
    result = run(setup, [order(setup), order(setup, 'second', 3)], [first, first, duplicate])
    assert len(result['fills']) == 1
    assert result['orders'][1]['status'] == 'EXPIRED'


@pytest.mark.parametrize('change', [{'ask':D(102)}, {'ask_size':D(200)}])
def test_libro_contradictorio_no_elige_precio_favorable(setup, change):
    first = book(setup, settlement_at=at(20))
    duplicate = replace(first, event_id='other', received_at=at(4), **change)
    with pytest.raises(ValueError, match='contradictorio'):
        run(setup, [order(setup)], [first, duplicate])


def test_duplicado_de_id_con_otro_payload_es_error(setup):
    with pytest.raises(ValueError, match='Mismo ID'):
        run(setup, [], [book(setup), book(setup, ask=D(102))])


def test_no_fill_con_el_libro_de_la_senal_ni_con_uno_anterior_recibido_tarde(setup):
    same = book(setup, 1, settlement_at=at(20))
    late = book(setup, 3, book_at=at(0), settlement_at=at(20))
    result = run(setup, [order(setup)], [same, late, book(setup, 5)])
    assert result['fills'][0]['book_id'] == 'b5'


def test_libro_fuera_de_orden_no_ejecuta_a_precio_viejo(setup):
    # El primer libro no permite operar. El siguiente llega atrasado, pero
    # sigue siendo posterior a la orden: no basta chequear sólo la decisión.
    result = run(setup, [order(setup)], [book(setup, 5, session_open=False),
        book(setup, 6, book_at=at(4), ask=D(99), bid=D(98)), book(setup, 7)])
    assert result['fills'][0]['book_id'] == 'b7'


def test_latencia_exige_libro_posterior_al_arribo_modelado(setup):
    setup = (*setup[:4], replace(setup[4], latency_seconds=3), setup[5])
    result = run(setup, [order(setup)], [book(setup), book(setup, 5, book_at=at(3)), book(setup, 7)])
    assert result['fills'][0]['book_id'] == 'b7'


@pytest.mark.parametrize('changes', [{'session_open':False}, {'book_at':at(-200)}])
def test_sin_sesion_o_libro_vigente_no_inventa_fill(setup, changes):
    result = run(setup, [order(setup)], [book(setup, **changes)])
    assert result['fills'] == []


def test_vencimiento_no_ejecuta_en_el_limite(setup):
    result = run(setup, [order(setup, expires_at=at(2))], [book(setup)])
    assert result['orders'][0]['status'] == 'EXPIRED' and not result['fills']


def test_no_venta_en_descubierto(setup):
    result = run(setup, [order(setup, side='SELL')], [book(setup)])
    assert not result['fills'] and result['cash'] == '10000'


@pytest.mark.parametrize('changes', [{'known_at':at(2)}, {'valid_until':at(2)}])
def test_costos_futuros_o_vencidos_rechazados(setup, changes):
    setup = (*setup[:3], replace(setup[3], **changes), *setup[4:])
    result = run(setup, [order(setup)], [book(setup)])
    assert result['orders'][0]['status'] == 'REJECTED_COST_TERMS'


def test_doble_costo_se_recalcula_sobre_cada_fill(setup):
    orders = [order(setup), order(setup, 'sell', 3, side='SELL')]
    books = [book(setup), book(setup, 4, bid=D(110), ask=D(111))]
    normal = run(setup, orders, books)
    stressed = (*setup[:3], replace(setup[3], rate=D('.02')), *setup[4:])
    stressed = run(stressed, orders, books)
    assert D(normal['realized_pnl']) - D(stressed['realized_pnl']) == D('10.55')
    assert normal['run_id'] != stressed['run_id']


def test_salidas_parciales_concilian_costo_y_no_venden_mas_tenencia(setup):
    result = run(setup,[order(setup),order(setup,'s1',3,side='SELL',quantity=D(2)),
                       order(setup,'s2',5,side='SELL',quantity=D(8))],
                 [book(setup),book(setup,4,bid=D(110),ask=D(111)),book(setup,6,bid=D(120),ask=D(121))])
    assert [f['quantity'] for f in result['fills']] == ['5','2','3']
    assert result['orders'][-1]['status'] == 'PARTIAL_CANCELLED'
    assert result['open_quantity'] == '0' and result['open_cost_basis'] == '0'
    assert D(result['realized_pnl']) == D('64.15') and D(result['cash']) == D('10064.15')


def test_costo_de_venta_mayor_que_producido_no_genera_caja_negativa(setup):
    setup = (*setup[:3],replace(setup[3],rate=D(0),fixed=D(1000)),*setup[4:])
    result = run(setup,[order(setup),order(setup,'sell',3,side='SELL')],[book(setup),book(setup,4)])
    assert len(result['fills']) == 1 and result['open_quantity'] == '5'
    assert result['orders'][-1]['status'] == 'REJECTED_NEGATIVE_PROCEEDS'


def test_costo_de_salida_desconocido_no_inventa_patrimonio(setup):
    setup = (*setup[:3],replace(setup[3],valid_until=at(10)),*setup[4:])
    result = run(setup,[order(setup)],[book(setup)])
    assert result['curve'][-1]['quality'] == 'UNKNOWN_EXIT_COST'
    assert result['observed_drawdown_pct'] is None


def test_evidencia_futura_revisada_o_de_otra_serie_se_rechaza(setup):
    archive, s = setup[:2]
    archive.put(s, bar(open=D(99)), known_at=at(10))
    future = archive.read(s, as_of=at(10))[0]['version_id']
    with pytest.raises(ValueError, match='Evidencia'):
        run(setup, [order(setup, evidence_ids=(future,))], [book(setup)])
    with pytest.raises(ValueError, match='Evidencia'):
        run(setup, [order(setup, submitted=11)], [book(setup, 12)])
    # La corrección posterior no cambia el conjunto conocido al decidir.
    assert run(setup, [order(setup)], [book(setup)])['fills']


def test_identidad_de_moneda_y_multiplicador_no_se_infiere_del_ticker(setup):
    with pytest.raises(ValueError, match='Serie/contrato'):
        ExecutionReplay(setup[0], setup[1], replace(setup[2], currency='USD_MEP'), *setup[3:5])
    with pytest.raises(ValueError, match='Serie/contrato'):
        ExecutionReplay(setup[0], setup[1], replace(setup[2], cash_multiplier=D('.01')), *setup[3:5])
    with pytest.raises(ValueError, match='Costos'):
        ExecutionReplay(*setup[:3], replace(setup[3], series_id='other'), setup[4])


@pytest.mark.parametrize('changes',[{'adjustment':'SPLIT'},{'price_kind':'TRADE_SAMPLES'},
    {'volume_kind':'UNKNOWN'},{'source':'UNKNOWN'}])
def test_datos_ajustados_muestreados_o_sin_fuente_no_habilitan_replay(setup,changes):
    s = replace(setup[1],**changes)
    with pytest.raises(ValueError):
        ExecutionReplay(setup[0],s,setup[2],replace(setup[3],series_id=s.key),setup[4])


def test_bonos_por_cien_nominales_y_caja_usd_mep(setup):
    archive = setup[0]
    s = series(symbol='AE38D', asset_class='BONOS', currency='USD_MEP',
               cash_multiplier='.01', volume_kind='NOMINAL', settlement='INMEDIATA')
    archive.put(s, bar(), known_at=at(0))
    contract = InstrumentContract('AE38D','BONOS','USD_MEP','BYMA','INMEDIATA',D('.01'),D(100),'TEST')
    fees = replace(setup[3], series_id=s.key)
    evidence = (archive.read(s,as_of=at(0))[0]['version_id'],)
    setup = (archive,s,contract,fees,setup[4],evidence)
    result = run(setup, [order(setup,quantity=D(200))], [book(setup,ask_size=D(1000))])
    assert result['fills'][0]['notional'] == '202'
    assert result['curve'][-1]['currency'] == 'USD_MEP'


@pytest.mark.parametrize('family', ['CAUCIONES', 'FCI', 'FUTUROS', 'OPCIONES'])
def test_familias_especiales_no_simuladas_como_acciones(setup, family):
    s = replace(setup[1], asset_class=family)
    extras = dict(expires_at=at(3600)) if family in {'FUTUROS','OPCIONES'} else {}
    if family == 'FUTUROS': extras.update(initial_margin=D(100),maintenance_margin=D(80))
    if family == 'OPCIONES': extras.update(strike=D(100),underlying='TEST',option_right='CALL')
    contract = replace(setup[2],family=family,**extras)
    with pytest.raises(ValueError,match='especializado'):
        ExecutionReplay(setup[0],s,contract,replace(setup[3],series_id=s.key),setup[4])


def test_marca_vencida_no_presenta_patrimonio_ni_drawdown_completo(setup):
    result = run(setup,[order(setup)],[book(setup)],end=at(200))
    assert result['curve'][-1]['quality'] == 'STALE_MARKS'
    assert result['curve'][-1]['equity'] is None and result['observed_drawdown_pct'] is None
    assert result['open_quantity'] == '5'  # No fuerza cierre al final de los datos.


def test_replay_reproducible_no_escribe_la_base_y_no_depende_de_env(setup,monkeypatch):
    path = Path(setup[0].store.path)
    before = path.read_bytes()
    orders, books = [order(setup)], [book(setup)]
    first = run(setup,orders,books)
    monkeypatch.setenv('PPI_COMISION_ACCIONES','0.99')
    monkeypatch.setenv('BACKTEST_ASSUMED_SPREAD_PCT','100')
    second = run(setup,orders,books)
    assert first == second and path.read_bytes() == before
    json.dumps(first, allow_nan=False)


@pytest.mark.parametrize('changes', [{'rate':D('NaN')},{'rate':D(1)},{'minimum':D(-1)}])
def test_costos_invalidos_no_se_convierten_a_cero(setup,changes):
    with pytest.raises(ValueError): replace(setup[3],**changes)


@pytest.mark.parametrize('changes', [{'participation':D(0)},{'slippage_bps':D(10000)},
    {'price_tick':D(0)},{'latency_seconds':float('nan')},{'maximum_book_age_seconds':0}])
def test_supuestos_invalidos_fallan(setup,changes):
    with pytest.raises(ValueError): replace(setup[4],**changes)


@pytest.mark.parametrize('changes', [{'bid':D(102)},{'bid_size':D(-1)},
    {'book_at':at(3)},{'settlement_at':at(1)},{'session_open':'true'}])
def test_libro_invalido_falla(setup,changes):
    with pytest.raises(ValueError): book(setup,**changes)


def test_ordenes_duplicadas_versiones_mezcladas_y_libros_ajenos_fallan(setup):
    with pytest.raises(ValueError): run(setup,[order(setup)]*2,[book(setup)])
    with pytest.raises(ValueError): run(setup,[order(setup),order(setup,'b',strategy_version='OTHER')],[book(setup)])
    with pytest.raises(ValueError): run(setup,[order(setup)],[book(setup,series_id='other')])
    with pytest.raises(ValueError): run(setup,[order(setup)],[book(setup),book(setup,3,source='OTHER')])
    with pytest.raises(ValueError): run(setup,[order(setup)],[book(setup,received_at=at(3),settlement_at=at(3)),book(setup,3)])


def test_cli_reproduce_manifiesto_sin_modificar_sqlite(setup,tmp_path):
    import subprocess
    result = run(setup,[order(setup)],[book(setup)])
    spec = tmp_path/'input.json'
    spec.write_text(json.dumps(result['manifest']))
    database = Path(setup[0].store.path)
    before = database.read_bytes()
    process = subprocess.run([sys.executable, 'bx_execution_replay.py', '--database', str(database),
                              '--input', str(spec)], capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == result
    assert database.read_bytes() == before


def test_cli_entrada_invalida_no_crea_base(tmp_path):
    import subprocess
    spec = tmp_path/'invalid.json'; spec.write_text('{}')
    database = tmp_path/'absent.db'
    process = subprocess.run([sys.executable, 'bx_execution_replay.py', '--database', str(database),
                              '--input', str(spec)], capture_output=True, text=True, timeout=20)
    assert process.returncode == 2 and not database.exists()
    assert json.loads(process.stdout)['status'] == 'INVALID_REPLAY_INPUT'


def test_callback_no_puede_retrofechar_ni_mezclar_ordenes(setup):
    class Strategy:
        def on_event(self, view):
            return order(setup, submitted=1)
    engine = ExecutionReplay(*setup[:5])
    with pytest.raises(ValueError,match='instante actual'):
        engine.run([], [book(setup)],start=at(0),end=at(30),initial_cash='10000',strategy=Strategy())
    with pytest.raises(ValueError,match='mezclar'):
        engine.run([order(setup)], [book(setup)],start=at(0),end=at(30),initial_cash='10000',strategy=Strategy())


def test_callback_recibe_copia_del_ledger_sin_libros_futuros(setup):
    seen = []
    class Strategy:
        def on_event(self, view):
            seen.append((view['at'],len(view['fills'])))
            view['cash'] = D('9999999')
            view['orders'].append({'status':'PENDING'})
    result = ExecutionReplay(*setup[:5]).run([], [book(setup),book(setup,3)],
        start=at(0),end=at(30),initial_cash='10000',strategy=Strategy())
    assert len(seen) == 2 and result['cash'] == '10000' and not result['orders']


def test_salida_no_puede_inventar_plan_de_entrada(setup):
    with pytest.raises(ValueError,match='plan de entrada'):
        run(setup,[order(setup,side='SELL',entry_order_id='missing')],[book(setup)])
    with pytest.raises(ValueError,match='Sólo una salida'):
        order(setup,entry_order_id='anything')


def test_limite_de_venta_se_respeta_en_replay_preparado(setup):
    result = run(setup,[order(setup),order(setup,'sell',3,side='SELL',price_limit=D(110))],
                 [book(setup),book(setup,4)])
    assert result['orders'][-1]['status'] == 'UNFILLED_PRICE_LIMIT'
    assert result['open_quantity'] == '5'
