from datetime import datetime
from zoneinfo import ZoneInfo

import al_market_startup as arranque


ZONA = ZoneInfo("America/Argentina/Buenos_Aires")


def test_domingo_no_inicializa_el_motor():
    listo, motivo = arranque.evaluar_ventana(
        datetime(2026, 8, 23, 12, 0, tzinfo=ZONA))
    assert listo is False
    assert motivo == "Fin de semana"


def test_feriado_byma_no_inicializa_el_motor():
    listo, motivo = arranque.evaluar_ventana(
        datetime(2026, 12, 8, 12, 0, tzinfo=ZONA))
    assert listo is False
    assert "Inmaculada" in motivo


def test_arranca_quince_minutos_antes_de_la_apertura():
    antes, _ = arranque.evaluar_ventana(
        datetime(2026, 8, 24, 10, 44, tzinfo=ZONA))
    listo, motivo = arranque.evaluar_ventana(
        datetime(2026, 8, 24, 10, 45, tzinfo=ZONA))
    assert antes is False
    assert listo is True
    assert "calendario BYMA" in motivo


def test_no_inicializa_despues_del_cierre():
    listo, motivo = arranque.evaluar_ventana(
        datetime(2026, 8, 24, 17, 0, tzinfo=ZONA))
    assert listo is False
    assert motivo == "Rueda finalizada"


def test_motor_permanece_hasta_completar_el_resumen_de_cierre():
    activo, _ = arranque.motor_debe_estar_activo(
        datetime(2026, 8, 24, 17, 9, tzinfo=ZONA))
    hibernado, motivo = arranque.motor_debe_estar_activo(
        datetime(2026, 8, 24, 17, 10, tzinfo=ZONA))
    assert activo is True
    assert hibernado is False
    assert "hibernado" in motivo


def test_fin_de_semana_motor_hibernado_todo_el_dia():
    activo, motivo = arranque.motor_debe_estar_activo(
        datetime(2026, 8, 23, 11, 30, tzinfo=ZONA))
    assert activo is False
    assert motivo == "Fin de semana"


def test_sandbox_jamas_selecciona_modo_real(monkeypatch):
    monkeypatch.setattr(arranque, "AUTO_START_MODE", "REAL")
    monkeypatch.setattr(arranque, "ENVIRONMENT", "SANDBOX")
    assert arranque.modo_automatico() == "SIMULACION"


def test_produccion_real_requiere_dos_configuraciones_explicitas(monkeypatch):
    monkeypatch.setattr(arranque, "AUTO_START_MODE", "REAL")
    monkeypatch.setattr(arranque, "ENVIRONMENT", "PRODUCTION")
    assert arranque.modo_automatico() == "REAL"
