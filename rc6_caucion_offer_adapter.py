"""Fail-closed adapter from validated CAUCIONES evidence to PAPER CaucionOffer.

This module intentionally does *not* interpret raw PPI MarketData. In particular it
never maps ``current.price`` to TNA, ``volume``/``quantity`` to executable capital,
or a book side to the colocadora side. Those semantics must be validated upstream
and represented by the canonical snapshot/proof fields below.

No network, broker, database or order surface is imported here. The only output is
the existing PAPER ``bt_caucion_paper.CaucionOffer`` value object.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from bt_caucion_paper import CaucionOffer
from rc6_cauciones_contract import parse_ticker

CANONICAL_SCHEMA = "POROTA_RC6_CAUCION_CANONICAL_V1"
MAX_DYNAMIC_AGE_SECONDS = 300
MAX_EXPIRY_HORIZON_SECONDS = 900
_ALLOWED_CURRENCIES = {"ARS", "USD_MEP"}
_ALLOWED_SOURCES = {
    "PPI_API_AUTHENTICATED",
    "PPI_AUTHENTICATED_XHR",
    "PPI_AUTHENTICATED_WEB",
    "PPI_RECONCILED_EXPLICIT",
}
_AMBIGUOUS_RAW_KEYS = {"price", "volume", "quantity", "bids", "offers", "current", "book"}
_REQUIRED_PROOF = {
    "rate": "TNA_FRACTION_VALIDATED",
    "depth": "COLOCADORA_EXECUTABLE_PRINCIPAL_VALIDATED",
    "side": "COLOCADORA_SIDE_VALIDATED",
    "fees": "TOTAL_FEES_FOR_PRINCIPAL_VALIDATED",
    "maturity": "MATURITY_EXPLICIT_VALIDATED",
    "freshness": "PROVIDER_OBSERVED_AT_VALIDATED",
}


class CaucionOfferAdapterError(ValueError):
    """Canonical caucion snapshot is incomplete, ambiguous or stale."""


def _required(snapshot: Mapping[str, Any], key: str) -> Any:
    if key not in snapshot or snapshot[key] is None or snapshot[key] == "":
        raise CaucionOfferAdapterError(f"missing canonical field: {key}")
    return snapshot[key]


def _aware(value: Any, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CaucionOfferAdapterError(f"invalid {field}") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise CaucionOfferAdapterError(f"{field} must be timezone-aware")
    return dt


def _decimal(value: Any, field: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CaucionOfferAdapterError(f"invalid numeric field: {field}") from exc
    if not out.is_finite():
        raise CaucionOfferAdapterError(f"non-finite numeric field: {field}")
    if positive and out <= 0:
        raise CaucionOfferAdapterError(f"{field} must be positive")
    if nonnegative and out < 0:
        raise CaucionOfferAdapterError(f"{field} must be non-negative")
    return out


def offer_from_canonical_snapshot(
    snapshot: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = MAX_DYNAMIC_AGE_SECONDS,
) -> CaucionOffer:
    """Build a PAPER offer only from explicitly validated canonical semantics.

    ``max_age_seconds`` may only tighten the canonical five-minute dynamic TTL;
    callers cannot relax it. Raw provider shapes are rejected at the top level so
    this function cannot silently become a PPI field-semantic normalizer.
    """
    if not isinstance(snapshot, Mapping):
        raise CaucionOfferAdapterError("snapshot must be a mapping")
    if max_age_seconds <= 0 or max_age_seconds > MAX_DYNAMIC_AGE_SECONDS:
        raise CaucionOfferAdapterError("max_age_seconds cannot relax the 300s dynamic TTL")

    raw_keys = sorted(_AMBIGUOUS_RAW_KEYS.intersection(snapshot))
    if raw_keys:
        raise CaucionOfferAdapterError(
            "raw/ambiguous PPI market fields are forbidden in canonical adapter: " + ",".join(raw_keys)
        )

    if _required(snapshot, "schema") != CANONICAL_SCHEMA:
        raise CaucionOfferAdapterError("unsupported canonical caucion schema")
    if _required(snapshot, "semantics_status") != "VALIDATED":
        raise CaucionOfferAdapterError("caucion semantics are not VALIDATED")
    if _required(snapshot, "operation") != "COLOCAR-CAUCION":
        raise CaucionOfferAdapterError("only COLOCAR-CAUCION is admissible")
    if _required(snapshot, "side") != "COLOCADORA":
        raise CaucionOfferAdapterError("only the explicitly validated colocadora side is admissible")
    if _required(snapshot, "market") != "BYMA":
        raise CaucionOfferAdapterError("caucion market must be explicitly BYMA")
    if _required(snapshot, "settlement") != "INMEDIATA":
        raise CaucionOfferAdapterError("caucion settlement must be explicitly INMEDIATA")
    if _required(snapshot, "operable") is not True:
        raise CaucionOfferAdapterError("instrument is not explicitly operable")
    if _required(snapshot, "market_session_state") != "OPEN":
        raise CaucionOfferAdapterError("market session is not explicitly OPEN")

    proof = _required(snapshot, "semantic_proof")
    if not isinstance(proof, Mapping):
        raise CaucionOfferAdapterError("semantic_proof must be a mapping")
    for key, expected in _REQUIRED_PROOF.items():
        if proof.get(key) != expected:
            raise CaucionOfferAdapterError(f"semantic proof missing or unvalidated: {key}")

    ticker = str(_required(snapshot, "ticker")).strip().upper()
    identity = parse_ticker(ticker)
    try:
        term_days = int(_required(snapshot, "term_days"))
    except (TypeError, ValueError) as exc:
        raise CaucionOfferAdapterError("term_days must be an integer") from exc
    if term_days != identity.term_days:
        raise CaucionOfferAdapterError("term_days conflicts with caucion ticker")

    currency = str(_required(snapshot, "currency")).strip().upper()
    if currency not in _ALLOWED_CURRENCIES:
        raise CaucionOfferAdapterError("unsupported or ambiguous caucion currency")
    expected_currency = "ARS" if identity.currency_prefix == "PESOS" else "USD_MEP"
    if currency != expected_currency:
        raise CaucionOfferAdapterError("currency conflicts with caucion ticker")

    source = str(_required(snapshot, "metadata_source")).strip().upper()
    if source not in _ALLOWED_SOURCES:
        raise CaucionOfferAdapterError("untrusted/unknown contract metadata source")
    evidence_id = str(_required(snapshot, "evidence_id")).strip()
    provider_instrument_id = str(_required(snapshot, "provider_instrument_id")).strip()
    if not evidence_id or not provider_instrument_id:
        raise CaucionOfferAdapterError("provider identity/provenance is required")

    current = (now or datetime.now(timezone.utc))
    if current.tzinfo is None or current.utcoffset() is None:
        raise CaucionOfferAdapterError("now must be timezone-aware")
    observed = _aware(_required(snapshot, "observed_at"), "observed_at")
    age = (current.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
    if age < -5:
        raise CaucionOfferAdapterError("provider observation is in the future")
    if age > max_age_seconds:
        raise CaucionOfferAdapterError("provider observation is stale")
    expiry = _aware(_required(snapshot, "expiry_at"), "expiry_at")
    expiry_delta = (expiry.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds()
    if expiry_delta <= 0:
        raise CaucionOfferAdapterError("dynamic caucion evidence is expired")
    if expiry_delta > MAX_EXPIRY_HORIZON_SECONDS:
        raise CaucionOfferAdapterError("expiry_at exceeds canonical 15-minute horizon")

    annual_rate = _decimal(_required(snapshot, "annual_rate_fraction"), "annual_rate_fraction", nonnegative=True)
    available = _decimal(_required(snapshot, "available_principal"), "available_principal", positive=True)
    minimum = _decimal(_required(snapshot, "principal_min"), "principal_min", positive=True)
    step = _decimal(_required(snapshot, "principal_step"), "principal_step", positive=True)
    if available < minimum:
        raise CaucionOfferAdapterError("available_principal is below principal_min")
    if minimum % step:
        raise CaucionOfferAdapterError("principal_min is incompatible with principal_step")

    try:
        day_count_basis = int(_required(snapshot, "day_count_basis"))
    except (TypeError, ValueError) as exc:
        raise CaucionOfferAdapterError("day_count_basis must be an integer") from exc
    fee_payment = str(_required(snapshot, "fee_payment")).strip().upper()
    quoted_total_fees = _decimal(_required(snapshot, "quoted_total_fees"), "quoted_total_fees", nonnegative=True)
    fee_quote_principal = _decimal(_required(snapshot, "fee_quote_principal"), "fee_quote_principal", positive=True)
    if fee_quote_principal > available:
        raise CaucionOfferAdapterError("fee_quote_principal exceeds executable depth")
    if fee_quote_principal < minimum or fee_quote_principal % step:
        raise CaucionOfferAdapterError("fee_quote_principal violates min/step")

    # The existing PAPER model derives actual interest days from start/maturity.
    # The ticker tenor remains independently checked above and is never used to
    # invent a maturity timestamp.
    start_date = str(_required(snapshot, "start_date"))
    maturity_at = str(_required(snapshot, "maturity_at"))
    _aware(maturity_at, "maturity_at")

    provenance = f"{source}|evidence={evidence_id}|provider_id={provider_instrument_id}"
    try:
        return CaucionOffer(
            instrument_id=ticker,
            currency=currency,
            annual_rate_fraction=annual_rate,
            start_date=start_date,
            maturity_at=maturity_at,
            quoted_at=str(_required(snapshot, "observed_at")),
            available_principal=available,
            minimum_principal=minimum,
            principal_step=step,
            day_count_basis=day_count_basis,
            fee_payment=fee_payment,
            metadata_source=provenance,
            quoted_total_fees=quoted_total_fees,
            fee_quote_principal=fee_quote_principal,
        )
    except (TypeError, ValueError, ArithmeticError) as exc:
        raise CaucionOfferAdapterError(f"canonical snapshot rejected by PAPER CaucionOffer: {exc}") from exc
