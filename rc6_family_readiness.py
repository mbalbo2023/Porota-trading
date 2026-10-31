"""RC6 family/instrument source and contract readiness contract.

PPI is primary and IOL is complementary. This module only evaluates published
read-only evidence. Source readiness never grants an automated entry signal or
PAPER admission, and cannot authorize real orders.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping
from rc6_multisource_discovery import canonical_market, canonical_settlement
from rc6_shadow_runtime.source_authority import source_rank

# All of these states are simulation-ready. They never authorize real money.
READY_STATES = frozenset({
    "READY_PAPER", "READY_PAPER_SHADOW", "READY_PAPER_PARTIAL_SOURCE",
    "READY_SHADOW", "READY_SHADOW_COMPLEMENTED", "READY_SHADOW_PARTIAL",
})
# SHADOW readiness is evidence readiness, not execution readiness.  PAPER
# promotion requires an explicit PAPER state and a family executor/contract.
PAPER_SIMULATABLE_STATES = frozenset({
    "READY_PAPER", "READY_PAPER_SHADOW", "READY_PAPER_PARTIAL_SOURCE",
})
BLOCKED_PREFIX = "BLOCKED"

FAMILY_ALIASES = {
    "ACCIONES": "ACCIONES",
    "STOCKS": "ACCIONES",
    "CEDEARS": "CEDEARS",
    "CEDEAR": "CEDEARS",
    "BONOS": "BONOS",
    "ON": "OBLIGACIONES",
    "OBLIGACIONES": "OBLIGACIONES",
    "OBLIGACIONES_NEGOCIABLES": "OBLIGACIONES",
    "CAUCIONES": "CAUCIONES",
    "CAUCION": "CAUCIONES",
    "FUTUROS": "FUTUROS",
    "OPCIONES": "OPCIONES",
    "ETFS": "ETFS",
    "ETF": "ETFS",
    "FCI": "FCI",
    "LETRAS": "LETRAS",
    "LEBAC": "LEBAC",
    "NOBAC": "NOBAC",
    "LICITACIONES": "LICITACIONES",
    "FONDOS_COMUNES": "FCI",
}

# Count execution families, exploratory namespaces and legacy source contexts
# separately.  Keeping the latter visible preserves historical discoveries;
# it must not silently give them a new autonomous PAPER engine.
CANONICAL_PAPER_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
})
EXPLORATORY_FAMILIES = frozenset({"INDICES", "ACCIONES_USA", "FCI_EXTERIOR"})
SOURCE_CONTEXT_PARENTS = {
    "LEBAC": "LETRAS", "NOBAC": "LETRAS", "FCI_LOCAL": "FCI",
    "LICITACIONES": None, "CANJES": None,
}
READINESS_FAMILIES = frozenset(CANONICAL_PAPER_FAMILIES | EXPLORATORY_FAMILIES |
                               SOURCE_CONTEXT_PARENTS.keys())


def family_role(family):
    family = normalize_family(family)
    if family in CANONICAL_PAPER_FAMILIES:
        return "CANONICAL_PAPER_FAMILY", family
    if family == "INDICES":
        return "BENCHMARK_SHADOW_ONLY", None
    if family in EXPLORATORY_FAMILIES:
        return "CROSS_BORDER_DISCOVERY_SHADOW", None
    if family in SOURCE_CONTEXT_PARENTS:
        return "SOURCE_SUBTYPE_OR_CONTEXT", SOURCE_CONTEXT_PARENTS[family]
    return "UNCLASSIFIED_FAIL_CLOSED", None

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


def _freshness_state(value: Any) -> str:
    """Accept the producer's string and an explicitly published state object."""
    if isinstance(value, Mapping):
        value = value.get("state")
    return _text(value).upper()


