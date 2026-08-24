"""
y_infra_monitor.py — Monitoreo de infraestructura (NUEVO EN v13.0)

Pedido explícito: "Agregar al dashboard lo que tenga que ver con reportes
de infraestructura como nueva sección, ya sea por ejemplo informe de
backups, consumo de espacio y otras sugerencias a nivel infraestructura".

BUG DE INFRAESTRUCTURA REAL ENCONTRADO Y CORREGIDO ACÁ (ver bitácora v13,
entrada correspondiente): el instalador systemd legacy (eliminado en v14.0, previo a
Docker) SÍ configuraba un cronjob diario de backup de trading_system.db
(/etc/cron.d/tradingbot-backup). docker-compose.yml (v12.0), que pasó a
ser el camino de despliegue recomendado desde esa versión (ver Documento
Maestro, sección L), NUNCA tuvo un mecanismo equivalente — un contenedor
Docker no corre cron por default, y nadie lo agregó al mover el proyecto
a Docker. Cualquiera que desplegara con Docker (el camino oficial) se
quedaba SIN backups automáticos, sin que nada lo avisara. Se corrige acá
con run_daily_backup() (invocada por j_main.py vía APScheduler, adentro
del propio proceso — no depende de que el contenedor tenga cron) y se
expone el estado real de esos backups en el dashboard, para que la falta
de backups sea visible en vez de silenciosa.

Este módulo es de solo lectura/generación de reportes — igual que
o_dashboard.py, nunca toca la operatoria de trading.
"""

import glob
import logging
import os
import bb_runtime_status
bb_runtime_status.configure_process_timezone()
import shutil
import sqlite3
import time
from datetime import datetime
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("infra_monitor")

DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")
BACKUP_DIR = os.getenv("BACKUP_DIR", "data/backups")
BACKUP_RETENTION_DAYS = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
# Umbral de alerta de disco — el mismo criterio que usaba el instalador legacy /
# z_disk_check.sh en el despliegue systemd legacy, para que ambos caminos
# de despliegue avisen bajo el mismo criterio.
INFRA_DISK_WARN_PCT = float(os.getenv("INFRA_DISK_WARN_PCT", "85"))
# Si el último backup tiene más de esta cantidad de horas, se marca en
# rojo en el dashboard — 26hs da un pequeño margen sobre el ciclo diario
# de 24hs antes de considerarlo "atrasado".
BACKUP_MAX_AGE_HOURS_WARN = float(os.getenv("BACKUP_MAX_AGE_HOURS_WARN", "26"))


def run_daily_backup():
    """
    NUEVO EN v13.0 — usa la API de backup online de SQLite (conn.backup()),
    que no requiere pausar el proceso ni bloquear escrituras — corre
    seguro con el bot operando al mismo tiempo (WAL ya está activado por
    j_main.init_db()). Guarda con timestamp y poda backups más viejos que
    BACKUP_RETENTION_DAYS, mismo criterio que el cronjob legacy de
    el instalador legacy (30 días por default).
    """
    if not os.path.exists(DB_PATH):
        logger.warning("run_daily_backup: no existe %s todavía — nada que respaldar.", DB_PATH)
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_path = os.path.join(BACKUP_DIR, f"trading_system_{timestamp}.db")
    try:
        source = ac_db.connect_raw()
        dest = sqlite3.connect(dest_path)
        with dest:
            source.backup(dest)
        source.close()
        dest.close()
        logger.info("Backup diario completado: %s", dest_path)
    except Exception as e:
        logger.error("Falló el backup diario de la base: %s", e)
        return None

    _prune_old_backups()
    return dest_path


def _prune_old_backups():
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
    for path in glob.glob(os.path.join(BACKUP_DIR, "trading_system_*.db")):
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                logger.info("Backup viejo eliminado por retención (%s días): %s", BACKUP_RETENTION_DAYS, path)
        except OSError as e:
            logger.warning("No se pudo evaluar/eliminar backup %s: %s", path, e)


