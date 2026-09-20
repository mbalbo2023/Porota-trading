"""RC6 scheduler catalog consumed by the read-only dashboard.

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

from co_contract_ingestion_policy_hf6 import ttl_seconds as contract_ttl_seconds

TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
SCHEDULER_STATE_PATH = Path(os.getenv(
    "POROTA_SCHEDULER_STATE_PATH", "data/scheduler/systemd_timers.json"
))


def _news_cadence_seconds():
    enabled=str(os.getenv("PAPER_NEWS_INGEST_ENABLED","false")).strip().lower()
    return 45*60 if enabled in {"1","true","yes","si","sí"} else 12*3600

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
    InternalJob("HISTORY_BACKUP", "Backup History Store",
                "Backup online SQLite de market_history.db con restore quick_check.", 20*3600),
    InternalJob("HOST_GENERAL_BACKUP", "Backup general del host",
                "Backup data/ + sre_vector_db sin secretos; SQLite usa backup online.", 24*3600),
    InternalJob("FINANCIAL_REFRESH", "Información financiera",
                "Actualiza series públicas BCRA/INDEC usadas como contexto informativo.", 12*3600),
    InternalJob("NEWS_REFRESH", "Noticias",
                "Actualiza RSS sólo si la política de noticias está habilitada; con OFF sólo controla estado.", _news_cadence_seconds(),
                "45 min con ON; 12 h con OFF y NO_APLICA."),
    InternalJob("REPORTS", "Reportes",
                "Genera reportes operativos PAPER desde evidencia persistida. Artefactos IA heredados no participan del runtime HF6-v2.", 6*3600,
                "Sólo con mercado CLOSED y luego del cierre configurado."),
    InternalJob("CAUCION_CASH_SWEEP", "Barrido de caja a caución",
                "Evalúa caja ARS liquidada al final de rueda y propone/coloca sólo una caución PAPER con vencimiento antes del próximo deadline de liquidez.", 300,
                "Sólo dentro de la ventana final verificada. Requiere contrato/cutoff/costos/obligaciones VERIFIED; si falta evidencia queda HOLD."),
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
    InternalJob("CONTRACT_EVIDENCE_DYNAMIC", "Evidencia contractual dinámica", "XHR/web contractual read-only; no auto READY.", contract_ttl_seconds("OPERABILITY")),
    InternalJob("CONTRACT_EVIDENCE_CAUCIONES", "Contrato cauciones", "Cauciones read-only; fail-closed.", contract_ttl_seconds("CAUCION_LIVE_CONTRACT")),
    InternalJob("CONTRACT_EVIDENCE_AUCTIONS", "Licitaciones / subastas", "Estado de ventanas de licitación/subasta; fail-closed.", contract_ttl_seconds("AUCTION_STATUS")),
    InternalJob("CONTRACT_EVIDENCE_DERIVATIVES", "Contrato opciones/futuros", "Series/contratos derivados read-only.", contract_ttl_seconds("DERIVATIVE_SERIES")),
    InternalJob("CONTRACT_EVIDENCE_STATIC", "Contrato estático", "Hash/metadata contractual estática oficial.", contract_ttl_seconds("STATIC_CONTRACT")),
    InternalJob("CONTRACT_EVIDENCE_FULL_BROWSER", "Auditoría browser contractual", "Barrido autenticado read-only fuera del hot path.", contract_ttl_seconds("FULL_BROWSER_AUDIT")),
    InternalJob("FUNCTIONAL_HEALTH_SNAPSHOT", "Control funcional liviano", "Runtime/PPI/real_orders/marks/riesgo.", 5*60),
    InternalJob("FUNCTIONAL_DEEP_AUDIT", "Control funcional integral", "Auditoría funcional read-only.", 60*60),
)

# These workloads are deliberately not part of the current ACCIONES/CEDEARS
# production-PAPER scope. They may remain as historic evidence, but the dashboard
# must not present them as active internal work.
DISABLED_SCOPE_JOB_PREFIXES = (
    "CAUCION_",
    "CONTRACT_EVIDENCE_",
    "SCALP",
)


def _is_active_scope_job(key: str) -> bool:
    normalized=str(key or "").upper()
    return bool(normalized) and not normalized.startswith(DISABLED_SCOPE_JOB_PREFIXES)


def active_internal_jobs() -> tuple[InternalJob, ...]:
    return tuple(job for job in INTERNAL_JOBS if _is_active_scope_job(job.key))


SYSTEMD_DESCRIPTIONS = {
    "porota-introspection-rc6.timer": "Genera la introspección funcional RC6 cada hora, al minuto 15.",
    "porota-introspection-publish-rc6.timer": "Publica la copia sanitizada RC6 al minuto 20; GitHub nunca controla el runtime.",
    "porota-scheduler-export-rc6.timer": "Actualiza cada minuto el inventario systemd sanitizado del dashboard.",
    "porota-contract-evidence-hf6.timer": "Actualiza Contract Evidence HF6 read-only y conserva historial/versiones de cambios.",
    "porota-log-export-hf6.timer": "Exporta snapshots sanitizados y acotados de logs del observer/dashboard para la UI.",
    "porota-preopen.timer": "LEGACY: pre-open monolítico fuera del flujo operativo RC6.",
    "porota-history-postclose-hf6.timer": "Ejecuta reconciliación histórica post-cierre cuando la sesión/calendario lo permiten.",
    "porota-a3-cem-history-hf6.timer": "Actualiza referencia/históricos públicos A3 CEM fuera del hot path.",
    "porota-caucion-cash-sweep-hf6.timer": "Evalúa el cash sweep PAPER al final de rueda. Sin evidencia contractual/sesión/obligaciones completa debe registrar HOLD y no colocar.",
    "porota-contract-evidence-rc4.timer": "Evalúa cada 5 min qué subjobs contractuales están vencidos; browser/XHR read-only, sin login automático ni órdenes.",
    "porota-functional-health-rc4.timer": "Control funcional liviano cada 5 minutos.",
    "porota-functional-deep-audit-rc4.timer": "Introspección funcional profunda cada 60 minutos.",
    "porota-history-postclose-rc4.timer": "Evalúa elegibilidad post-cierre de History Store v2 sin horario global rígido.",
    "porota-a3-cem-history-rc4.timer": "Ingesta A3 CEM background/history; nunca autoridad live.",
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


def internal_rows(db_rows: list[dict], *, source_sync_rows=None, api_health_rows=None, contract_run_rows=None) -> list[dict]:
    persisted={str(r.get("job_key") or ""):dict(r,evidence_table="operational_jobs") for r in (db_rows or [])}
    for raw in source_sync_rows or []:
        key=str(raw.get("source") or "")
        if key and key not in persisted:persisted[key]={"job_key":key,"last_run_at":raw.get("last_attempt_at"),"last_success_at":raw.get("last_success_at"),"state":raw.get("status") or "SIN_REGISTRO","detail":raw.get("detail") or "","evidence_table":"source_sync"}
    for raw in api_health_rows or []:
        key=str(raw.get("component") or "")
        if key and key not in persisted:persisted[key]={"job_key":key,"last_run_at":raw.get("checked_at"),"last_success_at":raw.get("last_success_at"),"state":raw.get("state") or "SIN_REGISTRO","detail":raw.get("detail") or "","evidence_table":"api_health"}
    for raw in contract_run_rows or []:
        key=str(raw.get("job_key") or raw.get("run_type") or "")
        if key and key not in persisted:persisted[key]={"job_key":key,"last_run_at":raw.get("started_at") or raw.get("created_at"),"last_success_at":raw.get("finished_at"),"state":raw.get("state") or "SIN_REGISTRO","detail":raw.get("detail") or "","evidence_table":"contract_evidence_runs"}
    result=[]
    known={j.key for j in INTERNAL_JOBS}
    for job in active_internal_jobs():
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
            "evidence_table":row.get("evidence_table") or "SIN_EVIDENCIA",
        })
    for key,row in sorted(persisted.items()):
        if key in known or not _is_active_scope_job(key):
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


RC6_SYSTEMD_REQUIRED_TIMERS = (
    "porota-introspection-rc6.timer",
    "porota-introspection-publish-rc6.timer",
    "porota-scheduler-export-rc6.timer",
)


def assert_scheduler_invariants() -> None:
    keys=[j.key for j in INTERNAL_JOBS]
    if len(keys)!=len(set(keys)):
        raise AssertionError("duplicate internal scheduler job")
    if any(not _is_active_scope_job(key) for key in [j.key for j in active_internal_jobs()]):
        raise AssertionError("disabled scope jobs must not be listed as internal active work")
    if not set(RC6_SYSTEMD_REQUIRED_TIMERS).issubset(SYSTEMD_DESCRIPTIONS):
        raise AssertionError("all required RC6 introspection and dashboard timers must be catalogued")
