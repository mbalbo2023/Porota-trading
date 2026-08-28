"""Regresiones de persistencia financiera y entrega sin tocar cuentas ni red."""
import io
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from bn_telegram_bus import DeliveryError, OutboxWorker, TelegramTransport, enqueue
from bv_paper_runtime import broker_from_environment
from bw_daily_risk import loss_limit_crossed
from bz_replay_risk import ReplayDailyRisk, ReplayRiskConfig
from bt_caucion_paper import record_sale
from test_production_paper_v1634 import quote, caucion_offer

AT = '2026-08-28T11:00:00-03:00'


@pytest.mark.parametrize('daily,realized,budget,crossed',[
    ('-100','0','100',True), ('-99.99','0','100',False),
    (None,'-100','100',True), ('50','-100','100',True),
    (None,'-99.99','100',False), ('-1','0','1',True),
])
def test_corte_paper_y_replay_comparten_umbral_inclusivo(daily,realized,budget,crossed):
    assert loss_limit_crossed(daily,realized,budget) is crossed


@pytest.mark.parametrize('value',['0','-1','100.01','NaN','Infinity','texto',True])
def test_configuracion_riesgo_replay_exige_porcentaje_valido(value):
    with pytest.raises(ValueError):
        ReplayRiskConfig(AT,value)


def risk_tracker(**changes):
    return ReplayDailyRisk(**(dict(config=ReplayRiskConfig(AT,D(1)),currency='ARS',
                                  capital='10000',start=AT) | changes))


def evaluate_risk(risk,at=AT,**changes):
    return risk.evaluate(at,**(dict(held=D(0),equity=D(10000),quality='CURRENT',
        realized_total=D(0),realized_today=D(0),phase='TEST') | changes))


def test_replay_latch_no_se_borra_por_recuperacion_o_marca_faltante():
    risk = risk_tracker()
    evaluate_risk(risk)
    loss = evaluate_risk(risk,equity=D(9900))
    assert loss['state']=='LATCHED' and loss['loss_budget']==100
    assert evaluate_risk(risk,equity=D(11000))['state']=='LATCHED'
    assert evaluate_risk(risk,equity=None,quality='STALE_MARKS')['latched_at']==loss['latched_at']
    assert risk.observations[0]['state']=='READY'  # No mutar la historia.


def test_replay_sin_marca_bloquea_entrada_pero_realizado_puede_activar_corte():
    risk = risk_tracker()
    evaluate_risk(risk)
    unknown = evaluate_risk(risk,equity=D(10000),quality='UNKNOWN_EXIT_COST')
    assert unknown['state']=='STALE_MARKS' and unknown['last_equity'] is None
    assert unknown['remaining_budget']==0
    assert evaluate_risk(risk,equity=None,realized_total=D(-100),realized_today=D(-100))['state']=='LATCHED'


def test_replay_cambio_de_dia_argentino_y_base_patrimonial_no_caja():
    risk = risk_tracker()
    evaluate_risk(risk,realized_total=D(-100),realized_today=D(-100),equity=D(9900))
    # Medianoche UTC sigue siendo el mismo día bursátil local.
    same = evaluate_risk(risk,'2026-08-29T00:01:00+00:00',equity=D(9900),
                         realized_total=D(-100),realized_today=D(-100))
    assert same['state']=='LATCHED' and same['day']=='2026-08-28'
    next_day = evaluate_risk(risk,'2026-08-29T03:00:00+00:00',equity=D(9900),realized_total=D(-100))
    assert next_day['state']=='READY' and next_day['baseline_equity']==9900
    assert next_day['daily_pnl']==0 and next_day['loss_budget']==99


def test_replay_carry_bloquea_dia_entero_aun_despues_de_cerrar():
    risk = risk_tracker()
    evaluate_risk(risk)
    carry = evaluate_risk(risk,'2026-08-29T10:00:00-03:00',held=D(1),equity=D(10005))
    assert carry['state']=='BASELINE_UNAVAILABLE' and carry['baseline_equity'] is None
    closed = evaluate_risk(risk,'2026-08-29T11:00:00-03:00',equity=D(10005),
                           realized_total=D(5),realized_today=D(5))
    assert closed['state']=='BASELINE_UNAVAILABLE' and closed['remaining_budget']==0
    ready = evaluate_risk(risk,'2026-08-30T10:00:00-03:00',equity=D(10005),realized_total=D(5))
    assert ready['state']=='READY' and ready['baseline_equity']==10005


