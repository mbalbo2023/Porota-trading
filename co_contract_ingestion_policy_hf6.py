"""HF6 Contract Evidence v2 ingestion policy.

Policy only. It does not schedule work, access PPI, mutate the runtime or promote
an instrument. Dynamic market data stays in the existing PPI market-data path;
this module defines freshness for contractual/operability evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class ContractCadence:
    name: str
    ttl: timedelta
    source_class: str
    during_market: bool
    description: str


# Structured GET/XHR first. Browser is never part of the trading hot path.
CADENCES = {
    "OPERABILITY": ContractCadence(
        "OPERABILITY", timedelta(minutes=15), "PPI_STRUCTURED_READ", True,
        "Instrumentos operables, settlement, moneda, precisión y series vigentes.",
    ),
    "CAUCION_LIVE_CONTRACT": ContractCadence(
        "CAUCION_LIVE_CONTRACT", timedelta(minutes=5), "PPI_STRUCTURED_READ", True,
        "Cauciones operables, lado, plazo, TNA, profundidad y capital disponible.",
    ),
    "AUCTION_STATUS": ContractCadence(
        "AUCTION_STATUS", timedelta(minutes=5), "PPI_STRUCTURED_READ", True,
        "Estado y ventana de licitaciones abiertas.",
    ),
    "DERIVATIVE_SERIES": ContractCadence(
        "DERIVATIVE_SERIES", timedelta(minutes=15), "PPI_STRUCTURED_READ", True,
        "Series vigentes de opciones/futuros y contratos asociados.",
    ),
    "STATIC_CONTRACT": ContractCadence(
        "STATIC_CONTRACT", timedelta(days=1), "PPI_STRUCTURED_READ_OR_WEB", False,
        "ISIN, lámina, nominal, vencimiento, cupón, amortización, ratios y reglas estáticas.",
    ),
    "FUND_TERMS": ContractCadence(
        "FUND_TERMS", timedelta(days=1), "PPI_STRUCTURED_READ_OR_WEB", False,
        "Cutoff, mínimo, rescate, moneda, NAV/cuotaparte, fees y restricciones del fondo.",
    ),
    "TARIFF": ContractCadence(
        "TARIFF", timedelta(days=1), "PPI_OFFICIAL", False,
        "Checksum diario de tarifario; auditoría humana/documental mensual.",
    ),
    "FULL_BROWSER_AUDIT": ContractCadence(
        "FULL_BROWSER_AUDIT", timedelta(days=7), "PPI_AUTHENTICATED_WEB", False,
        "Auditoría completa de estructura web y detección de cambios; nunca ejecución.",
    ),
}

FAMILY_CADENCE = {
    "ACCIONES": ("OPERABILITY", "STATIC_CONTRACT"),
    "CEDEARS": ("OPERABILITY", "STATIC_CONTRACT"),
    "BONOS": ("OPERABILITY", "STATIC_CONTRACT"),
    "LETRAS": ("OPERABILITY", "STATIC_CONTRACT"),
    "ON": ("OPERABILITY", "STATIC_CONTRACT"),
    "LEBAC": ("OPERABILITY", "STATIC_CONTRACT"),
    "NOBAC": ("OPERABILITY", "STATIC_CONTRACT"),
    "CAUCIONES": ("CAUCION_LIVE_CONTRACT", "STATIC_CONTRACT"),
    "OPCIONES": ("DERIVATIVE_SERIES", "STATIC_CONTRACT"),
    "FUTUROS": ("DERIVATIVE_SERIES", "STATIC_CONTRACT"),
    "FCI": ("FUND_TERMS",),
    "FCI_LOCAL": ("FUND_TERMS",),
    "FCI_EXTERIOR": ("FUND_TERMS",),
    "ETF": ("OPERABILITY", "STATIC_CONTRACT"),
    "ACCIONES_USA": ("OPERABILITY", "STATIC_CONTRACT"),
    "LICITACIONES": ("AUCTION_STATUS", "STATIC_CONTRACT"),
    "CANJES": ("AUCTION_STATUS", "STATIC_CONTRACT"),
}


def required_cadences(family: str):
    """Return immutable cadence definitions for a family, fail-closed if unknown."""
    key = str(family or "").upper()
    names = FAMILY_CADENCE.get(key)
    if not names:
        return ()
    return tuple(CADENCES[name] for name in names)


def ttl_seconds(name: str) -> int:
    return int(CADENCES[name].ttl.total_seconds())
