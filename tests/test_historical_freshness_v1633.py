from datetime import datetime
from zoneinfo import ZoneInfo

import al_historical_ingest as hist
import az_maintenance_job as maintenance
import az_maintenance_scheduler as scheduler_module
import ba_data912_history


TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def _weekdays_except(*holidays):
    blocked = set(holidays)
    return lambda day: day.weekday() < 5 and day not in blocked


def test_viernes_a_lunes_antes_del_corte_no_atrasa(monkeypatch):
    monkeypatch.setattr(hist._byma_calendar, "es_dia_habil_operativo", _weekdays_except())
    now = datetime(2026, 8, 24, 17, 30, tzinfo=TZ)
    assert hist.calcular_ruedas_atrasadas("2026-08-21", now) == 0


def test_lunes_despues_del_corte_espera_la_rueda_de_hoy(monkeypatch):
    monkeypatch.setattr(hist._byma_calendar, "es_dia_habil_operativo", _weekdays_except())
    now = datetime(2026, 8, 24, 19, 31, tzinfo=TZ)
    assert hist.calcular_ruedas_atrasadas("2026-08-21", now) == 1


def test_feriado_no_cuenta_como_rueda(monkeypatch):
    holiday = datetime(2026, 8, 24, tzinfo=TZ).date()
    monkeypatch.setattr(hist._byma_calendar, "es_dia_habil_operativo", _weekdays_except(holiday))
    now = datetime(2026, 8, 24, 20, 0, tzinfo=TZ)
    assert hist.calcular_ruedas_atrasadas("2026-08-21", now) == 0


def test_jueves_a_lunes_antes_del_corte_debe_una_rueda(monkeypatch):
    monkeypatch.setattr(hist._byma_calendar, "es_dia_habil_operativo", _weekdays_except())
    now = datetime(2026, 8, 24, 17, 30, tzinfo=TZ)
    assert hist.calcular_ruedas_atrasadas("2026-08-20", now) == 1


def test_fecha_futura_o_invalida_falla_cerrada(monkeypatch):
    monkeypatch.setattr(hist._byma_calendar, "es_dia_habil_operativo", _weekdays_except())
    now = datetime(2026, 8, 24, 17, 30, tzinfo=TZ)
    assert hist.calcular_ruedas_atrasadas("2026-08-25", now) is None
    assert hist.calcular_ruedas_atrasadas("invalida", now) is None


def test_scheduler_no_reintroduce_escrituras_historicas_internas():
    scheduler = scheduler_module.build_scheduler()
    ids = {item.id for item in scheduler.get_jobs()}
    assert "maintenance_historical_startup_catchup" not in ids
    assert "maintenance_historical_catchup" not in ids
    assert "maintenance_historical_refresh" not in ids
    assert {"historical_refresh","historical_refresh_if_needed"}.issubset(
        scheduler_module.INTERNAL_HISTORICAL_WRITE_JOBS
    )


def test_catchup_no_descarga_si_esta_al_dia(monkeypatch):
    monkeypatch.setattr(hist, "estado_del_archivo", lambda: {
        "instrumentos_archivados": 10, "ruedas_atrasadas": 0})
    calls = {"refresh": 0}
    monkeypatch.setattr(ba_data912_history, "refresh",
                        lambda: calls.__setitem__("refresh", calls["refresh"] + 1))
    maintenance.run("historical_refresh_if_needed")
    assert calls["refresh"] == 0


def test_catchup_descarga_una_vez_si_falta_rueda(monkeypatch):
    monkeypatch.setattr(hist, "estado_del_archivo", lambda: {
        "instrumentos_archivados": 10, "ruedas_atrasadas": 1})
    calls = {"refresh": 0}
    monkeypatch.setattr(ba_data912_history, "refresh",
                        lambda: calls.__setitem__("refresh", calls["refresh"] + 1))
    maintenance.run("historical_refresh_if_needed")
    assert calls["refresh"] == 1
