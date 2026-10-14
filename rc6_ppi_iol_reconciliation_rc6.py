"""Cascada PPI -> IOL -> BYMA para evidencia y promoción SHADOW RC6.

La prioridad conserva la trazabilidad: PPI aporta primero, IOL completa sólo
ausencias y BYMA completa el remanente público. Una coincidencia consistente
puede habilitar PAPER/SHADOW aun si PPI no entregó un campo. Nunca autoriza una
orden real ni cambia rutas de broker.
"""
from __future__ import annotations
from datetime import datetime, timezone
import math
from typing import Any
from rc6_shadow_runtime.source_authority import (VERSION as AUTHORITY_VERSION, authority, native_time, receipt_time, source_rank)
from rc6_dynamic_universe.sources import FAMILIES, MARKETS, TERMS, _canonical_identity

SCHEMA_VERSION = 2
FIELDS = ("last", "bid", "ask", "bid_size", "ask_size", "spread_pct",
          "variation_pct", "cash_volume", "volume", "vwap")
CRITICAL_FIELDS = frozenset(("last", "bid", "ask"))
OPTIONAL_FIELDS = frozenset(("variation_pct", "cash_volume"))
METADATA_FIELDS = ("market", "settlement", "currency", "units_per_lot", "asset_type")
DEFAULT_TOLERANCE_PCT = 2.0
MAX_AGE_SECONDS = 120.0

def _num(value: Any) -> float | None:
    try:
        parsed = float(value) if value not in (None, "") and not isinstance(value, bool) else None
        return parsed if parsed is not None and math.isfinite(parsed) else None
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

