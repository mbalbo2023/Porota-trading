"""El mantenimiento debe sobrevivir a la hibernación del motor bursátil."""

from pathlib import Path

import az_maintenance_job
import az_maintenance_scheduler


RAIZ = Path(__file__).resolve().parents[1]


def test_scheduler_siempre_activo_contiene_todas_las_tareas():
    scheduler = az_maintenance_scheduler.build_scheduler()
    ids = {job.id for job in scheduler.get_jobs()}

    assert ids == {
        "maintenance_monthly_autotune",
        "maintenance_data_retention",
        "maintenance_macro_refresh",
        "maintenance_historical_refresh",
        "maintenance_daily_backup",
        "maintenance_model_guardian",
        "maintenance_monthly_report",
        "maintenance_learning_diagnostic",
        "maintenance_weekly_report",
        "maintenance_news_scan",
    }


def test_backup_ocurre_despues_del_refresco_macro():
    scheduler = az_maintenance_scheduler.build_scheduler()
    jobs = {job.id: str(job.trigger) for job in scheduler.get_jobs()}

    assert "hour='2', minute='30'" in jobs["maintenance_macro_refresh"]
    assert "hour='3', minute='0'" in jobs["maintenance_daily_backup"]


def test_entrypoint_posee_y_cierra_el_scheduler():
    fuente = (RAIZ / "entrypoint.py").read_text(encoding="utf-8")

    assert "az_maintenance_scheduler.start()" in fuente
    assert "maintenance_scheduler.shutdown(wait=False)" in fuente


def test_motor_no_duplica_mantenimientos():
    fuente = (RAIZ / "j_main.py").read_text(encoding="utf-8")

    prohibidos = (
        'scheduler.add_job(run_daily_backup_job',
        'scheduler.add_job(macro_history.refresh',
        'scheduler.add_job(news_247.run_continuous_scan',
        'scheduler.add_job(run_monthly_autotune_job',
        'scheduler.add_job(run_monthly_data_retention_job',
        'scheduler.add_job(send_weekly_report_job',
        'scheduler.add_job(send_monthly_report_job',
    )
    assert not any(texto in fuente for texto in prohibidos)


def test_ejecutor_rechaza_tareas_desconocidas():
    assert az_maintenance_job.main(["no_existe"]) == 2
