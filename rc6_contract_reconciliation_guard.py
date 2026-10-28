"""Pure reconciliation guards. No network, database, broker or order capability.

A historical internal review is not a permanent PPI veto. Recovery requires
an exact, current PPI identity and a complete normalized V2 contract. Material
changes, contradictory evidence and genuine provider negatives stay closed.
Legacy ON aliases can be distinguished only with strong matching identity;
this is not a rule to choose the first row or promote both aliases.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re

from bs_instrument_contracts import contract_from_metadata

HARD_PRIMARY_BLOCKS = frozenset({
    "TYPE_NOT_ENUMERATED", "MARKET_NOT_ENUMERATED", "UNSUPPORTED_FAMILY",
    "CONTRACT_SOURCE_CONFLICT",
})
IDENTITY_FIELDS = ("ticker", "instrument_type", "market", "currency", "settlement")
PPI_IDENTITY_MAX_AGE_SECONDS = 14 * 86400
UNKNOWN = frozenset({"", "UNKNOWN", "NO_VERIFICADO", "NONE", "*"})


def _raw(row):
    value = row.get("raw")
    if not isinstance(value, dict):
        value = row.get("metadata_json")
        try:
            value = json.loads(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _upper(value):
    return str(value or "").strip().upper()


def _key(row):
    return tuple(_upper(row.get(field)) for field in IDENTITY_FIELDS)


def _current_primary(row, checked_at):
    raw = _raw(row)
    if (row.get("status") != "AVAILABLE"
            or _upper(raw.get("_discovery_source")) != "PPI_PRIMARY"
            or row.get("capability") in HARD_PRIMARY_BLOCKS
            or any(raw.get(field) is False for field in ("operable", "isActive", "isTradable"))
            or any(value in UNKNOWN for value in _key(row))):
        return False
    try:
        observed = datetime.fromisoformat(str(row["last_seen_at"]).replace("Z", "+00:00"))
        checked = (checked_at if isinstance(checked_at, datetime) else
                   datetime.fromisoformat(str(checked_at).replace("Z", "+00:00")))
        if observed.tzinfo is None or checked.tzinfo is None:
            return False
        age = (checked - observed).total_seconds()
        return 0 <= age <= PPI_IDENTITY_MAX_AGE_SECONDS
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _normalized_exact_contract(primary, complement):
    bridge = complement.get("contract_bridge")
    contract = complement.get("financial_contract_v17")
    explicit = complement.get("identity_evidence")
    if (complement.get("source") != "CONTRACT_EVIDENCE_V2"
            or not isinstance(bridge, dict)
            or bridge.get("status") != "NORMALIZED"
            or bridge.get("gaps") != []
            or not isinstance(contract, dict)
            or contract.get("metadata_source") != "CONTRACT_EVIDENCE_V2_BOUND"
            or not isinstance(explicit, dict)
            or any(explicit.get(field) is not True for field in
                   ("market_explicit", "currency_explicit", "settlement_explicit"))
            or _key(primary) != _key(complement)):
        return False
    provenance = contract.get("field_provenance")
    if not isinstance(provenance, dict) or not provenance:
        return False
    # The canonical bridge validates the source and material-change history.
    # This additional boundary rejects fabricated/empty proof and malformed
    # contracts rather than trusting a NORMALIZED label alone.
    for proof in provenance.values():
        if (not isinstance(proof, dict) or not proof.get("source_class")
                or not proof.get("source_ref") or not proof.get("observed_at")
                or re.fullmatch(r"[0-9a-f]{64}", str(proof.get("evidence_hash") or "")) is None):
            return False
    try:
        spec = contract_from_metadata(primary["ticker"], primary["instrument_type"], contract)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False
    return spec.key == _key(primary)


def reconciliation_allowed(primary, complement, *, checked_at=None):
    """Keep hard vetoes; re-evaluate an old review only from canonical V2.

    This grants no trading permission. Existing conflict comparison, catalog
    capability, candidate identity and dynamic execution gates still apply.
    """
    capability = str(primary.get("capability") or "")
    if capability in HARD_PRIMARY_BLOCKS:
        return False
    if capability != "CONTRACT_EVIDENCE_REVIEW_REQUIRED":
        return True
    checked = checked_at or datetime.now(timezone.utc)
    return (_current_primary(primary, checked)
            and not _raw(primary).get("_contract_conflicts")
            and _normalized_exact_contract(primary, complement))


def select_verified_primary(matches, complement, *, checked_at=None):
    """Resolve exactly one current PPI OBLIGACIONES + one obsolete ON alias.

    Both identity records remain stored. Only the proven current identity may
    receive a complement. Anything else returns every match to the existing
    ambiguity gate: different ISIN/currency/market/settlement, two live records,
    missing proof, unknown alias, explicit negative, or conflicting quote basis.
    """
    if len(matches) != 2:
        return matches
    canonical = [r for r in matches if r.get("instrument_type") == "OBLIGACIONES"]
    legacy = [r for r in matches if r.get("instrument_type") == "ON"]
    if len(canonical) != 1 or len(legacy) != 1:
        return matches
    primary, old = canonical[0], legacy[0]
    checked = checked_at or datetime.now(timezone.utc)
    if (not _current_primary(primary, checked) or old.get("status") != "STALE"
            or old.get("capability") in HARD_PRIMARY_BLOCKS
            or not _normalized_exact_contract(primary, complement)):
        return matches
    # Compare the other four identity dimensions literally, not by ticker
    # parsing, currency guessing or venue aliases.
    if any(_upper(primary.get(k)) != _upper(old.get(k))
           for k in ("ticker", "market", "currency", "settlement")):
        return matches
    p_raw, o_raw = _raw(primary), _raw(old)
    isin = _upper(p_raw.get("isin"))
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin) is None or isin != _upper(o_raw.get("isin")):
        return matches
    for raw in (p_raw, o_raw):
        if raw.get("_contract_conflicts") or any(raw.get(k) is False for k in ("operable", "isActive", "isTradable")):
            return matches
    if any(p_raw.get(k) not in (None, "") and o_raw.get(k) not in (None, "")
           and str(p_raw[k]) != str(o_raw[k]) for k in ("cajaValoresCode",)):
        return matches
    try:
        p_basis = Decimal(str(p_raw["nominalInPrice"]))
        o_basis = Decimal(str(o_raw["nominalInPrice"]))
        if not p_basis.is_finite() or p_basis <= 0 or p_basis != o_basis:
            return matches
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return matches
    return [primary]
