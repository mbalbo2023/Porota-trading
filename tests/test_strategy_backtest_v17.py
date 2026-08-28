"""Decisiones y fills temporales con series sintéticas; no rentabilidad real."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import PaperStore
from bl_candle_engine import Bar, CandleArchive, stamp
from bo_signal_core import SessionGrid, SignalConfig, candidate, window_at
from bs_instrument_contracts import InstrumentContract
from bx_execution_replay import BookEvent, ExecutionAssumptions, ExecutionReplay, FeeTerms, ReplayOrder
from by_strategy_backtest import run_strategy
from bz_replay_risk import ReplayRiskConfig
from test_candle_archive_v17 import series


def at(seconds):
    return (datetime.fromisoformat('2026-08-28T11:08:00-03:00') + timedelta(seconds=seconds)).isoformat()


@pytest.fixture
def setup(tmp_path):
    archive = CandleArchive(PaperStore(str(tmp_path/'strategy.db')))
    s = series(settlement='INMEDIATA')
    for i in range(8):
        p = D(101+i)
        archive.put(s, Bar(at((i-8)*60),p,p+1,p-1,p,volume=D(100),quality='COMPLETE'),
                    known_at=at((i-7)*60))
    contract = InstrumentContract('GGAL','ACCIONES','ARS','BYMA','INMEDIATA',D(1),D(1),'TEST')
    fees = FeeTerms(s.key,'TEST_NOT_PPI',at(-600),at(-600),at(3600),D('.001'),D(0),D(0))
    assumptions = ExecutionAssumptions(D(1),D(0),D('.01'),0,120,'TEST')
    engine = ExecutionReplay(archive,s,contract,fees,assumptions)
    config = SignalConfig(at(-600),3,8,3,D('.62'),D('.02'),D(1),D(3),D('1.5'),
                          D('.01'),D('.25'),D(0),120,30,600)
    grid = SessionGrid(tuple(at(i*60) for i in range(-8,20)),'TEST_CALENDAR',at(-600),at(-480),at(1200))
    return engine,config,grid


def book(setup, second, bid='108.9', ask='109', **changes):
    return BookEvent(**(dict(event_id=f'b{second}',series_id=setup[0].series.key,source='TEST_BOOK',
        book_at=at(second),received_at=at(second),bid=D(bid),ask=D(ask),bid_size=D(100),ask_size=D(100),
        settlement_at=at(second),settlement_source='TEST',session_open=True,session_source='TEST') | changes))


def run(setup, books, **kwargs):
    return run_strategy(*[setup[0],books,*setup[1:]],**(dict(start=at(0),end=at(30),initial_cash='10000',
        risk_config=ReplayRiskConfig(at(-600),D(5))) | kwargs))


def scenario(setup):
    return [book(setup,1),book(setup,2),book(setup,3,'115','115.1'),book(setup,4,'116','116.1')]


def test_candidato_exige_riesgo_explicito_y_congelado(setup):
    with pytest.raises(ValueError):
        run(setup,scenario(setup),risk_config=None)
    with pytest.raises(ValueError):
        run(setup,scenario(setup),risk_config=ReplayRiskConfig(at(1),D(1)))


def test_corte_genera_salida_posterior_y_no_reentra_con_recuperacion(setup):
    result = run(setup,[book(setup,1),book(setup,2),book(setup,3,'80','81'),
        book(setup,4,'115','116'),book(setup,5)],risk_config=ReplayRiskConfig(at(-600),D(1)))
    assert result['decisions'][2]['code']=='DAILY_LOSS_LIMIT'
    assert result['fills'][-1]['executed_at']==stamp(at(4))
    assert result['decisions'][-1]['code']=='DAILY_RISK_LATCHED'
    assert [f['side'] for f in result['fills']]==['BUY','SELL']
    assert result['daily_risk']['days'][0]['state']=='LATCHED'


def test_corte_sin_libro_ejecutable_conserva_intencion_y_salida_parcial(setup):
    result = run(setup,[book(setup,1),book(setup,2),book(setup,3,'80','81',session_open=False),
        book(setup,4),book(setup,5,bid_size=D(1)),book(setup,6)],
        risk_config=ReplayRiskConfig(at(-600),D(1)))
    assert result['decisions'][2]['code']=='EXIT_WAITING_FOR_BOOK'
    assert result['decisions'][2]['exit_reason']=='DAILY_LOSS_LIMIT'
    assert result['decisions'][3]['code']==result['decisions'][4]['code']=='DAILY_LOSS_LIMIT'
    assert len(result['fills'])==3 and result['open_quantity']=='0'


def test_dimensionamiento_respeta_presupuesto_diario_menor_que_por_operacion(setup):
    result = run(setup,[book(setup,1)],risk_config=ReplayRiskConfig(at(-600),D('.10')))
    decision = result['decisions'][0]
    assert decision['action']=='BUY' and D(decision['quantity'])<22
    assert D(decision['features']['risk_budget'])==10
    assert D(decision['features']['modeled_stop_loss'])<=10


def test_limite_diario_forma_parte_de_version_y_normaliza_representacion(setup):
    one = run(setup,scenario(setup),risk_config=ReplayRiskConfig(at(-600),D(1)))
    same = run(setup,scenario(setup),risk_config=ReplayRiskConfig(stamp(at(-600)),D('1.00')))
    two = run(setup,scenario(setup),risk_config=ReplayRiskConfig(at(-600),D(2)))
    assert one==same
    assert one['strategy_version']!=two['strategy_version']
    assert one['manifest']['orders'][0]['order_id']!=two['manifest']['orders'][0]['order_id']


def test_genera_entrada_y_salida_con_fills_posteriores_y_caja_conciliada(setup):
    result = run(setup,scenario(setup))
    buy,sell = result['fills']
    assert buy['executed_at'] == stamp(at(2)) and sell['executed_at'] == stamp(at(4))
    assert buy['price'] == '109' and sell['price'] == '116'
    assert result['decisions'][0]['action'] == 'BUY'
    assert result['decisions'][2]['code'] == 'TARGET_TRIGGER'
    assert result['decisions'][-1]['code'] == 'FLAT_AFTER_EXIT'
    expected = D(sell['notional'])-D(sell['fee'])-D(buy['notional'])-D(buy['fee'])
    assert D(result['cash']) == D(10000)+expected == D(10000)+D(result['realized_pnl'])
    assert result['open_quantity'] == '0' and not result['promotion_allowed']


def test_resultado_equivale_a_replay_de_ordenes_generadas(setup):
    books = scenario(setup)
    dynamic = run(setup,books)
    orders = [ReplayOrder(**(o | {'evidence_ids':tuple(o['evidence_ids'])})) for o in dynamic['manifest']['orders']]
    fixed = setup[0].run(orders,books,start=at(0),end=at(30),initial_cash='10000',
                         risk_config=ReplayRiskConfig(**dynamic['manifest']['risk_config']))
    assert dynamic['execution_run_id'] == fixed['run_id']
    assert dynamic['fills'] == fixed['fills'] and dynamic['curve'] == fixed['curve']


def test_prefijo_de_decisiones_no_cambia_por_libros_futuros(setup):
    prefix = [book(setup,1),book(setup,2)]
    short = run(setup,prefix,end=at(2))
    future = run(setup,prefix+[book(setup,3,'20','21'),book(setup,4,'200','201')])
    assert short['decisions'] == future['decisions'][:1]
    assert short['fills'] == future['fills'][:1]


def test_revision_futura_no_altera_decision_pasada(setup):
    before = run(setup,scenario(setup))
    engine = setup[0]
    engine.archive.put(engine.series,Bar(at(-60),D(90),D(91),D(89),D(90),volume=D(100),quality='COMPLETE'),
                       known_at=at(100))
    after = run(setup,scenario(setup))
    assert before == after


def test_revision_invalida_no_impide_salida_de_proteccion(setup):
    engine = setup[0]
    engine.archive.put(engine.series,Bar(at(-60),D(108),D(109),D(107),D(108),volume=D(100),quality='CONFLICT'),
                       known_at=at(3))
    result = run(setup,[book(setup,1),book(setup,2),book(setup,3,'106','106.1'),book(setup,4,'100','100.1')])
    assert result['decisions'][2]['code'] == 'STOP_TRIGGER'
    assert result['fills'][-1]['price'] == '100'  # No vende mágicamente en 107.
    assert result['open_quantity'] == '0'
    assert result['manifest']['orders'][-1]['entry_order_id'] == result['fills'][0]['order_id']


def test_limite_de_compra_no_ejecuta_gap_que_rompe_presupuesto(setup):
    result = run(setup,[book(setup,1),book(setup,2,'120','120.1'),book(setup,3)])
    assert result['fills'] == []
    assert result['orders'][0]['status'] == 'UNFILLED_PRICE_LIMIT'
    assert len(result['orders']) == 1  # No reintenta cada snapshot de la misma vela.


def test_timeout_se_cuenta_desde_fill_y_no_desde_senal(setup):
    engine,config,grid = setup
    engine = ExecutionReplay(engine.archive,engine.series,engine.contract,engine.fees,
                             replace(engine.assumptions,latency_seconds=3))
    setup = engine,replace(config,maximum_holding_seconds=2),grid
    result = run(setup,[book(setup,1),book(setup,2),book(setup,4),book(setup,5),
                       book(setup,6),book(setup,9)])
    assert result['fills'][0]['executed_at'] == stamp(at(4))
    assert next(d for d in result['decisions'] if d['code']=='MAX_HOLDING_TIME')['at'] == stamp(at(6))
    assert result['fills'][-1]['executed_at'] == stamp(at(9))


def test_salida_parcial_se_reintenta_sin_perder_intencion(setup):
    result = run(setup,[book(setup,1),book(setup,2),book(setup,3,'106','106.1'),
                       book(setup,4,'100','100.1',bid_size=D(2)),book(setup,5,'110','110.1')])
    assert result['orders'][1]['status'] == 'PARTIAL_CANCELLED'
    assert result['decisions'][3]['code'] == 'STOP_TRIGGER'
    assert len(result['fills']) == 3 and result['open_quantity'] == '0'


def test_datos_faltantes_no_se_reemplazan_por_otra_barra(setup):
    engine = setup[0]
    with engine.archive.store.connect() as c:
        c.execute('DELETE FROM candle_versions WHERE bar_start=?',(stamp(at(-180)),))
    result = run(setup,[book(setup,1),book(setup,2)])
    assert not result['orders'] and result['decisions'][0]['code'] == 'MISSING_EXPECTED_BARS'


def test_datos_tardios_no_se_usan_antes_de_su_publicacion(setup):
    engine = setup[0]
    with engine.archive.store.connect() as c:
        c.execute('UPDATE candle_versions SET known_at=? WHERE bar_start=?',(stamp(at(3)),stamp(at(-60))))
    result = run(setup,[book(setup,1),book(setup,2),book(setup,3),book(setup,4)])
    assert [d['code'] for d in result['decisions'][:2]] == ['MISSING_EXPECTED_BARS']*2
    assert result['fills'][0]['executed_at'] == stamp(at(4))


@pytest.mark.parametrize('changes',[{'quality':'CONFLICT'},{'synthetic':True},{'volume':None}])
def test_calidad_invalida_bloquea_entrada(setup,changes):
    engine = setup[0]
    engine.archive.put(engine.series,Bar(**(dict(start=at(-60),open=D(108),high=D(109),low=D(107),
        close=D(108),volume=D(100),quality='COMPLETE')|changes)),known_at=at(1))
    result = run(setup,[book(setup,1)])
    assert result['decisions'][0]['code'] == 'UNVERIFIED_BARS'


def test_sin_volumen_no_suma_puntos_favorables(setup):
    engine = setup[0]
    for i in range(8):
        price = D(101+i)
        engine.archive.put(engine.series,Bar(at((i-8)*60),price,price+1,price-1,price,volume=D(0),quality='COMPLETE'),known_at=at(1))
    result = run(setup,[book(setup,1)])
    assert result['decisions'][0]['code'] == 'NO_OBSERVED_VOLUME'


def test_cambiar_costos_vuelve_a_decidir_no_solo_resta_pnl(setup):
    normal = run(setup,scenario(setup))
    engine = setup[0]
    stressed = ExecutionReplay(engine.archive,engine.series,engine.contract,
                               replace(engine.fees,rate=D('.02')),engine.assumptions)
    stressed = run((stressed,*setup[1:]),scenario(setup))
    assert normal['fills'] and stressed['fills'] == []
    assert stressed['decisions'][0]['code'] == 'NET_REWARD_RISK_TOO_LOW'
    assert normal['run_id'] != stressed['run_id']


def test_riesgo_y_caja_incluyen_costos_y_gap_explicito(setup):
    setup = setup[0],replace(setup[1],stop_gap_fraction=D('.05'),minimum_net_reward_risk=D('.1')),setup[2]
    result = run(setup,[book(setup,1),book(setup,2)])
    features = result['decisions'][0]['features']
    assert D(features['modeled_stop_loss']) <= D(features['risk_budget'])
    assert D(features['entry_debit']) <= D(features['cash_budget'])
    assert D(result['fills'][0]['quantity']) < 22


def test_falta_de_liquidez_no_genera_orden(setup):
    result = run(setup,[book(setup,1,ask_size=D(0))])
    assert not result['orders'] and result['decisions'][0]['code'] == 'NO_LOT_WITHIN_CASH_RISK_DEPTH'


def test_libro_cerrado_no_genera_senales(setup):
    result = run(setup,[book(setup,1,session_open=False)])
    assert not result['orders'] and result['decisions'][0]['code'] == 'BOOK_NOT_USABLE'


@pytest.mark.parametrize('changes',[{'short_window':0},{'long_window':3},{'atr_period':True},
    {'risk_fraction':D(2)},{'score_threshold':D('NaN')},{'stop_gap_fraction':D(1)},
    {'order_ttl_seconds':0},{'maximum_position_fraction':D(0)}])
def test_configuracion_invalida_se_rechaza(setup,changes):
    with pytest.raises(ValueError): replace(setup[1],**changes)


@pytest.mark.parametrize('changes',[{'known_at':at(1)},{'source':'UNKNOWN'},
    {'starts':(at(-60),at(-60))},{'coverage_end':at(5)}])
def test_grilla_invalida_o_conocida_en_el_futuro_falla(setup,changes):
    with pytest.raises(ValueError): run((*setup[:2],replace(setup[2],**changes)),scenario(setup))


def test_no_calibra_parametros_despues_de_comenzar_test(setup):
    with pytest.raises(ValueError,match='congelados'):
        run((setup[0],replace(setup[1],frozen_at=at(1)),setup[2]),scenario(setup))


@pytest.mark.parametrize('daily_cut',[False,True])
def test_cli_reproduce_senales_y_fills_del_manifiesto(setup,tmp_path,daily_cut):
    books = [book(setup,1),book(setup,2),book(setup,3,'20','21'),book(setup,4)] if daily_cut else scenario(setup)
    result = run(setup,books)
    spec = tmp_path/'strategy.json'; spec.write_text(json.dumps(result['manifest']))
    database = Path(setup[0].archive.store.path)
    before = database.read_bytes()
    process = subprocess.run([sys.executable,'bx_execution_replay.py','--database',str(database),
                              '--input',str(spec)],capture_output=True,text=True,timeout=20)
    assert process.returncode == 0,process.stdout+process.stderr
    assert json.loads(process.stdout) == result
    assert database.read_bytes() == before


def test_cli_candidato_sin_riesgo_no_usa_un_default(setup,tmp_path):
    result = run(setup,scenario(setup))
    result['manifest'].pop('risk_config')
    spec = tmp_path/'missing-risk.json'; spec.write_text(json.dumps(result['manifest']))
    database = Path(setup[0].archive.store.path)
    before = database.read_bytes()
    process = subprocess.run([sys.executable,'bx_execution_replay.py','--database',str(database),
                              '--input',str(spec)],capture_output=True,text=True,timeout=20)
    assert process.returncode==2 and json.loads(process.stdout)['status']=='INVALID_REPLAY_INPUT'
    assert database.read_bytes()==before


def test_identidad_de_configuracion_normaliza_decimales_y_zona(setup):
    config = setup[1]
    alternate = replace(config,stop_gap_fraction=D('0.00'),atr_stop_multiple=D('1.00'),
                        frozen_at=stamp(config.frozen_at))
    assert config.version == alternate.version


def test_warmup_y_antiguedad_de_velas_no_usan_cierres_futuros(setup):
    engine,config,grid = setup
    bars, reason = window_at(engine.archive,engine.series,grid,config,at(-30))
    assert not bars and reason == 'INSUFFICIENT_WARMUP'
    old_grid = replace(grid,starts=grid.starts[:8])
    result = run((engine,config,old_grid),[book(setup,121)],end=at(150))
    assert result['decisions'][0]['code'] == 'STALE_BARS' and not result['orders']


@pytest.mark.parametrize('change,code',[
    ({'maximum_spread':D('.00001')},'SPREAD_TOO_WIDE'),
    ({'atr_stop_multiple':D(1000)},'INVALID_STOP_TARGET'),
    ({'risk_fraction':D('.0000001')},'NO_LOT_WITHIN_CASH_RISK_DEPTH'),
])
def test_limites_de_entrada_con_motivo(setup,change,code):
    result = run((setup[0],replace(setup[1],**change),setup[2]),[book(setup,1)])
    assert result['decisions'][0]['code'] == code and not result['orders']


def test_score_bajo_no_compra_con_solo_volatilidad(setup):
    engine = setup[0]
    for i in range(8):
        p = D(101108-i*1000)
        engine.archive.put(engine.series,Bar(at((i-8)*60),p,p+1,p-1,p,volume=D(100),quality='COMPLETE'),known_at=at(1))
    result = run(setup,[book(setup,1)])
    assert result['decisions'][0]['code'] == 'SCORE_BELOW_THRESHOLD'


def test_atr_cero_no_produce_stop_artificial(setup):
    engine = setup[0]
    for i in range(8):
        engine.archive.put(engine.series,Bar(at((i-8)*60),D(100),D(100),D(100),D(100),volume=D(100),quality='COMPLETE'),known_at=at(1))
    result = run((engine,replace(setup[1],score_threshold=D('.1')),setup[2]),[book(setup,1)])
    assert result['decisions'][0]['code'] == 'NO_VOLATILITY_ESTIMATE'


def test_futuro_tarifario_no_habilita_senal(setup):
    e = setup[0]
    e = ExecutionReplay(e.archive,e.series,e.contract,replace(e.fees,known_at=at(3)),e.assumptions)
    result = run((e,*setup[1:]),[book(setup,1)])
    assert result['decisions'][0]['code'] == 'COST_TERMS_UNAVAILABLE'


def test_falla_de_libros_no_borra_intencion_de_salida(setup):
    setup = setup[0],replace(setup[1],maximum_holding_seconds=2),setup[2]
    result = run(setup,[book(setup,1),book(setup,2),book(setup,4,session_open=False),
                       book(setup,5),book(setup,6)])
    assert result['decisions'][2]['code'] == 'EXIT_WAITING_FOR_BOOK'
    assert result['decisions'][3]['code'] == 'MAX_HOLDING_TIME'
    assert result['open_quantity'] == '0'


def test_costos_minimos_revaluados_tras_fill_parcial(setup):
    e = setup[0]
    e = ExecutionReplay(e.archive,e.series,e.contract,replace(e.fees,rate=D(0),fixed=D(3)),e.assumptions)
    result = run((e,*setup[1:]),[book(setup,1),book(setup,2,ask_size=D(1)),book(setup,3)])
    assert result['fills'][0]['quantity'] == '1'
    assert result['decisions'][1]['code'] == 'FILL_ECONOMICS_CHANGED'
    assert result['open_quantity'] == '0'
