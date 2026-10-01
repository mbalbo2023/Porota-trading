"""Scheduler liviano de mantenimiento para el supervisor siempre activo."""

import logging
import os
import subprocess
import sys

from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("maintenance_scheduler")
SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")
JOB_TIMEOUT_SECONDS = int(os.getenv("MAINTENANCE_JOB_TIMEOUT_SECONDS", "1800"))

# El histórico completo quedó aislado fuera del scheduler del observer. PPI Watch
# conserva su circuito propio; este bloqueo sólo impide escrituras históricas desde
# el mantenimiento interno del proceso siempre activo.
INTERNAL_HISTORICAL_WRITE_JOBS = frozenset({
    "historical_refresh",
    "historical_refresh_if_needed",
})


def _run_job(job_name: str) -> None:
    """Ejecuta y libera toda la memoria importada por una tarea pesada."""
    if job_name in INTERNAL_HISTORICAL_WRITE_JOBS:
        logger.warning(
            "Mantenimiento histórico %s bloqueado por política RC6.", job_name
        )
        return

    try:
        result = subprocess.run(
            [sys.executable, "az_maintenance_job.py", job_name],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            check=False,
            timeout=JOB_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error("Mantenimiento %s excedió %ss y fue terminado.",
                     job_name, JOB_TIMEOUT_SECONDS)
        return
    except Exception:
        logger.exception("No se pudo lanzar mantenimiento %s.", job_name)
        return

    if result.returncode:
        logger.error("Mantenimiento %s terminó con código %s.",
                     job_name, result.returncode)


def build_scheduler(*, only_jobs: set[str] | None = None) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        timezone=SERVER_TIMEZONE,
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 3600,
        },
    )

    def add(job_name, trigger, **kwargs):
        if only_jobs is None or job_name in only_jobs:
            scheduler.add_job(_run_job, trigger, args=[job_name], **kwargs)

    add("monthly_autotune", "cron", id="maintenance_monthly_autotune",
        day=1, hour=1)
    add("data_retention", "cron", id="maintenance_data_retention",
        day=1, hour=1, minute=30)
    add("macro_refresh", "cron", id="maintenance_macro_refresh",
        hour=2, minute=30)
    add("backup", "cron", id="maintenance_daily_backup",
        hour=3, minute=0)
    # Sin catch-up de arranque ni cron histórico: sólo PPI Watch puede gestionar
    # ese circuito tras su revisión individual.
    add("model_guardian", "cron", id="maintenance_model_guardian",
        day_of_week="mon", hour=9)
    add("monthly_report", "cron", id="maintenance_monthly_report",
        day=1, hour=9)
    add("learning_diagnostic", "cron", id="maintenance_learning_diagnostic",
        day_of_week="sun", hour=19)
    add("weekly_report", "cron", id="maintenance_weekly_report",
        day_of_week="sun", hour=20)
    add("news_scan", "interval", id="maintenance_news_scan",
        minutes=45)
    add("gdelt_shadow_refresh", "interval", id="maintenance_gdelt_shadow",
        minutes=30)

    add("action4_audit", "cron", id="maintenance_action4_audit",
        hour=7, minute=5)
    add("validation_projection", "cron", id="maintenance_validation_projection",
        hour=7, minute=15)
    # Evidencia pre-rueda: lectura solamente contra la última sesión esperada.
    add("preopen_freshness_audit", "cron", id="maintenance_preopen_freshness_audit",
        hour=8, minute=30)

    return scheduler


def start(*, only_jobs: set[str] | None = None) -> BackgroundScheduler:
    scheduler = build_scheduler(only_jobs=only_jobs)
    scheduler.start()
    logger.info("Scheduler de mantenimiento activo en %s (%s tareas).",
                SERVER_TIMEZONE, len(scheduler.get_jobs()))
    return scheduler
