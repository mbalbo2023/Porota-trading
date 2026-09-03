"""RC4 health/freshness explanation model.

Pure logic only: no DB, network, subprocess or runtime writes.  It separates the
component's persisted health state from freshness/policy applicability so the UI
never renders an unexplained yellow/gray badge.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone


GREEN = {"OK", "GREEN", "VERDE", "READY", "RUNNING", "SUCCESS", "HEALTHY"}
YELLOW = {"WARN", "WARNING", "AMARILLO", "PENDING", "DEGRADED", "STALE"}
RED = {"ERROR", "FAILED", "FAIL", "RED", "ROJO", "UNHEALTHY", "BLOCKED"}
NA = {"NO_APLICA", "NOT_APPLICABLE", "DISABLED_BY_POLICY"}


@dataclass(frozen=True)
class HealthView:
    visual_state: str
    cause: str
    action: str
    age_seconds: float | None
    cadence_seconds: int | None
    stale_after_seconds: int | None
    persisted_state: str
    applicable: bool
    evidence_present: bool

    def as_dict(self):
        return asdict(self)


def _parse(value) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def explain(*, state, checked_at=None, last_success_at=None,
            cadence_seconds=None, now=None, applicable=True,
            enabled_by_policy=True, expected_first_sample=True,
            detail="") -> HealthView:
    """Return an explicit reason for green/yellow/red/gray health.

    `cadence_seconds` is the effective cadence, not a nominal cadence that is
    disabled by policy.  Freshness only downgrades components that are expected
    to produce evidence.
    """
    persisted=str(state or "SIN_EVIDENCIA").strip().upper()
    now_dt=_parse(now) or datetime.now(timezone.utc)
    checked=_parse(checked_at)
    success=_parse(last_success_at)
    evidence=checked is not None or success is not None
    cadence=None if cadence_seconds is None else max(1, int(cadence_seconds))
    stale_after=None if cadence is None else max(cadence * 2, cadence + 120)
    reference=checked or success
    age=max(0.0, (now_dt-reference).total_seconds()) if reference else None

    if not applicable:
        return HealthView("GRAY", "NO_APLICA",
                          "No requiere acción en el modo/mercado actual.", age,
                          cadence, stale_after, persisted, False, evidence)
    if not enabled_by_policy or persisted in NA:
        return HealthView("GRAY", "DESHABILITADO_POR_POLITICA",
                          "No es una falla; mostrar la política efectiva y su próxima revisión.",
                          age, cadence, stale_after, persisted, True, evidence)
    if not evidence:
        cause="ESPERANDO_PRIMERA_MUESTRA" if expected_first_sample else "SIN_EVIDENCIA"
        return HealthView("GRAY", cause,
                          "Verificar instalación/fuente de evidencia si ya debía haber ejecutado.",
                          None, cadence, stale_after, persisted, True, False)

    if persisted in RED:
        return HealthView("RED", "FALLA_COMPROBADA",
                          str(detail or "Revisar el error persistido y la última ejecución."),
                          age, cadence, stale_after, persisted, True, True)

    if cadence is not None and age is not None and age > stale_after:
        return HealthView("YELLOW", "EVIDENCIA_RETRASADA",
                          f"Última evidencia hace {int(age)} s; cadencia efectiva {cadence} s. Revisar job/fuente.",
                          age, cadence, stale_after, persisted, True, True)

    if persisted in YELLOW:
        return HealthView("YELLOW", "DEGRADACION_REPORTADA",
                          str(detail or "La fuente reportó degradación; revisar detalle."),
                          age, cadence, stale_after, persisted, True, True)
    if persisted in GREEN:
        return HealthView("GREEN", "FRESH_OK",
                          "Sin acción; evidencia dentro de la ventana esperada.",
                          age, cadence, stale_after, persisted, True, True)

    return HealthView("YELLOW", "ESTADO_NO_NORMALIZADO",
                      f"Estado persistido no reconocido: {persisted}. Revisar normalización, no inferir OK.",
                      age, cadence, stale_after, persisted, True, True)
