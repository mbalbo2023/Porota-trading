import m_instrument_universe as universe
class Client:
    def __init__(self): self.calls=[]
    def search_instruments(self,t): self.calls.append(('search',t)); return [{'ticker':'TEST'}]
    def budget_order(self,*a,**k): raise AssertionError('NO_BUDGET')
    def confirm_order(self,*a,**k): raise AssertionError('NO_CONFIRM')
    def cancel_order(self,*a,**k): raise AssertionError('NO_CANCEL')
def test_generic_search_only(monkeypatch):
    monkeypatch.setattr(universe,'ALL_TYPES',{'acciones':{'instrument_type':'ACCIONES'}})
    c=Client(); r=universe.detect_account_permissions(c)['acciones']
    assert r['puede_operar'] is False
    assert r['estado_verificacion']=='EXECUTION_NOT_PROVEN_NO_ORDER_ROUTE_PROBE'
    assert r['instrumentos_visibles']==1
    assert c.calls==[('search','ACCIONES')]
def test_specialized_preserves_local_adapter_truth(monkeypatch):
    monkeypatch.setattr(universe,'ALL_TYPES',{'cauciones':{'instrument_type':'CAUCIONES'}})
    c=Client(); r=universe.detect_account_permissions(c)['cauciones']
    assert r['puede_operar'] is False
    assert r['estado_verificacion']=='LOCAL_ADAPTER_UNAVAILABLE'
    assert 'ni se verificó permiso' in r['motivo']
    assert c.calls==[('search','CAUCIONES')]
