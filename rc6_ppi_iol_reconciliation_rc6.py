"""PPI-primary / IOL-complementary reconciliation contract for RC6.

This module is read-only and deterministic. It normalizes comparable fields,
records field-level coverage, and fails closed on stale, ambiguous, or
materially divergent observations. It does not select a broker or authorize
orders.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 1
FIELDS = ("last", "bid", "ask", "variation_pct", "cash_volume")
DEFAULT_TOLERANCE_PCT = 2.0
MAX_AGE_SECONDS = 120.0

def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

def _utc(value: Any) -> datetime | None:
    if not value: return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None

def _quote(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value.get("quote") if isinstance(value.get("quote"), dict) else value
    return {"last": value}

def reconcile(primary: Any, secondary: Any, *, now: datetime | None = None,
              tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
              primary_source: str = "PPI", secondary_source: str = "IOL") -> dict[str, Any]:
    p, s = _quote(primary), _quote(secondary)
    compared: dict[str, Any] = {}
    matches = divergences = missing = 0
    for field in FIELDS:
        pv, sv = _num(p.get(field)), _num(s.get(field))
        if pv is None or sv is None:
            state = "MISSING_COMPARABLE_FIELD"
            missing += 1
            compared[field] = {"state": state, "primary": pv, "secondary": sv}
            continue
        diff = abs(pv - sv) / abs(pv) * 100 if pv else (0.0 if sv == 0 else None)
        state = "MATCH" if diff is not None and diff <= tolerance_pct else "DIVERGENCE"
        matches += state == "MATCH"
        divergences += state == "DIVERGENCE"
        compared[field] = {"state": state, "primary": pv, "secondary": sv, "difference_pct": diff}
    ptime = _utc(p.get("provider_observed_at") or p.get("observed_at"))
    stime = _utc(s.get("provider_observed_at") or s.get("observed_at"))
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ages = {}
    for name, value in ((primary_source, ptime), (secondary_source, stime)):
        ages[name] = (ref - value).total_seconds() if value else None
    freshness = {
        name: ("FRESH" if age is not None and 0 <= age <= MAX_AGE_SECONDS else
               "STALE" if age is not None else "UNKNOWN")
        for name, age in ages.items()
    }
    if freshness[primary_source] == "STALE" or freshness[secondary_source] == "STALE":
        contract_state = "BLOCKED_STALE"
    elif divergences:
        contract_state = "BLOCKED_CONFLICT"
    elif matches == 0:
        contract_state = "INSUFFICIENT_EVIDENCE"
    elif missing:
        contract_state = "READY_SHADOW_PARTIAL"
    else:
        contract_state = "READY_SHADOW"
    last_state = compared.get("last", {}).get("state")
    state = "MATCH" if last_state == "MATCH" else ("PRICE_DIVERGENCE" if last_state == "DIVERGENCE" else "BACKGROUND_COMPARISON_INCOMPLETE")
    return {"schema_version": SCHEMA_VERSION, "state": state, "contract_state": contract_state, "primary_source": primary_source,
            "secondary_source": secondary_source, "matches": matches, "divergences": divergences,
            "missing": missing, "freshness": freshness, "fields": compared,
            "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
            "real_money_authorized": False}

\n\ndef summarize_rows(symbols: list[str], rows: dict[str, Any], primary_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    """Publish auditable aggregate status without treating cache size as coverage."""
    selected = [rows.get(symbol) for symbol in symbols if isinstance(rows.get(symbol), dict)]
    comparisons = [row.get("primary_comparison") or {} for row in selected]
    aligned = sum(item.get("state") == "MATCH" for item in comparisons)
    divergent = sum(item.get("state") == "PRICE_DIVERGENCE" for item in comparisons)
    incomplete = sum(item.get("state") == "BACKGROUND_COMPARISON_INCOMPLETE" for item in comparisons)
    contract_states = [item.get("contract_state", "INSUFFICIENT_EVIDENCE") for item in comparisons]
    ready_shadow = sum(state.startswith("READY_SHADOW") for state in contract_states)
    blocked = sum(state.startswith("BLOCKED") for state in contract_states)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY",
        "scope_count": len(symbols),
        "observed_count": len(selected),
        "price_aligned": aligned,
        "price_divergent": divergent,
        "comparison_incomplete": incomplete,
        "ready_shadow": ready_shadow,
        "blocked": blocked,
        "primary_contract": primary_contract or {},
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }
