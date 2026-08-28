"""La traza de intercepción conserva unidades, no inventa valuación ni fills."""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ao_startup_gate as gate
import c_ppi_client as client


@pytest.mark.parametrize('mode', [gate.MODO_SIMULACION, gate.MODO_DETENIDO])
@pytest.mark.parametrize('quantity_type', ['PAPELES', 'DINERO', 'CANTIDAD-TOTAL'])
@pytest.mark.parametrize('ticker,kind', [('GGAL', 'ACCIONES'), ('AL30D', 'BONOS')])
def test_traza_no_inventa_pesos_ni_convierte_unidades(monkeypatch,mode,quantity_type,ticker,kind):
    trace=[]
    monkeypatch.setattr(gate,'estado_actual',lambda:{'modo':mode})
    monkeypatch.setattr(gate,'registrar_paso',lambda *args:trace.append(args))
    order=gate.interceptar_orden(ticker,100,24,'VENTA',kind,quantity_type,'INMEDIATA')
    assert order['cantidad']==100 and order['precio']==24
    assert order['quantity_type']==quantity_type and order['settlement']=='INMEDIATA'
    assert order['monto_ars'] is None and order['valuacion']=='NO_CALCULADA_SIN_CONTRATO'
    assert order['bloqueada_por']==mode and order['simulada']
    assert len(trace)==1 and trace[0][-1]==order
    assert '$' not in trace[0][2] and 'sin valuación' in trace[0][2]


@pytest.mark.parametrize('quantity_type', ['PAPELES','DINERO','CANTIDAD-TOTAL'])
def test_confirmacion_interceptada_conserva_terminos_canonicos(monkeypatch,quantity_type):
    monkeypatch.setitem(sys.modules,'ao_startup_gate',gate)
    monkeypatch.setattr(gate,'estado_actual',lambda:{'modo':gate.MODO_SIMULACION})
    trace=[]
    monkeypatch.setattr(gate,'registrar_paso',lambda *args:trace.append(args))
    wrapper=client.ResilientPPIClient.__new__(client.ResilientPPIClient)
    wrapper.client=SimpleNamespace()  # cualquier acceso al SDK falla
    result=wrapper.confirm_order('TEST',100,24,'AL30D',[],instrument_type='bonos',
        quantity_type=quantity_type.lower(),operation='venta',settlement='inmediata')
    assert result['simulada'] and result['quantity']==100
    assert result['quantityType']==quantity_type and result['instrumentType']=='BONOS'
    assert result['settlement']=='INMEDIATA' and result['operation']=='VENTA'
    assert trace[0][-1]['quantity_type']==quantity_type
    assert trace[0][-1]['monto_ars'] is None


def test_invocacion_antigua_no_inventa_valuacion(monkeypatch):
    monkeypatch.setattr(gate,'estado_actual',lambda:{'modo':gate.MODO_DETENIDO})
    monkeypatch.setattr(gate,'registrar_paso',lambda *args:None)
    result=gate.interceptar_orden('TEST',10,25,'COMPRA','ACCIONES')
    assert result['monto_ars'] is None and result['quantity_type']=='PAPELES'
    assert result['settlement'] is None


def test_modo_real_conserva_intercepcion_sin_efecto(monkeypatch):
    monkeypatch.setattr(gate,'estado_actual',lambda:{'modo':gate.MODO_REAL})
    def unexpected(*args):
        raise AssertionError('No escribir una simulación en modo REAL')
    monkeypatch.setattr(gate,'registrar_paso',unexpected)
    assert gate.interceptar_orden('TEST',10,25,'COMPRA','ACCIONES','DINERO','INMEDIATA') is None
