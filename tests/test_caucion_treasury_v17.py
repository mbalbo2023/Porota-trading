"""Política prudente PAPER delegada: límites durables, sin cuentas ni red."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bs_instrument_contracts import aware_datetime
from cb_caucion_audit import allocation_history
from ce_caucion_treasury import CaucionWindow, PROFILE, window_error
from test_production_paper_v1634 import caucion_offer, quote

AT = '2026-08-28T11:00:00-03:00'


@pytest.fixture
def treasury(tmp_path):
    broker=PaperBroker(PaperStore(str(tmp_path/'treasury.db')),initial_cash='10000',daily_loss_pct='1')
    window=CaucionWindow('2026-08-28T10:00:00-03:00',AT,
        '2026-08-28T16:00:00-03:00','2026-08-31T16:00:00-03:00','TEST_SESSION_NOT_PPI')
    return broker,window


def place(b,w,key='first',offers=None,at=AT):
    return b.allocate_conservative_caucion(
        [caucion_offer(fee_quote_principal=D(5000))] if offers is None else offers,w,key,as_of=at)


def test_perfil_reserva_mitad_y_unica_colocacion_sin_convertir_moneda(treasury):
    b,w=treasury
    result=place(b,w)
    assert result['status']=='PLACED_SIMULATED'
    assert D(result['reserve_cash'])==5000 and D(result['principal_limit'])==5000
    assert result['profile']==PROFILE and not result['promotion_allowed'] and not result['data_certified']
    assert b._cash(as_of=AT)==5000
    assert b._cash(as_of=AT,currency='USD_MEP')==0
    assert result['allocation']['manifest']['policy']['ranking']=='EARLIEST_MATURITY_NET_RETURN'
    assert place(b,w,'second')['code']=='DAILY_PLACEMENT_LIMIT'
    assert len(b.cauciones.positions())==1
    history=allocation_history(b.store.path)
    assert history['records'][0]['state']=='CONSISTENT'


def test_reserva_no_se_reduce_al_reintentar_tras_gastar_caja(treasury):
    b,w=treasury
    first=place(b,w,offers=[caucion_offer(fee_quote_principal=D(6000))])
    assert first['status']=='HOLD' and D(first['reserve_cash'])==5000
    q=quote(ask_size='100',at=AT)
    b.store.add_quote(q)
    assert b._open(q,D('.8'),{})[0]
    cash=b._cash(as_of=AT)
    assert cash<9000
    # Sería admisible con una nueva reserva del 50% de la caja reducida.
    second=place(b,w,'after-buy',offers=[caucion_offer(fee_quote_principal=D(4000))])
    assert second['status']=='HOLD' and D(second['reserve_cash'])==5000
    assert b._cash(as_of=AT)==cash and not b.cauciones.positions()


@pytest.mark.parametrize('payment,principal,expected',[
    ('UPFRONT','5000','HOLD'),('UPFRONT','4999','PLACED_SIMULATED'),
    ('MATURITY','5000','PLACED_SIMULATED')])
def test_debito_con_costos_nunca_toca_reserva(treasury,payment,principal,expected):
    b,w=treasury
    result=place(b,w,offers=[caucion_offer(fee_payment=payment,fee_quote_principal=D(principal))])
    assert result['status']==expected
    assert b._cash(as_of=AT)>=5000


def test_no_extiende_plazo_por_perseguir_tna_y_auditor_concuerda(treasury):
    b,w=treasury
    early=caucion_offer(instrument_id='EARLY',fee_quote_principal=D(5000),maturity_at='2026-08-31T13:00:00-03:00')
    late=caucion_offer(instrument_id='LATE',fee_quote_principal=D(5000),annual_rate_fraction=D(1))
    result=place(b,w,offers=[late,early])
    assert result['allocation']['selected']['instrument_id']=='EARLY'
    assert allocation_history(b.store.path)['records'][0]['state']=='CONSISTENT'


@pytest.mark.parametrize('changes,code',[
    ({'currency':'USD_MEP'},'OTHER_CURRENCY'),
    ({'maturity_at':'2026-09-01T12:00:00-03:00'},'MATURITY_OUTSIDE_LIQUIDITY_WINDOW'),
    ({'maturity_at':'2026-08-29T12:00:00-03:00'},'MATURITY_CALENDAR_UNAVAILABLE_OR_CLOSED'),
    ({'quoted_at':'2026-08-28T10:59:29-03:00'},'QUOTE_STALE_OR_FUTURE'),
    ({'quoted_total_fees':None,'fee_quote_principal':None},'EXPLICIT_COST_BUDGET_REQUIRED'),
    ({'quoted_total_fees':D(100)},'NET_PROFIT_TOO_LOW'),
    ({'metadata_source':'UNKNOWN'},'UNKNOWN_CONTRACT_SOURCE'),
])
def test_falta_evidencia_o_no_compensa_costos_conserva_caja(treasury,changes,code):
    b,w=treasury
    result=place(b,w,offers=[caucion_offer(fee_quote_principal=D(5000),**{k:v for k,v in changes.items() if k!='fee_quote_principal'})
        if 'fee_quote_principal' not in changes else caucion_offer(**changes)])
    assert result['status']=='HOLD'
    assert result['allocation']['candidates'][0]['code']==code
    assert b._cash(as_of=AT)==10000


def test_idempotencia_tras_reinicio_y_hold_durable(treasury):
    b,w=treasury
    first=place(b,w,offers=[])
    restarted=PaperBroker(PaperStore(b.store.path),initial_cash='10000',daily_loss_pct='1')
    assert place(restarted,w,offers=[],at='2026-09-01T11:00:00-03:00')==first
    with pytest.raises(ValueError,match='reutilizada'):
        place(restarted,w)
    placed=place(restarted,w,'new-offer')
    assert place(restarted,w,'new-offer',at='2026-09-01T11:00:00-03:00')==placed


def test_concurrencia_no_puede_gastar_otro_cincuenta_por_ciento(treasury):
    b,w=treasury
    def run(key):
        other=PaperBroker(PaperStore(b.store.path),initial_cash='10000',daily_loss_pct='1')
        return place(other,w,key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,['a','b']))
    assert sorted(r['status'] for r in results)==['HOLD','PLACED_SIMULATED']
    assert b._cash(as_of=AT)==5000


@pytest.mark.parametrize('table',['paper_caucion_treasury_attempts','paper_caucion_allocations','paper_notification_outbox'])
def test_tesoreria_y_asignacion_se_revierten_juntas(treasury,table):
    from ce_caucion_treasury import init_schema
    b,w=treasury
    init_schema(b.store)
    with b.store.connect() as c:
        c.execute('CREATE TRIGGER fail_treasury BEFORE INSERT ON '+table+" BEGIN SELECT RAISE(ABORT,'TEST_TREASURY'); END")
    with pytest.raises(sqlite3.IntegrityError,match='TEST_TREASURY'):
        place(b,w)
    with b.store.connect() as c:
        for table in ('paper_caucion_treasury_days','paper_caucion_treasury_attempts','paper_cauciones','paper_caucion_allocations','paper_notification_outbox'):
            assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
    assert b._cash(as_of=AT)==10000


def test_sesion_faltante_y_fuera_de_horario_no_congelan_presupuesto(treasury):
    b,w=treasury
    assert place(b,None)['code']=='SESSION_EVIDENCE_MISSING'
    assert place(b,w,'closed',at=w.closes_at)['code']=='OUTSIDE_SESSION'
    with b.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_caucion_treasury_days').fetchone()[0]==0


def test_ventana_cambiada_y_reloj_retrocedido_no_reinician_reserva(treasury):
    b,w=treasury
    place(b,w,offers=[])
    changed=replace(w,closes_at='2026-08-28T15:59:00-03:00')
    assert place(b,changed,'change',offers=[])['code']=='FROZEN_POLICY_CHANGED'
    assert place(b,w,'past',at='2026-08-28T10:59:00-03:00')['code']=='TREASURY_CLOCK_ROLLBACK'


@pytest.mark.parametrize('today,deadline,code',[
    ('2026-08-27','2026-08-28',''),('2026-08-28','2026-08-31',''),
    ('2026-11-05','2026-11-09',''),('2026-12-04','2026-12-09','NEXT_SETTLEMENT_DAY_OUTSIDE_LIMIT'),
    ('2027-01-04','2027-01-05','CALENDAR_UNAVAILABLE_OR_CLOSED'),
    ('2026-08-29','2026-08-31','CALENDAR_UNAVAILABLE_OR_CLOSED'),
    ('2026-08-28','2026-09-01','DEADLINE_NOT_NEXT_SETTLEMENT_DAY'),
])
def test_dia_siguiente_real_y_limite_de_cuatro_corridos(today,deadline,code):
    w=CaucionWindow(today+'T10:00:00-03:00',today+'T11:00:00-03:00',
                    today+'T16:00:00-03:00',deadline+'T16:00:00-03:00','TEST')
    assert window_error(w,aware_datetime(w.opens_at))==code


def test_no_reinvierte_hasta_acreditar_y_no_duplica_tras_acreditar(treasury):
    b,w=treasury
    assert place(b,w)['status']=='PLACED_SIMULATED'
    monday=CaucionWindow('2026-08-31T10:00:00-03:00','2026-08-31T11:00:00-03:00',
        '2026-08-31T16:00:00-03:00','2026-09-01T16:00:00-03:00','TEST')
    new=caucion_offer(start_date='2026-08-31',quoted_at='2026-08-31T15:01:00-03:00',
                     maturity_at='2026-09-01T15:00:00-03:00',fee_quote_principal=D(5000))
    assert place(b,monday,'before-credit',[new],new.quoted_at)['code']=='EXISTING_CAUCION_EXPOSURE'
    b.settle_cauciones(new.quoted_at)
    assert place(b,monday,'after-credit',[new],new.quoted_at)['status']=='PLACED_SIMULATED'
    assert place(b,monday,'again',[new],new.quoted_at)['code']=='DAILY_PLACEMENT_LIMIT'


def test_riesgo_obligatorio_y_reloj_vivo_prevalece(treasury):
    b,w=treasury
    b.daily_risk=None
    assert place(b,w)['code']=='DAILY_RISK_NOT_CONFIGURED'
    b.clock_fn=lambda:w.closes_at
    assert place(b,w,'clock',at=AT)['code']=='OUTSIDE_SESSION'


def test_presupuesto_alterado_no_habilita_colocacion(treasury):
    b,w=treasury
    place(b,w,offers=[])
    with b.store.connect() as c:
        c.execute("UPDATE paper_caucion_treasury_days SET reserve_cash='0'")
    with pytest.raises(ValueError,match='inconsistente'):
        place(b,w,'tampered')
    assert not b.cauciones.positions()


def test_no_elige_vencimiento_sin_liquidacion_aunque_tenga_mejor_tasa(treasury):
    b,_=treasury
    w=CaucionWindow('2026-11-05T10:00:00-03:00','2026-11-05T11:00:00-03:00',
        '2026-11-05T16:00:00-03:00','2026-11-09T16:00:00-03:00','TEST')
    common=dict(start_date='2026-11-05',quoted_at=w.opens_at,fee_quote_principal=D(5000))
    closed=caucion_offer(**common,instrument_id='NO_SETTLEMENT',
                        maturity_at='2026-11-06T12:00:00-03:00',annual_rate_fraction=D(1))
    valid=caucion_offer(**common,instrument_id='NEXT_OPERATING_DAY',
                       maturity_at='2026-11-09T12:00:00-03:00')
    result=place(b,w,offers=[closed,valid],at=w.opens_at)
    assert result['allocation']['selected']['instrument_id']=='NEXT_OPERATING_DAY'
    rejected=next(c for c in result['allocation']['candidates'] if c['instrument_id']=='NO_SETTLEMENT')
    assert rejected['code']=='MATURITY_CALENDAR_UNAVAILABLE_OR_CLOSED'
    assert allocation_history(b.store.path)['records'][0]['state']=='CONSISTENT'


def test_caja_insuficiente_no_congela_un_presupuesto_inutil(treasury,tmp_path):
    _,w=treasury
    # Caja realista no negativa pero menor que dos centavos: no se crea un
    # principal cero ni se redondea hacia arriba dinero que no existe.
    b=PaperBroker(PaperStore(str(tmp_path/'small-cash.db')),
                  initial_cash='.01',daily_loss_pct='1')
    result=place(b,w,offers=[])
    assert result['code']=='NO_AVAILABLE_CASH'
    with b.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_caucion_treasury_days').fetchone()[0]==0
