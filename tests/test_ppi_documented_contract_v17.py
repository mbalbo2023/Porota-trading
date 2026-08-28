"""Contratos oficiales de API contrastados sin login, cuentas ni órdenes."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import c_ppi_client as client
from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyTransportGuard, ReadOnlyPolicyViolation


@pytest.fixture
def wrapper(monkeypatch):
    calls=[]
    def capture(kind,request):
        calls.append((kind,asdict(request)))
        return {'status':'TEST_RESPONSE_NOT_PPI'}
    w=client.ResilientPPIClient.__new__(client.ResilientPPIClient)
    w.client=SimpleNamespace(orders=SimpleNamespace(
        budget=lambda req:capture('budget',req),confirm=lambda req:capture('confirm',req)))
    w.is_sandbox=True
    w._call_with_retry=lambda fn,label:fn()
    monkeypatch.setitem(sys.modules,'ao_startup_gate',SimpleNamespace(interceptar_orden=lambda *args:None))
    return w,calls


@pytest.mark.parametrize('kind',['CAUCIONES','FCI','FCI-EXTERIOR','OPCIONES','FUTUROS','ACCIONES-USA','DESCONOCIDO'])
@pytest.mark.parametrize('quantity_type',[None,'DINERO'])
def test_familias_especializadas_no_pasan_como_spot(wrapper,monkeypatch,kind,quantity_type):
    w,calls=wrapper
    def unexpected(*args):
        raise AssertionError('No registrar una simulación spot para esa familia')
    monkeypatch.setitem(sys.modules,'ao_startup_gate',SimpleNamespace(interceptar_orden=unexpected))
    for call in (lambda:w.budget_order('TEST',1,1,'TEST',kind,quantity_type=quantity_type),
                 lambda:w.confirm_order('TEST',1,1,'TEST',[],kind,quantity_type=quantity_type),
                 lambda:w.confirm_order('TEST',1,1,'TEST',[],kind,sandbox_probe=True,
                                         external_id='api-verifier-sandbox-test',quantity_type=quantity_type)):
        with pytest.raises(NotImplementedError,match='SPECIALIZED_ORDER_ROUTE'):
            call()
    assert calls==[]


@pytest.mark.parametrize('kind',['ACCIONES','CEDEARS','ETF','BONOS','ON','LETRAS','LEBAC','NOBAC'])
def test_contado_conserva_identidad_y_vigencia_diaria_en_ambos_pedidos(wrapper,kind):
    w,calls=wrapper
    w.budget_order('TEST',10,100,'TEST',kind,operation='VENTA',settlement='INMEDIATA')
    w.confirm_order('TEST',10,100,'TEST',[],kind,operation='VENTA',settlement='INMEDIATA',external_id='TEST_ONLY')
    for name,p in calls:
        assert p['instrumentType']==kind and p['quantityType']=='PAPELES'
        assert p['operation']=='VENTA' and p['settlement']=='INMEDIATA'
        assert p['operationTerm']=='POR-EL-DIA' and p['operationMaxDate'] is None
    assert {k:v for k,v in calls[1][1].items() if k not in {'externalId','disclaimers'}}==calls[0][1]


def test_adaptador_no_devuelve_diccionario_global_mutable():
    params=client.order_param_adapter('acciones')
    params['quantity_type']='MONTO'
    assert client.order_param_adapter('ACCIONES')['quantity_type']=='PAPELES'


@pytest.mark.parametrize('changes',[
    {'quantity_type':'MONTO'}, {'quantity_type':''}, {'quantity_type':False},
    {'settlement':'CI'}, {'operation':'COLOCAR-CAUCIÓN'},
    {'operation':'SUSCRIPCIÓN-FCI'}, {'order_type':'UNKNOWN'}, {'term':'UNKNOWN'},
    {'term':'VÁLIDA-HASTA-EL'},
    {'term':'VÁLIDA-HASTA-EL','operation_max_date':'2020-01-01T12:00:00Z'},
    {'term':'VÁLIDA-HASTA-EL','operation_max_date':'2030-01-01T12:00:00'},
    {'operation_max_date':'2030-01-01T12:00:00Z'},
])
def test_terminos_inconsistentes_rechazan_antes_del_sdk(wrapper,changes):
    w,calls=wrapper
    with pytest.raises(ValueError):
        w.budget_order('TEST',1,100,'TEST',**changes)
    with pytest.raises(ValueError):
        w.confirm_order('TEST',1,100,'TEST',[],**changes)
    assert calls==[]


def test_vigencia_explicita_pasa_fecha_real_al_sdk(wrapper):
    w,calls=wrapper
    expiry=datetime.now(timezone.utc)+timedelta(days=1)
    params=dict(term='VÁLIDA-HASTA-EL',operation_max_date=expiry.isoformat(),quantity_type='DINERO')
    w.budget_order('TEST',1000,100,'TEST',**params)
    w.confirm_order('TEST',1000,100,'TEST',[],**params)
    assert all(p['operationMaxDate']==expiry and p['quantityType']=='DINERO' for _,p in calls)


def test_revalida_terminos_inmediatamente_antes_del_transporte(wrapper,monkeypatch):
    w,calls=wrapper
    original=client._order_terms
    counter=[]
    def validate(*args):
        counter.append(1)
        if len(counter)==2:
            raise ValueError('PPI_ORDER_EXPIRY_NOT_FUTURE')
        return original(*args)
    monkeypatch.setattr(client,'_order_terms',validate)
    with pytest.raises(ValueError,match='EXPIRY'):
        w.budget_order('TEST',1,100,'TEST')
    assert len(counter)==2 and calls==[]


def test_intercepcion_de_simulacion_contado_se_conserva(wrapper,monkeypatch):
    w,calls=wrapper
    monkeypatch.setitem(sys.modules,'ao_startup_gate',SimpleNamespace(interceptar_orden=lambda *args:{'id':'TEST_SIM'}))
    result=w.confirm_order('TEST',1,100,'TEST',[])
    assert result['simulada'] and calls==[]


def test_sandbox_probe_no_habilita_produccion(wrapper,monkeypatch):
    w,calls=wrapper
    monkeypatch.setenv('ENVIRONMENT','PRODUCTION')
    with pytest.raises(RuntimeError,match='sandbox_probe'):
        w.confirm_order('TEST',1,100,'TEST',[],sandbox_probe=True,external_id='api-verifier-sandbox-test')
    assert calls==[]


def config_reader(override=None):
    values={'instrument_types':['CAUCIONES','FCI','FUTUROS','ACCIONES'],
        'markets':['BYMA'],'settlements':['INMEDIATA'],
        'quantity_types':['DINERO','PAPELES','CANTIDAD-TOTAL'],
        'operation_terms':['POR-EL-DÍA'],'operation_types':['PRECIO-LIMITE'],
        'operations':['COMPRA','VENTA','COLOCAR-CAUCIÓN']}
    values.update(override or {})
    calls=[]
    def get(name):
        calls.append(name)
        return values[name]
    config=SimpleNamespace(**{'get_'+name:(lambda name=name:get(name)) for name in values})
    reader=ProductionMarketReader.__new__(ProductionMarketReader)
    reader._ProductionMarketReader__authenticated=True
    reader._ProductionMarketReader__client=SimpleNamespace(configuration=config,marketdata=object())
    return reader,values,calls


def test_enum_publicos_se_recogen_sin_cuenta_ni_login_nuevo():
    reader,values,calls=config_reader()
    assert reader.market_configuration()==values
    assert calls==list(values)


@pytest.mark.parametrize('value',[None,{'message':'error'},'COLOCAR-CAUCIÓN',[None],['']])
def test_configuracion_invalida_no_se_presenta_como_lista_verificada(value):
    reader,_,_=config_reader({'operations':value})
    with pytest.raises(ValueError,match='INVALID_SHAPE'):
        reader.market_configuration()


def test_configuracion_vacia_se_conserva_sin_inventar_soporte():
    reader,_,_=config_reader({'operations':[]})
    assert reader.market_configuration()['operations']==[]


def test_barrera_conserva_cuentas_y_ordenes_bloqueadas():
    guard=ReadOnlyTransportGuard()
    root='https://clientapi.portfoliopersonal.com/api/1.0/'
    for endpoint in ('QuantityTypes','Operations','OperationTerms','OperationTypes'):
        assert guard.check('GET',root+'Configuration/'+endpoint)
        with pytest.raises(ReadOnlyPolicyViolation):
            guard.check('POST',root+'Configuration/'+endpoint)
    for method,path in (('POST','Order/Budget'),('POST','Order/Confirm'),
                        ('GET','Account/AvailableBalance'),('GET','Account/Accounts')):
        with pytest.raises(ReadOnlyPolicyViolation):
            guard.check(method,root+path)


def test_falta_adaptador_no_se_informa_como_denegacion_del_broker(wrapper,tmp_path,monkeypatch):
    import sqlite3
    import ac_db
    import m_instrument_universe as universe
    w,calls=wrapper
    w.search_instruments=lambda kind:[{'ticker':'TEST'}]
    monkeypatch.setattr(universe,'ALL_TYPES',{'cauciones':{'instrument_type':'CAUCIONES'}})
    monkeypatch.setattr(ac_db,'connect_raw',lambda:sqlite3.connect(tmp_path/'permissions.db'))
    result=universe.detect_account_permissions(w)['cauciones']
    assert not result['puede_operar'] and result['estado_verificacion']=='LOCAL_ADAPTER_UNAVAILABLE'
    assert 'ni se verificó permiso' in result['motivo'] and calls==[]
