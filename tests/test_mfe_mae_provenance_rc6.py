import ast, inspect
from decimal import Decimal
import fc_mfe_mae_provenance_rc6 as m

ID=m.Identity('GGAL','ACCIONES','BYMA','A-24HS','ARS')
T=m.TradeWindow('P1',ID,'LONG',100,'2026-09-07T14:00:00+00:00','2026-09-07T15:00:00+00:00')

def obs(at,p,kind='BID',identity=ID,source='PPI_BOOK',hash='h'):
    return m.PriceObservation(identity,at,p,kind,source,at,hash)

def test_long_uses_executable_bid_and_normalizes_returns():
    r=m.measure(T,[obs('2026-09-07T14:10:00+00:00',98),obs('2026-09-07T14:20:00+00:00',105),obs('2026-09-07T14:30:00+00:00',102)])
    assert r['status']=='MEDIDO'
    assert r['mfe_exec_return']==Decimal('0.05')
    assert r['mae_exec_return']==Decimal('-0.02')
    assert r['mfe_price']==Decimal('105') and r['mae_price']==Decimal('98')
    assert r['observations_used']==3

def test_last_or_ask_is_not_silently_used_for_long_exit():
    r=m.measure(T,[obs('2026-09-07T14:10:00+00:00',110,'LAST'),obs('2026-09-07T14:11:00+00:00',109,'ASK')])
    assert r['status']=='NO_MEDIDO'
    assert r['reason']=='NO_EXECUTABLE_PROVENANCE'

def test_wrong_identity_or_outside_window_is_rejected():
    other=m.Identity('BMA','ACCIONES','BYMA','A-24HS','ARS')
    r=m.measure(T,[obs('2026-09-07T13:59:00+00:00',90),obs('2026-09-07T14:20:00+00:00',120,identity=other),obs('2026-09-07T14:21:00+00:00',101)])
    assert r['observations_used']==1 and r['observations_rejected']==2
    assert r['mfe_exec_return']==Decimal('0.01') and r['mae_exec_return']==Decimal('0.01')

def test_short_uses_ask_not_bid():
    t=m.TradeWindow('S1',ID,'SHORT',100,'2026-09-07T14:00:00+00:00','2026-09-07T15:00:00+00:00')
    r=m.measure(t,[obs('2026-09-07T14:10:00+00:00',95,'ASK'),obs('2026-09-07T14:20:00+00:00',103,'ASK'),obs('2026-09-07T14:30:00+00:00',80,'BID')])
    assert r['mfe_exec_return']==Decimal('0.05')
    assert r['mae_exec_return']==Decimal('-0.03')
    assert r['observations_rejected']==1

def test_provenance_is_preserved():
    r=m.measure(T,[obs('2026-09-07T14:10:00+00:00',101,source='PPI_BOOK',hash='abc')])
    assert r['source_set']==['PPI_BOOK']
    assert r['provenance'][0]['payload_hash']=='abc'
    assert r['provenance'][0]['kind']=='BID'

def test_module_is_pure_offline():
    m.assert_offline_only(); tree=ast.parse(inspect.getsource(m)); imports=[]
    for n in ast.walk(tree):
        if isinstance(n,ast.Import): imports.extend(a.name for a in n.names)
        elif isinstance(n,ast.ImportFrom) and n.module: imports.append(n.module)
    assert not any(x in {'sqlite3','requests','httpx'} or 'order' in x.lower() or 'ppi' in x.lower() for x in imports)
