"""Un registro de caución roto no puede crear efectivo o acreditarse."""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bt_caucion_paper import validate_position
from test_production_paper_v1634 import caucion_offer, quote

AT='2026-08-28T11:00:00-03:00'
MATURITY='2026-08-31T15:00:00-03:00'


@pytest.fixture
def ledger(tmp_path):
    b=PaperBroker(PaperStore(str(tmp_path/'ledger.db')),initial_cash='10000')
    p=b.place_caucion(caucion_offer(),'1000','first',AT)
    assert b._cash(AT)==9000
    return b,p


def test_principal_negativo_no_crea_caja(ledger):
    b,_=ledger
    with b.store.connect() as c:
        c.execute("UPDATE paper_cauciones SET principal='-1000'")
    with pytest.raises(ValueError,match='CAUCION_LEDGER'):
        b._cash(AT)


@pytest.mark.parametrize('field,value',[
    ('principal','NaN'),('principal','999'),('principal','1000.001'),
    ('total_fees','-1'),('total_fees','NaN'),('total_fees','0'),
    ('gross_interest','99999'),('gross_interest','Infinity'),
    ('currency','USD_CCL'),('instrument_id','OTRO'),('source','REAL'),
    ('annual_rate_fraction','.50'),('interest_days',1),('day_count_basis',360),
    ('fee_payment','UNKNOWN'),('request_fingerprint','broken'),('terms_json','{}'),
    ('terms_json','[]'),('terms_json','invalid'),('status','MATURED'),('status','UNKNOWN'),
    ('settled_at',''),('maturity_at','2026-08-29T15:00:00-03:00'),
    ('opened_at','2026-08-28T10:59:00-03:00'),
])
def test_ledger_inconsistente_no_libera_saldo_ni_se_acredita(ledger,field,value):
    b,_=ledger
    with b.store.connect() as c:
        c.execute('UPDATE paper_cauciones SET '+field+'=?',(value,))
        before=[tuple(r) for r in c.execute('SELECT * FROM paper_cauciones')]
        events=c.execute('SELECT COUNT(*) FROM paper_events').fetchone()[0]
    for read in (lambda:b._cash(AT),lambda:b.cauciones.valuation(AT),
                 lambda:b.cauciones.positions(currency='ARS'),lambda:b.settle_cauciones(MATURITY)):
        with pytest.raises(ValueError,match='CAUCION_LEDGER'):
            read()
    with b.store.connect() as c:
        assert [tuple(r) for r in c.execute('SELECT * FROM paper_cauciones')]==before
        assert c.execute('SELECT COUNT(*) FROM paper_events').fetchone()[0]==events


def test_acreditacion_invalida_no_genera_ganancia_realizada(ledger):
    b,p=ledger
    b.settle_cauciones(MATURITY)
    assert b._cash(MATURITY)==10002
    with b.store.connect() as c:
        c.execute("UPDATE paper_cauciones SET settled_at=?",(AT,))
    with pytest.raises(ValueError,match='CAUCION_LEDGER'):
        b._cash(MATURITY)


def test_lectura_idempotente_no_devuelve_una_operacion_rota(ledger):
    b,_=ledger
    with b.store.connect() as c:
        c.execute("UPDATE paper_cauciones SET principal='999'")
    with pytest.raises(ValueError,match='CAUCION_LEDGER'):
        b.place_caucion(caucion_offer(),'1000','first',AT)


def test_no_acredita_parte_del_lote_si_otra_caucion_no_concilia(ledger):
    b,p=ledger
    other=b.place_caucion(caucion_offer(instrument_id='SECOND'),'1000','second',AT)
    with b.store.connect() as c:
        c.execute("UPDATE paper_cauciones SET total_fees='20' WHERE paper_id=?",(other['paper_id'],))
    with pytest.raises(ValueError,match='CAUCION_LEDGER'):
        b.settle_cauciones(MATURITY)
    with b.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_cauciones WHERE status='MATURED'").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_CAUCION_MATURED'").fetchone()[0]==0


def test_costo_legado_se_conserva_sin_recalcular_tarifario(ledger,monkeypatch):
    b,_=ledger
    offer=caucion_offer(instrument_id='LEGACY',quoted_total_fees=None,fee_quote_principal=None)
    p=b.place_caucion(offer,'1000','legacy',AT)
    before=b._cash(AT)
    import au_fee_schedule as tariff
    def no_tariff(*args,**kwargs):
        raise AssertionError('No recalcular costos de un fill histórico')
    monkeypatch.setattr(tariff,'arancel_de',no_tariff)
    assert validate_position(p).quoted_total_fees is None
    assert b._cash(AT)==before
    b.settle_cauciones(MATURITY)
    assert b._cash(MATURITY)==D(10002)+D(p['gross_interest'])-D(p['total_fees'])


