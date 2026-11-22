"""Strict, read-only intake for exact-class FCI source evidence.

The structural FundSourceFact quarantine has no entry authority. Settlement
attestations are checked separately against operator-owned signer and account
pins. A self-computed digest or source label cannot satisfy that check.

The exact-key schema also prevents accidentally copying token-bearing provider
payloads to a public audit receipt.  Callers retain raw evidence in their own
appropriately protected source system; this module only handles allowlisted
financial fields and hashes.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime


PAPER_ONLY = True
RUNTIME_ENTRY_AUTHORITY = False
SOURCE_AUTHENTICATED = False
REAL_ROUTES_USED = ()
SCHEMA = "rc6-fci-exact-class-quarantine-v1"
SETTLEMENT_SCHEMA = "rc6-fci-paper-settlement-attestation-v1"
SETTLEMENT_SOURCE_REQUIRED = "FCI_RUNTIME_SETTLEMENT_SOURCE_PIN_MISSING"
SETTLEMENT_RECEIPT_REQUIRED = "FCI_RUNTIME_ATTESTED_SETTLEMENT_RECEIPT_REQUIRED"
SETTLEMENT_AUTHORITY_SCHEMA = "rc6-fci-paper-settlement-authority-v1"
SETTLEMENT_AUTHORITY_FILE = "paper-fci-settlement-authority-v1.json"

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_OPAQUE_REF = re.compile(r"[A-Za-z0-9_./:@+-]{1,200}\Z")
_CLASS = re.compile(r"[A-Za-z0-9_.-]{2,80}\Z")
_HHMM = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
_FIELDS = {
    "CLASS_TERMS": frozenset({
        "manager", "regulation_reference", "regulation_sha256",
        "subscription_min", "subscription_step", "broker_minimum_reference",
        "broker_step_reference", "redemption_lead_days"}),
    "CALENDAR": frozenset({"calendar_date", "cutoff_hhmm", "cutoff_zone"}),
    "ACCOUNT_FEES": frozenset({
        "subscription_fee", "redemption_fee", "account_tariff_reference"}),
    "NAV": frozenset({"nav_per_unit", "nav_unit"}),
}
_SOURCES = frozenset({
    "PPI_STRUCTURED_API", "PPI_AUTHENTICATED_WEB", "IOL_MCP_READONLY",
    "FUND_MANAGER_DOCUMENT", "BROKER_ACCOUNT_TARIFF"})
_LEGAL_SOURCES = frozenset({"PPI_AUTHENTICATED_WEB", "FUND_MANAGER_DOCUMENT"})
_FEE_SOURCES = frozenset({"BROKER_ACCOUNT_TARIFF", "PPI_AUTHENTICATED_WEB"})
_NAV_SOURCES = frozenset({
    "PPI_STRUCTURED_API", "PPI_AUTHENTICATED_WEB", "FUND_MANAGER_DOCUMENT",
    "IOL_MCP_READONLY"})
_SETTLEMENT_FIELDS = frozenset({
    "schema", "account_scope_sha256", "identity", "lifecycle_id", "transitions",
    "class_evidence_sha256", "receipt_hashes", "payout_ars", "known_at", "valid_until",
})


def settlement_attestation_bytes(claims):
    return json.dumps(claims, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _settlement_authority_pins():
    from scripts.rc6_operator_paper_rearm import OPERATOR_DIR_RUNTIME, _root_owned_receipt

    try:
        authority = _root_owned_receipt(OPERATOR_DIR_RUNTIME, receipt_name=SETTLEMENT_AUTHORITY_FILE)
    except (OSError, ValueError) as exc:
        raise ValueError(SETTLEMENT_SOURCE_REQUIRED) from exc
    if (set(authority) != {"schema", "signer_sha256", "account_scope_sha256"}
            or authority["schema"] != SETTLEMENT_AUTHORITY_SCHEMA
            or any(not isinstance(authority[key], str) or not _DIGEST.fullmatch(authority[key])
                   for key in ("signer_sha256", "account_scope_sha256"))):
        raise ValueError(SETTLEMENT_SOURCE_REQUIRED)
    return authority["signer_sha256"], authority["account_scope_sha256"]


def verify_settlement_attestation(proof, *, at):
    """Accept a scoped attestation only under a separately pinned release authority.

    These pins are supplied by the release owner, independently of the evidence.
    The passive browser capture and decision-timing signer have no authority here.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signer_pin, account_pin = _settlement_authority_pins()
    if not isinstance(proof, dict) or set(proof) != {"claims", "public_key_hex", "signature_hex"}:
        raise ValueError("FCI_SETTLEMENT_ATTESTATION_REQUIRED")
    try:
        claims = proof["claims"]
        if (not isinstance(claims, dict) or set(claims) != _SETTLEMENT_FIELDS
                or claims["schema"] != SETTLEMENT_SCHEMA
                or claims["account_scope_sha256"] != account_pin):
            raise ValueError("FCI_SETTLEMENT_ATTESTATION_SCOPE_MISMATCH")
        raw = settlement_attestation_bytes(claims)
        if len(raw) > 8192:
            raise ValueError("FCI_SETTLEMENT_ATTESTATION_CAPACITY")
        point, start, end = map(aware_datetime, (at, claims["known_at"], claims["valid_until"]))
        if not start <= point < end:
            raise ValueError("FCI_SETTLEMENT_ATTESTATION_NOT_CURRENT")
        if (not isinstance(proof["public_key_hex"], str) or len(proof["public_key_hex"]) != 64
                or not isinstance(proof["signature_hex"], str) or len(proof["signature_hex"]) != 128):
            raise ValueError("FCI_SETTLEMENT_ATTESTATION_INVALID")
        public = bytes.fromhex(proof["public_key_hex"])
        signature = bytes.fromhex(proof["signature_hex"])
        if len(public) != 32 or len(signature) != 64 or hashlib.sha256(public).hexdigest() != signer_pin:
            raise ValueError("FCI_SETTLEMENT_SIGNER_NOT_PINNED")
        Ed25519PublicKey.from_public_bytes(public).verify(signature, raw)
    except (InvalidSignature, TypeError, OverflowError) as exc:
        raise ValueError("FCI_SETTLEMENT_ATTESTATION_INVALID") from exc
    return json.loads(raw)


