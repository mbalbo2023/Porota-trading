"""Review-only PAPER FI exit preflight; no BUY, sale, maturity close or cash authority.

This is the bounded first transaction in a native-exit implementation.  Its
durable result is always BLOCKED.  A caller-supplied hash, quote, fee or FX
rate is never an issuer/release approval.  The existing event ledger owns
coupon, fixing and principal cash; this module records no economic movement.

The integrating owner must implement one atomic native transaction, preserving
these invariants before changing NATIVE_EXIT_AUTHORITY:

* Under BEGIN IMMEDIATE, compare the immutable BUY seed, reviewed event and
  entitlement ledger, residual VN, paper mode, quote/release PIT and shared
  liquidity.  Allocate a sale against residual VN, never original VN.
* Credit sale proceeds once in trade currency after explicit sale settlement;
  retain issuer payments in their original currency.  A maturity transition
  must add zero cash, zero SELL fills, and consume a fully reconciled terminal
  review.  Neither path may reduce the original spot BUY commitment while the
  existing FI cash credit remains: that would count recovered principal twice.
* Reconcile every reader of OPEN/CLOSED, cash, risk, cost basis, learning and
  exit supervision in the same release.  cd_spot_ledger.partition currently
  requires a SELL fill for CLOSED; rc6_fixed_income_portfolio_bridge.position_at
  currently requires the original OPEN BUY.  Merely updating status breaks both.
* Use a unique request key and immutable digest in the same transaction as a
  future VN/cash transition; exact retry returns its prior result, conflicting
  retry raises, and a failed write rolls back every table.  Prove PIT snapshots,
  payout/trading currency separation, fixing provenance and real_orders_sent=0.

This journal makes the conflict/retry and input contracts executable without
prematurely adding an incompatible economic or runtime path.  DDL is an
explicit migration-owner call and never runs in a hot exit attempt.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from bs_instrument_contracts import aware_datetime, cash_currency, decimal_value
from rc6_fixed_income_event_ledger import _canonical, _hash, _seed
from rc6_fixed_income_portfolio_bridge import position_at
from rc6_fixed_income_runtime import _digest, _json

PAPER_ONLY = True
ENTRY_AUTHORITY = False
NATIVE_EXIT_AUTHORITY = False
REAL_ROUTES_USED = ()
SCHEMA = "rc6.fixed-income-native-exit-preflight.v1"
MAX_REQUEST_BYTES = 16 * 1024
_FIELDS = frozenset({
    "schema", "paper_only", "real_routes_used", "paper_id", "request_id",
    "kind", "as_of", "requested_nominal", "quote_currency", "bid",
    "available_nominal", "fee_amount", "fee_currency", "settlement_currency",
    "source_reference", "source_sha256", "source_at", "known_at",
    "fixing_at", "fx_source_sha256",
})


def init_schema(store):
    """Migration owner only; separate review journal, no FI or spot cash DDL."""
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS paper_fi_native_exit_preflights(
          paper_id TEXT NOT NULL REFERENCES paper_positions(paper_id),
          request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
          assessed_at TEXT NOT NULL, result_json TEXT NOT NULL,
          PRIMARY KEY(paper_id,request_id))""")


def _required(value, label):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 512):
        raise ValueError(f"FI_EXIT_{label}_INVALID")
    return value


def _amount(value, label, *, positive=False):
    if not isinstance(value, str):
        raise ValueError(f"FI_EXIT_{label}_INVALID")
    return decimal_value(value, f"FI_EXIT_{label}", positive=positive,
                         nonnegative=not positive)


