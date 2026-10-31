"""Read-only contract review for a long BYMA option at expiry.

This module deliberately cannot exercise, expire, sell, settle, or post cash.
An official-looking source label or checksum is not authentication of a BYMA
publication. The owner must independently authenticate the source, holder
instruction, deliverable, costs and clearing outcome before any PAPER ledger
integration. In particular, an in-the-money option is *not* an exercise receipt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Mapping
from zoneinfo import ZoneInfo

from bs_instrument_contracts import InstrumentContract, aware_datetime, utc_microseconds


PAPER_ONLY = True
REAL_ROUTES_USED = ()
EXPIRY_REFERENCE_SCHEMA = "rc6.option.expiry.reference.v1"
_ART_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_AUTHORITY_FIELDS = frozenset({
    "paper_exercise_authorized", "paper_cash_post_authorized",
    "real_order_id", "broker_order_id", "fill_id", "cash_posted",
    "settlement_cash", "real_routes_used",
})


@dataclass(frozen=True)
class OptionExpiryReview:
    """A structural diagnostic, never an accounting or execution decision."""

    status: str
    reason_code: str
    contract_matched: bool = False
    indicative_intrinsic_per_unit: Decimal | None = None
    paper_exercise_authorized: bool = False
    paper_cash_post_authorized: bool = False
    real_routes_used: tuple[str, ...] = ()


def contract_snapshot_sha256(contract: InstrumentContract) -> str:
    """Bind an evidence record to the exact immutable contract snapshot."""

    if not isinstance(contract, InstrumentContract):
        raise TypeError("OPTION_CONTRACT_REQUIRED")
    def encode_decimal(value):
        if isinstance(value, Decimal):
            return str(value)
        raise TypeError("OPTION_CONTRACT_SNAPSHOT_VALUE_UNSUPPORTED")

    encoded = json.dumps(
        asdict(contract),
        sort_keys=True,
        separators=(",", ":"),
        default=encode_decimal,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _blocked(reason: str) -> OptionExpiryReview:
    return OptionExpiryReview(status="BLOQUEADO", reason_code=reason)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        return aware_datetime(value)
    except (ValueError, TypeError, OverflowError):
        return None


def review_long_option_expiry_reference(
    contract: InstrumentContract,
    reference: Mapping[str, object] | None,
    *,
    as_of: datetime | str,
    position_side: str | None = None,
) -> OptionExpiryReview:
    """Check PIT/identity/adjustment evidence before *considering* expiry work.

    `reference` must come from a separately authenticated official document or
    clearing feed; this pure checker only validates its declared structure.
    A coherent record yields ``NO_VERIFICADO`` (never READY) because exercise
    instructions, security delivery, economic cash and authenticated clearing
    settlement are different evidence contracts, not inferred from moneyness.
    """

    if not isinstance(contract, InstrumentContract) or contract.family != "OPCIONES":
        return _blocked("OPTION_EXACT_LONG_CONTRACT_REQUIRED")
    if position_side != "LONG":
        return _blocked("OPTION_LONG_POSITION_SIDE_UNVERIFIED")
    if (contract.market != "BYMA" or contract.currency != "ARS"
            or contract.settlement != "INMEDIATA"
            or contract.option_right not in {"CALL", "PUT"}
            or not contract.underlying):
        return _blocked("OPTION_EXACT_BYMA_LONG_CONTRACT_REQUIRED")

    now, expiry = _stamp(as_of), _stamp(contract.expires_at)
    if now is None or expiry is None:
        return _blocked("OPTION_EXPIRY_REVIEW_CLOCK_INVALID")
    if utc_microseconds(now) < utc_microseconds(expiry):
        return _blocked("OPTION_EXPIRY_NOT_DUE")
    if not isinstance(reference, Mapping):
        return _blocked("OPTION_OFFICIAL_REFERENCE_MISSING")
    if reference.get("schema") != EXPIRY_REFERENCE_SCHEMA:
        return _blocked("OPTION_OFFICIAL_REFERENCE_SCHEMA_UNVERIFIED")
    if any(name in reference for name in _FORBIDDEN_AUTHORITY_FIELDS):
        return _blocked("OPTION_REFERENCE_ECONOMIC_AUTHORITY_NOT_ACCEPTED")
    if reference.get("source") != "BYMA_CLEARING_OFFICIAL":
        return _blocked("OPTION_OFFICIAL_REFERENCE_SOURCE_UNVERIFIED")
    digest = reference.get("document_sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        return _blocked("OPTION_OFFICIAL_DOCUMENT_DIGEST_MISSING")
    if reference.get("contract_sha256") != contract_snapshot_sha256(contract):
        return _blocked("OPTION_EXPIRY_CONTRACT_SNAPSHOT_MISMATCH")

    identity = reference.get("identity")
    if not isinstance(identity, Mapping):
        return _blocked("OPTION_EXPIRY_IDENTITY_UNVERIFIED")
    required = {
        "symbol": contract.symbol,
        "family": contract.family,
        "market": contract.market,
        "currency": contract.currency,
        "settlement": contract.settlement,
        "underlying": contract.underlying,
        "option_right": contract.option_right,
    }
    if any(identity.get(name) != expected for name, expected in required.items()):
        return _blocked("OPTION_EXPIRY_IDENTITY_MISMATCH")
    if (_decimal(identity.get("strike")) != contract.strike
            or _decimal(identity.get("cash_multiplier")) != contract.cash_multiplier
            or _decimal(identity.get("quantity_step")) != contract.quantity_step
            or _stamp(identity.get("expires_at")) != expiry):
        return _blocked("OPTION_EXPIRY_ECONOMIC_TERMS_MISMATCH")

    # An ordinary-dividend policy is not a blanket exemption from corporate
    # actions. Any adjusted or unknown deliverable needs a new reviewed native
    # contract; the old strike/multiplier must never be silently reused.
    if reference.get("adjustment_status") != "UNCHANGED_REVIEWED":
        return _blocked("OPTION_CORPORATE_ACTION_REVIEW_REQUIRED")
    if reference.get("delivery_rule") not in {"PHYSICAL", "CASH"}:
        return _blocked("OPTION_EXERCISE_DELIVERY_RULE_UNVERIFIED")

    effective = _stamp(reference.get("effective_at"))
    published = _stamp(reference.get("published_at"))
    received = _stamp(reference.get("received_at"))
    mark_at = _stamp(reference.get("official_underlying_price_at"))
    if any(value is None for value in (effective, published, received, mark_at)):
        return _blocked("OPTION_EXPIRY_PROVENANCE_CLOCK_UNVERIFIED")
    if not (utc_microseconds(expiry) <= utc_microseconds(effective)
            <= utc_microseconds(published) <= utc_microseconds(received)
            <= utc_microseconds(now)):
        return _blocked("OPTION_EXPIRY_PIT_SEQUENCE_INVALID")
    if (utc_microseconds(mark_at) > utc_microseconds(effective)
            or mark_at.astimezone(_ART_TZ).date() != expiry.astimezone(_ART_TZ).date()):
        return _blocked("OPTION_EXPIRY_UNDERLYING_REFERENCE_CLOCK_INVALID")

    official_price = _decimal(reference.get("official_underlying_price"))
    if official_price is None or official_price <= 0:
        return _blocked("OPTION_EXPIRY_UNDERLYING_REFERENCE_INVALID")

    intrinsic = (max(Decimal("0"), official_price - contract.strike)
                 if contract.option_right == "CALL"
                 else max(Decimal("0"), contract.strike - official_price))
    # Indicative intrinsic is *not* a cash settlement claim or exercise
    # instruction. Even a fully matched official reference stays non-posting.
    return OptionExpiryReview(
        status="NO_VERIFICADO",
        reason_code="OPTION_HOLDER_INSTRUCTION_AND_CLEARING_OUTCOME_REQUIRED",
        contract_matched=True,
        indicative_intrinsic_per_unit=intrinsic,
    )
