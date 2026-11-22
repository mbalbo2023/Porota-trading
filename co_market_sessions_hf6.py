"""Sesiones de mercado versionadas por familia/mercado.

RC5 centraliza aquí los relojes que usa POROTA. No convierte una ventana de
mercado en autorización para operar: la capacidad de órdenes reales continúa
BLOCKED y las familias no verificadas siguen fail-closed.

BYMA publicó el Comunicado 19024 el 2026-09-22 como referencia vigente de horarios.
La página pública actual de horarios apunta a un PDF ajeno (INFORME BYMA 2026),
por lo que RC5 no infiere subastas/cierres especiales desde ese adjunto roto.
Para el PAPER spot que POROTA ya simula se promueve únicamente la ventana
regular 10:30 <= t < 17:00, consistente con la extensión de negociación vigente
y con páginas operativas actuales de BYMA. El archivo local usa un reloj de
fuente separado para las series T+1 auditadas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from ak_byma_t1_source_clock import BYMA_T1_EXACT_SETTLEMENTS, estado_negociacion_fuente_byma

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


@dataclass(frozen=True)
class MarketSession:
    market: str
    family: str
    regular_open: time
    regular_close: time
    preopen_start: time | None
    source: str
    source_date: date
    notes: str = ""


# ---- BYMA -----------------------------------------------------------------
BYMA_HOURS_SOURCE = (
    "https://www.byma.com.ar/comunicados/com19024-horarios"
)
BYMA_HOURS_SOURCE_DATE = date(2026, 9, 22)
BYMA_HOURS_COMMUNICATION = "19024"
BYMA_CURRENT_HOURS_PAGE = "https://www.byma.com.ar/mercado/horarios"

# Source-history clocks from COM19031 (effective 02-Nov-2026).
# This informational clock does not extend the conservative PAPER session.
BYMA_EOD_FLOOR_EFFECTIVE_DATE = date(2026, 11, 2)
BYMA_T1_SOURCE_REVIEWED_THROUGH = date(2026, 12, 31)

# Ventana operacional RC5 del simulador spot. Intervalo half-open: 17:00 ya no
# admite nuevas evaluaciones regulares. El warm-up 10:15 es interno de POROTA,
# NO se presenta como subasta oficial.
BYMA_PAPER_SPOT_OPEN = time(10, 30)
BYMA_PAPER_SPOT_CLOSE = time(17, 0)
BYMA_PAPER_PREOPEN_START = time(10, 15)

BYMA_PAPER_SPOT_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "ON", "OBLIGACIONES",
    "ETF", "ETFS",
})

BYMA_PAPER_SPOT = MarketSession(
    market="BYMA",
    family="PAPER_SPOT",
    regular_open=BYMA_PAPER_SPOT_OPEN,
    regular_close=BYMA_PAPER_SPOT_CLOSE,
    preopen_start=BYMA_PAPER_PREOPEN_START,
    source=BYMA_HOURS_SOURCE,
    source_date=BYMA_HOURS_SOURCE_DATE,
    notes=(
        "RC6 PAPER regular only; comunicado vigente 19024. "
        "Adjunto horario público 2026-09-05 detectado mal enlazado; "
        "subastas y sesiones extendidas no se infieren ni se habilitan."
    ),
)

BYMA_T1_EQUITY_SOURCE = MarketSession(
    market="BYMA",
    family="T1_EQUITY_SOURCE",
    regular_open=BYMA_PAPER_SPOT_OPEN,
    regular_close=time(18, 0),
    preopen_start=None,
    source="https://www.byma.com.ar/comunicados/com19031-horarios",
    source_date=date(2026, 10, 28),
    notes="Regular trading window for exact T+1 equities and CEDEARs.",
)


# ---- A3 / MATBA-ROFEX -----------------------------------------------------
# Referencia oficial por producto verificada el 22-Nov-2026.
A3_SOURCE = "https://a3mercados.com.ar/info-de-mercado/datos-de-mercado"
A3_SOURCE_DATE = date(2026, 11, 22)

# AGRO y OTROS requieren el producto o una variante con horario común.
A3_SESSIONS = {
    **dict.fromkeys(
        ("DLR", "MONEDAS", "FUTUROS_OPCIONES_MONEDAS"),
        MarketSession("A3", "FUTUROS_OPCIONES_MONEDAS", time(10), time(15), time(9, 30),
                      A3_SOURCE, A3_SOURCE_DATE, "Dólar/Yuan"),
    ),
    **dict.fromkeys(
        ("AGRO_ESTANDAR", "AGRO_MINI", "SOJA", "GIRASOL", "TRIGO", "CEBADA", "MAIZ",
         "MAÍZ", "SORGO", "SOJA_MINI", "TRIGO_MINI", "MAIZ_MINI", "MAÍZ_MINI"),
        MarketSession("A3", "AGRO_ESTANDAR_MINI", time(11), time(17), time(10, 30),
                      A3_SOURCE, A3_SOURCE_DATE, "Agro estándar y Mini"),
    ),
    **dict.fromkeys(
        ("AGRO_CHICAGO", "SOJA_CHICAGO", "MAIZ_CHICAGO", "MAÍZ_CHICAGO"),
        MarketSession("A3", "AGRO_CHICAGO", time(11), time(16, 20), time(10, 30),
                      A3_SOURCE, A3_SOURCE_DATE, "Soja y Maíz Chicago"),
    ),
    **dict.fromkeys(
        ("RFX20", "ACCIONES"),
        MarketSession("A3", "RFX20_ACCIONES", time(10, 30), time(18), time(10),
                      A3_SOURCE, A3_SOURCE_DATE, "RFX20 y acciones individuales"),
    ),
    **dict.fromkeys(
        ("BTC", "ORO", "WTI", "TITULOS_PUBLICOS", "CER", "CAUC", "TAMAR"),
        MarketSession("A3", "OTROS", time(10, 30), time(17), time(10),
                      A3_SOURCE, A3_SOURCE_DATE, "BTC, Oro, WTI, Títulos, CER y TAMAR"),
    ),
}


def session_for(market: str, family: str) -> MarketSession | None:
    market = str(market or "").upper()
    family = str(family or "").upper()
    if market == "BYMA":
        return BYMA_PAPER_SPOT if family in BYMA_PAPER_SPOT_FAMILIES else None
    if market in {"A3", "MATBA_ROFEX", "ROFEX"}:
        return A3_SESSIONS.get(family)
    return None


def byma_source_session_for(day: date, *, market: str, family: str,
                            settlement: str) -> MarketSession | None:
    """Observed BYMA spot window for the archive's exact series and date."""
    base_session = session_for(market, family)
    state = estado_negociacion_fuente_byma(
        day, market=market, family=family, settlement=settlement)
    if base_session is None or state not in {"REGULAR", "LIMITED_T1_SOURCE_ONLY"}:
        return None
    if (state == "REGULAR"
            and BYMA_EOD_FLOOR_EFFECTIVE_DATE <= day <= BYMA_T1_SOURCE_REVIEWED_THROUGH
            and family.upper() in {"ACCIONES", "CEDEARS"}
            and str(settlement or "").strip().upper() in BYMA_T1_EXACT_SETTLEMENTS):
        return BYMA_T1_EQUITY_SOURCE
    return base_session


