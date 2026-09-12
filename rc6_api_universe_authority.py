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


def validate_search_results(query: DiscoveryQuery, rows, *, api_types, api_markets) -> list[dict]:
    """Valida la identidad DEVUELTA por PPI, nunca la semilla consultada.

    SearchInstrument puede devolver coincidencias parciales. Por ejemplo una
    búsqueda de ``QQQ`` puede devolver CQQQ/SQQQ/TQQQ. Esas especies pueden ser
    identidades API válidas, pero la respuesta NO prueba que ``QQQ`` exista.
    """
    declared_types = {canonical_family(x) for x in (api_types or [])}
    declared_markets = {str(x or "").strip().upper() for x in (api_markets or [])}
    out = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        family = canonical_family(raw.get("type") or raw.get("instrumentType"))
        ticker = str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
        market = str(raw.get("market") or "").strip().upper()
        if family != query.family or family not in declared_types:
            continue
        if market != query.market or market not in declared_markets:
            continue
        if not concrete(ticker) or not concrete(market):
            continue
        out.append({
            "family": family,
            "ticker": ticker,
            "market": market,
            "description": str(raw.get("description") or "").strip(),
            "currency": raw.get("currency"),
            "api_discovered": True,
            "exact_ticker_match": ticker == query.ticker,
            "search_seed": query.ticker,
            "paper_candidate": False,
        })
    return out


def eligible_for_contract_binding(identity: dict) -> bool:
    """Binding contractual aplica sólo a identidad concreta reconocida por API."""
    return bool(identity.get("api_declared") and identity.get("api_discovered") and not identity.get("context_only"))


def assert_no_execution_capability() -> None:
    assert not any(name in globals() for name in ("send_order", "place_order", "cancel_order"))
