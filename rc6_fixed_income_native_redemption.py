"""Fail-closed PAPER review of an issuer's *already booked* final redemption.

This is a transition primitive, not a native exit executor.  A reviewed issuer
document, an exact lot-specific issuer schedule, and three independent release
pins are required.  The schedule is a *reviewed normalization* of the source,
not an automatic parser or proof of authenticity.  Every economic obligation
must already exist in the reconciled FI event/cash ledger, and the residual
nominal must be zero.  Recording the review never books cash again, changes
``paper_positions``, creates a SELL fill or grants admission authority.

An integrating owner must build and test the terminal-position transition and
supervisor semantics separately.  In particular, this review is not sufficient
to make ``fixed_income_native_exit_authorized`` return True.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timezone
from decimal import Decimal

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from rc6_fixed_income_event_ledger import (
    MAX_LOT_EVENTS, MAX_RECEIPT_BYTES, MAX_SOURCE_BYTES, _canonical, _hash, _seed,
)
from rc6_fixed_income_event_reducer import EconomicEvent
from rc6_fixed_income_portfolio_bridge import position_at
from rc6_fixed_income_runtime import _digest, _json

PAPER_ONLY = True
ENTRY_AUTHORITY = False
NATIVE_EXIT_AUTHORITY = False
REAL_ROUTES_USED = ()
REVIEW_SCHEMA = "rc6.fixed-income-native-redemption-review.v2"
SCHEDULE_SCHEMA = "rc6.fixed-income-lot-issuer-schedule.v1"
REVIEW_AUTHORITY = "RELEASE_REVIEWED_FI_FINAL_REDEMPTION"
FI_FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})
INSTRUMENT_CLASS = {"BONOS": "BOND", "LETRAS": "BILL", "OBLIGACIONES": "CORPORATE_ON"}
_SCHEDULE_KEYS = frozenset({
    "schema", "review_authority", "identity", "issuer_id", "series_id",
    "instrument_class", "bopreal_classification", "source_sha256",
    "source_reference", "source_at", "known_at", "maturity_at",
    "principal_currency", "redemption_basis", "indexation",
    "redemption_face_official", "final_obligation_id", "obligations",
})
_REVIEW_KEYS = frozenset({
    "schema", "paper_only", "real_routes_used", "review_authority",
    "all_maturity_obligations_reviewed", "paper_id", "identity",
    "final_event_id", "paid_obligation_ids", "final_principal_cash",
    "final_nominal_redeemed", "expected_principal_by_currency",
    "expected_cash_by_currency", "issuer_schedule",
})


def _utc(value):
    return aware_datetime(value).astimezone(timezone.utc).isoformat()


def normalized_issuer_schedule_sha256(schedule):
    """Digest the exact reviewed UTF-8 canonical schedule for an external pin.

    A digest produced here is *not* approval: the release process must approve
    the value independently before passing it to the review operation.
    """
    if not isinstance(schedule, dict):
        raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_REQUIRED")
    return _hash(_canonical(schedule).encode("utf-8"))


def _required_string(value, label):
    if (not isinstance(value, str) or not value.strip() or
            value != value.strip() or len(value) > 512):
        raise ValueError(f"FI_REDEMPTION_{label}_INVALID")
    return value


def _ledger_amounts(value, label):
    """Parse literal payout-currency totals; no rounding, FX or implicit ARS."""
    if not isinstance(value, list) or not value or len(value) > MAX_LOT_EVENTS:
        raise ValueError(f"FI_REDEMPTION_{label}_INVALID")
    result = {}
    for item in value:
        if (not isinstance(item, list) or len(item) != 2 or
                not isinstance(item[1], str)):
            raise ValueError(f"FI_REDEMPTION_{label}_INVALID")
        currency = cash_currency(item[0])
        if currency in result:
            raise ValueError(f"FI_REDEMPTION_{label}_DUPLICATE_CURRENCY")
        result[currency] = decimal_value(item[1], label, nonnegative=True)
    return result


def _issuer_schedule(schedule, *, approved_schedule_sha256, approved_issuer_sha256,
                     identity, at):
    """Validate release-pinned *normalized* issuer terms, never infer a bond.

    BOPREAL has no approved instrument-specific cash/indexation/currency
    lifecycle here.  An explicit reviewed non-BOPREAL classification is
    mandatory: a generic BONOS family, a ticker prefix or a hash cannot
    silently authorize it.
    """
    if not isinstance(schedule, dict) or schedule.get("schema") != SCHEDULE_SCHEMA:
        raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_REQUIRED")
    if set(schedule) != _SCHEDULE_KEYS:
        raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_FIELDS_INVALID")
    if normalized_issuer_schedule_sha256(schedule) != _digest(approved_schedule_sha256):
        raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_PIN_MISMATCH")
    if (not isinstance(schedule.get("identity"), list) or
            tuple(schedule["identity"]) != identity):
        raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_IDENTITY_MISMATCH")
    issuer_id = _required_string(schedule.get("issuer_id"), "ISSUER_ID")
    series_id = _required_string(schedule.get("series_id"), "ISSUER_SERIES")
    source_reference = _required_string(schedule.get("source_reference"), "ISSUER_REFERENCE")
    if (schedule.get("instrument_class") == "BOPREAL" or
            schedule.get("bopreal_classification") != "NOT_BOPREAL_INDEPENDENTLY_REVIEWED" or
            any("BOPREAL" in str(value).upper() for value in (
                identity[0], issuer_id, series_id, source_reference,
                schedule.get("redemption_basis"), schedule.get("indexation")))):
        raise ValueError("FI_REDEMPTION_BOPREAL_POLICY_UNVERIFIED")
    if schedule.get("instrument_class") != INSTRUMENT_CLASS[identity[1]]:
        raise ValueError("FI_REDEMPTION_ISSUER_INSTRUMENT_CLASS_MISMATCH")
    if (schedule.get("source_sha256") != approved_issuer_sha256 or
            schedule.get("review_authority") != "INDEPENDENT_ISSUER_SCHEDULE_REVIEWED"):
        raise ValueError("FI_REDEMPTION_ISSUER_SOURCE_OR_REVIEW_UNVERIFIED")
    source_at = aware_datetime(schedule.get("source_at"))
    known_at = aware_datetime(schedule.get("known_at"))
    maturity_at = aware_datetime(schedule.get("maturity_at"))
    if not (source_at <= known_at <= at and source_at <= maturity_at <= at):
        raise ValueError("FI_REDEMPTION_ISSUER_CLOCK_INVALID")
    principal_currency = cash_currency(schedule.get("principal_currency"))
    obligations = schedule.get("obligations")
    if (not isinstance(obligations, list) or not 0 < len(obligations) <= MAX_LOT_EVENTS):
        raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_OBLIGATIONS_INVALID")
    indexed = {}
    exact_keys = {"obligation_id", "kind", "effective_at", "payment_currency",
                  "cash_amount", "nominal_base", "nominal_redeemed"}
    for row in obligations:
        if not isinstance(row, dict) or set(row) != exact_keys:
            raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_ROW_INVALID")
        obligation_id = _required_string(row["obligation_id"], "SCHEDULE_OBLIGATION_ID")
        if obligation_id in indexed:
            raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_DUPLICATE_OBLIGATION")
        if row["kind"] not in {"COUPON", "AMORTIZATION"}:
            raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_KIND_INVALID")
        due = aware_datetime(row["effective_at"])
        if not source_at <= due <= maturity_at:
            raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_CLOCK_INVALID")
        currency = cash_currency(row["payment_currency"])
        if row["kind"] == "AMORTIZATION" and currency != principal_currency:
            raise ValueError("FI_REDEMPTION_ISSUER_PRINCIPAL_CURRENCY_MISMATCH")
        if not isinstance(row["cash_amount"], str) or not isinstance(row["nominal_base"], str):
            raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_AMOUNT_INVALID")
        amount = decimal_value(row["cash_amount"], "schedule cash", nonnegative=True)
        nominal_base = decimal_value(row["nominal_base"], "schedule nominal", positive=True)
        if row["kind"] == "COUPON":
            if row["nominal_redeemed"] is not None or amount <= 0:
                raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_COUPON_INVALID")
            redeemed = None
        else:
            if not isinstance(row["nominal_redeemed"], str):
                raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_REDEMPTION_INVALID")
            redeemed = decimal_value(row["nominal_redeemed"], "schedule redeemed", positive=True)
            if redeemed > nominal_base:
                raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_REDEMPTION_INVALID")
        indexed[obligation_id] = (row["kind"], due, currency, amount, nominal_base, redeemed)
    return issuer_id, series_id, maturity_at, principal_currency, indexed


def init_schema(store):
    """Explicit migration-owner operation; never auto-DDL during an event."""
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS paper_fi_native_redemption_reviews(
          paper_id TEXT PRIMARY KEY REFERENCES paper_fi_lots(paper_id),
          receipt_sha256 TEXT NOT NULL,
          issuer_sha256 TEXT NOT NULL,
          ledger_sha256 TEXT NOT NULL,
          final_event_id TEXT NOT NULL,
          reviewed_at TEXT NOT NULL,
          review_json TEXT NOT NULL)""")