def test_replay_presupuesto_no_aumenta_por_ganancias_y_respeta_realizado():
    risk = risk_tracker()
    assert evaluate_risk(risk,equity=D(10100))['remaining_budget']==100
    assert evaluate_risk(risk,equity=D(9975))['remaining_budget']==75
    assert evaluate_risk(risk,equity=D(10100),realized_total=D(-50),realized_today=D(-50))['remaining_budget']==50
    assert not risk.permits_projected_equity(AT,D(9900))
    assert risk.permits_projected_equity(AT,D('9900.01'))
    assert risk.days['2026-08-28']['state']=='READY'


def test_replay_riesgo_rechaza_reloj_futuro_moneda_y_capital_invalidos():
    for changes in ({'config':None},{'currency':'ZZZ'},{'capital':'NaN'},
                    {'start':'2026-08-28T10:59:00-03:00'}):
        with pytest.raises(ValueError):
            risk_tracker(**changes)
    risk = risk_tracker()
    evaluate_risk(risk,'2026-08-28T11:01:00-03:00')
    with pytest.raises(ValueError):
        evaluate_risk(risk,AT)


def test_replay_dia_sin_capital_positivo_no_habilita_compras():
    risk = risk_tracker()
    evaluate_risk(risk,equity=D(0),realized_total=D(-10000),realized_today=D(-10000))
    row = evaluate_risk(risk,'2026-08-29T11:00:00-03:00',equity=D(0),realized_total=D(-10000))
    assert row['state']=='NO_CAPITAL' and row['remaining_budget']==0


@pytest.fixture
def store(tmp_path):
    return PaperStore(str(tmp_path/'paper.db'))


def records(store, table):
    with store.connect() as c:
        return [dict(r) for r in c.execute('SELECT * FROM '+table)]


def queue(store, key='test', at=AT):
    with store.connect() as c:
        enqueue(c,key,'TEST','Aviso simulado',at)


def seed_closed(store, *, key='closed', currency='ARS', net='-100',
                opened=AT, closed='2026-08-28T11:01:00-03:00'):
    # Fixture económico completo: el PnL pedido debe conciliar con ambos fills.
    exit_price=D(1000)+D(net)
    assert exit_price>0
    with store.connect() as c:
        c.execute('''INSERT INTO paper_positions
          (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
           quantity,entry_price,entry_cost,stop_price,target_price,opened_at,closed_at,
           exit_price,exit_cost,gross_pnl,net_pnl,close_reason,features_json,currency,market)
          VALUES(?,'PRODUCTION_PAPER','fixture',?,'ACCIONES','CI','CLOSED',
            '1','1000','0','980','1040',?, ?,?,'0',?,?,'TEST','{}',?,'BYMA')''',
            (key,key,opened,closed,str(exit_price),net,net,currency))
        for side,at,price in (('BUY_SIMULATED',opened,'1000'),('SELL_SIMULATED',closed,str(exit_price))):
            c.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',
                (key,'PRODUCTION_PAPER',side,at,'1',price,'0','0'))
        record_sale(c,key,'CI',closed,exit_price,currency)


def test_aviso_y_fill_comparten_commit_y_rollback(store):
    broker = PaperBroker(store)
    assert broker._open(quote(at=AT),D('.8'),{})[0]
    row = records(store,'paper_notification_outbox')[0]
    assert row['kind']=='PAPER_FILLED_BUY' and 'ARS' in row['body']
    with pytest.raises(RuntimeError), store.connect() as c:
        c.execute('INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)',
                  (AT,'PRODUCTION_PAPER','PAPER_FILLED_SELL','rollback','No commit'))
        raise RuntimeError('corte')
    assert len(records(store,'paper_notification_outbox'))==1
    with store.connect() as c:
        c.execute('DROP TABLE paper_notification_outbox')
    with pytest.raises(sqlite3.OperationalError):
        broker._open(quote(symbol='ALUA',at=AT),D('.8'),{})
    assert len(records(store,'paper_fills'))==1


def test_encolar_idempotente_y_sin_credenciales_no_consume_intentos(store):
    queue(store); queue(store)
    worker=OutboxWorker(store,clock_fn=lambda:AT)
    assert worker.tick() is False
    row=records(store,'paper_notification_outbox')[0]
    assert row['state']=='PENDING' and row['attempts']==0
    assert records(store,'paper_notification_worker')[0]['state']=='NOT_CONFIGURED'


