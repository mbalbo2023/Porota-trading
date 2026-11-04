"""Atomic PAPER maturity closure of a fully paid, independently reviewed FI lot.

An issuer review is necessary but is not an order or an admission decision.
This transition is invoked explicitly by the release-reviewed PAPER owner; it
never fetches data, creates SELL fills, translates FX, or credits cash.  The
existing event ledger has *already* booked every issuer payment exactly once.

Market sales are deliberately a separate operation: an issuer's repayment is
not a bid, an order book, a clearing receipt, or a sale settlement.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timezone

from bs_instrument_contracts import aware_datetime, decimal_value
from rc6_fixed_income_event_ledger import _canonical, _hash, _seed
from rc6_fixed_income_portfolio_bridge import position_at, valuation_at
from rc6_fixed_income_runtime import _digest

PAPER_ONLY = True
REAL_ROUTES_USED = ()
ENTRY_AUTHORITY = False
MARKET_SALE_AUTHORITY = False
SCHEMA = "rc6.fi-maturity-terminal.v1"
REASON = "FI_NATIVE_MATURITY_REVIEWED"
_RESULT_FIELDS = frozenset({
    "schema", "paper_id", "request_id", "closed_at", "kind", "family",
    "review_receipt_sha256", "issuer_sha256", "ledger_sha256",
    "residual_nominal", "realized_pnl", "pnl_quality",
    "economic_cash_written", "sell_fills_created", "paper_only",
    "real_routes_used", "real_orders_sent",
})


def init_schema(store):
    """Called only by the parent SQLite migration, never a hot writer."""
    with store.connect() as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS paper_fi_native_maturities(
          paper_id TEXT PRIMARY KEY REFERENCES paper_positions(paper_id),
          request_id TEXT NOT NULL, review_receipt_sha256 TEXT NOT NULL,
          issuer_sha256 TEXT NOT NULL, ledger_sha256 TEXT NOT NULL,
          closed_at TEXT NOT NULL, result_json TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS paper_fi_native_maturity_no_update
          BEFORE UPDATE ON paper_fi_native_maturities
          BEGIN SELECT RAISE(ABORT,'FI_TERMINAL_RECEIPT_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS paper_fi_native_maturity_no_delete
          BEFORE DELETE ON paper_fi_native_maturities
          BEGIN SELECT RAISE(ABORT,'FI_TERMINAL_RECEIPT_IMMUTABLE'); END;
        """)


