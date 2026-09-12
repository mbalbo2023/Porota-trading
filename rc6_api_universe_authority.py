"""RC6 post-final: autoridad pura del universo operativo PPI.

Este módulo NO consulta red, NO envía órdenes y NO habilita PAPER. Modela la
regla de arquitectura: la API productiva declara familias y SearchInstrument
confirma identidades concretas; web/XHR sólo enriquece esas identidades.
"""
from __future__ import annotations

from dataclasses import dataclass


ALIASES = {
    "ACCIONES_USA": "ACCIONES-USA",
    "FCI_EXTERIOR": "FCI-EXTERIOR",
    "FCI_LOCAL": "FCI",
    "OBLIGACIONES": "ON",
    "ETFS": "ETF",
}
PLACEHOLDERS = {"", "*", "UNKNOWN", "NONE", "N/A", "NULL"}


def canonical_family(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    return ALIASES.get(raw, raw)


def concrete(value: object) -> bool:
    return str(value or "").strip().upper() not in PLACEHOLDERS


@dataclass(frozen=True)
class DiscoveryQuery:
    family: str
    ticker: str
    name: str
    market: str

    def __post_init__(self):
        fam = canonical_family(self.family)
        ticker = str(self.ticker or "").strip().upper()
        name = str(self.name or "").strip()
        market = str(self.market or "").strip().upper()
        if not fam or not ticker or not name or not market:
            raise ValueError("API_DISCOVERY_QUERY_INCOMPLETE")
        object.__setattr__(self, "family", fam)
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "market", market)


def classify_family(family: str, *, api_types) -> dict:
    fam = canonical_family(family)
    declared = fam in {canonical_family(x) for x in (api_types or [])}
    return {
        "family": fam,
        "api_declared": declared,
        "context_only": not declared,
        "status": "API_DECLARED" if declared else "CONTEXT_ONLY",
    }


def classify_identity(*, family: str, ticker: str, market: str, api_types) -> dict:
    base = classify_family(family, api_types=api_types)
    discovered = bool(base["api_declared"] and concrete(ticker) and concrete(market))
    return {
        **base,
        "ticker": str(ticker or "").strip().upper(),
        "market": str(market or "").strip().upper(),
        "api_discovered": discovered,
        "status": (
            "API_DISCOVERED" if discovered else
            "API_DECLARED_NOT_DISCOVERED" if base["api_declared"] else
            "CONTEXT_ONLY"
        ),
        # Nunca se promueve a PAPER desde este módulo.
        "paper_candidate": False,
    }


def eligible_for_contract_binding(identity: dict) -> bool:
    """Binding contractual aplica sólo a identidad concreta reconocida por API."""
    return bool(identity.get("api_declared") and identity.get("api_discovered") and not identity.get("context_only"))


def assert_no_execution_capability() -> None:
    assert not any(name in globals() for name in ("send_order", "place_order", "cancel_order"))
