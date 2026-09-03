"""Scheduler catalog/model for RC4 dashboard.

Read-only model. Jobs persist evidence in different stores; this module
normalizes that evidence without pretending that absence from
``operational_jobs`` means a job did not run. Host systemd state is consumed
separately from a sanitized JSON snapshot; the dashboard never receives
systemctl or Docker privileges.

RC4 adds explicit Contract Evidence jobs and distinguishes scheduled policy
from observed evidence.  A grey row must have a reason such as
``NUNCA_EJECUTADO``/``SIN_EVIDENCIA``/``NO_APLICA``; grey is never a generic
"unknown" bucket.
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
    enabled = str(os.getenv("PAPER_NEWS_INGEST_ENABLED", "false")).strip().lower()
    return 45 * 60 if enabled in {"1", "true", "yes", "si", "sí"} else 12 * 3600


@dataclass(frozen=True)
class InternalJob:
    key: str
    label: str
    description: str
    cadence_seconds: int | None
    condition: str = ""
    expected_source: str = "operational_jobs"


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
                int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200")),
                expected_source="api_health"),
    InternalJob("PPI_PRODUCTION_HISTORY", "Históricos PPI producción",
                "Actualiza históricos PPI producción read-only.",
                int(os.getenv("PPI_BACKGROUND_INGEST_SECONDS", "7200")),
                expected_source="source_sync"),
    InternalJob("PPI_PRODUCTION_CATALOG", "Catálogo PPI",
                "Actualiza catálogo/instrumentos observables y metadatos de operatoria.",
                int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")),
                expected_source="source_sync"),
    InternalJob("BYMA_OPEN_DATA", "BYMA datos públicos",
                "Actualiza evidencia pública BYMA usada para calendario/referencia.",
                int(os.getenv("PUBLIC_SOURCE_CHECK_SECONDS", "21600")),
                expected_source="source_sync"),
    InternalJob("PAPER_FOCUS_COVERAGE", "Cobertura del universo",
                "Recalcula cobertura y disponibilidad sin habilitar familias por inferencia.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300")),
                expected_source="api_health"),
    InternalJob("PAPER_SIGNAL_SAMPLING", "Muestreo de señales",
                "Registra cobertura del motor de señales sobre el universo elegible.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300")),
                expected_source="api_health"),
    InternalJob("PAPER_SIGNAL_ROTATION", "Rotación de universo",
                "Rota la ventana evaluada para cubrir progresivamente el universo.",
                int(os.getenv("PAPER_READINESS_CHECK_SECONDS", "300")),
                expected_source="api_health"),

    # Contract Evidence collection policy approved on 2026-09-03. These jobs
    # are read-only collectors and never make a family READY merely because a
    # route/XHR exists.
    InternalJob("PPI_CONTRACT_XHR_DYNAMIC", "PPI contrato dinámico / XHR",
                "Captura campos contractuales dinámicos oficiales y versiona cambios.",
                15*60,"Sólo lectura. Auth/2FA/stale/conflict => HOLD.",
                "contract_evidence_v2_runs"),
    InternalJob("PPI_CONTRACT_CAUCIONES_OPEN_AUCTIONS", "PPI cauciones / subastas abiertas",
                "Actualiza términos dinámicos de cauciones/open auctions con cadencia corta.",
                5*60,"Sólo lectura; nunca Continuar/Confirmar ni routing de órdenes.",
                "contract_evidence_v2_runs"),
    InternalJob("PPI_CONTRACT_DERIVATIVES", "PPI opciones y futuros",
                "Actualiza metadata/contratos dinámicos de opciones y futuros.",
                15*60,"Readiness sigue HOLD hasta contrato y simulador especializados completos.",
                "contract_evidence_v2_runs"),
    InternalJob("PPI_CONTRACT_STATIC_HASH", "PPI contrato estático / hash",
                "Revalida páginas/datos contractuales estáticos por hash diario.",
                24*3600,"Cambios de hash exigen revisión; no auto-promoción.",
                "contract_evidence_v2_runs"),
    InternalJob("PPI_AUTHENTICATED_WEB_EVIDENCE", "PPI navegador autenticado completo",
                "Barrido browser completo para descubrimiento, snapshots y cambio contractual.",
                7*24*3600,"Semanal y fuera de mercado; 2FA humana si PPI la exige; sin secretos persistidos.",
                "contract_evidence_v2_runs"),
    InternalJob("CONTRACT_READINESS_RECONCILE", "Reconciliación de readiness contractual",
                "Normaliza aliases, autoridad, freshness y conflictos antes de recalcular readiness.",
                15*60,"Nunca convierte evidencia incompleta en READY_PAPER.",
                "contract_evidence_v2_runs"),
)

EVIDENCE_TABLE = {job.key:job.expected_source for job in INTERNAL_JOBS}

SYSTEMD_DESCRIPTIONS = {
    "porota-functional-health-rc4.timer": "Control funcional liviano read-only cada 5 minutos.",
    "porota-functional-deep-audit-rc4.timer": "Introspección funcional profunda read-only cada hora.",
    "porota-introspeccion-hf5.timer": "LEGACY: introspección horaria con nombre HF5; RC4 debe retirarlo al activar el reemplazo para evitar duplicados.",
    "porota-introspection-publish.timer": "Publica copia sanitizada; GitHub nunca controla runtime.",
    "porota-contract-evidence-hf6.timer": "LEGACY/HF6: Contract Evidence read-only; RC4 debe migrar a jobs versionados sin duplicar ejecución.",
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


def _duration(started,finished):
    a=_parse(started); b=_parse(finished)
    if a is None or b is None or b<a:
        return None
    return (b-a).total_seconds()


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
        "duration_seconds":row.get("duration_seconds"),
        "evidence_source": table,
    }


def _normalize_contract_run(row: dict) -> dict:
    row=dict(row or {})
    state=str(row.get("state") or "SIN_REGISTRO")
    finished=row.get("finished_at")
    return {
        "job_key":str(row.get("job_key") or ""),
        "last_run_at":row.get("started_at"),
        "last_success_at":finished if state.upper() in {"OK","SUCCESS","VERDE"} else None,
        "state":state,
        "detail":str(row.get("detail") or ""),
        "duration_seconds":_duration(row.get("started_at"),finished),
        "auth_state":row.get("auth_state"),
        "counts":{key:int(row.get(key) or 0) for key in
                  ("observed","recorded","changed","conflicts","blocked","errors")},
        "evidence_source":"contract_evidence_v2_runs",
    }


def _prefer(persisted: dict[str, dict], candidate: dict) -> None:
    """Prefer a job's own ledger; otherwise keep the freshest evidence."""
    key=candidate.get("job_key") or ""
    if not key:
        return
    current=persisted.get(key)
    if current is None:
        persisted[key]=candidate
        return
    priority={"operational_jobs":0,"contract_evidence_v2_runs":0,
              "source_sync":1,"api_health":2}
    cp=priority.get(current.get("evidence_source"),9)
    np=priority.get(candidate.get("evidence_source"),9)
    if np<cp:
        persisted[key]=candidate; return
    if np>cp:
        return
    old=_parse(current.get("last_run_at")); new=_parse(candidate.get("last_run_at"))
    if new is not None and (old is None or new > old):
        persisted[key]=candidate


