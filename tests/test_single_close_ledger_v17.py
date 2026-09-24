"""Un cierre simple debe conciliar con su fill y su recibo antes de dar caja."""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bt_caucion_paper import pending_proceeds
from test_production_paper_v1634 import quote

AT='2026-08-28T11:00:00-03:00'
CLOSE='2026-08-28T11:01:00-03:00'
LATER='2026-09-01T11:00:00-03:00'


@pytest.fixture(autouse=True)
def isolate_ledger_integrity_from_entry_selection_policy(monkeypatch):
    """Ledger tests start after admission; sector eligibility is covered separately."""
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "SHADOW")


@pytest.fixture
def closed(tmp_path):
    def make(settlement='INMEDIATA',currency='ARS'):
        b=PaperBroker(PaperStore(str(tmp_path/(settlement+currency+'.db'))),
            initial_cash='10000',initial_cash_by_currency={currency:'10000'})
        q=replace(quote(at=AT,ask_size='100'),settlement=settlement,currency=currency)
        assert b._open(q,D('.8'),{})[0]
        p=b.store.open_positions()[0]
        assert b._close(p,replace(q,bid=D(110),ask=D(111),observed_at=CLOSE,book_at=CLOSE),'TEST')
        return b,dict(b.store.recent_closed()[0])
    return make


@pytest.mark.parametrize('field,value',[
    ('net_pnl','9999'),('gross_pnl','9999'),('exit_price','200'),('exit_cost','0'),
    ('entry_cost','0'),('quantity','1'),('entry_price','99'),('net_pnl','NaN'),
    ('quantity','Infinity'),('entry_cost','-1'),('exit_cost','-1'),('exit_price','NaN'),
    ('features_json','[]'),('features_json','{"contract_cash_multiplier":".01"}'),
    ('closed_at','2026-08-28T11:02:00-03:00'),
])
def test_cierre_no_conciliado_no_financia_caja_ni_aprendizaje(closed,field,value):
    b,p=closed()
    with b.store.connect() as c:
        c.execute('UPDATE paper_positions SET '+field+'=? WHERE paper_id=?',(value,p['paper_id']))
    for read in (lambda:b._cash(LATER),lambda:b.store.recent_closed(),lambda:b.threshold(LATER)):
        with pytest.raises(ValueError):
            read()


@pytest.mark.parametrize('field,value',[
    ('quantity','9'),('price','109'),('costs','0'),('quantity','NaN'),
    ('costs','-1'),('source','OTHER'),('side','UNKNOWN'),
    ('filled_at','2026-08-28T11:02:00-03:00'),
])
def test_fill_de_venta_incompatible_bloquea_el_cierre(closed,field,value):
    b,p=closed()
    with b.store.connect() as c:
        c.execute('UPDATE paper_fills SET '+field+"=? WHERE paper_id=? AND side='SELL_SIMULATED'",(value,p['paper_id']))
    with pytest.raises(ValueError):
        b._cash(LATER)


@pytest.mark.parametrize('field,value',[
    ('net_proceeds','0'),('net_proceeds','-1'),('net_proceeds','9999'),
    ('available_at','2026-08-28T11:01:00-03:00'),
    ('available_at','2026-08-27T11:00:00-03:00'),
    ('available_at','2026-08-31T12:00:00'),('basis','UNKNOWN'),
])
def test_recibo_no_puede_adelantar_liquidacion_o_inventar_producido(closed,field,value):
    b,p=closed('A-24HS')
    with b.store.connect() as c:
        c.execute('UPDATE paper_sale_receivables SET '+field+'=? WHERE paper_id=?',(value,p['paper_id']))
    with pytest.raises(ValueError):
        b._cash(CLOSE)


@pytest.mark.parametrize('settlement',['INMEDIATA','A-24HS'])
@pytest.mark.parametrize('currency',['ARS','USD_MEP'])
def test_cierres_validos_conservan_caja_historica_y_costos(closed,settlement,currency,monkeypatch):
    b,p=closed(settlement,currency)
    before=b._cash(AT,currency)
    proceeds=D(p['exit_price'])*D(p['quantity'])-D(p['exit_cost'])
    expected=before+proceeds if settlement=='INMEDIATA' else before
    assert b._cash(CLOSE,currency)==expected
    assert pending_proceeds(b.store,CLOSE,currency)==(0 if settlement=='INMEDIATA' else proceeds)
    def no_reprice(*args):
        raise AssertionError('No recalcular tarifas de un fill histórico')
    monkeypatch.setattr(b,'_cost',no_reprice)
    later_expected=10000+D(p['net_pnl']) if settlement=='INMEDIATA' else before
    assert b._cash(LATER,currency)==later_expected
    assert b._cash('2026-08-28T10:59:00-03:00',currency)==10000
    with b.store.connect() as c:
        c.execute('UPDATE paper_positions SET closed_at=? WHERE paper_id=?',
                  ('2026-08-28T14:01:00+00:00',p['paper_id']))
    assert b._cash(LATER,currency)==later_expected


def test_reinicio_no_crea_recibo_a_partir_de_cierre_roto(closed):
    b,p=closed()
    with b.store.connect() as c:
        c.execute('DELETE FROM paper_sale_receivables WHERE paper_id=?',(p['paper_id'],))
        c.execute("UPDATE paper_positions SET net_pnl='9999' WHERE paper_id=?",(p['paper_id'],))
        original=tuple(c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(p['paper_id'],)).fetchone())
    restarted=PaperStore(b.store.path)
    with restarted.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_sale_receivables').fetchone()[0]==0
        assert tuple(c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(p['paper_id'],)).fetchone())==original
    with pytest.raises(ValueError):
        PaperBroker(restarted,initial_cash='10000')._cash(LATER)


