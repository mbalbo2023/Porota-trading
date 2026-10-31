"""Read-only economic reconciliation of an existing FCI PAPER lifecycle.

The durable lifecycle reserves cash on SUBSCRIBE_REQUESTED and releases it on
REDEEMED. This module checks explicit NAV/allocation/payment receipts and
their clocks before reporting units or a closed PAPER result. It neither
submits an intent nor attests that a supplied receipt is authentic. Fund class,
cutoff, redemption terms, account fees and a strategy require independent
evidence before a caller may initiate a new subscription.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Mapping, Sequence

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from rc6_paper_family_lifecycle import PaperFundTerms


PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STATES = ("SUBSCRIBE_REQUESTED", "PENDING", "NAV_APPLIED", "SETTLED",
           "REDEEM_REQUESTED", "REDEEMED")


@dataclass(frozen=True)
class FundClassEvidence:
    terms: PaperFundTerms
    class_key: str
    regulation_reference: str
    regulation_sha256: str
    known_at: str


@dataclass(frozen=True)
class FundReceipt:
    event_id: str
    kind: str
    class_key: str
    currency: str
    source_kind: str
    source_reference: str
    source_sha256: str
    source_at: str
    recorded_at: str
    nav_effective_at: str | None = None
    nav_reference: str | None = None
    nav_per_unit: Decimal | None = None
    units: Decimal | None = None
    explicit_fee: Decimal | None = None
    cash_amount: Decimal | None = None


@dataclass(frozen=True)
class FundSnapshot:
    lifecycle_id: str
    symbol: str
    state: str
    currency: str
    subscription_ars: Decimal
    reserved_cash_ars: Decimal
    units: Decimal | None
    nav_per_unit: Decimal | None
    redemption_cash_ars: Decimal | None
    net_pnl_ars: Decimal | None
    evidence_references: tuple[str, ...]
    status: str = "PAPER_INPUT_RECONCILIATION_ONLY"
    entry_authority: bool = False
    real_routes_used: tuple = ()


def _required(value, code):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(code)
    return value


def _hash(value, code):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(code)


def _amount(value, code, *, positive=False):
    try:
        return decimal_value(value, code, positive=positive, nonnegative=not positive)
    except (ValueError, TypeError) as exc:
        raise ValueError(code) from exc


def _time(value, code):
    try:
        return aware_datetime(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(code) from exc


def _fund_terms_digest(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("FCI_TERMS_SNAPSHOT_REQUIRED")
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def _receipt(receipt, *, event, evidence, as_of, expected_kind, expected_source):
    if not isinstance(receipt, FundReceipt) or (
        receipt.event_id != event["event_id"] or receipt.kind != expected_kind
        or receipt.class_key != evidence.class_key
        or receipt.currency != evidence.terms.currency
        or receipt.source_kind != expected_source
    ):
        raise ValueError("FCI_RECEIPT_IDENTITY_OR_SOURCE_MISMATCH")
    _required(receipt.source_reference, "FCI_RECEIPT_SOURCE_REQUIRED")
    _hash(receipt.source_sha256, "FCI_RECEIPT_SHA256_INVALID")
    source = _time(receipt.source_at, "FCI_RECEIPT_SOURCE_TIME_INVALID")
    recorded = _time(receipt.recorded_at, "FCI_RECEIPT_RECORDED_TIME_INVALID")
    event_at = _time(event["occurred_at"], "FCI_EVENT_TIME_INVALID")
    if not source <= recorded <= event_at <= as_of:
        raise ValueError("FCI_RECEIPT_PIT_INVALID")
    return receipt.source_reference


def reconcile_fund_events(
    lifecycle_id: str, events: Sequence[Mapping], receipts: Sequence[FundReceipt],
    evidence: FundClassEvidence, *, as_of,
) -> FundSnapshot:
    """Fold exact durable PAPER transitions; do not infer an absent receipt.

    Receipt arithmetic validates only supplied evidence and cannot authenticate
    a fund administrator or broker statement by itself. Full liquidation only;
    a partial redemption needs its own independently specified ledger policy.
    """
    _required(lifecycle_id, "FCI_LIFECYCLE_ID_REQUIRED")
    if not isinstance(evidence, FundClassEvidence) or not isinstance(evidence.terms, PaperFundTerms):
        raise ValueError("FCI_CLASS_TERMS_REQUIRED")
    terms = evidence.terms
    if terms.currency != "ARS" or terms.family != "FCI":
        raise ValueError("FCI_ARS_CLASS_REQUIRED")
    if evidence.class_key != terms.symbol:
        raise ValueError("FCI_CLASS_IDENTITY_MISMATCH")
    _required(evidence.regulation_reference, "FCI_REGULATION_REFERENCE_REQUIRED")
    _hash(evidence.regulation_sha256, "FCI_REGULATION_SHA256_INVALID")
    cutoff = _time(as_of, "FCI_AS_OF_INVALID")
    if _time(evidence.known_at, "FCI_REGULATION_TIME_INVALID") > cutoff:
        raise ValueError("FCI_REGULATION_NOT_KNOWN_AT_PIT")
    if not isinstance(events, Sequence) or not events or len(events) > len(_STATES):
        raise ValueError("FCI_EVENT_SEQUENCE_INVALID")
    receipt_by_event = {}
    for receipt in receipts:
        if not isinstance(receipt, FundReceipt) or receipt.event_id in receipt_by_event:
            raise ValueError("FCI_RECEIPT_DUPLICATE_OR_INVALID")
        receipt_by_event[receipt.event_id] = receipt
    seen_events = set()
    source_refs = [evidence.regulation_reference]
    paid = None
    units = nav = redemption = net = None
    nav_reference = nav_effective_at = None
    initial_at = redeem_requested_at = None
    last_at = None
    state = ""
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            raise ValueError("FCI_EVENT_REQUIRED")
        event_id = _required(event.get("event_id"), "FCI_EVENT_ID_REQUIRED")
        if event_id in seen_events or event.get("lifecycle_id") != lifecycle_id:
            raise ValueError("FCI_EVENT_IDENTITY_OR_REPLAY_CONFLICT")
        seen_events.add(event_id)
        state = event.get("to_state")
        if state != _STATES[index] or event.get("family") != "FCI":
            raise ValueError("FCI_EVENT_SEQUENCE_INVALID")
        at = _time(event.get("occurred_at"), "FCI_EVENT_TIME_INVALID")
        if (last_at is not None and at < last_at) or at > cutoff:
            raise ValueError("FCI_EVENT_PIT_INVALID")
        last_at = at
        detail = event.get("detail")
        if not isinstance(detail, dict):
            raise ValueError("FCI_EVENT_DETAIL_INVALID")
        amount = _amount(event.get("amount"), "FCI_EVENT_AMOUNT_INVALID") if index != 0 else None
        if index == 0:
            initial_at = at
            amount = decimal_value(event.get("amount"), "FCI_EVENT_AMOUNT_INVALID")
            paid = -amount
            _amount(paid, "FCI_SUBSCRIPTION_AMOUNT_INVALID", positive=True)
            expected = json.loads(json.dumps(asdict(terms), default=str))
            if (detail.get("mode") != "PRODUCTION_PAPER"
                    or detail.get("execution") != "SIMULATION"
                    or detail.get("fund_terms") != expected
                    or detail.get("metadata_source") != terms.metadata_source
                    or detail.get("subscription_amount") != str(paid)):
                raise ValueError("FCI_SUBSCRIPTION_TERMS_OR_AMOUNT_MISMATCH")
            if _time(evidence.known_at, "FCI_REGULATION_TIME_INVALID") > at:
                raise ValueError("FCI_REGULATION_NOT_KNOWN_BEFORE_SUBSCRIPTION")
        elif index in (1, 2, 3, 4) and amount != 0:
            raise ValueError("FCI_INTERMEDIATE_EVENT_CANNOT_MOVE_CASH")
        if state == "NAV_APPLIED":
            receipt = receipt_by_event.pop(event_id, None)
            source_refs.append(_receipt(receipt, event=event, evidence=evidence,
                                        as_of=cutoff, expected_kind="NAV_APPLIED",
                                        expected_source="FUND_ADMINISTRATOR_NAV"))
            nav = _amount(receipt.nav_per_unit, "FCI_NAV_INVALID", positive=True)
            nav_effective_at = _time(receipt.nav_effective_at, "FCI_NAV_DATE_REQUIRED")
            if not initial_at <= nav_effective_at <= _time(receipt.source_at, "FCI_NAV_SOURCE_TIME_INVALID"):
                raise ValueError("FCI_NAV_EFFECTIVE_TIME_INVALID")
            nav_reference = receipt.source_reference
            if any(value is not None for value in (receipt.units, receipt.explicit_fee, receipt.cash_amount)):
                raise ValueError("FCI_NAV_RECEIPT_FIELDS_CONFLICT")
        elif state == "SETTLED":
            receipt = receipt_by_event.pop(event_id, None)
            source_refs.append(_receipt(receipt, event=event, evidence=evidence,
                                        as_of=cutoff, expected_kind="SETTLED",
                                        expected_source="BROKER_FUND_ALLOCATION"))
            units = _amount(receipt.units, "FCI_UNITS_INVALID", positive=True)
            fee = _amount(receipt.explicit_fee, "FCI_SUBSCRIPTION_FEE_UNKNOWN")
            if (receipt.nav_per_unit != nav or receipt.cash_amount != paid
                    or receipt.nav_reference != nav_reference
                    or _time(receipt.nav_effective_at, "FCI_ALLOCATION_NAV_DATE_REQUIRED") != nav_effective_at):
                raise ValueError("FCI_ALLOCATION_NAV_OR_CASH_MISMATCH")
            if units * nav + fee != paid:
                raise ValueError("FCI_ALLOCATION_ARITHMETIC_MISMATCH")
        elif state == "REDEEM_REQUESTED":
            redeem_requested_at = at
            if _amount(detail.get("units"), "FCI_REDEMPTION_UNITS_UNKNOWN", positive=True) != units:
                raise ValueError("FCI_REDEMPTION_UNITS_MISMATCH")
        elif state == "REDEEMED":
            receipt = receipt_by_event.pop(event_id, None)
            source_refs.append(_receipt(receipt, event=event, evidence=evidence,
                                        as_of=cutoff, expected_kind="REDEEMED",
                                        expected_source="BROKER_FUND_PAYMENT"))
            if _amount(receipt.units, "FCI_REDEMPTION_UNITS_UNKNOWN", positive=True) != units:
                raise ValueError("FCI_REDEMPTION_UNITS_MISMATCH")
            redemption_nav = _amount(receipt.nav_per_unit, "FCI_REDEMPTION_NAV_INVALID", positive=True)
            nav_date = _time(receipt.nav_effective_at, "FCI_REDEMPTION_NAV_DATE_REQUIRED")
            if (not redeem_requested_at <= nav_date <= _time(receipt.source_at, "FCI_REDEMPTION_SOURCE_TIME_INVALID")
                    or not isinstance(receipt.nav_reference, str) or not receipt.nav_reference.strip()
                    or receipt.nav_reference == receipt.source_reference):
                raise ValueError("FCI_REDEMPTION_NAV_PROVENANCE_INVALID")
            fee = _amount(receipt.explicit_fee, "FCI_REDEMPTION_FEE_UNKNOWN")
            redemption = _amount(receipt.cash_amount, "FCI_REDEMPTION_CASH_INVALID", positive=True)
            if amount != redemption or units * redemption_nav - fee != redemption:
                raise ValueError("FCI_REDEMPTION_ARITHMETIC_MISMATCH")
            net = redemption - paid
    if receipt_by_event:
        raise ValueError("FCI_ORPHAN_RECEIPT")
    return FundSnapshot(lifecycle_id, terms.symbol, state, cash_currency(terms.currency),
                        paid, -paid if state != "REDEEMED" else Decimal(0), units, nav,
                        redemption, net, tuple(source_refs))


def reconcile_fund_store(path, lifecycle_id, receipts, evidence, *, as_of):
    """Read only one identified PAPER lifecycle; never follow database aliases."""
    from cg_paper_workspace import checked_path, identity_from_connection
    resolved = checked_path(path)
    if not resolved.is_file():
        raise ValueError("FCI_DURABLE_DATABASE_UNVERIFIED")
    with sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        identity_from_connection(db)
        root = db.execute("SELECT * FROM paper_family_lifecycle WHERE lifecycle_id=?",
                          (lifecycle_id,)).fetchone()
        if not root or (root["family"], root["instrument"], root["currency"]) != (
            "FCI", evidence.terms.symbol, evidence.terms.currency
        ):
            raise ValueError("FCI_DURABLE_IDENTITY_MISMATCH")
        rows = db.execute("""SELECT * FROM paper_family_lifecycle_events WHERE lifecycle_id=?
                         ORDER BY rowid""", (lifecycle_id,)).fetchall()
    events = [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]
    result = reconcile_fund_events(lifecycle_id, events, receipts, evidence, as_of=as_of)
    if root["state"] != result.state:
        raise ValueError("FCI_DURABLE_STATE_MISMATCH")
    if (not rows or _time(root["updated_at"], "FCI_DURABLE_CLOCK_INVALID") !=
            _time(rows[-1]["occurred_at"], "FCI_DURABLE_CLOCK_INVALID")
            or decimal_value(root["ledger_total"], "FCI_DURABLE_TOTAL_INVALID") !=
            sum((decimal_value(row["amount"], "FCI_DURABLE_AMOUNT_INVALID") for row in rows), Decimal(0))):
        raise ValueError("FCI_DURABLE_TOTAL_OR_CLOCK_MISMATCH")
    return result