def phase_for(session: MarketSession | None, now: datetime | None = None) -> str:
    """Pure clock phase. Calendar admission is market-specific."""
    if session is None:
        return "UNKNOWN"
    local = (now or datetime.now(TZ)).astimezone(TZ)
    clock = local.time().replace(tzinfo=None)
    if session.preopen_start and session.preopen_start <= clock < session.regular_open:
        return "PREOPEN"
    if session.regular_open <= clock < session.regular_close:
        return "OPEN"
    return "CLOSED"


def _byma_business_day(local: datetime) -> bool:
    try:
        import ak_byma_calendar as calendar
        return bool(calendar.es_dia_habil_operativo(local.date()))
    except Exception:
        # Sin calendario verificable no se abre la sesión: fail-closed.
        return False


def byma_paper_spot_phase(now: datetime | None = None) -> str:
    local = (now or datetime.now(TZ)).astimezone(TZ)
    if not _byma_business_day(local):
        return "CLOSED"
    return phase_for(BYMA_PAPER_SPOT, local)


def byma_paper_spot_open(now: datetime | None = None) -> bool:
    return byma_paper_spot_phase(now) == "OPEN"


def byma_eod_publication_floor(at: datetime | date) -> datetime:
    """Minimum source EOD attempt time, never a publication/PAPER receipt."""
    if isinstance(at, datetime):
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("BYMA_EOD_TIMEZONE_REQUIRED")
        local_day = at.astimezone(TZ).date()
    elif isinstance(at, date):
        local_day = at
    else:
        raise ValueError("BYMA_EOD_DATE_REQUIRED")
    clock = time(18, 15) if local_day >= BYMA_EOD_FLOOR_EFFECTIVE_DATE else time(17, 15)
    return datetime.combine(local_day, clock, TZ)


def byma_schedule_status() -> dict:
    """Estado auditable del horario BYMA usado por el PAPER RC5."""
    return {
        "state": "RC5_REGULAR_SPOT_WINDOW_VERIFIED_LIMITED_SCOPE",
        "communication": BYMA_HOURS_COMMUNICATION,
        "source": BYMA_HOURS_SOURCE,
        "source_date": BYMA_HOURS_SOURCE_DATE.isoformat(),
        "current_hours_page": BYMA_CURRENT_HOURS_PAGE,
        "paper_spot_regular_open": BYMA_PAPER_SPOT_OPEN.strftime("%H:%M"),
        "paper_spot_regular_close": BYMA_PAPER_SPOT_CLOSE.strftime("%H:%M"),
        "interval": "[10:30,17:00)",
        "extended_sessions_enabled": False,
        "unverified_special_sessions": "FAIL_CLOSED",
        "public_attachment_status": "MISLINKED_UNRELATED_PDF_OBSERVED_2026-09-05",
    }
