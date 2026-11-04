"""Sesiones BYMA/A3 versionadas por familia y settlement sin autoridad real.

El Comunicado BYMA 19031 (28-Oct-2026, vigente 02-Nov-2026) cierra renta
variable T+1 18:00, renta fija/CI/caución 17:00, opciones 18:00. Un motor sin
identidad exacta mantiene el reloj PAPER conservador de 17:00. El reloj EOD
18:15 es sólo piso temporal: NO certifica publicación de fuente.
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
BYMA_HOURS_SOURCE = "https://www.byma.com.ar/comunicados/com19031-horarios"
BYMA_HOURS_SOURCE_DATE = date(2026, 10, 28)
BYMA_HOURS_EFFECTIVE_DATE = date(2026, 11, 2)
BYMA_HOURS_COMMUNICATION = "19031"
BYMA_HOURS_PDF_SHA256 = "fe31c7baf8c51692e4bbbcc9138455563025964e9ae7178337c3c55990df6af0"
BYMA_CURRENT_HOURS_PAGE = "https://www.byma.com.ar/mercado/horarios"

# El reloj PAPER genérico no hereda renta variable T+1 sin identidad. Warm-up
# 10:15 es propio de POROTA, NO una subasta oficial BYMA.
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
        "RC6 PAPER conservative until 17:00 without exact settlement; "
        "COM19031 BYMA T+1 equity requires separate 18:00 session."
    ),
)

_BYMA_EQUITY_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETF", "ETFS"})
_BYMA_EXACT_T1_SETTLEMENT = frozenset({"A-24HS", "24HS", "T1"})
BYMA_PAPER_EQUITY_T1 = MarketSession(
    market="BYMA", family="RENTA_VARIABLE_T1", regular_open=time(10, 30),
    regular_close=time(18, 0), preopen_start=BYMA_PAPER_PREOPEN_START,
    source=BYMA_HOURS_SOURCE, source_date=BYMA_HOURS_SOURCE_DATE,
    notes="COM19031 PPT renta variable contado 24hs VWAP; PAPER only")
BYMA_CAUCION_MARKET = MarketSession(
    market="BYMA", family="CAUCIONES", regular_open=time(10, 30),
    regular_close=time(17, 0), preopen_start=None,
    source=BYMA_HOURS_SOURCE, source_date=BYMA_HOURS_SOURCE_DATE,
    notes="COM19031 PPT pase y caución; source observation only")
BYMA_VERIFIED_SEGMENT_SESSIONS = {
    "RENTA_VARIABLE_T1": BYMA_PAPER_EQUITY_T1,
    "RENTA_FIJA_T1": MarketSession("BYMA", "RENTA_FIJA_T1", time(10, 30),
        time(17, 0), None, BYMA_HOURS_SOURCE, BYMA_HOURS_SOURCE_DATE),
    "CONTADO_INMEDIATO": MarketSession("BYMA", "CONTADO_INMEDIATO", time(10, 30),
        time(17, 0), None, BYMA_HOURS_SOURCE, BYMA_HOURS_SOURCE_DATE),
    "CAUCIONES": BYMA_CAUCION_MARKET,
    "OPCIONES_PLAZOS": MarketSession("BYMA", "OPCIONES_PLAZOS", time(10, 30),
        time(18, 0), None, BYMA_HOURS_SOURCE, BYMA_HOURS_SOURCE_DATE,
        "COM19031 ejercicio de opciones tiene cutoff propio 15:59"),
    "FUTUROS_INDICE_RENTA_VARIABLE": MarketSession("BYMA",
        "FUTUROS_INDICE_RENTA_VARIABLE", time(10, 30), time(18, 0), None,
        BYMA_HOURS_SOURCE, BYMA_HOURS_SOURCE_DATE, "BYMA, no A3"),
    "FUTUROS_TITULOS_PUBLICOS": MarketSession("BYMA",
        "FUTUROS_TITULOS_PUBLICOS", time(10, 30), time(17, 0), None,
        BYMA_HOURS_SOURCE, BYMA_HOURS_SOURCE_DATE, "BYMA, no A3"),
    "FUTUROS_FX": MarketSession("BYMA", "FUTUROS_FX", time(10), time(15),
        None, BYMA_HOURS_SOURCE, BYMA_HOURS_SOURCE_DATE,
        "BYMA FX vencimiento 14:00 requiere gate adicional"),
}


def verified_byma_segment_session(segment: str) -> MarketSession | None:
    """Reloj informativo oficial; jamás autoriza PAPER ni órdenes reales."""
    return BYMA_VERIFIED_SEGMENT_SESSIONS.get(str(segment or "").upper())


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


def session_for(market: str, family: str, settlement: str | None = None,
                *, as_of: datetime | date | None = None) -> MarketSession | None:
    market = str(market or "").upper()
    family = str(family or "").upper()
    if market == "BYMA":
        if family not in BYMA_PAPER_SPOT_FAMILIES:
            return None
        if isinstance(as_of, datetime):
            if as_of.tzinfo is None or as_of.utcoffset() is None:
                return BYMA_PAPER_SPOT
            local_day = as_of.astimezone(TZ).date()
        elif isinstance(as_of, date):
            local_day = as_of
        else:
            local_day = datetime.now(TZ).date()
        if (local_day >= BYMA_HOURS_EFFECTIVE_DATE
                and family in _BYMA_EQUITY_FAMILIES
                and str(settlement or "").upper() in _BYMA_EXACT_T1_SETTLEMENT):
            return BYMA_PAPER_EQUITY_T1
        return BYMA_PAPER_SPOT
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


def byma_paper_instrument_phase(market: str, family: str, settlement: str,
                                now: datetime | None = None) -> str:
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        return "CLOSED"
    local = (now or datetime.now(TZ)).astimezone(TZ)
    if str(market or "").upper() != "BYMA" or not _byma_business_day(local):
        return "CLOSED"
    return phase_for(session_for(market, family, settlement, as_of=local), local)


def byma_paper_instrument_open(market: str, family: str, settlement: str,
                               now: datetime | None = None) -> bool:
    return byma_paper_instrument_phase(market, family, settlement, now) == "OPEN"


def byma_caucion_collection_open(now: datetime | None = None) -> bool:
    local = (now or datetime.now(TZ)).astimezone(TZ)
    return _byma_business_day(local) and phase_for(BYMA_CAUCION_MARKET, local) == "OPEN"


def byma_eod_publication_floor(at: datetime | date) -> datetime:
    """Hora mínima de intento EOD; no representa recibo de PPI/CAFCI."""
    if isinstance(at, datetime):
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("BYMA_EOD_TIMEZONE_REQUIRED")
        local_day = at.astimezone(TZ).date()
    elif isinstance(at, date):
        local_day = at
    else:
        raise ValueError("BYMA_EOD_DATE_REQUIRED")
    clock = time(18, 15) if local_day >= BYMA_HOURS_EFFECTIVE_DATE else time(17, 15)
    return datetime.combine(local_day, clock, TZ)


def byma_schedule_status() -> dict:
    """Fuente oficial segmentada y fallback PAPER sin identidad."""
    return {
        "state": "RC6_COM19031_SEGMENTED_WITH_CONSERVATIVE_PAPER_FALLBACK",
        "communication": BYMA_HOURS_COMMUNICATION,
        "source": BYMA_HOURS_SOURCE,
        "source_date": BYMA_HOURS_SOURCE_DATE.isoformat(),
        "effective_date": BYMA_HOURS_EFFECTIVE_DATE.isoformat(),
        "official_pdf_sha256": BYMA_HOURS_PDF_SHA256,
        "current_hours_page": BYMA_CURRENT_HOURS_PAGE,
        "paper_spot_regular_open": BYMA_PAPER_SPOT_OPEN.strftime("%H:%M"),
        "paper_spot_regular_close": BYMA_PAPER_SPOT_CLOSE.strftime("%H:%M"),
        "interval": "[10:30,17:00)",
        "equity_t1_regular_close": "18:00",
        "caucion_regular_close": "17:00",
        "option_regular_close": "18:00",
        "extended_sessions_enabled": False,
        "unverified_special_sessions": "FAIL_CLOSED",
        "public_attachment_status": "OFFICIAL_COM19031_PDF_VERIFIED_SHA256",
    }
