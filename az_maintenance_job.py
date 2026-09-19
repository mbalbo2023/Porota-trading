"""Ejecutor de una sola tarea de mantenimiento.

Cada invocación carga únicamente lo necesario, ejecuta la tarea y termina.
Así el supervisor conserva pocos recursos cuando el motor bursátil hiberna.
"""

import logging
import sys

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] maintenance: %(message)s",
)
logger = logging.getLogger("maintenance")

VALID_JOBS = {
    "backup",
    "macro_refresh",
    "historical_refresh",
    "historical_refresh_if_needed",
    "weekly_report",
    "monthly_autotune",
    "data_retention",
    "news_scan",
    "gdelt_shadow_refresh",
    "learning_diagnostic",
    "model_guardian",
    "monthly_report",
    "validation_projection",
    "preopen_freshness_audit",
    "action4_audit",
}


def _notifier():
    from b_notifiers import MultiChannelNotifier
    return MultiChannelNotifier()


def run(job_name: str) -> None:
    if job_name == "backup":
        import y_infra_monitor
        y_infra_monitor.run_daily_backup()
    elif job_name == "macro_refresh":
        import ad_macro_history
        ad_macro_history.refresh()
    elif job_name == "historical_refresh":
        import ba_data912_history
        ba_data912_history.refresh()
    elif job_name == "historical_refresh_if_needed":
        import al_historical_ingest
        state = al_historical_ingest.estado_del_archivo()
        if (not state.get("instrumentos_archivados")
                or state.get("ruedas_atrasadas") is None
                or state.get("ruedas_atrasadas", 0) > 0):
            import ba_data912_history
            ba_data912_history.refresh()
        else:
            logger.info("Archivo histórico al día; catch-up omitido.")
    elif job_name == "weekly_report":
        from i_auto_tuner import AutoTuner
        _notifier().send_telegram(AutoTuner().generate_weekly_report())
    elif job_name == "monthly_autotune":
        from i_auto_tuner import AutoTuner
        AutoTuner().run_monthly_autotune()
    elif job_name == "data_retention":
        # La lógica ya auditada permanece como fuente única en j_main. La
        # importación pesada ocurre una vez al mes y el proceso termina luego.
        import j_main
        j_main.run_monthly_data_retention_job()
    elif job_name == "news_scan":
        import r_news_engine_247
        r_news_engine_247.run_continuous_scan()
    elif job_name == "gdelt_shadow_refresh":
        # Única ingesta GDELT RC6: evidencia estructurada, acotada y SHADOW.
        # No existe refresh genérico en paralelo; el dashboard sólo lee este store.
        import rc6_gdelt_event_risk_job
        payload = rc6_gdelt_event_risk_job.run_once(maxrecords=10)
        logger.info("GDELT Event Risk estructurado: %s (%s/%s tipos).",
                    payload["state"], payload["successful_event_types"],
                    payload["requested_event_types"])
    elif job_name == "learning_diagnostic":
        from s_learning_engine import LearningEngine
        LearningEngine(_notifier()).analyze_and_diagnose()
    elif job_name == "model_guardian":
        import t_model_guardian
        t_model_guardian.check_model_status(_notifier())
    elif job_name == "monthly_report":
        import z_reports_engine
        _notifier().send_telegram(z_reports_engine.build_monthly_summary_text())
    elif job_name == "preopen_freshness_audit":
        import rc6_preopen_freshness_audit
        payload = rc6_preopen_freshness_audit.refresh()
        logger.info("Freshness pre-rueda publicado: %s (%s/%s).", payload["state"], payload["fresh_total"], payload["target_total"])
    elif job_name == "action4_audit":
        import rc6_action4_audit
        payload = rc6_action4_audit.publish()
        logger.info("Auditoría Action 4 publicada: %s.", payload["dashboard_daily_report"]["periodo"])
    elif job_name == "validation_projection":
        import rc6_validation_projection
        snapshot = rc6_validation_projection.refresh(full_verify=False, daily_audit=True)
        logger.info("Proyección RC6 actualizada: %s/%s hitos GREEN.",
                    snapshot["summary"]["milestones_green"],
                    snapshot["summary"]["milestones_total"])
    else:
        raise ValueError(f"Tarea de mantenimiento desconocida: {job_name}")


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in VALID_JOBS:
        logger.error("Uso: python az_maintenance_job.py <%s>", "|".join(sorted(VALID_JOBS)))
        return 2

    job_name = args[0]
    try:
        logger.info("Iniciando tarea %s.", job_name)
        run(job_name)
        logger.info("Tarea %s completada.", job_name)
        return 0
    except Exception:
        logger.exception("Tarea %s falló.", job_name)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
