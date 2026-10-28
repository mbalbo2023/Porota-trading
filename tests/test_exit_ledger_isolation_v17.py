"""Una abierta inconsistente no puede cegar los stops de otras posiciones."""
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from be_paper_engine import D, PaperBroker, PaperStore
from bm_exit_supervisor import PositionExitSupervisor, admission_error
from bq_exit_policy import PaperSessionPolicy
from bv_paper_runtime import collect_exit_books
from bw_daily_risk import DailyRisk
from test_production_paper_v1634 import quote

AT='2026-08-28T11:00:00-03:00'
LATER='2026-08-28T11:02:00-03:00'


def key(p):
    return tuple(p[k] for k in ('symbol','asset_class','settlement','currency','market'))


@pytest.fixture
def positions(tmp_path):
    b=PaperBroker(PaperStore(str(tmp_path/'isolation.db')),initial_cash='10000')
    first=quote(symbol='GGAL',ask_size='100',at=AT)
    assert b._open(first,D('.8'),{})[0]
    b.store.add_quote(first)
    second=quote(symbol='ALUA',ask_size='100',at=AT)
    assert b._open(second,D('.8'),{})[0]
    good,bad=sorted(b.store.open_positions(),key=lambda p:p['symbol'],reverse=True)
    assert good['symbol']=='GGAL'
    assert b._close(bad,quote(symbol='ALUA',bid_size='10',at='2026-08-28T11:01:00-03:00'),'TEST_PARTIAL')
    b.daily_risk=DailyRisk(b,'1')
    return b,good,bad


def corrupt(b,p,kind='date'):
    with b.store.connect() as c:
        if kind=='date':
            c.execute("UPDATE paper_positions SET opened_at='invalid' WHERE paper_id=?",(p['paper_id'],))
        elif kind=='state':
            c.execute('UPDATE paper_positions SET closed_at=? WHERE paper_id=?',(LATER,p['paper_id']))
        elif kind=='pnl':
            c.execute("UPDATE paper_spot_sales SET net_pnl='999' WHERE paper_id=?",(p['paper_id'],))
        elif kind=='quantity':
            c.execute("UPDATE paper_fills SET quantity='Infinity' WHERE paper_id=? AND side='SELL_SIMULATED'",(p['paper_id'],))
        elif kind=='features':
            c.execute("UPDATE paper_positions SET features_json='[]' WHERE paper_id=?",(p['paper_id'],))


@pytest.mark.parametrize('kind',['date','state','pnl','quantity','features'])
def test_posicion_rota_no_detiene_stop_valido_ni_libera_caja(positions,kind):
    b,good,bad=positions
    corrupt(b,bad,kind)
    with b.store.connect() as c:
        bad_before=tuple(c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(bad['paper_id'],)).fetchone())
        fills_before=c.execute('SELECT COUNT(*) FROM paper_fills WHERE paper_id=?',(bad['paper_id'],)).fetchone()[0]
    sup=PositionExitSupervisor(b,clock_fn=lambda:LATER)
    results={v.paper_id:v for v in sup.tick({key(good):quote(price='90',at=LATER)})}
    assert results[good['paper_id']].state=='CLOSED'
    assert results[bad['paper_id']].state=='WATCH_INVALID_LEDGER'
    assert results[bad['paper_id']].cause=='TEST_PARTIAL'
    assert admission_error(b.store,LATER)=='EXIT_SUPERVISOR_UNAVAILABLE'
    with b.store.connect() as c:
        assert tuple(c.execute('SELECT * FROM paper_positions WHERE paper_id=?',(bad['paper_id'],)).fetchone())==bad_before
        assert c.execute('SELECT COUNT(*) FROM paper_fills WHERE paper_id=?',(bad['paper_id'],)).fetchone()[0]==fills_before
        state=c.execute('SELECT * FROM paper_supervisor_state').fetchone()
        assert state['state']=='DEGRADED' and 'SPOT_LEDGER_BLOCKED' in state['detail']
        assert c.execute('SELECT status FROM paper_positions WHERE paper_id=?',(good['paper_id'],)).fetchone()[0]=='CLOSED'
    for read in (lambda:b.store.open_positions(),lambda:b._cash(LATER)):
        with pytest.raises(ValueError):
            read()
    assert all(r['state']=='INVALID_LEDGER' for r in b.daily_risk.evaluate(LATER).values())


