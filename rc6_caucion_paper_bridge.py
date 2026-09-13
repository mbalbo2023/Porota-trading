"""RC6 fail-closed bridge from validated caucion evidence to the existing PAPER sweep.

This module does not fetch PPI data, does not scrape, does not persist contract
facts and cannot route real orders. It only accepts already validated canonical
snapshots plus an explicit GREEN family gate and then delegates to the existing
PAPER cash-sweep runtime.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from di_caucion_cash_sweep_runtime_hf6 import (
    CASH_SWEEP_ORDER_ROUTING_ALLOWED,
    ObligationSnapshot,
    run_paper_sweep,
)
from rc6_caucion_offer_adapter import (
    CaucionOfferAdapterError,
    offer_from_canonical_snapshot,
)

PAPER_BRIDGE_ORDER_ROUTING_ALLOWED = False
FRESHNESS_GATE_NAME = "CAUCION_FRESH_DATA_AGENT_GREEN"


class CaucionPaperBridgeError(ValueError):
    pass


def _hold(code: str, *, detail: str = "", offers: int = 0) -> dict:
    return {
        "status": "HOLD",
        "code": code,
        "detail": detail,
        "offers": offers,
        "real_order_capability": False,
        "routing_allowed": False,
    }


def _validate_gate(gate: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(gate, Mapping):
        return False, "FRESHNESS_GATE_MISSING"
    if gate.get("name") != FRESHNESS_GATE_NAME:
        return False, "FRESHNESS_GATE_IDENTITY_INVALID"
    if gate.get("family") != "CAUCIONES":
        return False, "FRESHNESS_GATE_FAMILY_INVALID"
    if gate.get("green") is not True:
        return False, "FRESHNESS_GATE_RED"
    if gate.get("contract_status") != "READY_PAPER_CANDIDATE":
        return False, "CONTRACT_GATE_NOT_READY_PAPER_CANDIDATE"
    if gate.get("real_order_capability") not in (False, 0):
        return False, "REAL_ORDER_CAPABILITY_MUST_BE_ZERO"
    if not str(gate.get("evidence_id") or "").strip():
        return False, "FRESHNESS_GATE_EVIDENCE_ID_MISSING"
    return True, "GREEN"


def run_verified_caucion_paper_cycle(
    broker,
    canonical_snapshots: Sequence[Mapping[str, Any]],
    *,
    freshness_gate: Mapping[str, Any],
    obligation_snapshot: ObligationSnapshot,
    currency: str,
    as_of,
    sweep_start_at,
    order_cutoff_at,
    liquidity_deadline,
    schedule_source: str,
    request_id: str,
    participation="0.10",
    max_quote_age_seconds=30,
) -> dict:
    """Execute one fail-closed PAPER-only caucion sweep decision.

    Any invalid canonical snapshot blocks the whole cycle rather than silently
    dropping an identity. The bridge never relaxes the adapter's 300-second
    canonical TTL; the sweep may use a tighter quote age such as 30 seconds.
    """
    if PAPER_BRIDGE_ORDER_ROUTING_ALLOWED or CASH_SWEEP_ORDER_ROUTING_ALLOWED:
        raise RuntimeError("CAUCION_PAPER_BRIDGE_ROUTING_INVARIANT_BROKEN")

    ok, code = _validate_gate(freshness_gate)
    if not ok:
        return _hold(code)
    if not isinstance(obligation_snapshot, ObligationSnapshot):
        return _hold("OBLIGATION_SNAPSHOT_MISSING")
    if not canonical_snapshots:
        return _hold("CANONICAL_CAUCION_SNAPSHOTS_EMPTY")

    offers = []
    identities = set()
    try:
        for snapshot in canonical_snapshots:
            offer = offer_from_canonical_snapshot(snapshot, now=as_of)
            identity = (offer.instrument_id, offer.currency, offer.quoted_at)
            if identity in identities:
                return _hold("DUPLICATE_CANONICAL_CAUCION_SNAPSHOT", offers=len(offers))
            identities.add(identity)
            offers.append(offer)
    except (CaucionOfferAdapterError, ValueError, TypeError) as exc:
        return _hold("CAUCION_CANONICAL_SNAPSHOT_INVALID", detail=str(exc), offers=len(offers))

    result = run_paper_sweep(
        broker,
        offers,
        obligation_snapshot=obligation_snapshot,
        currency=currency,
        as_of=as_of,
        sweep_start_at=sweep_start_at,
        order_cutoff_at=order_cutoff_at,
        liquidity_deadline=liquidity_deadline,
        schedule_source=schedule_source,
        request_id=request_id,
        participation=participation,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    if not isinstance(result, dict):
        raise CaucionPaperBridgeError("existing PAPER sweep returned a non-dict result")
    result = dict(result)
    result["bridge"] = "RC6_CAUCION_PAPER_BRIDGE_V1"
    result["freshness_gate"] = FRESHNESS_GATE_NAME
    result["freshness_evidence_id"] = str(freshness_gate["evidence_id"])
    result["canonical_offer_count"] = len(offers)
    result["real_order_capability"] = False
    result["routing_allowed"] = False
    return result


def assert_paper_bridge_invariants() -> None:
    if PAPER_BRIDGE_ORDER_ROUTING_ALLOWED is not False:
        raise AssertionError("bridge must remain PAPER-only")
    if CASH_SWEEP_ORDER_ROUTING_ALLOWED is not False:
        raise AssertionError("underlying sweep must remain PAPER-only")