def _parse(request_bytes, approved_sha256):
    if not isinstance(request_bytes, bytes) or not 0 < len(request_bytes) <= MAX_REQUEST_BYTES:
        raise ValueError("FI_EXIT_REQUEST_SIZE_OR_TYPE_INVALID")
    approved_sha256 = _digest(approved_sha256)
    if _hash(request_bytes) != approved_sha256:
        raise ValueError("FI_EXIT_REQUEST_PIN_MISMATCH")
    value = _json(request_bytes)
    if set(value) != _FIELDS or value.get("schema") != SCHEMA:
        raise ValueError("FI_EXIT_REQUEST_SCHEMA_INVALID")
    if value["paper_only"] is not True or value["real_routes_used"] != []:
        raise ValueError("FI_EXIT_PAPER_ONLY_REQUIRED")
    _required(value["paper_id"], "PAPER_ID")
    _required(value["request_id"], "REQUEST_ID")
    if not isinstance(value["kind"], str) or value["kind"] not in {"MARKET_SALE", "MATURITY"}:
        raise ValueError("FI_EXIT_KIND_INVALID")
    at = aware_datetime(value["as_of"])
    if not (aware_datetime(value["source_at"]) <=
            aware_datetime(value["known_at"]) <= at):
        raise ValueError("FI_EXIT_SOURCE_CLOCK_INVALID")
    _required(value["source_reference"], "SOURCE_REFERENCE")
    _digest(value["source_sha256"])
    _amount(value["requested_nominal"], "NOMINAL")
    if cash_currency(value["settlement_currency"]) != value["settlement_currency"]:
        raise ValueError("FI_EXIT_SETTLEMENT_CURRENCY_NOT_CANONICAL")
    for label in ("fixing_at", "fx_source_sha256"):
        if value[label] is not None:
            (aware_datetime(value[label]) if label == "fixing_at" else _digest(value[label]))
    if value["kind"] == "MATURITY":
        if (value["requested_nominal"] != "0" or any(value[key] is not None for key in (
                "quote_currency", "bid", "available_nominal", "fee_amount", "fee_currency"))):
            raise ValueError("FI_EXIT_MATURITY_NO_SALE_TERMS")
    else:
        _amount(value["requested_nominal"], "NOMINAL", positive=True)
        _amount(value["bid"], "BID", positive=True)
        _amount(value["available_nominal"], "AVAILABLE_NOMINAL")
        _amount(value["fee_amount"], "FEE")
        for name in ("quote_currency", "fee_currency"):
            if cash_currency(value[name]) != value[name]:
                raise ValueError(f"FI_EXIT_{name.upper()}_NOT_CANONICAL")
    return value, at, approved_sha256


