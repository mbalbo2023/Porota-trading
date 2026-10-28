"""Un parcial no libera caja por una fecha o fuente de liquidación inválida."""
from pathlib import Path
from dataclasses import replace
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bt_caucion_paper import CaucionOffer, pending_proceeds
from test_production_paper_v1634 import partial_spot, quote

LATER='2026-09-01T11:00:00-03:00'


@pytest.fixture
def sold(partial_spot):
    def make(final=False,settlement='A-24HS',currency='ARS'):
        b,p,q,sell=partial_spot(settlement,currency)
        assert b._close(p,sell(),'TEST')
        if final:
            assert b._close(b.store.open_positions()[0],sell(2,'100'),'TEST')
        return b,p,q,sell
    return make


@pytest.mark.parametrize('final',[False,True])
@pytest.mark.parametrize('value',[
    '2026-08-25T11:01:00-03:00',
    '2026-08-24T11:00:00-03:00',
    '2026-08-27T11:00:00-03:00',
    '2026-08-26T23:59:59.999999',
    '',
])
def test_t1_pendiente_con_timestamp_inventado_no_libera_caja(sold,final,value):
    b,p,_,sell=sold(final)
    with b.store.connect() as c:
        c.execute("UPDATE paper_spot_sales SET basis='PENDING_CONFIRMATION',available_at=? WHERE paper_id=?",
                  (value,p['paper_id']))
        before=list(c.iterdump())
    for at in (sell(2).observed_at,LATER):
        with pytest.raises(ValueError):
            b._cash(at)
        with pytest.raises(ValueError):
            pending_proceeds(b.store,at)
    with b.store.connect() as c:
        assert list(c.iterdump())==before


@pytest.mark.parametrize('final',[False,True])
def test_t1_pending_confirmation_none_es_valido_y_conservador(sold,final):
    b,p,q,_=sold(final)
    before=b._cash(q.observed_at)
    with b.store.connect() as c:
        rows=c.execute('SELECT basis,available_at FROM paper_spot_sales WHERE paper_id=?',(p['paper_id'],)).fetchall()
        assert rows and all(r['basis']=='PENDING_CONFIRMATION' and r['available_at'] is None for r in rows)
    assert b._cash(LATER)==before
    assert pending_proceeds(b.store,LATER)>0


@pytest.mark.parametrize('final',[False,True])
def test_t1_basis_desconocida_falla_cerrado(sold,final):
    b,p,_,_=sold(final)
    with b.store.connect() as c:
        c.execute("UPDATE paper_spot_sales SET basis='UNKNOWN' WHERE paper_id=?",(p['paper_id'],))
    with pytest.raises(ValueError):
        b._cash(LATER)


@pytest.mark.parametrize('final',[False,True])
@pytest.mark.parametrize('settlement',['INMEDIATA','A-24HS'])
@pytest.mark.parametrize('currency',['ARS','USD_MEP','USD_CCL'])
def test_recibos_validos_conservan_caja_por_moneda_fecha_y_reinicio(sold,final,settlement,currency,monkeypatch):
    b,p,q,sell=sold(final,settlement,currency)
    before=b._cash(q.observed_at,currency)
    with b.store.connect() as c:
        rows=c.execute('SELECT * FROM paper_spot_sales').fetchall()
        proceeds=sum(D(r['net_proceeds']) for r in rows)
        original=list(c.iterdump())
    if settlement=='INMEDIATA':
        assert b._cash(sell(2).observed_at,currency)==before+proceeds
        assert b._cash(LATER,currency)==before+proceeds
    else:
        # T+1 stays frozen until an authoritative reconciliation exists.
        assert b._cash(sell(2).observed_at,currency)==before
        assert b._cash(LATER,currency)==before
        assert all(r['basis']=='PENDING_CONFIRMATION' and r['available_at'] is None for r in rows)
    assert b._cash(LATER,'USD')==0
    def no_reprice(*args):
        raise AssertionError('No recalcular un costo histórico')
    monkeypatch.setattr(b,'_cost',no_reprice)
    assert b._cash(LATER,currency)==(before+proceeds if settlement=='INMEDIATA' else before)
    with b.store.connect() as c:
        assert list(c.iterdump())==original
        if settlement=='INMEDIATA':
            for r in rows:
                from bs_instrument_contracts import aware_datetime
                from datetime import timezone
                equivalent=aware_datetime(r['available_at']).astimezone(timezone.utc).isoformat()
                c.execute('UPDATE paper_spot_sales SET available_at=? WHERE fill_id=?',(equivalent,r['fill_id']))
    restarted=PaperBroker(PaperStore(b.store.path),initial_cash='10000',initial_cash_by_currency={currency:'10000'})
    assert restarted._cash(LATER,currency)==(before+proceeds if settlement=='INMEDIATA' else before)
    assert restarted._cash(q.observed_at,currency)==before