def test_dos_workers_no_toman_mismo_ni_otro_aviso_en_vuelo(store):
    queue(store,'uno'); queue(store,'dos')
    first=OutboxWorker(store,clock_fn=lambda:AT,send=lambda _:1)
    second=OutboxWorker(store,clock_fn=lambda:AT,send=lambda _:2)
    assert first.claim()['event_key']=='uno'
    assert second.claim() is None
    assert records(store,'paper_notification_outbox')[1]['attempts']==0


def test_lease_vencido_recupera_y_ack_viejo_no_pisa_nuevo(store):
    queue(store)
    clock=[datetime.fromisoformat(AT)]
    now=lambda:clock[0].isoformat()
    second=OutboxWorker(store,clock_fn=now,send=lambda _:222)
    def slow_send(_):
        clock[0]+=timedelta(seconds=121)
        assert second.tick()
        return 111
    first=OutboxWorker(store,clock_fn=now,send=slow_send)
    assert first.tick() is False
    row=records(store,'paper_notification_outbox')[0]
    assert row['state']=='SENT' and row['message_id']=='222' and row['attempts']==2


def test_429_pausa_toda_cola_y_sobrevive_reinicio(store):
    queue(store,'uno'); queue(store,'dos')
    clock=[datetime.fromisoformat(AT)]
    now=lambda:clock[0].isoformat()
    def limited(_): raise DeliveryError(429,retry_after=60)
    assert OutboxWorker(store,clock_fn=now,send=limited).tick()
    sent=[]
    worker=OutboxWorker(PaperStore(store.path),clock_fn=now,send=lambda body:sent.append(body) or 1)
    clock[0]+=timedelta(seconds=59)
    assert worker.tick() is False and sent==[]
    clock[0]+=timedelta(seconds=1)
    assert worker.tick() and len(sent)==1
    assert worker.tick() is False  # Separación global de un segundo.


def test_reintentos_agotados_quedan_visibles_no_descartados(store):
    queue(store)
    clock=[datetime.fromisoformat(AT)]
    def fail(_): raise TimeoutError('red lenta')
    worker=OutboxWorker(store,clock_fn=lambda:clock[0].isoformat(),send=fail,max_attempts=2)
    assert worker.tick()
    assert records(store,'paper_notification_outbox')[0]['state']=='PENDING'
    clock[0]+=timedelta(seconds=5)
    assert worker.tick()
    assert records(store,'paper_notification_outbox')[0]['state']=='DEAD'
    clock[0]+=timedelta(hours=1)
    assert not worker.tick()


@pytest.mark.parametrize('payload', [
    {'ok':False,'error_code':400}, {'ok':True,'result':{}},
    {'ok':True,'result':{'message_id':True}}, {'ok':True,'result':{'message_id':0}},
])
def test_http_200_sin_ack_valido_no_equivale_a_entregado(payload):
    transport=TelegramTransport('test','fixture',opener=lambda *a,**k:io.BytesIO(json.dumps(payload).encode()))
    with pytest.raises(DeliveryError): transport('texto')


def test_transporte_parsea_429_y_envia_texto_sin_markdown():
    def limited(req,**kwargs):
        raise HTTPError(req.full_url,429,'rate',{},io.BytesIO(b'{"parameters":{"retry_after":45}}'))
    with pytest.raises(DeliveryError) as err: TelegramTransport('test','fixture',limited)('x')
    assert err.value.retry_after==45 and not err.value.permanent
    def success(req,**kwargs):
        body=json.loads(req.data)
        assert body=={'chat_id':'fixture','text':'A_B <C>'} and kwargs['timeout']==15
        return io.BytesIO(b'{"ok":true,"result":{"message_id":42}}')
    assert TelegramTransport('test','fixture',success)('A_B <C>')=='42'


def test_resumen_fallido_viejo_se_encola_y_ack_actualiza_estado(store,monkeypatch):
    import bi_operational_services as services
    services.init_schema(store)
    today=services.datetime.now(services.TZ).date().isoformat()
    services._job(store,'TELEGRAM_CLOSE_'+today,'ROJO','falló')
    monkeypatch.setattr(services,'urlopen',lambda *_a,**_k:pytest.fail('El generador no envía por red'))
    assert services.send_closing_summary(store)=='EN_COLA'
    assert services.send_closing_summary(store)=='PENDING'
    row=records(store,'paper_notification_outbox')[0]
    worker=OutboxWorker(store,clock_fn=lambda:row['created_at'],send=lambda _:42)
    assert worker.tick()
    assert services.send_closing_summary(store)=='ENTREGADO'
    job=next(r for r in records(store,'operational_jobs') if r['job_key'].startswith('TELEGRAM'))
    assert job['state']=='VERDE' and job['last_success_at']