def apply_verified_redemption_review(store, *, receipt_bytes, approved_sha256,
                                     issuer_bytes, approved_issuer_sha256,
                                     approved_schedule_sha256, applied_at):
    """Atomically pin one auditable terminal review, with zero cash effects.

    The *independent* release process must approve receipt, issuer-document
    and normalized-schedule SHA256 pins, inspect the actual issuer provenance,
    and verify normalization/economic rights.  Even three caller-computed
    hashes do not constitute authenticity.  A matching cash ledger remains
    mandatory; an identical review cannot pay a second time.
    """
    if (not isinstance(receipt_bytes, bytes) or not 0 < len(receipt_bytes) <= MAX_RECEIPT_BYTES
            or not isinstance(issuer_bytes, bytes) or not 0 < len(issuer_bytes) <= MAX_SOURCE_BYTES):
        raise ValueError("FI_REDEMPTION_EVIDENCE_SIZE_OR_TYPE_INVALID")
    approved_sha256 = _digest(approved_sha256)
    if _hash(receipt_bytes) != approved_sha256:
        raise ValueError("FI_REDEMPTION_RECEIPT_PIN_MISMATCH")
    receipt = _json(receipt_bytes)
    if (receipt.get("schema") != REVIEW_SCHEMA or receipt.get("paper_only") is not True
            or receipt.get("real_routes_used") != []
            or receipt.get("review_authority") != REVIEW_AUTHORITY
            or receipt.get("all_maturity_obligations_reviewed") is not True):
        raise ValueError("FI_REDEMPTION_REVIEW_ATTESTATION_INVALID")
    if set(receipt) != _REVIEW_KEYS:
        raise ValueError("FI_REDEMPTION_REVIEW_FIELDS_INVALID")
    paper_id = _required_string(receipt.get("paper_id"), "PAPER_ID")
    final_event_id = _required_string(receipt.get("final_event_id"), "FINAL_EVENT_ID")
    issuer_sha256 = _digest(approved_issuer_sha256)
    if _hash(issuer_bytes) != issuer_sha256:
        raise ValueError("FI_REDEMPTION_ISSUER_SOURCE_MISMATCH")
    at = aware_datetime(applied_at)
    raw_identity = receipt.get("identity")
    if (not isinstance(raw_identity, list) or len(raw_identity) != 5 or
            any(not isinstance(item, str) or not item or item != item.strip()
                for item in raw_identity)):
        raise ValueError("FI_REDEMPTION_IDENTITY_MISMATCH")
    identity = tuple(raw_identity)
    if identity[1] not in FI_FAMILIES:
        raise ValueError("FI_REDEMPTION_IDENTITY_MISMATCH")
    schedule = receipt.get("issuer_schedule")
    issuer_id, series_id, maturity_at, principal_currency, expected = _issuer_schedule(
        schedule, approved_schedule_sha256=approved_schedule_sha256,
        approved_issuer_sha256=issuer_sha256, identity=identity, at=at)
    source_reference = _required_string(schedule.get("source_reference"), "ISSUER_REFERENCE")
    obligations = receipt.get("paid_obligation_ids")
    if (not isinstance(obligations, list) or not obligations or
            any(not isinstance(o, str) or not o.strip() or len(o) > 512
                for o in obligations) or len(obligations) != len(set(obligations))):
        raise ValueError("FI_REDEMPTION_OBLIGATIONS_INVALID")

    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'paper_fi_%'")}
        if "paper_fi_native_redemption_reviews" not in tables:
            raise ValueError("FI_REDEMPTION_REVIEW_SCHEMA_MISSING")
        safety = db.execute(
            "SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if (safety is None or safety[0] != "PRODUCTION_PAPER" or
                type(safety[1]) is not int or safety[1] != 0):
            raise ValueError("FI_REDEMPTION_PAPER_SAFETY_REQUIRED")
        seed = _seed(db, paper_id)  # immutable PAPER BUY, OPEN, one verified fill
        if seed.family not in FI_FAMILIES or identity != (
                seed.symbol, seed.family, seed.market, seed.trading_currency, seed.settlement):
            raise ValueError("FI_REDEMPTION_IDENTITY_MISMATCH")
        if seed.family == "LETRAS" and (
                schedule.get("redemption_basis") != "FIXED_KNOWN_FACE" or
                schedule.get("indexation") != "NONE" or
                schedule.get("redemption_face_official") is not True):
            raise ValueError("FI_REDEMPTION_LETTER_FACE_UNVERIFIED")
        review = position_at(db, paper_id, applied_at)
        if review is None or review.residual_nominal != Decimal(0):
            raise ValueError("FI_REDEMPTION_NOT_FULLY_AMORTIZED")
        rows = db.execute("""SELECT event_json,receipt_sha256,booked_at
             FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id""", (paper_id,)).fetchall()
        if not rows or any(aware_datetime(row["booked_at"]) > at for row in rows):
            raise ValueError("FI_REDEMPTION_EVENT_NOT_BOOKED_AT_REVIEW")
        events = [EconomicEvent(**json.loads(row["event_json"])) for row in rows]
        economic = [e for e in events if e.kind != "FIXING"]
        if sorted(obligations) != sorted(e.obligation_id for e in economic):
            raise ValueError("FI_REDEMPTION_OBLIGATION_COVERAGE_MISMATCH")
        if set(expected) != set(obligations):
            raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_COVERAGE_MISMATCH")
        for event in economic:
            row = expected[event.obligation_id]
            economic_row = (event.kind, aware_datetime(event.effective_at),
                            cash_currency(event.payment_currency),
                            decimal_value(event.cash_amount, "ledger cash", nonnegative=True),
                            decimal_value(event.nominal_base, "ledger nominal", positive=True),
                            (decimal_value(event.nominal_redeemed, "ledger redeemed", positive=True)
                             if event.kind == "AMORTIZATION" else None))
            if row != economic_row:
                raise ValueError("FI_REDEMPTION_ISSUER_SCHEDULE_ECONOMICS_MISMATCH")
        final = next((e for e in economic if e.event_id == final_event_id), None)
        if (final is None or final.kind != "AMORTIZATION" or
                aware_datetime(final.effective_at) != maturity_at or
                schedule.get("final_obligation_id") != final.obligation_id or
                any(aware_datetime(e.effective_at) > maturity_at for e in events)):
            raise ValueError("FI_REDEMPTION_TERMINAL_EVENT_MISMATCH")
        try:
            final_economics_match = (
                cash_currency(final.payment_currency) == principal_currency and
                decimal_value(final.cash_amount, "final cash", positive=True) ==
                decimal_value(receipt.get("final_principal_cash"), "final receipt cash", positive=True) and
                decimal_value(final.nominal_redeemed, "final nominal", positive=True) ==
                decimal_value(receipt.get("final_nominal_redeemed"), "final receipt nominal", positive=True))
        except (TypeError, ValueError) as exc:
            raise ValueError("FI_REDEMPTION_TERMINAL_ECONOMICS_MISMATCH") from exc
        if not final_economics_match:
            raise ValueError("FI_REDEMPTION_TERMINAL_ECONOMICS_MISMATCH")
        if (_ledger_amounts(receipt.get("expected_principal_by_currency"), "PRINCIPAL_TOTALS") !=
                dict(review.principal_by_currency) or
                _ledger_amounts(receipt.get("expected_cash_by_currency"), "CASH_TOTALS") !=
                dict(review.cash_by_currency)):
            raise ValueError("FI_REDEMPTION_LEDGER_CASH_TOTALS_MISMATCH")
        ledger_sha = _hash(_canonical({
            "seed": asdict(seed), "view": asdict(review),
            "events": [[row["event_json"], row["receipt_sha256"], row["booked_at"]]
                       for row in rows],
        }).encode())
        prior = db.execute("""SELECT receipt_sha256,issuer_sha256,ledger_sha256,review_json,reviewed_at
             FROM paper_fi_native_redemption_reviews WHERE paper_id=?""", (paper_id,)).fetchone()
        if prior:
            if (prior[0] != approved_sha256 or prior[1] != issuer_sha256 or prior[2] != ledger_sha):
                raise ValueError("FI_REDEMPTION_REVIEW_REPLAY_CONFLICT")
            if at < aware_datetime(prior[4]):
                raise ValueError("FI_REDEMPTION_REVIEW_CLOCK_ROLLBACK")
            return json.loads(prior[3])
        result = {
            "schema": REVIEW_SCHEMA,
            "paper_id": paper_id,
            "family": seed.family,
            "symbol": seed.symbol,
            "final_event_id": final_event_id,
            "maturity_at": _utc(maturity_at),
            "reviewed_at": _utc(at),
            "issuer_id": issuer_id,
            "issuer_series_id": series_id,
            "issuer_source_reference": source_reference,
            "issuer_sha256": issuer_sha256,
            "issuer_schedule_sha256": approved_schedule_sha256,
            "receipt_sha256": approved_sha256,
            "ledger_sha256": ledger_sha,
            "residual_nominal": "0",
            "principal_by_currency": [[name, str(amount)] for name, amount in review.principal_by_currency],
            "cash_by_currency": [[name, str(amount)] for name, amount in review.cash_by_currency],
            "reconciled_cash_event_count": len(economic),
            "cash_side_effect": "0",
            "paper_position_changed": False,
            "paper_fills_created": 0,
            "native_exit_authorized": False,
            "paper_only": True,
            "real_routes_used": [],
        }
        db.execute("""INSERT INTO paper_fi_native_redemption_reviews
          (paper_id,receipt_sha256,issuer_sha256,ledger_sha256,final_event_id,reviewed_at,review_json)
          VALUES (?,?,?,?,?,?,?)""", (
            paper_id, approved_sha256, issuer_sha256, ledger_sha, final_event_id,
            _utc(at), _canonical(result)))
        return result