@pytest.mark.parametrize('final',[False,True])
def test_pendiente_real_sigue_inmovilizado_sin_inventar_acreditacion(sold,final):
    b,_,q,_=sold(final)
    before=b._cash(q.observed_at)
    with b.store.connect() as c:
        c.execute("UPDATE paper_spot_sales SET basis='PENDING_CONFIRMATION',available_at=NULL")
    assert b._cash(LATER)==before
    restarted=PaperBroker(PaperStore(b.store.path),initial_cash='10000')
    assert restarted._cash(LATER)==before


def test_calendario_no_verificable_no_inventa_timestamp_ni_reescribe_recibos(sold,monkeypatch):
    import ak_byma_calendar as calendar
    from cf_sale_settlement import modeled_sale_settlement_date
    b,_,q,_=sold()
    with b.store.connect() as c:
        before=list(c.iterdump())
    monkeypatch.setattr(calendar,'ANIOS_AUDITADOS',set())
    assert modeled_sale_settlement_date('A-24HS',q.observed_at) is None
    assert b._cash(LATER)==b._cash(q.observed_at)
    with b.store.connect() as c:
        assert list(c.iterdump())==before


@pytest.mark.parametrize('final',[False,True])
def test_panel_valida_recibos_tambien_si_solo_hay_abiertas_parciales(sold,final,monkeypatch):
    import bg_paper_dashboard as dashboard
    b,_,_,sell=sold(final)
    b.mark_equity({},as_of=sell(2).observed_at)
    monkeypatch.setattr(dashboard,'DB_PATH',b.store.path)
    assert dashboard.snapshot()['balances_by_currency']
    with b.store.connect() as c:
        c.execute("UPDATE paper_spot_sales SET basis='UNKNOWN'")
        before=list(c.iterdump())
    snapshot=dashboard.snapshot()
    assert snapshot['spot_state']=='UNAVAILABLE'
    assert snapshot['balances_by_currency']==[] and snapshot['equity']=={}
    assert 'no interpretar como cero' in dashboard.home_page()
    with b.store.connect() as c:
        assert list(c.iterdump())==before


def test_caucion_no_usa_saldo_de_parcial_sin_fecha_conciliada(sold):
    b,_,_,sell=sold()
    at=sell(2).observed_at
    offer=CaucionOffer('FIXTURE','ARS',D('.5'),'2026-08-25','2026-08-26T17:00:00-03:00',
        at,D('100000'),D('1'),D('1'),365,'MATURITY','TEST_FIXTURE',D('0'),D('1000'))
    with b.store.connect() as c:
        c.execute('UPDATE paper_spot_sales SET available_at=?',(sell().observed_at,))
        before=list(c.iterdump())
    with pytest.raises(ValueError):
        b.place_caucion(offer,D('1000'),'INVALID_RECEIPT',as_of=at)
    with b.store.connect() as c:
        assert list(c.iterdump())==before


def test_recibo_roto_no_impide_reducir_exposicion_abierta_valida(sold):
    from bm_exit_supervisor import PositionExitSupervisor
    b,_,_,sell=sold()
    good=b.store.open_positions()[0]
    with b.store.connect() as c:
        c.execute("UPDATE paper_spot_sales SET basis='UNKNOWN'")
    next_quote=replace(quote(symbol=good['symbol'],minute=3,price='90',bid_size='100'),
                       settlement=good['settlement'],currency=good['currency'],market=good['market'])
    identity=tuple(good[k] for k in ('symbol','asset_class','settlement','currency','market'))
    verdicts=PositionExitSupervisor(b,clock_fn=lambda:next_quote.observed_at).tick({identity:next_quote})
    assert next(v for v in verdicts if v.paper_id==good['paper_id']).state=='CLOSED'
    with pytest.raises(ValueError):
        b._cash(next_quote.observed_at)