def test_latch_al_limite_incluye_realizado_reinicio_y_moneda_independiente(store):
    seed_closed(store)
    at='2026-08-28T11:02:00-03:00'
    broker=PaperBroker(store,initial_cash='10000',initial_cash_by_currency={'USD_MEP':'10000'},daily_loss_pct='1')
    result=broker.daily_risk.evaluate(at)
    assert result['ARS']['state']=='LATCHED' and D(result['ARS']['baseline_equity'])==10000
    assert result['USD_MEP']['state']=='READY'
    seed_closed(store,key='gain',net='1000')
    restarted=PaperBroker(PaperStore(store.path),initial_cash='10000',daily_loss_pct='1')
    assert restarted.daily_risk.evaluate(at)['ARS']['state']=='LATCHED'
    assert len([r for r in records(store,'paper_notification_outbox') if r['kind']=='PAPER_DAILY_LOSS'])==1
    assert restarted._open(quote(at=at),D('.8'),{})[1]=='DAILY_RISK_LATCHED'
    next_day=restarted.daily_risk.evaluate('2026-08-29T11:00:00-03:00')['ARS']
    assert next_day['state']=='READY' and D(next_day['baseline_equity'])==10900


def test_reinicio_no_toma_riqueza_perdida_como_base_y_ignora_ganancia_otras_monedas(store):
    seed_closed(store,net='-120')
    seed_closed(store,key='usd',currency='USD_MEP',net='10000')
    risk=PaperBroker(store,initial_cash='10000',daily_loss_pct='1').daily_risk
    row=risk.evaluate('2026-08-28T11:02:00-03:00')['ARS']
    assert row['state']=='LATCHED' and D(row['daily_pnl'])==-120


def test_perdida_abierta_decide_salida_no_inventa_fill_sin_profundidad(store):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    q=quote(at=AT)
    assert broker._open(q,D('.8'),{})[0]
    p=store.open_positions()[0]
    loss=quote(price='10',bid_size='0',at='2026-08-28T11:01:00-03:00')
    store.add_quote(loss)
    supervisor=PositionExitSupervisor(broker,clock_fn=lambda:loss.observed_at)
    verdict=supervisor.tick()[0]
    assert verdict.cause=='DAILY_LOSS_PAPER' and verdict.state=='EXIT_PENDING_NO_LIQUIDITY'
    assert store.open_positions()[0]['paper_id']==p['paper_id']
    assert len(records(store,'paper_fills'))==1
    fresh=quote(price='110',at='2026-08-28T11:02:00-03:00')
    store.add_quote(fresh)
    assert broker.daily_risk.evaluate(fresh.observed_at)['ARS']['state']=='LATCHED'
    PositionExitSupervisor(broker,clock_fn=lambda:fresh.observed_at).tick()
    assert store.recent_closed()[0]['close_reason']=='DAILY_LOSS_PAPER'
    assert len(records(store,'paper_fills'))==2


def test_cierre_y_latch_y_aviso_atomicos(store):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    assert broker._open(quote(at=AT),D('.8'),{})[0]
    assert broker._close(store.open_positions()[0],quote(price='10',at='2026-08-28T11:01:00-03:00'),'TEST')
    assert next(r for r in records(store,'paper_daily_risk') if r['currency']=='ARS')['state']=='LATCHED'
    assert {r['kind'] for r in records(store,'paper_notification_outbox')}=={
        'PAPER_FILLED_BUY','PAPER_FILLED_SELL','PAPER_DAILY_LOSS'}


def test_libro_vencido_no_da_pnl_cero_ni_habilita_entradas(store):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    assert broker._open(quote(at=AT),D('.8'),{})[0]
    row=broker.daily_risk.evaluate('2026-08-28T11:03:00-03:00')['ARS']
    assert row['state']=='STALE_MARKS' and row['daily_pnl'] is None
    assert broker._open(quote(symbol='ALUA',at='2026-08-28T11:03:00-03:00'),D('.8'),{})[1]=='DAILY_RISK_STALE_MARKS'


