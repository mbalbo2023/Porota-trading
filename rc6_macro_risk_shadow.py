"""Contexto macro BCRA en modo Shadow para el motor RC6.

Consume únicamente la caché local ya actualizada por el mantenimiento diario.
No realiza red, no crea órdenes y no modifica ninguna decisión.
"""
from __future__ import annotations


def collect():
    try:
        import ad_macro_history
        raw = ad_macro_history.get_macro_context(dias=180)
    except Exception as exc:
        return {
            "mode": "SHADOW",
            "state": "UNAVAILABLE",
            "decision_effect": "OBSERVE_ONLY",
            "source": "BCRA_CACHE",
            "reason": f"{type(exc).__name__}:{str(exc)[:180]}",
        }

    indicators = raw.get("indicadores") if isinstance(raw, dict) else {}
    compact = {}
    for name, item in (indicators or {}).items():
        if not isinstance(item, dict):
            continue
        compact[str(name)] = {
            key: item.get(key)
            for key in ("ultimo", "fecha_ultimo", "tendencia", "percentil_actual", "fuente")
        }
    return {
        "mode": "SHADOW",
        "state": "READY" if compact else "INSUFFICIENT_DATA",
        "decision_effect": "OBSERVE_ONLY",
        "source": "BCRA_AND_OFFICIAL_MACRO_CACHE",
        "indicators": compact,
        "feature_version": "rc6-macro-risk-shadow-v1",
    }
