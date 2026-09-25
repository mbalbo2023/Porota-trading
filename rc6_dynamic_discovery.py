"""RC6 automatic read-only instrument discovery plan.

The universe is discovered from provider APIs. There is no ticker watchlist or
CSV authority here: query tokens are generated mechanically and the provider
configuration decides which family/market pairs are queried. The plan is
bounded and resumable so discovery cannot monopolize the live market-data loop.
"""
from __future__ import annotations
import string

QUERY_TOKENS = tuple(string.ascii_uppercase + string.digits)

# canonical family -> PPI provider type + preferred markets.
FAMILY_SPECS = {
    "ACCIONES": ("ACCIONES", ("BYMA",)),
    "CEDEARS": ("CEDEARS", ("BYMA",)),
    "ETFS": ("ETF", ("BYMA", "NYSE", "NASDAQ")),
    "BONOS": ("BONOS", ("BYMA",)),
    "LETRAS": ("LETRAS", ("BYMA",)),
    "OBLIGACIONES": ("ON", ("BYMA",)),
    "OPCIONES": ("OPCIONES", ("BYMA",)),
    "FUTUROS": ("FUTUROS", ("ROFEX", "A3", "BYMA")),
    "CAUCIONES": ("CAUCIONES", ("BYMA",)),
    "FCI": ("FCI", ("BYMA", "OTC")),
}
PROVIDER_TO_CANONICAL = {provider: family for family, (provider, _) in FAMILY_SPECS.items()}
PROVIDER_TO_CANONICAL.update({"ON":"OBLIGACIONES", "ETF":"ETFS"})
CAUCION_SENTINEL = ("CAUCION", "CAUCION")


def canonical_family(provider_type: str) -> str:
    return PROVIDER_TO_CANONICAL.get(str(provider_type or "").strip().upper(),
                                     str(provider_type or "").strip().upper())


def _configured(values, key):
    if not isinstance(values, dict):
        return None
    items = values.get(key)
    return {str(x).strip().upper() for x in items} if isinstance(items, list) else None


def full_plan(configuration=None):
    """Deterministic complete scan plan generated without instrument symbols."""
    types = _configured(configuration, "instrument_types")
    markets = _configured(configuration, "markets")
    plan = []
    for canonical, (provider, preferred_markets) in FAMILY_SPECS.items():
        if types is not None and provider not in types:
            continue
        selected_markets = [m for m in preferred_markets if markets is None or m in markets]
        for market in selected_markets:
            if canonical == "CAUCIONES":
                plan.append({
                    "canonical_family": canonical, "provider_type": provider,
                    "market": market, "ticker_query": CAUCION_SENTINEL[0],
                    "name_query": CAUCION_SENTINEL[1], "mode": "OFFICIAL_SPECIAL",
                })
                continue
            for token in QUERY_TOKENS:
                plan.append({
                    "canonical_family": canonical, "provider_type": provider,
                    "market": market, "ticker_query": token, "name_query": token,
                    "mode": "GENERATED_PREFIX",
                })
    return tuple(plan)


def batch(configuration=None, *, cursor=0, budget=24):
    plan = full_plan(configuration)
    if not plan:
        return (), 0, True
    budget = max(1, min(int(budget), 72))
    start = int(cursor) % len(plan)
    take = min(budget, len(plan))
    selected = tuple(plan[(start+i) % len(plan)] for i in range(take))
    next_cursor = (start + take) % len(plan)
    wrapped = start + take >= len(plan)
    return selected, next_cursor, wrapped


def assert_no_manual_ticker_universe():
    assert QUERY_TOKENS == tuple(string.ascii_uppercase + string.digits)
    assert not any(len(item) > 1 for item in QUERY_TOKENS)
    assert set(FAMILY_SPECS) == {
        "ACCIONES","CEDEARS","ETFS","BONOS","LETRAS","OBLIGACIONES",
        "OPCIONES","FUTUROS","CAUCIONES","FCI",
    }