def classify_row(row: dict, *, now=None, grace_seconds: int=120) -> dict:
    """Return explicit UI state/reason; grey must never be unexplained."""
    row=dict(row or {})
    now_dt=_parse(now) if now else datetime.now(TZ)
    state=str(row.get("state") or "SIN_REGISTRO").upper()
    last=_parse(row.get("last_run_at")); due=_parse(row.get("next_run_at"))
    if not row.get("last_run_at"):
        ui="GRAY"; reason="NUNCA_EJECUTADO" if state=="SIN_REGISTRO" else state
    elif state in {"NO_APLICA","NOT_APPLICABLE","DISABLED_BY_POLICY"}:
        ui="GRAY"; reason="NO_APLICA"
    elif state in {"ERROR","FAILED","FAIL","ROJO","CRITICAL"}:
        ui="RED"; reason="ERROR_COMPROBADO"
    elif state in {"BLOCKED_AUTH","AUTH_REQUIRED","HOLD","WAITING_CONDITION"}:
        ui="YELLOW"; reason=state
    elif due is not None and now_dt > due + timedelta(seconds=max(0,int(grace_seconds))):
        ui="YELLOW"; reason="STALE_EVIDENCE"
    elif state in {"OK","SUCCESS","VERDE","READY","RUNNING","PARTIAL","AMARILLO","WARN"}:
        ui="GREEN" if state in {"OK","SUCCESS","VERDE","READY","RUNNING"} else "YELLOW"
        reason="EVIDENCE_CURRENT" if ui=="GREEN" else state
    else:
        ui="GRAY"; reason="SIN_EVIDENCIA_CLASIFICABLE"
    row["ui_state"]=ui; row["ui_reason"]=reason
    row["age_seconds"]=(None if last is None else max(0.0,(now_dt-last).total_seconds()))
    return row


