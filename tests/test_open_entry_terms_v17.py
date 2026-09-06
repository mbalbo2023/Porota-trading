"""Validar entradas sin ventas antes de usarlas como tenencias o caja."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor
from bw_daily_risk import DailyRisk
from test_production_paper_v1634 import quote

AT='2026-08-28T11:00:00-03:00'
LATER='2026-08-28T11:01:00-03:00'


@pytest.fixture
def opened(tmp_path):
    # This fixture fabricates two imported positions before explicitly attaching
    # the 1% DailyRisk under test. Disable admission risk only for that setup;
    # production/runtime keeps the canonical 2.5% guard.
    b=PaperBroker(PaperStore(str(tmp_path/'open.db')),initial_cash='10000',daily_loss_pct=None)
    for symbol in ('ALUA','GGAL'):
        assert b._open(quote(symbol=symbol,at=AT,ask_size='100'),D('.8'),{})[0]
    bad,good=sorted(b.store.open_positions(),key=lambda p:p['symbol'])
    b.daily_risk=DailyRisk(b,'1')
    return b,bad,good


@pytest.mark.parametrize('field,value',[
    ('quantity','0'),('quantity','-1'),('quantity','NaN'),('quantity','Infinity'),
    ('entry_price','0'),('entry_price','-1'),('entry_price','NaN'),
    ('entry_cost','-1'),('entry_cost','NaN'),('entry_cost','Infinity'),
    ('features_json','[]'),('features_json','null'),('features_json','invalid'),
    ('features_json','{"contract_cash_multiplier":"0"}'),
    ('features_json','{"contract_cash_multiplier":"-1"}'),
    ('features_json','{"contract_cash_multiplier":"NaN"}'),
    ('currency','PESOS'),('currency','ars'),('currency','EUR'),('currency',''),
])
def test_entrada_invalida_bloquea_caja_y_no_detiene_otro_stop(opened,field,value):
    b,bad,good=opened
    with b.store.connect() as c:
        c.execute('UPDATE paper_positions SET '+field+'=? WHERE paper_id=?',(value,bad['paper_id']))
        before=tuple(c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(bad['paper_id'],)).fetchone())
    for read in (lambda:b.store.open_positions(),lambda:b._cash(AT)):
        with pytest.raises(ValueError):
            read()
    valid,invalid=b.store.exit_positions()
    assert [p['paper_id'] for p in valid]==[good['paper_id']]
    assert invalid[0][0]['paper_id']==bad['paper_id']
    identity=tuple(good[k] for k in ('symbol','asset_class','settlement','currency','market'))
    results={v.paper_id:v for v in PositionExitSupervisor(b,clock_fn=lambda:LATER).tick(
        {identity:quote(symbol='GGAL',price='90',at=LATER)})}
    assert results[bad['paper_id']].state=='WATCH_INVALID_LEDGER'
    assert results[good['paper_id']].state=='CLOSED'
    with b.store.connect() as c:
        assert tuple(c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(bad['paper_id'],)).fetchone())==before
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE paper_id=? AND side='SELL_SIMULATED'",(bad['paper_id'],)).fetchone()[0]==0
        assert c.execute('SELECT state FROM paper_supervisor_state').fetchone()[0]=='DEGRADED'
    assert all(r['state']=='INVALID_LEDGER' for r in b.daily_risk.evaluate(LATER).values())


def test_importada_valida_sin_factor_explicito_conserva_unidad_original(opened):
    b,bad,_=opened
    with b.store.connect() as c:
        c.execute("UPDATE paper_positions SET features_json='{}' WHERE paper_id=?",(bad['paper_id'],))
        c.execute('DELETE FROM paper_book_consumption WHERE fill_id IN (SELECT id FROM paper_fills WHERE paper_id=?)',(bad['paper_id'],))
        c.execute('DELETE FROM paper_fills WHERE paper_id=?',(bad['paper_id'],))
        before=list(c.iterdump())
    rows=b.store.open_positions()
    assert len(rows)==2
    assert b._cash(AT)<10000 and b._cash(AT)>0
    with b.store.connect() as c:
        assert list(c.iterdump())==before


def test_panel_no_presenta_entrada_invalida_como_tenencia(opened,monkeypatch):
    import bg_paper_dashboard as dashboard
    b,bad,_=opened
    with b.store.connect() as c:
        c.execute("UPDATE paper_positions SET features_json='[]' WHERE paper_id=?",(bad['paper_id'],))
    monkeypatch.setattr(dashboard,'DB_PATH',b.store.path)
    assert dashboard.snapshot()['spot_state']=='UNAVAILABLE'
    assert 'no interpretar como cero' in dashboard.motor_page()
