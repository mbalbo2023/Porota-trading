"""Pure RC6 dashboard truth semantics.

No FastAPI, DB, broker, network or runtime imports.  Keeping these rules pure
lets CI prove the market/session/policy semantics without requiring the web
stack and prevents presentation code from quietly redefining them.
"""
from __future__ import annotations

MARKET_OPEN = "MARKET_OPEN"
ERROR_STATES = frozenset({"ERROR", "FAILED", "ROJO", "DEGRADED", "STALE", "UNKNOWN"})


def market_sensitive_display_state(raw_state: str, *, session_state: str,
                                   open_positions: int = 0) -> str:
    """Map worker liveness to a truthful operator-facing state.

    A healthy process can remain alive outside the market window.  That must
    never be presented as active market execution.  Conversely, a real fault is
    never hidden just because the market is closed.
    """
    raw = str(raw_state or "UNKNOWN").upper()
    session = str(session_state or "UNKNOWN").upper()
    if raw in ERROR_STATES:
        return raw
    if session != MARKET_OPEN:
        return "MONITOREO_PASIVO" if int(open_positions or 0) > 0 else "EN_ESPERA_MERCADO_CERRADO"
    return raw


def policy_description(policy: str) -> str:
    """Describe a gate policy without conflating it with the global mode."""
    key = str(policy or "UNKNOWN").upper()
    if key == "BINDING":
        return "BINDING = una señal PAPER que falla la economía queda bloqueada; no es el modo global del sistema."
    if key in {"SHADOW", "OBSERVATION_ONLY", "OBSERVE"}:
        return f"{key} = sólo observación; no bloquea por esta política."
    return f"{key} = política no reconocida; requiere revisión."


def session_permits_market_activity(session_state: str) -> bool:
    """Only answers whether the session window is open; not whether a trade is authorized."""
    return str(session_state or "UNKNOWN").upper() == MARKET_OPEN
