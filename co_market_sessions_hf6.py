"""Sesiones de mercado versionadas por familia/mercado.

RC5 centraliza aquí los relojes que usa POROTA. No convierte una ventana de
mercado en autorización para operar: la capacidad de órdenes reales continúa
BLOCKED y las familias no verificadas siguen fail-closed.

BYMA publicó el Comunicado 19024 el 2026-09-22 como referencia vigente de horarios.
La página pública actual de horarios apunta a un PDF ajeno (INFORME BYMA 2026),
por lo que RC5 no infiere subastas/cierres especiales desde ese adjunto roto.
Para el PAPER spot que POROTA ya simula se promueve únicamente la ventana
regular 10:30 <= t < 17:00, consistente con la extensión de negociación vigente
y con páginas operativas actuales de BYMA. Sesiones especiales/extendidas no
quedan habilitadas por este módulo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

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


# ---- A3 / MATBA-ROFEX -----------------------------------------------------
# Fuente oficial verificada 2026-09-02. No se extrapola a BYMA.
A3_SOURCE = "https://matbarofex.com.ar/horario_de_negociacion"
A3_SOURCE_DATE = date(2026, 9, 2)

A3_SESSIONS = {
    "FUTUROS_OPCIONES_MONEDAS": MarketSession(
        "A3", "FUTUROS_OPCIONES_MONEDAS", time(10, 0), time(15, 0), time(9, 30),
        A3_SOURCE, A3_SOURCE_DATE, "Dólar/Yuan"),
    "FUTUROS_OPCIONES_AGRO": MarketSession(
        "A3", "FUTUROS_OPCIONES_AGRO", time(10, 30), time(17, 0), time(10, 0),
        A3_SOURCE, A3_SOURCE_DATE, "Agro general; mini/Chicago tienen excepciones"),
    "FUTUROS_OPCIONES_OTROS": MarketSession(
        "A3", "FUTUROS_OPCIONES_OTROS", time(10, 30), time(17, 0), time(10, 0),
        A3_SOURCE, A3_SOURCE_DATE,
        "RFX20, acciones individuales, BTC, Oro, WTI, Títulos Públicos, CER, CAUC"),
}


def session_for(market: str, family: str) -> MarketSession | None:
    market = str(market or "").upper()
    family = str(family or "").upper()
    if market == "BYMA":
        return BYMA_PAPER_SPOT if family in BYMA_PAPER_SPOT_FAMILIES else None
    if market in {"A3", "MATBA_ROFEX", "ROFEX"}:
        aliases = {
            "DLR": "FUTUROS_OPCIONES_MONEDAS",
            "MONEDAS": "FUTUROS_OPCIONES_MONEDAS",
            "AGRO": "FUTUROS_OPCIONES_AGRO",
            "RFX20": "FUTUROS_OPCIONES_OTROS",
            "ACCIONES": "FUTUROS_OPCIONES_OTROS",
            "BTC": "FUTUROS_OPCIONES_OTROS",
            "ORO": "FUTUROS_OPCIONES_OTROS",
            "WTI": "FUTUROS_OPCIONES_OTROS",
            "TITULOS_PUBLICOS": "FUTUROS_OPCIONES_OTROS",
            "CER": "FUTUROS_OPCIONES_OTROS",
            "CAUC": "FUTUROS_OPCIONES_OTROS",
        }
        return A3_SESSIONS.get(aliases.get(family, family))
    return None


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