def test_realizado_dispara_aunque_otra_abierta_tenga_libro_invalido(store):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    assert broker._open(quote(at=AT),D('.8'),{})[0]
    store.add_quote(replace(quote(at=AT),currency=None,book_at=None))
    seed_closed(store)
    result=broker.daily_risk.evaluate('2026-08-28T11:02:00-03:00')['ARS']
    assert result['state']=='LATCHED' and result['daily_pnl'] is None


def test_limite_se_revalida_dentro_del_lock_antes_del_fill(store,monkeypatch):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    original=broker.admission_error
    def interleave(q,at,**kwargs):
        result=original(q,at,**kwargs)
        if not kwargs.get('connection'):
            seed_closed(store,opened=AT,closed=AT)
        return result
    monkeypatch.setattr(broker,'admission_error',interleave)
    assert broker._open(quote(at=AT),D('.8'),{})[1]=='DAILY_RISK_LATCHED'
    assert len(records(store,'paper_fills'))==2
    assert {r['paper_id'] for r in records(store,'paper_fills')}=={'closed'}
    assert len([r for r in records(store,'paper_notification_outbox') if r['kind']=='PAPER_DAILY_LOSS'])==1


def test_migracion_outbox_no_reenvia_historicos(store):
    with store.connect() as c:
        c.execute('DROP TRIGGER paper_events_notify_v17')
        c.execute('INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)',
                  (AT,'PRODUCTION_PAPER','PAPER_FILLED_BUY','histórico','Ya pasó'))
    PaperStore(store.path)
    assert records(store,'paper_notification_outbox')==[]


def test_reintentar_aviso_no_repite_operacion(store):
    assert PaperBroker(store)._open(quote(at=AT),D('.8'),{})[0]
    clock=[datetime.fromisoformat(AT)]
    calls=[]
    def send(body):
        calls.append(body)
        if len(calls)==1: raise TimeoutError()
        return 123
    worker=OutboxWorker(store,clock_fn=lambda:clock[0].isoformat(),send=send)
    worker.tick(); clock[0]+=timedelta(seconds=5); worker.tick()
    assert len(calls)==2 and calls[0]==calls[1]
    assert len(records(store,'paper_fills'))==1


def test_baseline_incluye_cierre_anterior_sin_confundir_utc_con_dia_local(store):
    seed_closed(store,net='500',opened='2026-08-27T12:00:00-03:00',closed='2026-08-28T01:00:00+00:00')
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    row=broker.daily_risk.evaluate(AT)['ARS']
    assert D(row['baseline_equity'])==10500 and D(row['daily_pnl'])==0


def test_causa_anterior_no_se_reemplaza_con_corte_diario(store):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    assert broker._open(quote(at=AT),D('.8'),{})[0]
    p=store.open_positions()[0]
    with store.connect() as c:
        c.execute('INSERT INTO paper_exit_intents VALUES(?,?,?,?,?,?,?)',
                  (p['paper_id'],'EXIT_PENDING_NO_QUOTE','MAX_HOLD_PAPER',AT,'test',AT,0))
    seed_closed(store,net='-200')
    broker.daily_risk.evaluate('2026-08-28T11:02:00-03:00')
    assert records(store,'paper_exit_intents')[0]['cause']=='MAX_HOLD_PAPER'


def test_carry_sin_base_bloquea_dia_incluso_si_se_cierra(store):
    seed_closed(store,opened='2026-08-27T11:00:00-03:00')
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    row=broker.daily_risk.evaluate('2026-08-28T11:02:00-03:00')['ARS']
    assert row['state']=='BASELINE_UNAVAILABLE' and row['baseline_equity'] is None
    assert broker.daily_risk.evaluate('2026-08-29T11:00:00-03:00')['ARS']['state']=='READY'


def test_config_y_reloj_no_pueden_resetear_control(store):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    assert broker.daily_risk.evaluate(AT)['ARS']['state']=='READY'
    changed=PaperBroker(store,initial_cash='20000',daily_loss_pct='2')
    assert changed.daily_risk.evaluate(AT)['ARS']['state']=='CONFIG_CHANGED'
    assert changed.daily_risk.evaluate('2026-08-29T11:00:00-03:00')['ARS']['state']=='CONFIG_CHANGED'
    assert broker.daily_risk.evaluate(AT)['ARS']['state']=='CLOCK_ROLLBACK'


