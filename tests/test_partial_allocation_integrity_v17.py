"""Parciales: procedencia, reparto histórico y precio agregado conciliados."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from bw_daily_risk import DailyRisk
from bt_caucion_paper import CaucionOffer
import cd_spot_ledger as ledger
from test_production_paper_v1634 import partial_spot, quote

LATER = '2026-09-01T11:00:00-03:00'


@pytest.fixture
def sold(partial_spot):
    def make(final=False, currency='ARS'):
        b, p, q, sell = partial_spot('INMEDIATA', currency)
        assert b._close(p, sell(), 'TEST_PARTIAL')
        if final:
            assert b._close(b.store.open_positions()[0], sell(2, '100', '105'), 'TEST_FINAL')
        return b, p, q, sell
    return make


def shift_cost(b, p, final=False, delta=D('.01')):
    """Mantiene la aritmética de cada fill y, al cerrar, también los totales."""
    with b.store.connect() as c:
        rows = ledger.sales(c, p['paper_id'])
        first = rows[0]
        c.execute('UPDATE paper_spot_sales SET entry_cost=?,net_pnl=? WHERE fill_id=?',
                  (str(D(first['entry_cost']) + delta), str(D(first['net_pnl']) - delta), first['fill_id']))
        if final:
            last = rows[-1]
            c.execute('UPDATE paper_spot_sales SET entry_cost=?,net_pnl=? WHERE fill_id=?',
                      (str(D(last['entry_cost']) - delta), str(D(last['net_pnl']) + delta), last['fill_id']))


def assert_reads_reject_without_writes(b, p, at, final):
    with b.store.connect() as c:
        before = list(c.iterdump())
    for cut in (at, LATER):
        with pytest.raises(ValueError):
            b._cash(cut, p['currency'])
        with b.store.connect() as c:
            with pytest.raises(ValueError):
                ledger.positions_at(c, cut)
    if final:
        with pytest.raises(ValueError):
            b.store.recent_closed()
    else:
        with pytest.raises(ValueError):
            b.store.open_positions()
    with b.store.connect() as c:
        assert list(c.iterdump()) == before


@pytest.mark.parametrize('final', [False, True])
@pytest.mark.parametrize('source', ['SANDBOX', 'PRODUCTION', ''])
def test_source_de_venta_debe_coincidir_con_la_posicion(sold, final, source):
    b, p, _, sell = sold(final)
    with b.store.connect() as c:
        c.execute("UPDATE paper_fills SET source=? WHERE paper_id=? AND side='SELL_SIMULATED'",
                  (source, p['paper_id']))
    assert_reads_reject_without_writes(b, p, sell(2).observed_at, final)


@pytest.mark.parametrize('final', [False, True])
@pytest.mark.parametrize('delta', [D('.01'), D('-.01')])
@pytest.mark.parametrize('currency', ['ARS', 'USD_MEP', 'USD_CCL'])
def test_redistribuir_costo_no_vale_aunque_concilien_totales(sold, final, delta, currency):
    b, p, _, sell = sold(final, currency)
    shift_cost(b, p, final, delta)
    if final:
        with b.store.connect() as c:
            rows = ledger.sales(c, p['paper_id'])
            root = c.execute('SELECT * FROM paper_positions WHERE paper_id=?', (p['paper_id'],)).fetchone()
            assert sum(D(r['entry_cost']) for r in rows) == D(root['entry_cost'])
            assert sum(D(r['net_pnl']) for r in rows) == D(root['net_pnl'])
    assert_reads_reject_without_writes(b, p, sell(2).observed_at, final)


@pytest.mark.parametrize('price', ['999', '0', '-1', 'NaN', 'Infinity', None])
def test_precio_agregado_de_cierre_debe_concordar_con_sus_fills(sold, price):
    b, p, _, sell = sold(True)
    with b.store.connect() as c:
        c.execute('UPDATE paper_positions SET exit_price=? WHERE paper_id=?', (price, p['paper_id']))
    assert_reads_reject_without_writes(b, p, sell(2).observed_at, True)


@pytest.mark.parametrize('kind', ['source', 'allocation'])
def test_error_bloquea_caja_riesgo_y_caucion_sin_cegar_otro_stop(sold, kind):
    b, bad, _, sell = sold()
    good_q = quote(symbol='ALUA', minute=2, ask_size='100')
    assert b._open(good_q, D('.8'), {})[0]
    good = next(p for p in b.store.open_positions() if p['symbol'] == 'ALUA')
    if kind == 'allocation':
        shift_cost(b, bad)
    else:
        with b.store.connect() as c:
            c.execute("UPDATE paper_fills SET source='SANDBOX' WHERE paper_id=? AND side='SELL_SIMULATED'", (bad['paper_id'],))
    with b.store.connect() as c:
        before = list(c.iterdump())
    at = sell(3).observed_at
    offer = CaucionOffer('TEST_ONLY', 'ARS', D('.5'), '2026-08-25',
        '2026-08-26T17:00:00-03:00', at, D('100000'), D('1'), D('1'), 365,
        'MATURITY', 'FIXTURE', D('0'), D('1000'))
    with pytest.raises(ValueError):
        b.place_caucion(offer, D('1000'), 'INVALID_PARTIAL', as_of=at)
    with b.store.connect() as c:
        assert list(c.iterdump()) == before
    b.daily_risk = DailyRisk(b, '1')
    results = {v.paper_id: v for v in PositionExitSupervisor(b, clock_fn=lambda: at).tick({
        tuple(good[k] for k in ('symbol','asset_class','settlement','currency','market')):
            quote(symbol='ALUA', minute=3, price='90')})}
    assert results[bad['paper_id']].state == 'WATCH_INVALID_LEDGER'
    assert results[bad['paper_id']].cause == 'TEST_PARTIAL'
    assert results[good['paper_id']].state == 'CLOSED'
    assert all(r['state'] == 'INVALID_LEDGER' for r in b.daily_risk.evaluate(at).values())


@pytest.mark.parametrize('kind', ['source', 'allocation', 'average'])
def test_dashboard_no_recicla_balances_y_oculta_resultados_inconsistentes(sold, monkeypatch, kind):
    import bg_paper_dashboard as dashboard
    b, p, _, sell = sold(True)
    b.mark_equity({}, as_of=sell(2).observed_at)
    monkeypatch.setattr(dashboard, 'DB_PATH', b.store.path)
    assert dashboard.snapshot()['balances_by_currency']
    if kind == 'allocation':
        shift_cost(b, p, True)
    else:
        with b.store.connect() as c:
            if kind == 'source':
                c.execute("UPDATE paper_fills SET source='SANDBOX' WHERE side='SELL_SIMULATED'")
            else:
                c.execute("UPDATE paper_positions SET exit_price='999' WHERE status='CLOSED'")
    with b.store.connect() as c:
        before = list(c.iterdump())
    state = dashboard.snapshot()
    assert state['spot_state'] == 'UNAVAILABLE'
    assert state['balances_by_currency'] == [] and state['equity'] == {}
    assert 'no interpretar como cero' in dashboard.home_page()
    with b.store.connect() as c:
        assert list(c.iterdump()) == before


@pytest.mark.parametrize('currency', ['ARS', 'USD_MEP', 'USD_CCL'])
def test_parciales_validos_preservan_cortes_reinicio_y_costo_historico(sold, monkeypatch, currency):
    b, p, q, sell = sold(True, currency)
    with b.store.connect() as c:
        root = c.execute('SELECT * FROM paper_positions WHERE paper_id=?', (p['paper_id'],)).fetchone()
        rows = ledger.sales(c, p['paper_id'])
        remaining, realized = ledger.partition(c, root, sell().observed_at)
        assert D(remaining['quantity']) == 6 and len(realized) == 1
        assert D(remaining['entry_cost']) + D(realized[0]['entry_cost']) == D(p['entry_cost'])
        before = list(c.iterdump())
    balance = b._cash(LATER, currency)
    restarted = PaperBroker(PaperStore(b.store.path), initial_cash='10000', initial_cash_by_currency={currency:'10000'})
    def no_reprice(*args):
        raise AssertionError('No recalcular tarifa histórica')
    monkeypatch.setattr(restarted, '_cost', no_reprice)
    assert restarted._cash(LATER, currency) == balance
    assert restarted.store.recent_closed()[0]['paper_id'] == p['paper_id']
    with b.store.connect() as c:
        assert list(c.iterdump()) == before


@pytest.mark.parametrize('fee,parts,expected', [
    ('0.01', [1,1,1], ['0.00','0.00','0.01']),
    ('0.02', [1,1,1], ['0.01','0.01','0.00']),
    ('0.03', [1,1], ['0.02','0.01']),
    ('0.005', [1,1], ['0.00','0.005']),
    ('0.009', [3,1], ['0.009','0.000']),
    ('0', [1,2], ['0.00','0.00'])])
def test_residuo_de_redondeo_exacto_en_ultimo_fill(fee, parts, expected):
    total, cost = D(sum(parts)), D(fee)
    sold_qty = allocated = D(0)
    actual = []
    for qty in parts:
        portion = ledger.allocated_entry_cost(total, cost, sold_qty, allocated, D(qty))
        actual.append(portion)
        sold_qty += qty
        allocated += portion
    assert actual == [D(s) for s in expected]
    assert allocated == cost


@pytest.mark.parametrize('args', [
    ('0', '1', '0', '0', '1'), ('10', '-1', '0', '0', '1'),
    ('10', '1', '-1', '0', '1'), ('10', '1', '0', '-1', '1'),
    ('10', '1', '0', '0', '0'), ('10', '1', '11', '0', '1'),
    ('10', '1', '0', '2', '1'), ('10', '1', '9', '0', '2'),
    ('10', 'NaN', '0', '0', '1'), ('10', '1', '0', '0', 'Infinity')])
def test_reparto_invalido_no_produce_importes(args):
    with pytest.raises(ValueError):
        ledger.allocated_entry_cost(*args)