def test_cierre_roto_bloquea_riesgo_e_informes_sin_reescribirlo(closed,monkeypatch):
    import bg_paper_dashboard as dashboard
    import bi_operational_services as services
    from bw_daily_risk import DailyRisk
    b,p=closed()
    services.init_schema(b.store)
    with b.store.connect() as c:
        c.execute("UPDATE paper_positions SET net_pnl='9999' WHERE paper_id=?",(p['paper_id'],))
        before=list(c.iterdump())
    monkeypatch.setattr(dashboard,'DB_PATH',b.store.path)
    assert dashboard.snapshot()['spot_state']=='UNAVAILABLE'
    assert 'no interpretar como cero' in dashboard.motor_page()
    with pytest.raises(ValueError):
        services._period_data(b.store,AT,LATER)
    with b.store.connect() as c:
        assert list(c.iterdump())==before
    assert all(r['state']=='INVALID_LEDGER' for r in DailyRisk(b,'1').evaluate(CLOSE).values())


def test_recibo_pendiente_sin_confirmacion_no_inventa_fecha(closed):
    b,p=closed('A-24HS')
    with b.store.connect() as c:
        c.execute("UPDATE paper_sale_receivables SET available_at=NULL,basis='PENDING_CONFIRMATION'")
    assert b._cash(LATER)==b._cash(AT)


@pytest.mark.parametrize('family',['BONOS','LETRAS','ON'])
def test_renta_fija_fuera_de_alcance_no_abre_en_paper_rc6(tmp_path,family):
    from bs_instrument_contracts import InstrumentContract
    b=PaperBroker(PaperStore(str(tmp_path/'nominal.db')),initial_cash='10000',slippage_bps='0')
    q=replace(quote(at=AT,ask_size='100'),asset_class=family,settlement='INMEDIATA',ask=D(100))
    contract=InstrumentContract(q.symbol,family,'ARS','BYMA','INMEDIATA',D('.01'),D(2),'TEST_FIXTURE')
    q=replace(q,contract=contract)
    opened, reason = b._open(q,D('.8'),{})
    assert opened is False
    assert reason
    assert b.store.open_positions() == []


def test_cierre_inconsistente_no_detiene_stop_de_otra_posicion(closed):
    from bm_exit_supervisor import PositionExitSupervisor
    from bw_daily_risk import DailyRisk
    b,p=closed()
    q=replace(quote(symbol='ALUA',at=CLOSE,ask_size='100'),settlement='INMEDIATA')
    assert b._open(q,D('.8'),{})[0]
    good=b.store.open_positions()[0]
    b.daily_risk=DailyRisk(b,'1')
    with b.store.connect() as c:
        c.execute("UPDATE paper_positions SET net_pnl='9999' WHERE paper_id=?",(p['paper_id'],))
    later='2026-08-28T11:02:00-03:00'
    sell=replace(q,bid=D(90),ask=D(91),observed_at=later,book_at=later)
    identity=tuple(good[k] for k in ('symbol','asset_class','settlement','currency','market'))
    assert PositionExitSupervisor(b,clock_fn=lambda:later).tick({identity:sell})[0].state=='CLOSED'
    with b.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions WHERE paper_id=?',(good['paper_id'],)).fetchone()[0]=='CLOSED'
    with pytest.raises(ValueError):
        b._cash(later)


@pytest.mark.parametrize('problem',['receipt','pnl'])
def test_panel_no_recicla_balance_anterior_si_ledger_deja_de_conciliar(closed,monkeypatch,problem):
    import bg_paper_dashboard as dashboard
    b,p=closed()
    b.mark_equity({},as_of=CLOSE)
    monkeypatch.setattr(dashboard,'DB_PATH',b.store.path)
    assert dashboard.snapshot()['balances_by_currency']
    with b.store.connect() as c:
        if problem=='receipt':
            c.execute("UPDATE paper_sale_receivables SET net_proceeds='0'")
        else:
            c.execute("UPDATE paper_positions SET net_pnl='9999'")
    snapshot=dashboard.snapshot()
    assert snapshot['spot_state']=='UNAVAILABLE'
    assert snapshot['balances_by_currency']==[] and snapshot['equity']=={}
    assert '<th>Disponible</th>' not in dashboard._balances_panel()
    assert 'no interpretar como cero' in dashboard.home_page()


@pytest.mark.parametrize('problem',['missing','duplicated'])
def test_cierre_sin_fill_unico_no_crea_caja(closed,problem):
    b,p=closed()
    with b.store.connect() as c:
        fill=dict(c.execute("SELECT * FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone())
        if problem=='missing':
            c.execute('DELETE FROM paper_book_consumption WHERE fill_id=?',(fill['id'],))
            c.execute('DELETE FROM paper_fills WHERE id=?',(fill['id'],))
        else:
            c.execute('INSERT INTO paper_fills VALUES(NULL,?,?,?,?,?,?,?,?)',tuple(fill[k] for k in
                ('paper_id','source','side','filled_at','quantity','price','costs','slippage')))
    with pytest.raises(ValueError):
        b._cash(LATER)