def _primary_readiness_gaps(comparison: Mapping[str, Any]) -> list[str]:
    """Require positive, field-level PPI evidence before displaying READY.

    The real reconciliation producer publishes ``selection_eligible`` and
    ``field_provenance`` rather than ``comparison_complete``. Either explicit
    positive completeness signal is accepted, but never a mere READY label.
    IOL/BYMA freshness is complementary: its outage cannot invalidate a
    verified PPI-only critical book.
    """
    gaps: list[str] = []
    freshness = comparison.get("freshness")
    if not isinstance(freshness, Mapping) or _freshness_state(freshness.get("PPI")) != "FRESH":
        gaps.append("PPI_PRIMARY_FRESHNESS_NOT_VERIFIED")
    if _text(comparison.get("primary_source")).upper() != "PPI" or (
            _text(comparison.get("identity_binding")).upper() != "EXPLICIT_PRIMARY_IDENTITY"):
        gaps.append("PPI_PRIMARY_IDENTITY_NOT_VERIFIED")
    if (comparison.get("comparison_complete") is False or
            comparison.get("background_comparison_complete") is False or
            comparison.get("selection_eligible") is False or
            (comparison.get("comparison_complete") is not True and
             comparison.get("selection_eligible") is not True)):
        gaps.append("PRIMARY_COMPARISON_COMPLETENESS_NOT_VERIFIED")
    if (comparison.get("review_status") not in (None, "NO_CURRENT_CONFLICT") or
            comparison.get("identity_conflicts") or comparison.get("invalid_numeric_fields") or
            comparison.get("divergences", 0)):
        gaps.append("PRIMARY_COMPARISON_CONFLICT_REVIEW_REQUIRED")
    provenance = comparison.get("field_provenance")
    for field in ("last", "bid", "ask"):
        item = provenance.get(field) if isinstance(provenance, Mapping) else None
        if (not isinstance(item, Mapping) or
                source_rank(item.get("source")) != 0 or
                _freshness_state(item.get("freshness")) != "FRESH"):
            gaps.append(f"PPI_CRITICAL_FIELD_NOT_VERIFIED:{field}")
    return gaps

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
        if _text(comparison.get("contract_state")).upper() in READY_STATES:
            reasons.extend(_primary_readiness_gaps(comparison))
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
        reasons.append("NO_OPEN_SOURCE_CONTRACT_GAP")
    return reasons


def _effective_contract(comparison: Mapping[str, Any]) -> str:
    """Never expose READY when freshness or comparison evidence is incomplete."""
    state = _text(comparison.get("contract_state"), "INSUFFICIENT_EVIDENCE").upper()
    if state in READY_STATES and _primary_readiness_gaps(comparison):
        return "INSUFFICIENT_EVIDENCE"
    return state

def _key(family: str, symbol: Any, market: Any = "", currency: Any = "",
         settlement: Any = "") -> tuple[str, str, str, str, str]:
    # Normalize only supplied aliases. The helper's family default for an
    # absent settlement must never fabricate a missing identity field.
    term = _text(settlement)
    return (normalize_family(family), _text(symbol).upper(),
            canonical_market(market) if _text(market) else "",
            _text(currency).upper(), canonical_settlement(term) if term else "")


def _observed_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, str] | None:
    """Never promote a ticker-only complementary row to a catalog identity."""
    key = _key(_row_family(row), row.get("symbol"), row.get("market"), row.get("currency"),
               row.get("settlement") or row.get("term"))
    return key if all(key) else None