def assess_blocked_exit(store, *, request_bytes, approved_sha256):
    """Atomically record deterministic blockers without an economic side effect.

    The request digest pins *this attempt* only.  Provenance and issuer review
    still belong to independently approved economic receipts/release manifests.
    There is no success/authorized outcome in this API.
    """
    request, at, digest = _parse(request_bytes, approved_sha256)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                          ("paper_fi_native_exit_preflights",)).fetchone():
            raise ValueError("FI_EXIT_PREFLIGHT_SCHEMA_MISSING")
        safety = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if (safety is None or safety[0] != "PRODUCTION_PAPER" or
                type(safety[1]) is not int or safety[1] != 0):
            raise ValueError("FI_EXIT_PAPER_SAFETY_REQUIRED")
        prior = db.execute("""SELECT request_sha256,result_json FROM paper_fi_native_exit_preflights
                              WHERE paper_id=? AND request_id=?""",
                           (request["paper_id"], request["request_id"])).fetchone()
        if prior and prior[0] != digest:
            raise ValueError("FI_EXIT_PREFLIGHT_REPLAY_CONFLICT")
        seed = _seed(db, request["paper_id"])  # intact OPEN PAPER BUY
        view = position_at(db, request["paper_id"], at)
        review_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  ("paper_fi_native_redemption_reviews",)).fetchone()
        review = (db.execute("""SELECT receipt_sha256,issuer_sha256,ledger_sha256,reviewed_at
                   FROM paper_fi_native_redemption_reviews WHERE paper_id=?""",
                   (seed.paper_id,)).fetchone() if review_table else None)
        state_sha256 = _hash(_canonical({
            "seed": asdict(seed), "view": asdict(view) if view else None,
            "events": [tuple(row) for row in db.execute("""SELECT event_json,receipt_sha256,booked_at
                         FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id""",
                         (seed.paper_id,))],
            "review": tuple(review) if review else None,
        }).encode())
        if prior:
            saved = json.loads(prior[1])
            if saved.get("observed_state_sha256") != state_sha256:
                raise ValueError("FI_EXIT_PREFLIGHT_STATE_CHANGED")
            return saved
        blockers = []
        if view is None:
            blockers.append("FI_EXIT_NATIVE_EVENT_LEDGER_MISSING")
        if at < aware_datetime(seed.opened_at):
            blockers.append("FI_EXIT_BEFORE_PAPER_BUY")
        nominal = _amount(request["requested_nominal"], "NOMINAL")
        if request["fixing_at"] is not None and aware_datetime(request["fixing_at"]) > at:
            blockers.append("FI_EXIT_FUTURE_FIXING")
        if request["settlement_currency"] != seed.trading_currency:
            if request["fx_source_sha256"] is None:
                blockers.append("FI_EXIT_CROSS_CURRENCY_FX_UNVERIFIED")
            else:
                blockers.append("FI_EXIT_CROSS_CURRENCY_CASH_LEDGER_UNBOUND")
        if request["kind"] == "MATURITY":
            if view is None or view.residual_nominal != 0:
                blockers.append("FI_EXIT_NOT_FULLY_REDEEMED")
            if review is None or aware_datetime(review["reviewed_at"]) > at:
                blockers.append("FI_EXIT_FINAL_REDEMPTION_REVIEW_MISSING")
            # The future transition must recheck the review's ledger digest
            # inside its transaction; this journal cannot grant that authority.
            blockers.append("FI_EXIT_MATURITY_CLOSE_LEDGER_UNBOUND")
        else:
            if view is None or nominal > view.residual_nominal:
                blockers.append("FI_EXIT_QUANTITY_EXCEEDS_RESIDUAL")
            contract = json.loads(db.execute("SELECT features_json FROM paper_positions WHERE paper_id=?",
                                             (seed.paper_id,)).fetchone()[0])["financial_contract"]
            step = decimal_value(contract["quantity_step"], "FI step", positive=True)
            minimum = decimal_value(contract["minimum_quantity"], "FI minimum", positive=True)
            if nominal < minimum or nominal % step:
                blockers.append("FI_EXIT_NOMINAL_CONTRACT_STEP_INVALID")
            if nominal > _amount(request["available_nominal"], "AVAILABLE_NOMINAL"):
                blockers.append("FI_EXIT_QUOTE_DEPTH_INSUFFICIENT")
            if request["quote_currency"] != seed.trading_currency:
                blockers.append("FI_EXIT_QUOTE_CURRENCY_MISMATCH")
            if request["fee_currency"] != seed.trading_currency:
                blockers.append("FI_EXIT_FEE_CURRENCY_MISMATCH")
            # A caller-asserted bid/depth/fee is not a released PIT orderbook,
            # shared-liquidity allocation or fee schedule.
            blockers.extend(("FI_EXIT_QUOTE_AND_SHARED_LIQUIDITY_UNBOUND",
                             "FI_EXIT_FEE_POLICY_UNBOUND",
                             "FI_EXIT_ENTITLEMENT_AWARE_SALE_LEDGER_UNBOUND"))
        result = {
            "schema": SCHEMA, "paper_id": seed.paper_id,
            "request_id": request["request_id"], "kind": request["kind"],
            "as_of": at.isoformat(), "status": "BLOCKED",
            "blockers": list(dict.fromkeys(blockers)),
            "initial_nominal": str(view.initial_nominal) if view else None,
            "residual_nominal": str(view.residual_nominal) if view else None,
            "request_sha256": digest, "economic_side_effect": "0",
            "observed_state_sha256": state_sha256,
            "native_exit_authorized": False, "entry_authority": False,
            "real_orders_sent": 0,
        }
        db.execute("""INSERT INTO paper_fi_native_exit_preflights
                    (paper_id,request_id,request_sha256,assessed_at,result_json)
                    VALUES (?,?,?,?,?)""", (seed.paper_id, request["request_id"], digest,
                                             at.isoformat(), _canonical(result)))
        return result
