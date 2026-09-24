"""RC6 family/instrument readiness contract.

PPI is primary and IOL is complementary. This module only evaluates published
read-only evidence and exposes a PAPER/SHADOW promotion decision. It cannot
authorize real orders or remove the permanent real-money block.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

# All of these states are simulation-ready. They never authorize real money.
READY_STATES = frozenset({
    "READY_PAPER", "READY_PAPER_SHADOW",
    "READY_SHADOW", "READY_SHADOW_COMPLEMENTED", "READY_SHADOW_PARTIAL",
})
# Ambos estados son simulables en modo PAPER; ninguno autoriza dinero real.
PAPER_SIMULATABLE_STATES = frozenset({
    "READY_PAPER", "READY_PAPER_SHADOW",
    "READY_SHADOW", "READY_SHADOW_COMPLEMENTED", "READY_SHADOW_PARTIAL",
})
BLOCKED_PREFIX = "BLOCKED"

FAMILY_ALIASES = {
    "ACCIONES": "ACCIONES",
    "STOCKS": "ACCIONES",
    "CEDEARS": "CEDEARS",
    "CEDEAR": "CEDEARS",
    "BONOS": "BONOS",
    "ON": "ON",
    "OBLIGACIONES": "ON",
    "OBLIGACIONES_NEGOCIABLES": "ON",
    "CAUCIONES": "CAUCIONES",
    "CAUCION": "CAUCIONES",
    "FUTUROS": "FUTUROS",
    "OPCIONES": "OPCIONES",
    "ETFS": "ETFS",
    "ETF": "ETFS",
    "FCI": "FCI",
}

def normalize_family(value: Any) -> str:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return FAMILY_ALIASES.get(text, text or "UNKNOWN")

def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback

def _row_family(row: Mapping[str, Any], catalog_family: Any = None) -> str:
    value = catalog_family or row.get("family") or row.get("asset_type") or row.get("instrument_type")
    return normalize_family(value)

def _comparison(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("primary_comparison")
    return dict(value) if isinstance(value, Mapping) else {}

def _reason(row: Mapping[str, Any], contract: str) -> list[str]:
    comparison = _comparison(row)
    reasons: list[str] = []
    if not comparison:
        reasons.append("PPI_IOL_COMPARISON_NOT_PUBLISHED")
    if contract == "BLOCKED_STALE":
        reasons.append("SOURCE_OLDER_THAN_120_SECONDS")
    if contract == "BLOCKED_CONFLICT":
        reasons.append("PPI_IOL_CRITICAL_CONFLICT")
    if contract in {"INSUFFICIENT_EVIDENCE", "UNKNOWN"}:
        reasons.append("CRITICAL_FIELDS_OR_TIMESTAMP_MISSING")
    missing = comparison.get("missing")
    if isinstance(missing, (int, float)):
        if missing:
            reasons.append(f"MISSING_COMPARABLE_FIELDS:{int(missing)}")
    elif missing:
        reasons.extend(f"MISSING_COMPARABLE_FIELD:{field}" for field in missing)
    fields = comparison.get("fields") or {}
    if isinstance(fields, Mapping):
        missing_fields = [field for field, detail in fields.items()
                          if isinstance(detail, Mapping)
                          and str(detail.get("state", "")).upper() in {
                              "MISSING_COMPARABLE_FIELD", "NOT_AVAILABLE_BOTH_SIDES"}]
        for field in missing_fields:
            reason = f"MISSING_COMPARABLE_FIELD:{field}"
            if reason not in reasons:
                reasons.append(reason)
    complemented = comparison.get("complemented")
    if complemented:
        reasons.append(f"IOL_COMPLEMENTS_NONCRITICAL_FIELDS:{complemented}")
    if not reasons and contract in PAPER_SIMULATABLE_STATES:
        reasons.append("NO_OPEN_READINESS_GAP")
    return reasons


def _effective_contract(comparison: Mapping[str, Any]) -> str:
    """Never expose READY when freshness or comparison evidence is incomplete."""
    state = _text(comparison.get("contract_state"), "INSUFFICIENT_EVIDENCE").upper()
    freshness = comparison.get("freshness") or {}
    if state in READY_STATES:
        if not comparison:
            return "INSUFFICIENT_EVIDENCE"
        if any(
            (str(item.get("state", "").upper()) if isinstance(item, Mapping) else str(item).upper())
            in {"STALE", "UNKNOWN"}
            for item in freshness.values()
        ):
            return "INSUFFICIENT_EVIDENCE"
        if comparison.get("background_comparison_complete") is False:
            return "INSUFFICIENT_EVIDENCE"
        if comparison.get("comparison_complete") is False:
            return "INSUFFICIENT_EVIDENCE"
    return state

def _key(family: str, symbol: Any, market: Any = "", settlement: Any = "") -> tuple[str, str, str, str]:
    return (normalize_family(family), _text(symbol).upper(), _text(market).upper(), _text(settlement).upper())

def evaluate(catalog: Iterable[Mapping[str, Any]] = (), iol_rows: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Return family summaries and per-instrument PAPER promotion decisions.

    A missing IOL row is explicit pending evidence, never treated as green.
    The current operational universe is not reduced when complementary data is
    absent; this is an expansion/readiness layer for automatic PAPER promotion.
    """
    catalog_rows = [dict(row) for row in catalog if isinstance(row, Mapping)]
    observed = [dict(row) for row in iol_rows if isinstance(row, Mapping)]
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observed:
        symbol = _text(row.get("symbol")).upper()
        if symbol:
            by_symbol[symbol].append(row)

    instruments: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in catalog_rows:
        family = normalize_family(item.get("family") or item.get("instrument_type"))
        symbol = _text(item.get("symbol")).upper()
        if not symbol:
            continue
        key = _key(family, symbol, item.get("market"), item.get("settlement"))
        if key in seen:
            continue
        seen.add(key)
        matches = by_symbol.get(symbol, [])
        row = next((candidate for candidate in matches
                    if not item.get("market") or not candidate.get("market")
                    or _text(item.get("market")).upper() == _text(candidate.get("market")).upper()), None)
        if row is None:
            contract = "INSUFFICIENT_EVIDENCE"
            comparison = {}
            source_row = {}
        else:
            source_row = row
            comparison = _comparison(row)
            contract = _effective_contract(comparison)
        paper_enabled = contract in PAPER_SIMULATABLE_STATES
        instruments.append({
            "family": family,
            "symbol": symbol,
            "market": _text(item.get("market") or (source_row.get("market") if source_row else "")),
            "settlement": _text(item.get("settlement") or (source_row.get("term") if source_row else "")),
            "contract_state": contract,
            "paper_auto_enabled": paper_enabled,
            "real_money_authorized": False,
            "reasons": _reason(source_row, contract),
            "comparison": comparison,
        })

    # Include observed symbols that are not in the historical catalog so they
    # remain visible rather than silently disappearing.
    catalog_symbols = {item["symbol"] for item in instruments}
    for row in observed:
        symbol = _text(row.get("symbol")).upper()
        if not symbol or symbol in catalog_symbols:
            continue
        family = _row_family(row)
        comparison = _comparison(row)
        contract = _text(comparison.get("contract_state"), "INSUFFICIENT_EVIDENCE").upper()
        instruments.append({
            "family": family, "symbol": symbol, "market": _text(row.get("market")),
            "settlement": _text(row.get("term") or row.get("settlement")),
            "contract_state": contract, "paper_auto_enabled": contract in PAPER_SIMULATABLE_STATES,
            "real_money_authorized": False, "reasons": _reason(row, contract),
            "comparison": comparison,
        })

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in instruments:
        grouped[item["family"]].append(item)
    families: list[dict[str, Any]] = []
    for family in sorted(grouped):
        rows = grouped[family]
        ready = sum(item["paper_auto_enabled"] for item in rows)
        blocked = sum(item["contract_state"].startswith(BLOCKED_PREFIX) for item in rows)
        pending = len(rows) - ready - blocked
        if ready == len(rows) and rows:
            state = "READY"
        elif blocked:
            state = "BLOCKED"
        elif ready:
            state = "PARTIAL"
        else:
            state = "PENDING"
        gaps: list[str] = []
        for item in rows:
            for reason in item["reasons"]:
                if reason not in gaps:
                    gaps.append(reason)
        families.append({
            "family": family, "state": state, "instrument_count": len(rows),
            "paper_auto_enabled": ready, "blocked": blocked, "pending": pending,
            "real_money_authorized": False, "gaps": gaps[:8],
            "next_action": ("Mantener y operar en PAPER; no hay brechas abiertas."
                            if state == "READY" else
                            "Resolver: " + ", ".join(gaps[:3]) if gaps else
                            "Publicar capturas PPI/IOL comparables."),
        })
    return {
        "schema_version": 1,
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY",
        "paper_auto_promotion": True,
        "paper_simulatable_states": sorted(PAPER_SIMULATABLE_STATES),
        "real_money_authorized": False,
        "families": families,
        "instruments": sorted(instruments, key=lambda item: (item["family"], item["symbol"])),
    }