def get_backup_status() -> dict:
    """Estado del backup más reciente, para el dashboard — 'ok' si hay uno
    dentro de BACKUP_MAX_AGE_HOURS_WARN, 'atrasado' si no, 'sin_backups'
    si nunca corrió ninguno (ej. despliegue nuevo o Docker sin este
    módulo conectado todavía)."""
    backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "trading_system_*.db")),
                      key=os.path.getmtime, reverse=True)
    if not backups:
        return {"status": "sin_backups", "last_backup_at": None, "age_hours": None,
                "count": 0, "total_size_mb": 0.0}
    last = backups[0]
    age_hours = round((time.time() - os.path.getmtime(last)) / 3600, 1)
    total_size_mb = round(sum(os.path.getsize(b) for b in backups) / (1024 * 1024), 2)
    return {
        "status": "ok" if age_hours <= BACKUP_MAX_AGE_HOURS_WARN else "atrasado",
        "last_backup_at": datetime.fromtimestamp(os.path.getmtime(last)).isoformat(),
        "age_hours": age_hours,
        "count": len(backups),
        "total_size_mb": total_size_mb,
    }


def get_disk_usage() -> dict:
    """Consumo de espacio del disco donde vive la base — shutil.disk_usage
    no requiere ninguna dependencia nueva."""
    path = os.path.dirname(os.path.abspath(DB_PATH)) or "."
    total, used, free = shutil.disk_usage(path)
    used_pct = round(used / total * 100, 1) if total else 0.0
    return {
        "path": path,
        "total_gb": round(total / (1024 ** 3), 2),
        "used_gb": round(used / (1024 ** 3), 2),
        "free_gb": round(free / (1024 ** 3), 2),
        "used_pct": used_pct,
        "warn": used_pct >= INFRA_DISK_WARN_PCT,
    }


def get_db_size_mb() -> float:
    try:
        return round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
    except OSError:
        return 0.0


def get_infra_suggestions() -> list:
    """
    Sugerencias en base a reglas simples — a diferencia del resto del
    proyecto, no depende de ningún motor de IA (no tiene sentido gastar
    una llamada a Gemini/Claude para leer un % de disco). Devuelve una
    lista de dicts {"severity": "red"|"yellow"|"green", "text": "..."} —
    mismo esquema de semáforo que pide la sección de roadmap del
    Documento Maestro v13.
    """
    suggestions = []
    backup = get_backup_status()
    disk = get_disk_usage()

    if backup["status"] == "sin_backups":
        suggestions.append({
            "severity": "red",
            "text": "No se detectó ningún backup de trading_system.db todavía. Si el despliegue es "
                    "Docker, confirmar que el volumen ./data esté montado y que el bot lleve al menos "
                    "un ciclo completo corrido (el backup diario corre a las 03:00).",
        })
    elif backup["status"] == "atrasado":
        suggestions.append({
            "severity": "red",
            "text": f"El último backup tiene {backup['age_hours']}hs — más de lo esperado. Revisar "
                    "logs del job run_daily_backup_job (posible falla de permisos en BACKUP_DIR o "
                    "disco lleno).",
        })
    else:
        suggestions.append({"severity": "green", "text": f"Backups al día (último hace {backup['age_hours']}hs)."})

    if disk["warn"]:
        suggestions.append({
            "severity": "red",
            "text": f"Uso de disco al {disk['used_pct']}% en {disk['path']} (umbral {INFRA_DISK_WARN_PCT}%). "
                    "Revisar tamaño de trading_system.db, rotación de logs y retención de backups "
                    "(BACKUP_RETENTION_DAYS).",
        })
    elif disk["used_pct"] >= INFRA_DISK_WARN_PCT - 15:
        suggestions.append({
            "severity": "yellow",
            "text": f"Uso de disco al {disk['used_pct']}% — todavía por debajo del umbral de alerta "
                    f"({INFRA_DISK_WARN_PCT}%), pero conviene monitorear la tendencia.",
        })
    else:
        suggestions.append({"severity": "green", "text": f"Uso de disco normal ({disk['used_pct']}%)."})

    db_size = get_db_size_mb()
    if db_size > 500:
        suggestions.append({
            "severity": "yellow",
            "text": f"trading_system.db pesa {db_size}MB. Si SIGNALS_RETENTION_DAYS quedó desactivado "
                    "o muy alto, revisar run_monthly_data_retention_job() en j_main.py.",
        })

    return suggestions
