"""PPI-primary / IOL-complementary reconciliation contract for RC6.

Read-only and deterministic. PPI remains authoritative. IOL may confirm or
complete non-critical fields; it never replaces a valid PPI critical field,
changes a signal, or authorizes an order.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 2
FIELDS = ("last", "bid", "ask", "bid_size", "ask_size", "spread_pct",
          "variation_pct", "cash_volume")
CRITICAL_FIELDS = frozenset(("last", "bid", "ask"))
OPTIONAL_FIELDS = frozenset(("variation_pct", "cash_volume"))
METADATA_FIELDS = ("market", "settlement", "currency", "units_per_lot", "asset_type")
DEFAULT_TOLERANCE_PCT = 2.0
MAX_AGE_SECONDS = 120.0

def _num(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

def _utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None

def _quote(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value.get("quote") if isinstance(value.get("quote"), dict) else value
    return {"last": value}

def _with_derived_quote(value: Any) -> dict[str, Any]:
    quote = dict(_quote(value))
    bid, ask = _num(quote.get("bid")), _num(quote.get("ask"))
    if quote.get("spread_pct") in (None, "") and bid is not None and ask is not None and bid > 0 and ask >= bid:
        quote["spread_pct"] = (ask - bid) / bid * 100.0
    return quote

def reconcile(primary: Any, secondary: Any, *, now: datetime | None = None,
              tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
              primary_source: str = "PPI", secondary_source: str = "IOL") -> dict[str, Any]:
    p, s = _with_derived_quote(primary), _with_derived_quote(secondary)
    compared: dict[str, Any] = {}
    matches = divergences = missing = complemented = 0
    for field in FIELDS:
        pv, sv = _num(p.get(field)), _num(s.get(field))
        if pv is None or sv is None:
            if pv is None and sv is None:
                state = "NOT_AVAILABLE_BOTH_SIDES"
                if field not in OPTIONAL_FIELDS:
                    missing += 1
            elif pv is None and sv is not None and field not in CRITICAL_FIELDS:
                state = "COMPLEMENTED_SECONDARY"
                complemented += 1
            else:
                state = "MISSING_COMPARABLE_FIELD"
                missing += 1
            compared[field] = {"state": state, "primary": pv, "secondary": sv}
            continue
        diff = abs(pv - sv) / abs(pv) * 100 if pv else (0.0 if sv == 0 else None)
        state = "MATCH" if diff is not None and diff <= tolerance_pct else "DIVERGENCE"
        matches += state == "MATCH"
        divergences += state == "DIVERGENCE"
        compared[field] = {"state": state, "primary": pv, "secondary": sv, "difference_pct": diff}
    for field in METADATA_FIELDS:
        pv, sv = p.get(field), s.get(field)
        if pv in (None, "") or sv in (None, ""):
            continue
        state = "MATCH" if str(pv).upper() == str(sv).upper() else "DIVERGENCE"
        compared[field] = {"state": state, "primary": pv, "secondary": sv}
        divergences += state == "DIVERGENCE"
        matches += state == "MATCH"
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ages = {}
    ptime = _utc(p.get("provider_observed_at") or p.get("observed_at"))
    stime = _utc(s.get("provider_observed_at") or s.get("observed_at"))
    for name, value in ((primary_source, ptime), (secondary_source, stime)):
        ages[name] = (ref - value).total_seconds() if value else None
    freshness = {name: ("FRESH" if age is not None and 0 <= age <= MAX_AGE_SECONDS
                        else "STALE" if age is not None else "UNKNOWN")
                 for name, age in ages.items()}
    if freshness[primary_source] == "STALE" or freshness[secondary_source] == "STALE":
        contract_state = "BLOCKED_STALE"
    elif divergences:
        contract_state = "BLOCKED_CONFLICT"
    elif matches == 0 and complemented == 0:
        contract_state = "INSUFFICIENT_EVIDENCE"
    elif complemented:
        contract_state = "READY_SHADOW_COMPLEMENTED"
    elif missing:
        contract_state = "READY_SHADOW_PARTIAL"
    else:
        contract_state = "READY_SHADOW"
    last_state = compared.get("last", {}).get("state")
    state = "MATCH" if last_state == "MATCH" else (
        "PRICE_DIVERGENCE" if last_state == "DIVERGENCE"
        else "BACKGROUND_COMPARISON_INCOMPLETE")
    return {"schema_version": SCHEMA_VERSION, "state": state,
            "contract_state": contract_state, "primary_source": primary_source,
            "secondary_source": secondary_source, "matches": matches,
            "divergences": divergences, "missing": missing,
            "complemented": complemented, "freshness": freshness,
            "fields": compared, "decision_effect": "OBSERVE_ONLY",
            "live_decision_authority": False, "real_money_authorized": False}

def summarize_rows(symbols: list[str], rows: dict[str, Any],
                   primary_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = [rows.get(symbol) for symbol in symbols if isinstance(rows.get(symbol), dict)]
    comparisons = [row.get("primary_comparison") or {} for row in selected]
    aligned = sum(item.get("state") == "MATCH" for item in comparisons)
    divergent = sum(item.get("state") == "PRICE_DIVERGENCE" for item in comparisons)
    incomplete = sum(item.get("state") == "BACKGROUND_COMPARISON_INCOMPLETE" for item in comparisons)
    complemented = sum(item.get("contract_state") == "READY_SHADOW_COMPLEMENTED" for item in comparisons)
    contract_states = [item.get("contract_state", "INSUFFICIENT_EVIDENCE") for item in comparisons]
    ready_shadow = sum(state.startswith("READY_SHADOW") for state in contract_states)
    blocked = sum(state.startswith("BLOCKED") for state in contract_states)
    return {"schema_version": SCHEMA_VERSION,
            "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY",
            "scope_count": len(symbols), "observed_count": len(selected),
            "price_aligned": aligned, "price_divergent": divergent,
            "comparison_incomplete": incomplete, "complemented": complemented,
            "ready_shadow": ready_shadow, "blocked": blocked,
            "primary_contract": primary_contract or {},
            "decision_effect": "OBSERVE_ONLY", "real_money_authorized": False}