def reconcile(primary: Any, secondary: Any, public: Any | None = None, *, now: datetime | None = None,
              tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
              primary_source: str = "PPI", secondary_source: str = "IOL",
              public_source: str = "BYMA") -> dict[str, Any]:
    p, s = _with_derived_quote(primary), _with_derived_quote(secondary)
    b = _with_derived_quote(public or {})
    compared: dict[str, Any] = {}
    effective: dict[str, Any] = {}
    matches = divergences = missing = complemented = 0
    for field in FIELDS:
        values = [(primary_source, _num(p.get(field))), (secondary_source, _num(s.get(field))),
                  (public_source, _num(b.get(field)))]
        present = [(source, value) for source, value in values if value is not None]
        chosen_source, chosen_value = present[0] if present else (None, None)
        effective[field] = {"value": chosen_value, "source": chosen_source}
        disagreements = []
        for index, (left_source, left_value) in enumerate(present):
            for right_source, right_value in present[index + 1:]:
                diff = abs(left_value - right_value) / abs(left_value) * 100 if left_value else (0.0 if right_value == 0 else None)
                if diff is not None and diff > tolerance_pct:
                    disagreements.append({"left": left_source, "right": right_source, "difference_pct": diff})
        if not present:
            state = "MISSING_ALL_SOURCES"
            if field not in OPTIONAL_FIELDS:
                missing += 1
        elif disagreements:
            state = "DIVERGENCE"
            divergences += 1
        elif chosen_source != primary_source:
            state = "COMPLEMENTED_" + chosen_source
            complemented += 1
        elif len(present) > 1:
            state = "MATCH"
            matches += 1
        else:
            state = "PPI_ONLY"
        compared[field] = {"state": state, "primary": values[0][1], "secondary": values[1][1],
                           "public": values[2][1], "effective": chosen_value,
                           "effective_source": chosen_source, "disagreements": disagreements}
    for field in METADATA_FIELDS:
        pv, sv = p.get(field), s.get(field)
        if pv in (None, "") or sv in (None, ""):
            continue
        aliases = MARKETS if field == "market" else TERMS if field == "settlement" else FAMILIES if field == "asset_type" else {}
        left, right = str(pv).strip().upper(), str(sv).strip().upper()
        state = "MATCH" if aliases.get(left, left) == aliases.get(right, right) else "DIVERGENCE"
        compared[field] = {"state": state, "primary": pv, "secondary": sv}
        divergences += state == "DIVERGENCE"
        matches += state == "MATCH"
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ages = {}
    ptime = _utc(native_time(p))
    stime = _utc(native_time(s))
    for name, value in ((primary_source, ptime), (secondary_source, stime)):
        ages[name] = (ref - value).total_seconds() if value else None
    freshness = {name: ("FRESH" if age is not None and 0 <= age <= MAX_AGE_SECONDS
                        else "STALE" if age is not None else "UNKNOWN")
                 for name, age in ages.items()}
    btime = _utc(native_time(b))
    ages[public_source] = (ref - btime).total_seconds() if btime else None
    freshness[public_source] = ("FRESH" if ages[public_source] is not None and 0 <= ages[public_source] <= MAX_AGE_SECONDS
                                else "STALE" if ages[public_source] is not None else "UNKNOWN")
    active_sources = {item["source"] for item in effective.values() if item["source"]}
    if any(freshness[source] == "STALE" for source in active_sources):
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
    state = "MATCH" if last_state in {"MATCH", "PPI_ONLY", "COMPLEMENTED_IOL", "COMPLEMENTED_BYMA"} else (
        "PRICE_DIVERGENCE" if last_state == "DIVERGENCE"
        else "BACKGROUND_COMPARISON_INCOMPLETE")
    raw_sources = {primary_source: p, secondary_source: s, public_source: b}
    identity_fields = ("ticker", "family", "market", "currency", "settlement")
    def explicit_identity(value):
        try:
            return tuple(_canonical_identity(value))
        except (ValueError, TypeError):
            return (None,)*5
    primary_identity = explicit_identity(p)
    primary_origin = p.get("source") or primary_source
    primary_identity_authoritative = all(primary_identity) and source_rank(primary_origin) == 0
    source_reviews = ([] if source_rank(primary_origin) == 0 else [{
        "source": primary_origin, "comparison_source": primary_source,
        "reason": "PRIMARY_SOURCE_AUTHORITY_NOT_PROVEN"}])
    field_provenance = {}
    for field, selected in effective.items():
        source = selected["source"]
        raw = raw_sources.get(source, {})
        origin = raw.get("source") or source
        book_field = field in {"bid", "ask", "bid_size", "ask_size", "spread_pct"}
        native = _utc(raw.get("book_at") or raw.get("provider_book_at")) if book_field else None
        if not book_field or source != primary_source:
            native = native or _utc(native_time(raw))
        received = _utc(receipt_time(raw))
        field_provenance[field] = {"source": origin, "comparison_source": source,
            "source_path": raw.get("source_path") or origin, "authority": authority(origin),
            "source_at": native.isoformat() if native else None, "received_at": received.isoformat() if received else None,
            "clock_basis": "PROVIDER_EVENT_TIME", "receipt_is_provider_time": False,
            "freshness": "FRESH" if native and received and native <= received <= ref
                and 0 <= (ref-native).total_seconds() <= MAX_AGE_SECONDS
                and str(raw.get("state") or "READY").upper() in {"READY", "LIVE_FRESH", "FRESH", "OBSERVE_ONLY", "AVAILABLE"}
                else "NO_VERIFICADO",
            "comparison_only": True, "entry_authority": False}
    identity_conflicts = []
    if all(primary_identity):
        for source, raw in raw_sources.items():
            other = explicit_identity(raw)
            if source != primary_source and any(raw.get(field) is not None for field in FIELDS) and (not all(other) or other != primary_identity):
                identity_conflicts.append({"source": source, "primary": list(primary_identity), "complement": list(other),
                                           "reason": "IDENTITY_CONFLICT_REVIEW_REQUIRED" if all(other) else "EXACT_IDENTITY_REQUIRED"})
    if identity_conflicts:
        contract_state = "BLOCKED_CONFLICT"
        rejected_sources = {item["source"] for item in identity_conflicts}
        for field, item in effective.items():
            if item["source"] in rejected_sources:
                effective[field] = {"value": None, "source": None}
                field_provenance[field]["rejection_reason"] = "IDENTITY_REVIEW_REQUIRED"
                field_provenance[field]["freshness"] = "NO_VERIFICADO"
                compared[field].update(effective=None, effective_source=None)
    selection_eligible = (primary_identity_authoritative and not identity_conflicts and not divergences
                          and all(effective[field]["value"] is not None and field_provenance[field]["freshness"] == "FRESH"
                                  and effective[field]["value"] > 0 for field in CRITICAL_FIELDS)
                          and effective["ask"]["value"] >= effective["bid"]["value"])
    return {"schema_version": SCHEMA_VERSION, "state": state,
            "contract_state": contract_state, "primary_source": primary_source,
            "secondary_source": secondary_source, "public_source": public_source, "matches": matches,
            "divergences": divergences, "missing": missing,
            "complemented": complemented, "freshness": freshness,
            "source_authority_policy": AUTHORITY_VERSION, "field_provenance": field_provenance,
            "identity_primary": dict(zip(identity_fields, primary_identity)) if primary_identity_authoritative else None,
            "identity_binding": "EXPLICIT_PRIMARY_IDENTITY" if primary_identity_authoritative else "REFERENCE_COMPARISON_ONLY",
            "identity_conflicts": identity_conflicts, "identity_overwritten": False,
            "source_authority_reviews": source_reviews,
            "review_status": "CONFLICT_REVIEW_REQUIRED" if divergences or identity_conflicts or source_reviews else "NO_CURRENT_CONFLICT",
            "provider_available": {primary_source: None, secondary_source: None, public_source: None},
            "entry_authority": False, "selection_eligible": bool(selection_eligible),
            "fields": compared, "effective_fields": effective, "decision_effect": "OBSERVE_ONLY",
            "shadow_promotion": contract_state.startswith("READY_SHADOW"), "live_decision_authority": False, "real_money_authorized": False}

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
