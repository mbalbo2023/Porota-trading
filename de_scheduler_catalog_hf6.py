"""Scheduler catalog/model for HF6 v2 dashboard.

This module is read-only. It explains what each job does and derives due times
from persisted execution evidence. Host systemd state is consumed from a
sanitized JSON snapshot; the dashboard never receives systemctl or Docker
privileges.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os

TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
SCHEDULER_STATE_PATH = Path(os.getenv(
    "POROTA_SCHEDULER_STATE_PATH", "data/scheduler/systemd_timers.json"
))


@dataclass(frozen=True)
class InternalJob:
    key: str
    label: str
    description: str
    cadence_seconds: int | None
    condition: str = ""


INTERNAL_JOBS = (
    InternalJob("SRE_SNAPSHOT", "SRE snapshot",
                "Mide quick_check, espacio, WAL, memoria y latencia SQLite.", 300),
    InternalJob("DAILY_BACKUP", "Backup diario",
                "Genera backup online SQLite, lo comprime y valida restauración con quick_check.", 20*3600),
    InternalJob("FINANCIAL_REFRESH", "Información financiera",
                "Actualiza series públicas BCRA/INDEC usadas como contexto informativo.", 12*3600),
    InternalJob("NEWS_REFRESH", "Noticias",
                "Actualiza RSS sólo si la política de noticias está habilitada; HF6 la mantiene desactivada.", 45*60,
                "PAPER_NEWS_INGEST_ENABLED=true; si está OFF registra NO_APLICA con cadencia de control extendida."),
    InternalJob("REPORTS", "Reportes",
                "Genera reportes diarios y paquetes de lecciones IA a partir de evidencia PAPER persistida.", 6*3600,
                "Sólo con mercado CLOSED y luego del cierre configurado."),
    InternalJob("PPI_BACKGROUND_INGEST", "Históricos PPI",
                "Ingesta histórica/background PPI. En HF6 v2 se complementa con History Store v2 y fuentes batch.",
                int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200"))),
    InternalJob("PPI_PRODUCTION_HISTORY", "Históricos PPI producción",
                "Actualiza históricos PPI producción read-only; nunca es fuente de órdenes.",
                int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200"))),
    InternalJob("PPI_PRODUCTION_CATALOG", "Catálogo PPI",
                "Actualiza catálogo/instrumentos observables y metadatos de operatoria.",
                int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600"))),
    InternalJob("BYMA_OPEN_DATA", "BYMA datos públicos",
                "Actualiza evidencia pública BYMA usada para calendario/referencia.",
                int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600"))),
    InternalJob("PAPER_FOCUS_COVERAGE", "Cobertura del universo",
                "Recalcula cobertura y disponibilidad del universo PAPER sin habilitar familias por inferencia.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))),
    InternalJob("PAPER_SIGNAL_SAMPLING", "Muestreo de señales",
                "Registra cobertura del motor de señales sobre el universo elegible.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))),
    InternalJob("PAPER_SIGNAL_ROTATION", "Rotación de universo",
                "Rota la ventana evaluada para cubrir progresivamente el universo sin depender de un lote fijo.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))),
)

SYSTEMD_DESCRIPTIONS = {
    "porota-introspeccion-hf5.timer": "Genera snapshot horario de introspección funcional. El nombre HF5 es legado; el contenido se usa también en HF6.",
    "porota-introspection-publish.timer": "Publica a GitHub una copia sanitizada de observabilidad; GitHub nunca controla el runtime.",
    "porota-contract-evidence-hf6.timer": "Actualiza Contract Evidence HF6 read-only y conserva historial/versiones de cambios.",
    "porota-log-export-hf6.timer": "Exporta snapshots sanitizados y acotados de logs del observer/dashboard para la UI.",
    "porota-preopen.timer": "LEGACY: pre-open monolítico. HF6 v2 propone retirarlo y reemplazarlo por readiness continuo y sesiones por familia.",
    "porota-history-postclose-hf6.timer": "Ejecuta reconciliación histórica post-cierre cuando la sesión/calendario lo permiten.",
    "porota-a3-cem-history-hf6.timer": "Actualiza referencia/históricos públicos A3 CEM fuera del hot path.",
}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def next_due(last_run_at: str | None, cadence_seconds: int | None) -> str | None:
    if cadence_seconds is None:
        return None
    last=_parse(last_run_at)
    if last is None:
        return "DUE_NOW"
    return (last+timedelta(seconds=int(cadence_seconds))).isoformat()


def internal_rows(db_rows: list[dict]) -> list[dict]:
    persisted={str(r.get("job_key") or ""):r for r in (db_rows or [])}
    result=[]
    known={j.key for j in INTERNAL_JOBS}
    for job in INTERNAL_JOBS:
        row=persisted.get(job.key,{})
        result.append({
            "key":job.key,
            "label":job.label,
            "description":job.description,
            "condition":job.condition,
            "cadence_seconds":job.cadence_seconds,
            "last_run_at":row.get("last_run_at"),
            "last_success_at":row.get("last_success_at"),
            "state":row.get("state") or "SIN_REGISTRO",
            "detail":row.get("detail") or "Todavía no existe ejecución persistida.",
            "next_run_at":next_due(row.get("last_run_at"),job.cadence_seconds),
            "source":"INTERNAL",
        })
    for key,row in sorted(persisted.items()):
        if key in known:
            continue
        result.append({
            "key":key,"label":key,"description":"Job interno descubierto en operational_jobs.",
            "condition":"","cadence_seconds":None,"last_run_at":row.get("last_run_at"),
            "last_success_at":row.get("last_success_at"),"state":row.get("state") or "UNKNOWN",
            "detail":row.get("detail") or "","next_run_at":None,"source":"INTERNAL_DISCOVERED",
        })
    return result


def load_systemd_snapshot(path: Path | None = None) -> dict:
    target=(path or SCHEDULER_STATE_PATH)
    try:
        payload=json.loads(target.read_text(encoding="utf-8"))
        return payload if isinstance(payload,dict) else {"state":"INVALID","timers":[]}
    except FileNotFoundError:
        return {"state":"MISSING","timers":[]}
    except (OSError,ValueError,TypeError):
        return {"state":"INVALID","timers":[]}


def describe_systemd_timer(unit: str) -> str:
    return SYSTEMD_DESCRIPTIONS.get(str(unit), "Timer systemd Porota descubierto en el host.")


def assert_scheduler_invariants() -> None:
    keys=[j.key for j in INTERNAL_JOBS]
    if len(keys)!=len(set(keys)):
        raise AssertionError("duplicate internal scheduler job")
    if "porota-preopen.timer" not in SYSTEMD_DESCRIPTIONS:
        raise AssertionError("legacy preopen timer must remain visible until retirement is verified")