def test_lector_continua_con_posiciones_validas_y_cuenta_inconsistencias(positions,monkeypatch):
    import bf_production_paper_observer as observer
    b,good,bad=positions
    observer._support_schema(b.store)
    record=observer.financial_catalog.normalize_record(
        {'ticker':'GGAL','type':'ACCIONES','market':'BYMA','currency':'Pesos'},'A-24HS',AT,'fixture')
    with b.store.connect() as c:
        observer.financial_catalog.persist(c,record)
    corrupt(b,bad)
    monkeypatch.setattr(observer,'now_iso',lambda:LATER)
    calls=[]
    class Reader:
        def book(self,symbol,kind,settlement):
            calls.append((symbol,kind,settlement))
            return {'date':LATER,'bids':[{'price':90,'quantity':1000}],
                    'offers':[{'price':91,'quantity':1000}]}
    assert collect_exit_books(Reader(),b.store,PaperSessionPolicy(),LATER)==1
    assert calls==[('GGAL','ACCIONES','A-24HS')]
    assert b.store.latest_quote(good).book_at==LATER
    results={v.paper_id:v for v in PositionExitSupervisor(b,clock_fn=lambda:LATER).tick()}
    assert results[good['paper_id']].state=='CLOSED'


def test_snapshot_es_solo_lectura_y_no_sustituye_lector_financiero(positions):
    b,good,bad=positions
    corrupt(b,bad)
    with b.store.connect() as c:
        before=list(c.iterdump())
    opened,invalid=b.store.exit_positions()
    assert [p['paper_id'] for p in opened]==[good['paper_id']]
    assert invalid[0][0]['paper_id']==bad['paper_id']
    with b.store.connect() as c:
        assert list(c.iterdump())==before
    with pytest.raises(ValueError):
        b.store.open_positions()


def test_estado_desconocido_no_desaparece_como_si_fuera_un_cierre(positions):
    b,good,bad=positions
    with b.store.connect() as c:
        c.execute("UPDATE paper_positions SET status='UNKNOWN' WHERE paper_id=?",(bad['paper_id'],))
    results={v.paper_id:v for v in PositionExitSupervisor(b,clock_fn=lambda:LATER).tick(
        {key(good):quote(price='90',at=LATER)})}
    assert results[bad['paper_id']].state=='WATCH_INVALID_LEDGER'
    assert results[bad['paper_id']].cause=='TEST_PARTIAL'
    with b.store.connect() as c:
        assert c.execute('SELECT status FROM paper_positions WHERE paper_id=?',(bad['paper_id'],)).fetchone()[0]=='UNKNOWN'
        assert c.execute('SELECT status FROM paper_positions WHERE paper_id=?',(good['paper_id'],)).fetchone()[0]=='CLOSED'
        assert c.execute('SELECT state FROM paper_exit_intents WHERE paper_id=?',(bad['paper_id'],)).fetchone()[0]=='WATCH_INVALID_LEDGER'
        assert c.execute('SELECT state FROM paper_supervisor_state').fetchone()[0]=='DEGRADED'
    with pytest.raises(ValueError):
        b._cash(LATER)
    assert all(r['state']=='INVALID_LEDGER' for r in b.daily_risk.evaluate(LATER).values())


def test_error_de_base_no_se_convierte_en_cartera_vacia(positions,monkeypatch):
    import cd_spot_ledger
    b,_,_=positions
    def unavailable(*args):
        raise sqlite3.OperationalError('fixture database unavailable')
    monkeypatch.setattr(cd_spot_ledger,'partition',unavailable)
    with pytest.raises(sqlite3.OperationalError):
        b.store.exit_positions()


def test_reinicio_conserva_causa_y_no_duplica_evento_del_registro_roto(positions):
    b,good,bad=positions
    corrupt(b,bad)
    for _ in range(2):
        restarted=PaperBroker(PaperStore(b.store.path),initial_cash='10000',daily_loss_pct='1')
        PositionExitSupervisor(restarted,clock_fn=lambda:LATER).tick({})
    with b.store.connect() as c:
        intent=c.execute('SELECT * FROM paper_exit_intents WHERE paper_id=?',(bad['paper_id'],)).fetchone()
        assert intent['cause']=='TEST_PARTIAL' and intent['state']=='WATCH_INVALID_LEDGER'
        assert intent['due_at']=='2026-08-28T11:01:00-03:00' and intent['attempts']==1
        assert c.execute("SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_EXIT_STATE' AND paper_id=? AND detail LIKE 'WATCH_INVALID_LEDGER%'",(bad['paper_id'],)).fetchone()[0]==1


def test_reparacion_explicita_vuelve_a_supervisar_sin_olvidar_salida(positions):
    b,good,bad=positions
    corrupt(b,bad)
    sup=PositionExitSupervisor(b,clock_fn=lambda:LATER)
    sup.tick({})
    with b.store.connect() as c:
        c.execute('UPDATE paper_positions SET opened_at=? WHERE paper_id=?',(AT,bad['paper_id']))
    results={v.paper_id:v for v in sup.tick({key(bad):quote(symbol='ALUA',at=LATER),key(good):quote(at=LATER)})}
    assert results[bad['paper_id']].state=='CLOSED' and results[bad['paper_id']].cause=='TEST_PARTIAL'
    with b.store.connect() as c:
        assert c.execute('SELECT state FROM paper_supervisor_state').fetchone()[0]=='RUNNING'
