from datetime import date

import ak_byma_calendar as calendario


def test_domingo_queda_bloqueado():
    assert calendario.motivo_no_operativo(date(2026, 8, 23)) == "Fin de semana"
    assert calendario.es_dia_habil_operativo(date(2026, 8, 23)) is False


def test_feriado_oficial_queda_bloqueado():
    motivo = calendario.motivo_no_operativo(date(2026, 12, 8))
    assert motivo == "Inmaculada Concepción de María"
    assert calendario.es_dia_habil_operativo(date(2026, 12, 8)) is False


def test_jornada_especial_sin_liquidacion_queda_bloqueada():
    motivo = calendario.motivo_no_operativo(date(2026, 11, 6))
    assert "sin liquidación" in motivo
    assert calendario.es_dia_habil_operativo(date(2026, 11, 6)) is False


def test_dia_habil_auditado_permanece_abierto():
    assert calendario.motivo_no_operativo(date(2026, 8, 24)) is None
    assert calendario.es_dia_habil_operativo(date(2026, 8, 24)) is True


def test_anio_no_auditado_falla_cerrado():
    motivo = calendario.motivo_no_operativo(date(2027, 1, 4))
    assert motivo == "Calendario BYMA del año 2027 no auditado"
    assert calendario.es_dia_habil_operativo(date(2027, 1, 4)) is False
