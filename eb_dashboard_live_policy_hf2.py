"""RC4-HF2 candidate: política pura para /en-vivo accesible y centrado en la jornada.

No accede a PPI, no escribe SQLite y no conoce rutas de órdenes. Sólo decide qué
registros pertenecen a la jornada local y cómo paginarlos para una tablet.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime
from dh_dashboard_compact_lists_hf6 import DEFAULT_PAGE_SIZE, Page, paginate

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def is_local_day(value, *, now: datetime | None = None) -> bool:
    if not value:
        return False
    current=now or datetime.now(TZ)
    if current.tzinfo is None:
        current=current.replace(tzinfo=TZ)
    try:
        observed=aware_datetime(value).astimezone(TZ)
    except Exception:
        return False
    return observed.date()==current.astimezone(TZ).date()


def rows_for_today(rows, timestamp_key: str, *, now: datetime | None = None) -> list[dict]:
    """Filtra estrictamente la jornada AR; timestamp inválido no se presenta como hoy."""
    return [dict(row) for row in (rows or ()) if is_local_day(row.get(timestamp_key),now=now)]


def closed_for_live(rows, *, now: datetime | None = None) -> list[dict]:
    return rows_for_today(rows,"closed_at",now=now)


def decisions_for_live(rows, *, now: datetime | None = None) -> list[dict]:
    return rows_for_today(rows,"decided_at",now=now)


def page_for_tablet(rows, *, offset=0, limit=DEFAULT_PAGE_SIZE) -> Page:
    """Reutiliza el paginador canónico: 20 por defecto, máximo 50."""
    return paginate(rows,offset=offset,limit=limit)


def refresh_policy(*, requested_seconds: int | None, interactive_details: bool=True) -> dict:
    """El refresh automático es opt-in cuando la página contiene detalles expandibles.

    Un valor None/0 significa manual. Si se habilita explícitamente se exige un
    mínimo conservador para evitar recargas agresivas.
    """
    value=max(0,int(requested_seconds or 0))
    if interactive_details and value:
        value=max(60,value)
    return {
        "seconds":value,
        "automatic":bool(value),
        "interactive_details":bool(interactive_details),
        "preserve_focus_required":True,
        "preserve_pagination_required":True,
    }


def assert_live_policy_invariants() -> None:
    page=page_for_tablet(list(range(200)),limit=500)
    if len(page.items)>50:
        raise AssertionError("/en-vivo no puede renderizar listas masivas")
    if refresh_policy(requested_seconds=30,interactive_details=True)["seconds"]<60:
        raise AssertionError("refresh interactivo demasiado agresivo")