@dataclass(frozen=True)
class FundSourceFact:
    """Source assertion; source_kind is a label, never an auth proof."""

    class_key: str
    market: str
    currency: str
    settlement: str
    kind: str
    source_kind: str
    source_reference: str
    effective_at: str
    published_at: str
    received_at: str
    fields: Mapping[str, str]
    content_sha256: str


@dataclass(frozen=True)
class FundQuarantineReview:
    class_key: str
    evaluated_at: str
    source_fact_hashes: tuple[str, ...]
    covered_kinds: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    status: str
    schema: str = SCHEMA
    paper_only: bool = True
    source_authenticated: bool = False
    runtime_entry_authority: bool = False
    real_routes_used: tuple = ()


def content_hash(fact: FundSourceFact) -> str:
    """Bind identity, declared source, complete financial fields and clocks.

    Integrity is not provenance authentication. The hash is domain-separated
    and reproducible from a public, allowlisted schema without raw payloads.
    """
    if not isinstance(fact, FundSourceFact):
        raise ValueError("FCI_SOURCE_FACT_REQUIRED")
    material = {key: value for key, value in asdict(fact).items()
                if key != "content_sha256"}
    encoded = json.dumps({"schema": SCHEMA, "fact": material}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_positive(value):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return isinstance(value, str) and number.is_finite() and number > 0


def _valid_nonnegative(value):
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return isinstance(value, str) and number.is_finite() and number >= 0


def _clock(value):
    try:
        return aware_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _validate_one(fact: FundSourceFact, at: datetime, max_nav_age_days: int):
    """Return one exact error without trusting upstream source-name strings."""
    if (not isinstance(fact, FundSourceFact)
            or not isinstance(fact.class_key, str) or not _CLASS.fullmatch(fact.class_key)
            or (fact.market, fact.currency, fact.settlement) not in (
                ("FCI", "ARS", "INMEDIATA"), ("BYMA", "ARS", "INMEDIATA"))
            or not isinstance(fact.kind, str) or fact.kind not in _FIELDS
            or not isinstance(fact.source_kind, str) or fact.source_kind not in _SOURCES):
        return "FCI_SOURCE_IDENTITY_OR_KIND_INVALID"
    if (not isinstance(fact.source_reference, str)
            or not _OPAQUE_REF.fullmatch(fact.source_reference)
            or fact.source_reference.startswith("/")
            or not isinstance(fact.fields, Mapping)
            or set(fact.fields) != _FIELDS[fact.kind]
            or any(not isinstance(v, str) or not v or v != v.strip()
                   for v in fact.fields.values())):
        return "FCI_SOURCE_ALLOWLIST_OR_REFERENCE_INVALID"
    if (not isinstance(fact.content_sha256, str)
            or not _DIGEST.fullmatch(fact.content_sha256)
            or fact.content_sha256 != content_hash(fact)):
        return "FCI_SOURCE_HASH_OR_IDENTITY_MISMATCH"
    effective, published, received = (_clock(v) for v in (
        fact.effective_at, fact.published_at, fact.received_at))
    if (effective is None or published is None or received is None
            or published > received or received > at or effective > at):
        return "FCI_SOURCE_PIT_CLOCK_INVALID"
    if fact.kind == "NAV":
        if (not effective <= published
                or (at - effective).total_seconds() > max_nav_age_days * 86400):
            return "FCI_NAV_CLOCK_OR_AGE_INVALID"
        if fact.source_kind not in _NAV_SOURCES:
            return "FCI_NAV_SOURCE_SCOPE_INVALID"
        if not _valid_positive(fact.fields["nav_per_unit"]):
            return "FCI_NAV_AMOUNT_INVALID"
        if fact.fields["nav_unit"] != "CUOTAPARTE":
            return "FCI_NAV_UNIT_UNVERIFIED"
    elif fact.kind == "CLASS_TERMS":
        if fact.source_kind not in _LEGAL_SOURCES:
            return "FCI_CLASS_LEGAL_SOURCE_REQUIRED"
        if (not _valid_positive(fact.fields["subscription_min"])
                or not _valid_positive(fact.fields["subscription_step"])
                or not _DIGEST.fullmatch(fact.fields["regulation_sha256"])
                or not fact.fields["regulation_reference"]
                or not fact.fields["broker_minimum_reference"]
                or not fact.fields["broker_step_reference"]):
            return "FCI_CLASS_TERMS_UNVERIFIED"
        try:
            lead = int(fact.fields["redemption_lead_days"])
        except ValueError:
            return "FCI_CLASS_REDEMPTION_LEAD_INVALID"
        if str(lead) != fact.fields["redemption_lead_days"] or not 0 <= lead <= 366:
            return "FCI_CLASS_REDEMPTION_LEAD_INVALID"
    elif fact.kind == "CALENDAR":
        if fact.source_kind not in _LEGAL_SOURCES:
            return "FCI_CALENDAR_LEGAL_SOURCE_REQUIRED"
        try:
            day = datetime.strptime(fact.fields["calendar_date"], "%Y-%m-%d").date()
        except ValueError:
            return "FCI_CALENDAR_DAY_INVALID"
        if (fact.fields["cutoff_zone"] != "America/Argentina/Buenos_Aires"
                or not _HHMM.fullmatch(fact.fields["cutoff_hhmm"])
                or day != at.astimezone(ZoneInfo(
                    "America/Argentina/Buenos_Aires")).date()):
            return "FCI_CALENDAR_CUTOFF_OR_DAY_INVALID"
    elif fact.kind == "ACCOUNT_FEES":
        if fact.source_kind not in _FEE_SOURCES:
            return "FCI_ACCOUNT_TARIFF_SOURCE_REQUIRED"
        if (not _valid_nonnegative(fact.fields["subscription_fee"])
                or not _valid_nonnegative(fact.fields["redemption_fee"])):
            return "FCI_ACCOUNT_FEES_INVALID"
    return None


def inspect_fund_source_bundle(facts: Sequence[FundSourceFact], *, as_of,
                               max_nav_age_days=3) -> FundQuarantineReview:
    """Review financial structure, NEVER release any source as authenticated.

    Distinct fresh sources that disagree require manual causal reconciliation;
    PPI priority is applied by the future authenticated decision boundary, not
    by silently overwriting a fund administrator or fee contract here.
    """
    at = _clock(as_of)
    if at is None:
        raise ValueError("FCI_SOURCE_AS_OF_INVALID")
    if (type(max_nav_age_days) is not int or not 0 <= max_nav_age_days <= 366
            or not isinstance(facts, (list, tuple)) or len(facts) > 64):
        raise ValueError("FCI_SOURCE_BUNDLE_SCOPE_INVALID")
    reasons = []
    warnings = []

    def fail(reason):
        if reason not in reasons:
            reasons.append(reason)

    if not facts:
        fail("FCI_SOURCE_BUNDLE_EMPTY")
    valid = []
    seen = {}
    for fact in facts:
        error = _validate_one(fact, at, max_nav_age_days)
        if error:
            # Secondary provider failures are observable AMBER. They cannot
            # poison a valid PPI/manager origin; if no primary evidence exists
            # the required-kind check below still remains on HOLD.
            if isinstance(fact, FundSourceFact) and fact.source_kind == "IOL_MCP_READONLY":
                warning = "FCI_SECONDARY_SOURCE_IGNORED:" + error
                if warning not in warnings:
                    warnings.append(warning)
            else:
                fail(error)
            continue
        key = (fact.class_key, fact.market, fact.currency, fact.settlement,
               fact.kind, fact.source_kind, fact.source_reference)
        if key in seen and seen[key] != fact.content_sha256:
            fail("FCI_SOURCE_REVISION_CONFLICT")
        elif key not in seen:
            seen[key] = fact.content_sha256
            valid.append(fact)
    identities = {(f.class_key, f.market, f.currency, f.settlement) for f in valid}
    if len(identities) > 1:
        fail("FCI_SOURCE_CLASS_IDENTITY_COLLISION")
    for kind in _FIELDS:
        if kind not in {f.kind for f in valid}:
            fail("FCI_SOURCE_MISSING_" + kind)
        material = {tuple(sorted(f.fields.items())) for f in valid if f.kind == kind}
        if len(material) > 1:
            fail("FCI_SOURCE_CONTRADICTORY_" + kind)
    review = FundQuarantineReview(
        class_key=valid[0].class_key if valid and len(identities) == 1 else "UNVERIFIED",
        evaluated_at=at.isoformat(),
        source_fact_hashes=tuple(sorted({f.content_sha256 for f in valid})),
        covered_kinds=tuple(sorted({f.kind for f in valid})),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        status="QUARANTINED_STRUCTURALLY_COMPLETE" if not reasons else "QUARANTINED_INCOMPLETE")
    return review