def evaluate(catalog: Iterable[Mapping[str, Any]] = (), iol_rows: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Return source/contract summaries; no automated PAPER promotion.

    A missing IOL row is explicit pending evidence, never treated as green.
    The current operational universe is not reduced when complementary data is
    absent; this layer has no native signal, economics or admission evidence.
    """
    catalog_rows = [dict(row) for row in catalog if isinstance(row, Mapping)]
    observed = [dict(row) for row in iol_rows if isinstance(row, Mapping)]
    by_identity: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in observed:
        key = _observed_key(row)
        if key:
            by_identity[key].append(row)

    catalog_identity_counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for item in catalog_rows:
        symbol = _text(item.get("symbol")).upper()
        if symbol:
            key = _key(item.get("family") or item.get("instrument_type"), symbol,
                       item.get("market"), item.get("currency"), item.get("settlement"))
            catalog_identity_counts[key] += 1

    instruments: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for item in catalog_rows:
        family = normalize_family(item.get("family") or item.get("instrument_type"))
        symbol = _text(item.get("symbol")).upper()
        if not symbol:
            continue
        key = _key(family, symbol, item.get("market"), item.get("currency"), item.get("settlement"))
        if key in seen:
            continue
        seen.add(key)
        matches = by_identity.get(key, []) if all(key) else []
        # Duplicate exact identities are review evidence, not a first-row
        # authority. The caller can reconcile them before a future evaluation.
        row = matches[0] if len(matches) == 1 else None
        if row is None:
            contract = "BLOCKED_CONFLICT" if len(matches) > 1 else "INSUFFICIENT_EVIDENCE"
            comparison = {}
            source_row = {}
        else:
            source_row = row
            comparison = _comparison(row)
            contract = _effective_contract(comparison)
        duplicate_catalog = catalog_identity_counts[key] > 1
        if duplicate_catalog:
            contract = "BLOCKED_CONFLICT"
        contract_ready = (contract in PAPER_SIMULATABLE_STATES and
                          family in CANONICAL_PAPER_FAMILIES)
        reasons = (["DUPLICATE_CATALOG_EXACT_IDENTITY_REVIEW_REQUIRED"]
                   if duplicate_catalog else _reason(source_row, contract))
        if len(matches) > 1:
            reasons.append("DUPLICATE_EXACT_IDENTITY_REVIEW_REQUIRED")
        if contract_ready:
            reasons.append("ENTRY_SIGNAL_ECONOMICS_AND_ADMISSION_NOT_EVALUATED")
        else:
            if family not in CANONICAL_PAPER_FAMILIES:
                reasons.append("NO_INDEPENDENT_PAPER_ENGINE_FOR_NAMESPACE")
        role, parent = family_role(family)
        instruments.append({
            "family": family,
            "family_role": role, "parent_paper_family": parent,
            "symbol": symbol,
            "market": _text(item.get("market") or (source_row.get("market") if source_row else "")),
            "currency": _text(item.get("currency")),
            "settlement": _text(item.get("settlement") or (source_row.get("term") if source_row else "")),
            "contract_state": contract,
            "paper_contract_ready": contract_ready,
            "paper_auto_enabled": False,
            "entry_authority": False,
            "real_money_authorized": False,
            "reasons": reasons,
            "comparison": comparison,
        })

    # Include observed symbols that are not in the historical catalog so they
    # remain visible rather than silently disappearing.
    catalog_keys = {_key(item["family"], item["symbol"], item["market"], item["currency"], item["settlement"])
                    for item in instruments}
    for row in observed:
        symbol = _text(row.get("symbol")).upper()
        if not symbol or (_observed_key(row) in catalog_keys):
            continue
        family = _row_family(row)
        role, parent = family_role(family)
        comparison = _comparison(row)
        contract = _effective_contract(comparison)
        instruments.append({
            "family": family, "family_role": role, "parent_paper_family": parent,
            "symbol": symbol, "market": _text(row.get("market")),
            "currency": _text(row.get("currency")),
            "settlement": _text(row.get("term") or row.get("settlement")),
            "contract_state": contract, "paper_contract_ready": False,
            "paper_auto_enabled": False, "entry_authority": False,
            "real_money_authorized": False, "reasons": _reason(row, contract) + ["PPI_CATALOG_IDENTITY_NOT_VERIFIED"],
            "comparison": comparison,
        })

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # La ausencia de captura no elimina una familia del tablero: queda PENDING
    # con el motivo exacto, sin habilitar PAPER por defecto.
    for family in READINESS_FAMILIES:
        grouped[family]
    for item in instruments:
        grouped[item["family"]].append(item)
    families: list[dict[str, Any]] = []
    for family in sorted(grouped):
        role, parent = family_role(family)
        rows = grouped[family]
        ready = sum(item["paper_contract_ready"] for item in rows)
        blocked = sum(item["contract_state"].startswith(BLOCKED_PREFIX) for item in rows)
        pending = len(rows) - ready - blocked
        if ready == len(rows) and rows:
            state = "READY_CONTRACT_ONLY"
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
            "family": family, "family_role": role, "parent_paper_family": parent,
            "state": state, "instrument_count": len(rows),
            "paper_contract_ready": ready, "paper_auto_enabled": 0,
            "entry_authority": False, "blocked": blocked, "pending": pending,
            "real_money_authorized": False, "gaps": gaps[:8],
            "next_action": ("Evaluar señal, costos, riesgo y admisión PAPER por instrumento."
                            if state == "READY_CONTRACT_ONLY" else
                            "Resolver: " + ", ".join(gaps[:3]) if gaps else
                            "Publicar capturas PPI/IOL comparables."),
        })
    return {
        "schema_version": 2,
        "canonical_paper_families": sorted(CANONICAL_PAPER_FAMILIES),
        "exploratory_shadow_families": sorted(EXPLORATORY_FAMILIES),
        "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY_BYMA_PUBLIC_COMPLEMENTARY",
        "paper_auto_promotion": False,
        "paper_simulatable_states": sorted(PAPER_SIMULATABLE_STATES),
        "real_money_authorized": False,
        "families": families,
        "instruments": sorted(instruments, key=lambda item: (item["family"], item["symbol"])),
    }