def _table(connection):
    return bool(connection.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='paper_fi_native_maturities'""").fetchone())


def terminal_row(connection, paper_id):
    if not _table(connection):
        return None
    return connection.execute(
        "SELECT * FROM paper_fi_native_maturities WHERE paper_id=?", (paper_id,)
    ).fetchone()


def _ledger_digest(connection, seed, view):
    rows = connection.execute("""SELECT event_json,receipt_sha256,booked_at
        FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id""",
        (seed.paper_id,)).fetchall()
    return _hash(_canonical({
        "seed": asdict(seed), "view": asdict(view),
        "events": [[r[0], r[1], r[2]] for r in rows],
    }).encode()), rows


def terminal_at(connection, paper_id, as_of=None):
    """Validate the durable close; return None before its PIT booking clock.

    This is a reader of the *same* transaction as paper_positions. A corrupt
    sidecar must fail even for a historical cutoff instead of disappearing.
    """
    row = terminal_row(connection, paper_id)
    if row is None:
        return None
    root = connection.execute("SELECT * FROM paper_positions WHERE paper_id=?",
                              (paper_id,)).fetchone()
    if (root is None or root["status"] != "CLOSED" or root["close_reason"] != REASON
            or aware_datetime(root["closed_at"]) != aware_datetime(row["closed_at"])):
        raise ValueError("FI_TERMINAL_POSITION_RECEIPT_MISMATCH")
    review = connection.execute("""SELECT receipt_sha256,issuer_sha256,ledger_sha256,
        reviewed_at,review_json FROM paper_fi_native_redemption_reviews
        WHERE paper_id=?""", (paper_id,)).fetchone()
    if (review is None or tuple(row[k] for k in (
            "review_receipt_sha256", "issuer_sha256", "ledger_sha256")) !=
            (review[0], review[1], review[2]) or
            aware_datetime(review[3]) > aware_datetime(row["closed_at"])):
        raise ValueError("FI_TERMINAL_REVIEW_RECEIPT_MISMATCH")
    receipt = json.loads(row["result_json"])
    reviewed = json.loads(review[4])
    if (_canonical(reviewed) != review[4] or
            reviewed.get("receipt_sha256") != review[0] or
            reviewed.get("issuer_sha256") != review[1] or
            reviewed.get("ledger_sha256") != review[2] or
            reviewed.get("reviewed_at") != review[3] or
            reviewed.get("paper_only") is not True or
            reviewed.get("real_routes_used") != [] or
            reviewed.get("residual_nominal") != "0" or
            reviewed.get("native_exit_authorized") is not False):
        raise ValueError("FI_TERMINAL_REVIEW_CONTENT_CHANGED")
    if (_canonical(receipt) != row["result_json"] or set(receipt) != _RESULT_FIELDS or
            receipt.get("schema") != SCHEMA or receipt.get("paper_id") != paper_id or
            receipt.get("request_id") != row["request_id"] or
            receipt.get("closed_at") != row["closed_at"] or
            receipt.get("review_receipt_sha256") != row["review_receipt_sha256"] or
            receipt.get("issuer_sha256") != row["issuer_sha256"] or
            receipt.get("ledger_sha256") != row["ledger_sha256"] or
            receipt.get("residual_nominal") != "0" or
            receipt.get("kind") != "MATURITY" or
            receipt.get("real_routes_used") != [] or receipt.get("paper_only") is not True or
            receipt.get("real_orders_sent") != 0 or
            receipt.get("economic_cash_written") != "0" or
            receipt.get("sell_fills_created") != 0):
        raise ValueError("FI_TERMINAL_IMMUTABLE_RECEIPT_INVALID")
    seed = _seed(connection, paper_id)
    view = position_at(connection, paper_id, row["closed_at"])
    if view is None or view.residual_nominal != 0:
        raise ValueError("FI_TERMINAL_NOMINAL_RECONCILIATION_FAILED")
    digest, events = _ledger_digest(connection, seed, view)
    if digest != row["ledger_sha256"] or any(
            aware_datetime(e["booked_at"]) > aware_datetime(row["closed_at"])
            for e in events):
        raise ValueError("FI_TERMINAL_EVENT_LEDGER_CHANGED")
    valuation = valuation_at(connection, paper_id, row["closed_at"], root["entry_price"])
    pnl = valuation.realized_pnl
    gross = (pnl + decimal_value(root["entry_cost"], "FI entry cost", nonnegative=True)
             * view.redeemed_nominal / view.initial_nominal
             if pnl is not None else None)
    if (receipt["family"] != seed.family or
            receipt["realized_pnl"] != (str(pnl) if pnl is not None else None) or
            receipt["pnl_quality"] != (
                "FI_PAPER_COST_BASIS_RECONCILED" if pnl is not None
                else "FI_CROSS_CURRENCY_PNL_UNVERIFIED") or
            root["net_pnl"] != receipt["realized_pnl"] or
            root["gross_pnl"] != (str(gross) if gross is not None else None) or
            root["exit_price"] is not None or root["exit_cost"] != "0"):
        raise ValueError("FI_TERMINAL_PNL_OR_SETTLEMENT_CONFLICT")
    if as_of is not None and aware_datetime(as_of) < aware_datetime(row["closed_at"]):
        return None
    return receipt


def apply_reviewed_maturity(store, *, paper_id, request_id,
                            approved_review_receipt_sha256, applied_at):
    """One atomic no-SELL terminal transition, with exact idempotent retry.

    External approval of the review receipt must precede calling this API;
    caller-selected synthetic hashes never demonstrate issuer authenticity.
    The ledger's payment cash remains in its original currency exactly once.
    """
    if (not isinstance(paper_id, str) or not paper_id.startswith("PAPER-") or
            not isinstance(request_id, str) or not 0 < len(request_id) <= 128 or
            request_id != request_id.strip()):
        raise ValueError("FI_TERMINAL_REQUEST_IDENTITY_INVALID")
    approved = _digest(approved_review_receipt_sha256)
    at = aware_datetime(applied_at)
    at_iso = at.astimezone(timezone.utc).isoformat()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _table(db):
            raise ValueError("FI_TERMINAL_SCHEMA_MISSING")
        state = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if (state is None or state[0] != "PRODUCTION_PAPER" or
                type(state[1]) is not int or state[1] != 0):
            raise ValueError("FI_TERMINAL_PAPER_SAFETY_REQUIRED")
        previous = terminal_row(db, paper_id)
        if previous is not None:
            if (previous["request_id"] != request_id or
                    previous["review_receipt_sha256"] != approved):
                raise ValueError("FI_TERMINAL_REPLAY_CONFLICT")
            if at < aware_datetime(previous["closed_at"]):
                raise ValueError("FI_TERMINAL_CLOCK_ROLLBACK")
            return terminal_at(db, paper_id)
        review = db.execute("""SELECT * FROM paper_fi_native_redemption_reviews
            WHERE paper_id=?""", (paper_id,)).fetchone()
        if review is None or review["receipt_sha256"] != approved:
            raise ValueError("FI_TERMINAL_APPROVED_REVIEW_REQUIRED")
        review_json = json.loads(review["review_json"])
        if (_canonical(review_json) != review["review_json"] or
                review_json.get("receipt_sha256") != approved or
                review_json.get("ledger_sha256") != review["ledger_sha256"] or
                review_json.get("issuer_sha256") != review["issuer_sha256"] or
                review_json.get("residual_nominal") != "0" or
                review_json.get("cash_side_effect") != "0" or
                review_json.get("paper_fills_created") != 0 or
                review_json.get("real_routes_used") != [] or
                review_json.get("paper_only") is not True):
            raise ValueError("FI_TERMINAL_REVIEW_FIELDS_INVALID")
        if at < aware_datetime(review["reviewed_at"]):
            raise ValueError("FI_TERMINAL_REVIEW_NOT_YET_KNOWN")
        seed = _seed(db, paper_id)
        view = position_at(db, paper_id, at_iso)
        if view is None or view.residual_nominal != 0:
            raise ValueError("FI_TERMINAL_NOT_FULLY_REDEEMED")
        digest, events = _ledger_digest(db, seed, view)
        if digest != review["ledger_sha256"] or any(
                aware_datetime(e["booked_at"]) > at for e in events):
            raise ValueError("FI_TERMINAL_LEDGER_REVIEW_DRIFT")
        root = db.execute("SELECT * FROM paper_positions WHERE paper_id=?",
                          (paper_id,)).fetchone()
        if root["status"] != "OPEN" or root["closed_at"] is not None:
            raise ValueError("FI_TERMINAL_ALREADY_CLOSED_OR_INVALID")
        # A native PAPER sale is an allocated SELL with its own receipt. It
        # cannot be reclassified as issuer cash or another maturity event.
        from rc6_fixed_income_paper_market_sale import sale_rows_checked
        sale_rows_checked(db,paper_id)
        valuation = valuation_at(db, paper_id, at_iso, root["entry_price"])
        pnl = valuation.realized_pnl
        gross = (pnl + decimal_value(root["entry_cost"], "FI entry cost", nonnegative=True)
                 * view.redeemed_nominal / view.initial_nominal
                 if pnl is not None else None)
        result = {
            "schema": SCHEMA, "paper_id": paper_id, "request_id": request_id,
            "closed_at": at_iso, "kind": "MATURITY", "family": seed.family,
            "review_receipt_sha256": approved,
            "issuer_sha256": review["issuer_sha256"],
            "ledger_sha256": digest, "residual_nominal": "0",
            "realized_pnl": str(pnl) if pnl is not None else None,
            "pnl_quality": ("FI_PAPER_COST_BASIS_RECONCILED" if pnl is not None
                            else "FI_CROSS_CURRENCY_PNL_UNVERIFIED"),
            "economic_cash_written": "0", "sell_fills_created": 0,
            "paper_only": True, "real_routes_used": [], "real_orders_sent": 0,
        }
        db.execute("""INSERT INTO paper_fi_native_maturities VALUES(?,?,?,?,?,?,?)""",
                   (paper_id, request_id, approved, review["issuer_sha256"],
                    digest, at_iso, _canonical(result)))
        db.execute("""UPDATE paper_positions SET status='CLOSED',closed_at=?,
            close_reason=?,exit_price=NULL,exit_cost='0',gross_pnl=?,net_pnl=?
            WHERE paper_id=? AND status='OPEN'""",
            (at_iso, REASON, str(gross) if gross is not None else None,
             str(pnl) if pnl is not None else None, paper_id))
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise ValueError("FI_TERMINAL_ATOMIC_POSITION_CONFLICT")
        db.execute("""INSERT INTO paper_events
            (event_at,source,event_type,paper_id,detail) VALUES(?,?,?,?,?)""",
            (at_iso, "PRODUCTION_PAPER", "FI_NATIVE_MATURITY_NO_SELL", paper_id,
             "Issuer cash already booked; no new money, no SELL, no FX conversion"))
        if terminal_at(db, paper_id) != result:
            raise ValueError("FI_TERMINAL_POSTWRITE_RECONCILIATION_FAILED")
        return result