@pytest.mark.parametrize('payment,currency,basis',[
    ('UPFRONT','ARS',365),('MATURITY','USD_MEP',360),('UPFRONT','USD_CCL',365)])
def test_contratos_validos_preservan_caja_temporal(tmp_path,payment,currency,basis):
    b=PaperBroker(PaperStore(str(tmp_path/'valid.db')),initial_cash='10000',
                  initial_cash_by_currency={'USD_MEP':'10000','USD_CCL':'10000'})
    offer=caucion_offer(currency=currency,fee_payment=payment,day_count_basis=basis,
                       maturity_at='2026-08-31T18:00:00+00:00')
    p=b.place_caucion(offer,'1000','valid',AT)
    assert validate_position(p).currency==currency
    expected=D(8999) if payment=='UPFRONT' else D(9000)
    assert b._cash(AT,currency)==expected
    b.settle_cauciones(MATURITY)
    assert b._cash(AT,currency)==expected
    assert b._cash(MATURITY,currency)==10000+D(p['gross_interest'])-D(p['total_fees'])


def test_riesgo_e_informes_no_usan_un_ledger_inconsistente(ledger):
    import bi_operational_services as services
    b,_=ledger
    services.init_schema(b.store)
    risk_broker=PaperBroker(b.store,initial_cash='10000',daily_loss_pct='1')
    with b.store.connect() as c:
        c.execute("UPDATE paper_cauciones SET currency='USD_CCL'")
    states=risk_broker.daily_risk.evaluate(AT)
    assert all(r['state']=='INVALID_LEDGER' for r in states.values())
    assert all(r['daily_pnl'] is None for r in states.values())
    with pytest.raises(ValueError,match='CAUCION_LEDGER'):
        services._period_data(b.store,'2026-08-28T00:00:00-03:00','2026-09-01T00:00:00-03:00')


def test_panel_no_muestra_importes_rotos_ni_un_vacio_exitoso(ledger,monkeypatch):
    import bg_paper_dashboard as dashboard
    b,_=ledger
    monkeypatch.setattr(dashboard,'DB_PATH',b.store.path)
    assert dashboard._caucion_snapshot()['state']=='READY'
    with b.store.connect() as c:
        c.execute("UPDATE paper_cauciones SET principal='99999'")
    data=dashboard.snapshot()
    assert data['caucion_state']=='INVALID_LEDGER' and data['cauciones']==[]
    for html in (dashboard._cauciones_panel(),dashboard._balances_panel(),dashboard.home_page()):
        assert 'no interpretar como cero' in html
        assert '99999' not in html and 'Sin colocaciones simuladas' not in html


def test_lector_panel_no_crea_base_y_distingue_tabla_ausente(tmp_path,monkeypatch):
    import sqlite3
    import bg_paper_dashboard as dashboard
    path=tmp_path/'missing.db'
    monkeypatch.setattr(dashboard,'DB_PATH',str(path))
    assert dashboard._caucion_snapshot()['state']=='UNAVAILABLE'
    assert not path.exists()
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE fixture(id INTEGER)')
    assert dashboard._caucion_snapshot()['state']=='MISSING_TABLE'


def test_caucion_rota_no_detiene_supervision_ni_salidas_spot(ledger):
    from bm_exit_supervisor import PositionExitSupervisor
    b,_=ledger
    q=quote(at=AT)
    b.store.add_quote(q)
    assert b._open(q,D('.8'),{})[0]
    p=b.store.open_positions()[0]
    at='2026-08-28T11:01:00-03:00'
    q=replace(quote(at=at),bid=D(p['stop_price'])-1,ask=D(p['stop_price']),bid_size=D(10000))
    key=tuple(p[k] for k in ('symbol','asset_class','settlement','currency','market'))
    with b.store.connect() as c:
        c.execute("UPDATE paper_cauciones SET principal='-1000'")
    supervisor=PositionExitSupervisor(b,clock_fn=lambda:at)
    verdicts=supervisor.tick({key:q})
    assert verdicts[0].state=='CLOSED'
    assert not b.store.open_positions()
    with b.store.connect() as c:
        state=c.execute('SELECT * FROM paper_supervisor_state WHERE id=1').fetchone()
        assert state['state']=='DEGRADED' and 'CAUCION_SETTLEMENT_BLOCKED' in state['detail']
        assert c.execute("SELECT COUNT(*) FROM paper_cauciones WHERE status='MATURED'").fetchone()[0]==0
