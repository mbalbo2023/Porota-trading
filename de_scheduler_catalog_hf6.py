"""Scheduler catalog/model for RC4 dashboard.

Read-only model. Jobs persist evidence in different stores; this module
normalizes that evidence without pretending that absence from operational_jobs
means a job did not run. Host systemd state is consumed separately from a
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


def _news_cadence_seconds() -> int:
    """Return the effective control cadence, not the nominal RSS cadence."""
    enabled = str(os.getenv("PAPER_NEWS_INGEST_ENABLED", "false")).strip().lower()
    return 45 * 60 if enabled in {"1", "true", "yes", "si", "sí"} else 12 * 3600


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
                "Actualiza RSS sólo si la política de noticias está habilitada.",
                _news_cadence_seconds(),
                "ON: RSS cada 45 min. OFF: control de estado cada 12 h y resultado NO_APLICA."),
    InternalJob("REPORTS", "Reportes",
                "Genera reportes operativos PAPER desde evidencia persistida.", 6*3600,
                "Sólo con mercado CLOSED y luego del cierre configurado."),
    InternalJob("CAUCION_CASH_SWEEP", "Barrido de caja a caución",
                "Evalúa caja ARS liquidada al final de rueda; permanece HOLD sin contrato/cutoff/costos verificados.", 300,
                "Sólo dentro de ventana final verificada; falta de evidencia contractual => HOLD."),
    InternalJob("PPI_BACKGROUND_INGEST", "Históricos PPI",
                "Ingesta histórica/background PPI; nunca fuente de órdenes.",
                int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200"))),
    InternalJob("PPI_PRODUCTION_HISTORY", "Históricos PPI producción",
                "Actualiza históricos PPI producción read-only.",
                int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200"))),
    InternalJob("PPI_PRODUCTION_CATALOG", "Catálogo PPI",
                "Actualiza catálogo/instrumentos observables y metadatos de operatoria.",
                int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600"))),
    InternalJob("BYMA_OPEN_DATA", "BYMA datos públicos",
                "Actualiza evidencia pública BYMA usada para calendario/referencia.",
                int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600"))),
    InternalJob("PAPER_FOCUS_COVERAGE", "Cobertura del universo",
                "Recalcula cobertura y disponibilidad sin habilitar familias por inferencia.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))),
    InternalJob("PAPER_SIGNAL_SAMPLING", "Muestreo de señales",
                "Registra cobertura del motor de señales sobre el universo elegible.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))),
    InternalJob("PAPER_SIGNAL_ROTATION", "Rotación de universo",
                "Rota la ventana evaluada para cubrir progresivamente el universo.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300"))),
)

# Canonical evidence source per catalog job. operational_jobs has priority when
# a row exists because it is the job's own ledger. These mappings complete jobs
# whose implementation persists elsewhere.
EVIDENCE_TABLE = {
    "PPI_BACKGROUND_INGEST": "api_health",
    "PPI_PRODUCTION_HISTORY": "source_sync",
    "PPI_PRODUCTION_CATALOG": "source_sync",
    "BYMA_OPEN_DATA": "source_sync",
    "PAPER_FOCUS_COVERAGE": "api_health",
    "PAPER_SIGNAL_SAMPLING": "api_health",
    "PAPER_SIGNAL_ROTATION": "api_health",
}

SYSTEMD_DESCRIPTIONS = {
    "porota-introspeccion-hf5.timer": "Genera snapshot de introspección funcional; nombre HF5 legado.",
    "porota-introspection-publish.timer": "Publica copia sanitizada; GitHub nunca controla runtime.",
    "porota-contract-evidence-hf6.timer": "Actualiza Contract Evidence read-only y conserva historial de cambios.",
    "porota-log-export-hf6.timer": "Exporta snapshots sanitizados de logs para UI.",
    "porota-scheduler-export-hf6.timer": "Exporta estado systemd sanitizado para esta vista.",
    "porota-preopen.timer": "LEGACY retirado; no debe reactivarse sin decisión explícita.",
    "porota-history-postclose-hf6.timer": "Reconciliación histórica post-cierre; RC4 debe versionar/instalar sólo tras pruebas.",
    "porota-a3-cem-history-hf6.timer": "Referencia/históricos públicos A3 CEM fuera del hot path.",
    "porota-caucion-cash-sweep-hf6.timer": "Cash sweep PAPER; sin evidencia completa registra HOLD y no coloca.",
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


def _normalize_evidence(row: dict, *, key_field: str, run_field: str,
                        state_field: str, table: str) -> dict:
    return {
        "job_key": str(row.get(key_field) or ""),
        "last_run_at": row.get(run_field),
        "last_success_at": row.get("last_success_at"),
        "state": row.get(state_field) or "SIN_REGISTRO",
        "detail": str(row.get("detail") or ""),
        "evidence_source": table,
    }


def _prefer(persisted: dict[str, dict], candidate: dict) -> None:
    """Keep operational_jobs authoritative; otherwise prefer freshest evidence."""
    key=candidate.get("job_key") or ""
    if not key:
        return
    current=persisted.get(key)
    if current is None:
        persisted[key]=candidate
        return
    if current.get("evidence_source") == "operational_jobs":
        return
    old=_parse(current.get("last_run_at"))
    new=_parse(candidate.get("last_run_at"))
    if new is not None and (old is None or new > old):
        persisted[key]=candidate


def internal_rows(db_rows: list[dict], *, source_sync_rows=None,
                  api_health_rows=None) -> list[dict]:
    """Merge the three persisted evidence stores without duplicate jobs."""
    persisted={}
    for raw in db_rows or []:
        row=dict(raw)
        row["evidence_source"]="operational_jobs"
        _prefer(persisted,row)
    for raw in source_sync_rows or []:
        _prefer(persisted,_normalize_evidence(
            dict(raw), key_field="source", run_field="last_attempt_at",
            state_field="status", table="source_sync"))
    for raw in api_health_rows or []:
        _prefer(persisted,_normalize_evidence(
            dict(raw), key_field="component", run_field="checked_at",
            state_field="state", table="api_health"))

    result=[]
    known={j.key for j in INTERNAL_JOBS}
    for job in INTERNAL_JOBS:
        row=persisted.get(job.key,{})
        source=row.get("evidence_source") or EVIDENCE_TABLE.get(job.key) or "operational_jobs"
        result.append({
            "key":job.key,
            "label":job.label,
            "description":job.description,
            "condition":job.condition,
            "cadence_seconds":job.cadence_seconds,
            "last_run_at":row.get("last_run_at"),
            "last_success_at":row.get("last_success_at"),
            "state":row.get("state") or "SIN_REGISTRO",
            "detail":row.get("detail") or "Todavía no existe evidencia persistida en la fuente esperada.",
            "next_run_at":next_due(row.get("last_run_at"),job.cadence_seconds),
            "source":source,
        })
    for key,row in sorted(persisted.items()):
        if key in known:
            continue
        result.append({
            "key":key,"label":key,"description":"Job interno descubierto en evidencia persistida.",
            "condition":"","cadence_seconds":None,"last_run_at":row.get("last_run_at"),
            "last_success_at":row.get("last_success_at"),"state":row.get("state") or "UNKNOWN",
            "detail":row.get("detail") or "","next_run_at":None,
            "source":row.get("evidence_source") or "DISCOVERED",
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
    if "CAUCION_CASH_SWEEP" not in keys:
        raise AssertionError("caucion cash sweep must be visible in scheduler")
    if "porota-preopen.timer" not in SYSTEMD_DESCRIPTIONS:
        raise AssertionError("legacy preopen timer must remain visible for retirement evidence")