def test_caucion_devenga_sin_contar_principal_y_bloqueo_no_impide_vencer(store):
    broker=PaperBroker(store,initial_cash='10000',daily_loss_pct='1')
    offer=caucion_offer()
    p=broker.place_caucion(offer,'1000','req',AT)
    row=broker.daily_risk.evaluate(AT)['ARS']
    assert D(row['daily_pnl'])==-1  # Costo comprometido, no -1000 de principal.
    monday='2026-08-31T11:00:00-03:00'
    row=broker.daily_risk.evaluate(monday)['ARS']
    assert row['state']=='READY' and D(row['daily_pnl'])==0
    seed_closed(store,opened=monday,closed=monday,net='-200')
    later=caucion_offer(start_date='2026-08-31',quoted_at=monday,maturity_at='2026-09-01T15:00:00-03:00',quoted_total_fees=D('.5'))
    with pytest.raises(ValueError,match='DAILY_RISK_LATCHED'):
        broker.place_caucion(later,'1000','blocked',monday)
    assert any(r['state']=='LATCHED' for r in records(store,'paper_daily_risk'))
    assert broker.settle_cauciones(offer.maturity_at)==[p['paper_id']]
    assert broker.settle_cauciones(offer.maturity_at)==[]
    assert len([r for r in records(store,'paper_notification_outbox') if r['kind']=='PAPER_CAUCION_MATURED'])==1


@pytest.mark.parametrize('payment',['MATURITY','UPFRONT'])
@pytest.mark.parametrize('fee',['1','1.01'])
def test_caucion_costos_proyectados_no_agotan_limite_diario(store,payment,fee):
    broker = PaperBroker(store,initial_cash='10000',daily_loss_pct='.01')
    offer = caucion_offer(fee_payment=payment,quoted_total_fees=D(fee))
    with pytest.raises(ValueError,match='DAILY_RISK_PROJECTED_LOSS'):
        broker.place_caucion(offer,'1000','costly',AT)
    assert not broker.cauciones.positions()
    assert broker._cash(as_of=AT)==10000
    row = broker.daily_risk.evaluate(AT)['ARS']
    assert row['state']=='READY' and row['latched_at'] is None and D(row['daily_pnl'])==0
    assert not records(store,'paper_notification_outbox')


@pytest.mark.parametrize('payment',['MATURITY','UPFRONT'])
def test_caucion_costo_menor_al_remanente_se_registra_en_misma_moneda(store,payment):
    broker = PaperBroker(store,initial_cash='10000',initial_cash_by_currency={'USD_MEP':'10000'},daily_loss_pct='.01')
    offer = caucion_offer(currency='USD_MEP',fee_payment=payment,quoted_total_fees=D('.99'))
    broker.place_caucion(offer,'1000','accepted',AT)
    rows = broker.daily_risk.evaluate(AT)
    assert rows['USD_MEP']['state']=='READY' and D(rows['USD_MEP']['daily_pnl'])==D('-.99')
    assert D(rows['ARS']['daily_pnl'])==0 and broker._cash(as_of=AT)==10000
    with pytest.raises(ValueError,match='DAILY_RISK_PROJECTED_LOSS'):
        broker.place_caucion(offer,'1000','second',AT)
    assert len(broker.cauciones.positions())==1


@pytest.mark.parametrize('bad',['0','-1','NaN','Infinity','101'])
def test_limite_diario_invalido_no_arranca(store,bad):
    with pytest.raises(ValueError): PaperBroker(store,daily_loss_pct=bad)


def test_runtime_reusa_porcentaje_existente_y_panel_no_confunde_entrega(store,monkeypatch):
    import bg_paper_dashboard as dashboard
    import porota_mode_manager as mode
    monkeypatch.setenv('MAX_DAILY_LOSS_PCT','1.25')
    assert broker_from_environment(store).daily_risk.limit_pct==D('1.25')
    assert mode.paper_settings({'MAX_DAILY_LOSS_PCT':'1.25'})['MAX_DAILY_LOSS_PCT']=='1.25'
    queue(store)
    monkeypatch.setattr(dashboard,'DB_PATH',store.path)
    assert 'PENDING' in dashboard.telegram_page() and 'ACK' in dashboard.telegram_page()
    assert 'Corte diario por moneda' in dashboard.motor_page()
