"""A597: OFFHOST-only PPI native Book -> causal replay *witness* bridge.

This module does not simulate trades, call any provider, attest contract units,
or promote a journal event to a PAPER fill. Source verification and the strict
future native-clock predicate belong to rc6_ppi_scanner_provenance; never
reimplement its normalizer or Book-date logic here.

`journal_event_sha256` is a stable retention pin, not an execution ID. A
complete causal fill additionally requires an authenticated decision, economic
contract/units, session/settlement/fees, and the appropriate family executor.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from rc6_ppi_scanner_provenance import _aware, strictly_future_native_books


# These are *audit groups*, not an OPERATIONAL_SCOPE promise. ETF stays HOLD.
NINE_FAMILY_GROUPS = (
    "ACCIONES", "CEDEARS", "BONOS_LETRAS_ON", "OPCIONES", "FUTUROS",
    "CAUCIONES", "FCI", "ACCIONES_USA", "INDICES",
)
RECEIPT_SCHEMA = "rc6.ppi-native-book-causal-witness.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GROUP_TYPES = {
    "ACCIONES": frozenset({"ACCIONES"}),
    "CEDEARS": frozenset({"CEDEARS"}),
    "BONOS_LETRAS_ON": frozenset({"BONOS", "LETRAS", "OBLIGACIONES"}),
    "OPCIONES": frozenset({"OPCIONES"}),
    "FUTUROS": frozenset({"FUTUROS"}),
    "CAUCIONES": frozenset({"CAUCIONES"}),
    "FCI": frozenset({"FCI"}),
    # The current scanner identity enum does NOT prove geographical USA
    # markets or index semantics from a generic ACCIONES/other label.
    "ACCIONES_USA": frozenset({"ACCIONES_USA"}),
    "INDICES": frozenset({"INDICES"}),
}


def _positive(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return number.is_finite() and number > 0


def _digest(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _base(family: str | None) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "family_group": family,
        "state": "NO_VERIFICADO",
        "reason": "PPI_BOOK_JOURNAL_ABSENT",
        "provider_gets": 0,
        "real_orders_sent": 0,
        "fill_accredited": False,
        "family_ready": False,
        "unit_contract_verified": False,
        "decision_signature_verified": False,
        "source_artifact_independently_verified": False,
        "complementary_source_required_universally": False,
        "journal_event_sha256": None,
        "journal_chain_tip_sha256": None,
        "source_artifact_sha256_claim": None,
        "decision_receipt_sha256_claim": None,
        "causal_clock_witness": False,
        "first_book_has_positive_native_top_level": False,
        "candidate_count": 0,
        "dependency_edge": None,
    }


def causal_book_witness(
    journal_path: Path,
    *,
    family_group: str,
    identity: tuple[str, ...],
    decision_at: str,
    latency_seconds: float,
    side: str,
    decision_receipt_sha256: str | None = None,
    source_artifact_sha256: str | None = None,
) -> dict[str, Any]:
    """Name the *first* future native PPI Book; never score favorable later books.

    Digests received from the caller are merely external claims. A caller must
    independently verify immutable Actions artifacts and authentic strategies
    before evaluating fills through a family-specific execution model.
    """
    result = _base(family_group)
    if (family_group not in NINE_FAMILY_GROUPS or side not in {"BUY", "SELL"}
            or not isinstance(identity, (tuple, list)) or len(identity) != 5
            or any(not isinstance(part, str) or not part.strip() for part in identity)
            or not _aware(decision_at) or isinstance(latency_seconds, bool)
            or not isinstance(latency_seconds, (int, float))
            or not 0 <= latency_seconds <= 300
            or (decision_receipt_sha256 is not None
                and (not isinstance(decision_receipt_sha256, str)
                     or not _SHA256.fullmatch(decision_receipt_sha256)))
            or (source_artifact_sha256 is not None
                and (not isinstance(source_artifact_sha256, str)
                     or not _SHA256.fullmatch(source_artifact_sha256)))):
        return {**result, "state": "BLOQUEADO", "reason": "PIT_WITNESS_INPUT_INVALID"}
    if identity[1] not in _GROUP_TYPES[family_group]:
        return {**result, "state": "BLOQUEADO", "reason": "PIT_WITNESS_FAMILY_IDENTITY_MISMATCH"}
    result["decision_receipt_sha256_claim"] = decision_receipt_sha256
    result["source_artifact_sha256_claim"] = source_artifact_sha256

    future, verified = strictly_future_native_books(
        Path(journal_path), identity=tuple(identity), decision_at=decision_at,
        latency_seconds=latency_seconds)
    result["journal_chain_tip_sha256"] = verified.get("latest_sha256")
    result["candidate_count"] = len(future)
    if verified.get("state") == "HOLD":
        return {**result, "state": "BLOQUEADO", "reason": verified.get("reason", "PPI_BOOK_JOURNAL_HOLD")}
    if verified.get("state") != "HASH_CHAIN_VERIFIED":
        return {**result, "reason": verified.get("reason", "PPI_BOOK_JOURNAL_UNVERIFIED")}
    if not future:
        return {**result, "reason": "PPI_BOOK_NOT_STRICTLY_AFTER_DECISION_PLUS_LATENCY"}

    # The original journal is causally ordered; pick the earliest native event.
    # A second, later favorable level must not rescue an unfillable first Book.
    first = future[0]
    event_sha = first["event_sha256"]
    depth = first["book"]["offers" if side == "BUY" else "bids"]
    top_positive = bool(depth and _positive(depth[0]["price"])
                        and _positive(depth[0]["quantity"]))
    cutoff = _aware(decision_at) + timedelta(seconds=latency_seconds)
    native_at = _aware(first["native_book_at"])
    observed_at = _aware(first["known_at"])
    # Re-assert the causal cut, never substitute `observed_at` for Book.date.
    if not (native_at and observed_at and native_at > cutoff and observed_at >= native_at):
        return {**result, "state": "BLOQUEADO", "reason": "PPI_BOOK_CAUSAL_CUT_FAILED"}
    edge = ({"from_decision_sha256_claim": decision_receipt_sha256,
             "to_journal_event_sha256": event_sha,
             "relation": "POST_DECISION_NATIVE_BOOK_OBSERVATION_ONLY"})
    return {
        **result,
        "reason": ("PIT_NATIVE_BOOK_TOP_DEPTH_ZERO_OR_ABSENT" if not top_positive
                   else "PIT_WITNESS_ONLY_UNITS_CONTRACT_AND_DECISION_UNVERIFIED"),
        "causal_clock_witness": True,
        "first_book_has_positive_native_top_level": top_positive,
        "first_book_native_at": first["native_book_at"],
        "first_book_observed_at": first["known_at"],
        "journal_event_sha256": event_sha,
        "dependency_edge": edge,
        "witness_sha256": _digest({"schema": RECEIPT_SCHEMA, "identity": list(identity),
                                    "family_group": family_group, "side": side,
                                    "decision_at": decision_at,
                                    "latency_seconds": latency_seconds,
                                    "journal_event_sha256": event_sha,
                                    "journal_chain_tip_sha256": verified.get("latest_sha256"),
                                    "decision_receipt_sha256_claim": decision_receipt_sha256,
                                    "source_artifact_sha256_claim": source_artifact_sha256}),
    }


def nine_family_witness_matrix(journal_path: Path, requests: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Finite offhost matrix; absent requests remain explicitly NO_VERIFICADO."""
    if not isinstance(requests, dict) or any(k not in NINE_FAMILY_GROUPS for k in requests):
        raise ValueError("PIT_WITNESS_FAMILY_REQUEST_INVALID")
    rows = {}
    for family in NINE_FAMILY_GROUPS:
        request = requests.get(family)
        if request is None:
            rows[family] = {**_base(family), "reason": "DECISION_RECEIPT_NOT_SUPPLIED"}
        elif not isinstance(request, dict):
            rows[family] = {**_base(family), "state": "BLOQUEADO",
                            "reason": "PIT_WITNESS_INPUT_INVALID"}
        else:
            try:
                rows[family] = causal_book_witness(journal_path, family_group=family, **request)
            except TypeError:
                rows[family] = {**_base(family), "state": "BLOQUEADO",
                                "reason": "PIT_WITNESS_INPUT_INVALID"}
    return {"schema": RECEIPT_SCHEMA, "families": rows, "family_count": len(rows),
            "fills_accredited": 0, "real_orders_sent": 0,
            "all_families_ready": False, "provider_gets": 0}
