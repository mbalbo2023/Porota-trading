"""Fail-closed PAPER review of an issuer's *already booked* final redemption.

This is a transition primitive, not a native exit executor.  A reviewed issuer
document and an independently approved receipt pin are required.  The economic
payment must already exist in the immutable FI event/cash ledger, and the
residual nominal must be zero.  Recording the review never books cash again,
changes ``paper_positions``, creates a SELL fill or grants admission authority.

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
    MAX_RECEIPT_BYTES, MAX_SOURCE_BYTES, _canonical, _hash, _seed,
)
from rc6_fixed_income_event_reducer import EconomicEvent
from rc6_fixed_income_portfolio_bridge import position_at
from rc6_fixed_income_runtime import _digest, _json

PAPER_ONLY = True
ENTRY_AUTHORITY = False
NATIVE_EXIT_AUTHORITY = False
REAL_ROUTES_USED = ()
REVIEW_SCHEMA = "rc6.fixed-income-native-redemption-review.v1"
REVIEW_AUTHORITY = "RELEASE_REVIEWED_FI_FINAL_REDEMPTION"
FI_FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})


def _utc(value):
    return aware_datetime(value).astimezone(timezone.utc).isoformat()


def _required_string(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"FI_REDEMPTION_{label}_INVALID")
    return value


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
                                     issuer_bytes, applied_at):
    """Atomically pin one auditable terminal review, with zero cash effects.

    The *independent* release process must approve ``approved_sha256`` and
    authenticate the issuer source.  A matching caller-supplied hash alone is
    not proof of authenticity.  Missing schema, receipts and economic rights
    fail closed; replaying the identical review is idempotent.
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
    paper_id = _required_string(receipt.get("paper_id"), "PAPER_ID")
    final_event_id = _required_string(receipt.get("final_event_id"), "FINAL_EVENT_ID")
    issuer = receipt.get("issuer_terms")
    if not isinstance(issuer, dict):
        raise ValueError("FI_REDEMPTION_ISSUER_TERMS_MISSING")
    issuer_sha256 = _digest(issuer.get("source_sha256"))
    if _hash(issuer_bytes) != issuer_sha256:
        raise ValueError("FI_REDEMPTION_ISSUER_SOURCE_MISMATCH")
    source_reference = _required_string(issuer.get("source_reference"), "ISSUER_REFERENCE")
    at = aware_datetime(applied_at)
    issued_at, known_at = (aware_datetime(issuer.get(key)) for key in ("source_at", "known_at"))
    maturity_at = aware_datetime(issuer.get("maturity_at"))
    if not (issued_at <= known_at <= at and maturity_at <= at):
        raise ValueError("FI_REDEMPTION_ISSUER_CLOCK_INVALID")
    principal_currency = cash_currency(issuer.get("principal_currency"))
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
        if seed.family not in FI_FAMILIES or tuple(receipt.get("identity") or ()) != (
                seed.symbol, seed.family, seed.market, seed.trading_currency, seed.settlement):
            raise ValueError("FI_REDEMPTION_IDENTITY_MISMATCH")
        if seed.family == "LETRAS" and (
                issuer.get("redemption_basis") != "FIXED_KNOWN_FACE" or
                issuer.get("indexation") != "NONE" or
                issuer.get("redemption_face_official") is not True):
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
        final = next((e for e in economic if e.event_id == final_event_id), None)
        if (final is None or final.kind != "AMORTIZATION" or
                aware_datetime(final.effective_at) != maturity_at or
                any(aware_datetime(e.effective_at) > maturity_at for e in events)):
            raise ValueError("FI_REDEMPTION_TERMINAL_EVENT_MISMATCH")
        if (cash_currency(final.payment_currency) != principal_currency or
                decimal_value(final.cash_amount, "final cash", nonnegative=True) !=
                decimal_value(receipt.get("final_principal_cash"), "final receipt cash", nonnegative=True) or
                decimal_value(final.nominal_redeemed, "final nominal", positive=True) !=
                decimal_value(receipt.get("final_nominal_redeemed"), "final receipt nominal", positive=True)):
            raise ValueError("FI_REDEMPTION_TERMINAL_ECONOMICS_MISMATCH")
        ledger_sha = _hash(_canonical({
            "seed": asdict(seed), "view": asdict(review),
            "events": [[row["event_json"], row["receipt_sha256"], row["booked_at"]]
                       for row in rows],
        }).encode())
        prior = db.execute("""SELECT receipt_sha256,issuer_sha256,ledger_sha256,review_json
             FROM paper_fi_native_redemption_reviews WHERE paper_id=?""", (paper_id,)).fetchone()
        if prior:
            if (prior[0] != approved_sha256 or prior[1] != issuer_sha256 or prior[2] != ledger_sha):
                raise ValueError("FI_REDEMPTION_REVIEW_REPLAY_CONFLICT")
            return json.loads(prior[3])
        result = {
            "schema": REVIEW_SCHEMA,
            "paper_id": paper_id,
            "family": seed.family,
            "symbol": seed.symbol,
            "final_event_id": final_event_id,
            "maturity_at": _utc(maturity_at),
            "reviewed_at": _utc(at),
            "issuer_source_reference": source_reference,
            "issuer_sha256": issuer_sha256,
            "receipt_sha256": approved_sha256,
            "ledger_sha256": ledger_sha,
            "residual_nominal": "0",
            "principal_by_currency": [[name, str(amount)] for name, amount in review.principal_by_currency],
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