def internal_rows(db_rows: list[dict], *, source_sync_rows=None,
                  api_health_rows=None, contract_run_rows=None,
                  now=None) -> list[dict]:
    """Merge persisted evidence stores without duplicate jobs."""
    persisted={}
    for raw in db_rows or []:
        row=dict(raw); row["evidence_source"]="operational_jobs"
        _prefer(persisted,row)
    for raw in source_sync_rows or []:
        _prefer(persisted,_normalize_evidence(
            dict(raw), key_field="source", run_field="last_attempt_at",
            state_field="status", table="source_sync"))
    for raw in api_health_rows or []:
        _prefer(persisted,_normalize_evidence(
            dict(raw), key_field="component", run_field="checked_at",
            state_field="state", table="api_health"))
    for raw in contract_run_rows or []:
        _prefer(persisted,_normalize_contract_run(dict(raw)))

    result=[]
    known={j.key for j in INTERNAL_JOBS}
    for job in INTERNAL_JOBS:
        row=persisted.get(job.key,{})
        source=row.get("evidence_source") or job.expected_source
        item={
            "key":job.key,"label":job.label,"description":job.description,
            "condition":job.condition,"cadence_seconds":job.cadence_seconds,
            "last_run_at":row.get("last_run_at"),"last_success_at":row.get("last_success_at"),
            "state":row.get("state") or "SIN_REGISTRO","detail":row.get("detail") or
                f"Todavía no existe evidencia persistida en {job.expected_source}.",
            "next_run_at":next_due(row.get("last_run_at"),job.cadence_seconds),
            "duration_seconds":row.get("duration_seconds"),"source":source,
            "expected_source":job.expected_source,"auth_state":row.get("auth_state"),
            "counts":row.get("counts"),
        }
        result.append(classify_row(item,now=now))
    for key,row in sorted(persisted.items()):
        if key in known:
            continue
        item={
            "key":key,"label":key,"description":"Job interno descubierto en evidencia persistida.",
            "condition":"","cadence_seconds":None,"last_run_at":row.get("last_run_at"),
            "last_success_at":row.get("last_success_at"),"state":row.get("state") or "UNKNOWN",
            "detail":row.get("detail") or "","next_run_at":None,
            "duration_seconds":row.get("duration_seconds"),
            "source":row.get("evidence_source") or "DISCOVERED",
            "expected_source":"DISCOVERED","auth_state":row.get("auth_state"),
            "counts":row.get("counts"),
        }
        result.append(classify_row(item,now=now))
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
    required={"CAUCION_CASH_SWEEP","PPI_CONTRACT_XHR_DYNAMIC",
              "PPI_CONTRACT_CAUCIONES_OPEN_AUCTIONS","PPI_CONTRACT_DERIVATIVES",
              "PPI_CONTRACT_STATIC_HASH","PPI_AUTHENTICATED_WEB_EVIDENCE",
              "CONTRACT_READINESS_RECONCILE"}
    if not required.issubset(keys):
        raise AssertionError("required RC4 scheduler jobs missing")
    if "porota-preopen.timer" not in SYSTEMD_DESCRIPTIONS:
        raise AssertionError("legacy preopen timer must remain visible for retirement evidence")
    if "porota-functional-health-rc4.timer" not in SYSTEMD_DESCRIPTIONS:
        raise AssertionError("RC4 functional health timer must be visible")
