"""Durable, atomic PAPER fixed-income events with explicit entitlement proof.

This is an integration primitive, not a live collector or an entry authority.
The caller must obtain an approved receipt SHA independently of its payload;
that approval includes checking issuer authenticity and economic semantics.
Matching hashes alone do not establish source authenticity. The original
PAPER spot ledger is never rewritten here. Consumers must explicitly integrate
residual nominal and cash projections before advertising native FI support.

First supported scope: an intact PAPER buy lot, explicit coupons, partial/final
amortization, and fixings. Market sales are rejected until an entitlement-aware
sale allocator is implemented. Payment date alone is never evidence of rights.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import timezone
from decimal import Decimal

from bs_instrument_contracts import (aware_datetime, cash_currency,
                                     contract_from_metadata, decimal_value,
                                     family_name)
from cf_sale_settlement import conservative_unconfirmed_availability
from rc6_fixed_income_event_reducer import EconomicEvent, PositionSeed, reduce_events
from rc6_fixed_income_runtime import _json, _digest

PAPER_ONLY = True
ENTRY_AUTHORITY = False
REAL_ROUTES_USED = ()
RECEIPT_SCHEMA = "rc6.fixed-income-economic-receipt.v1"
MAX_RECEIPT_BYTES = 256 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_LOT_EVENTS = 4096


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=str)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _time(value):
    return aware_datetime(value).astimezone(timezone.utc).isoformat()


def init_schema(store):
    """Migration owner only; never called from an event's hot write path."""
    with store.connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS paper_fi_lots(
          paper_id TEXT PRIMARY KEY REFERENCES paper_positions(paper_id),
          seed_json TEXT NOT NULL, seed_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_fi_economic_events(
          id INTEGER PRIMARY KEY,
          paper_id TEXT NOT NULL REFERENCES paper_fi_lots(paper_id),
          event_id TEXT NOT NULL, obligation_id TEXT NOT NULL, kind TEXT NOT NULL,
          event_json TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
          source_sha256 TEXT NOT NULL, booked_at TEXT NOT NULL,
          entitlement_record_at TEXT,
          UNIQUE(paper_id,event_id), UNIQUE(paper_id,kind,obligation_id));
        CREATE TABLE IF NOT EXISTS paper_fi_cash_entries(
          event_row_id INTEGER PRIMARY KEY REFERENCES paper_fi_economic_events(id),
          paper_id TEXT NOT NULL REFERENCES paper_fi_lots(paper_id),
          currency TEXT NOT NULL, amount TEXT NOT NULL, kind TEXT NOT NULL,
          economic_at TEXT NOT NULL, known_at TEXT NOT NULL, booked_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_fi_events_lot
          ON paper_fi_economic_events(paper_id,id);
        CREATE INDEX IF NOT EXISTS idx_fi_cash_currency_at
          ON paper_fi_cash_entries(currency,booked_at);
        """)


def _seed(db, paper_id):
    row = db.execute("SELECT * FROM paper_positions WHERE paper_id=?", (paper_id,)).fetchone()
    if not row:
        raise ValueError("FI_LEDGER_POSITION_MISSING")
    root = dict(row)
    family = family_name(root["asset_class"])
    if (root["source"] != "PRODUCTION_PAPER" or family not in {"BONOS", "LETRAS", "OBLIGACIONES"}
            or not paper_id.startswith("PAPER-")):
        raise ValueError("FI_LEDGER_PAPER_FIXED_INCOME_REQUIRED")
    fills = db.execute("SELECT * FROM paper_fills WHERE paper_id=? ORDER BY id", (paper_id,)).fetchall()
    if (len(fills) != 1 or fills[0]["side"] != "BUY_SIMULATED"
            or fills[0]["source"] != "PRODUCTION_PAPER" or root["status"] != "OPEN"):
        raise ValueError("FI_LEDGER_INTACT_BUY_LOT_REQUIRED")
    buy = dict(fills[0])
    quantity = decimal_value(root["quantity"], "nominal", positive=True)
    if (decimal_value(buy["quantity"], "buy nominal", positive=True) != quantity
            or aware_datetime(buy["filled_at"]) != aware_datetime(root["opened_at"])
            or decimal_value(buy["price"], "buy price", positive=True) !=
               decimal_value(root["entry_price"], "entry price", positive=True)
            or decimal_value(buy["costs"], "buy cost", nonnegative=True) !=
               decimal_value(root["entry_cost"], "entry cost", nonnegative=True)):
        raise ValueError("FI_LEDGER_BUY_FILL_MISMATCH")
    features = json.loads(root["features_json"])
    contract = contract_from_metadata(root["symbol"], family, features["financial_contract"])
    key = (root["symbol"], family, root["market"], root["currency"], root["settlement"])
    if (contract.key != key or contract.minimum_quantity is None
            or contract.cash_multiplier != decimal_value(features["contract_cash_multiplier"], "basis", positive=True)
            or quantity % contract.quantity_step or quantity < contract.minimum_quantity):
        raise ValueError("FI_LEDGER_NOMINAL_CONTRACT_MISMATCH")
    immutable = {key: root[key] for key in ("paper_id", "source", "symbol", "asset_class",
                 "market", "currency", "settlement", "quantity", "entry_price", "entry_cost", "opened_at")}
    immutable.update(contract=asdict(contract), buy=buy)
    digest = _hash(_canonical(immutable).encode())
    seed = PositionSeed(paper_id, root["symbol"], family, root["market"], root["settlement"],
                        root["currency"], str(quantity), "VN", root["opened_at"],
                        "paper-fill:" + str(buy["id"]), digest)
    return seed


def _entitlement(seed, event, receipt, at, prior_events):
    if event.kind == "FIXING":
        if receipt.get("entitlement") is not None:
            raise ValueError("FI_LEDGER_FIXING_ENTITLEMENT_CONFLICT")
        return
    proof = receipt.get("entitlement")
    if not isinstance(proof, dict) or proof.get("rule") != "TRADE_BEFORE_EX_AND_SETTLED_BY_RECORD":
        raise ValueError("FI_LEDGER_ENTITLEMENT_RULE_UNVERIFIED")
    if (proof.get("source_sha256") != event.source_sha256
            or proof.get("source_reference") != event.source_reference):
        raise ValueError("FI_LEDGER_ENTITLEMENT_SOURCE_MISMATCH")
    opened = aware_datetime(seed.opened_at)
    ex_at, record_at = aware_datetime(proof["ex_at"]), aware_datetime(proof["record_at"])
    settled = aware_datetime(proof["paper_settled_at"])
    known = aware_datetime(proof["known_at"])
    if not (opened < ex_at <= record_at <= aware_datetime(event.effective_at)
            and opened <= settled <= record_at
            and known <= aware_datetime(event.recorded_at) <= at):
        raise ValueError("FI_LEDGER_NOT_ENTITLED_OR_CLOCK_INVALID")
    term = seed.settlement.upper()
    if term in {"INMEDIATA", "CI", "T+0"}:
        expected, basis = opened, "PAPER_CI_AT_TRADE"
    elif term in {"A-24HS", "24HS", "T+1"}:
        expected = conservative_unconfirmed_availability(term, seed.opened_at)
        basis = "PAPER_CONSERVATIVE_FULL_SETTLEMENT_DATE"
    else:
        expected, basis = None, ""
    if expected is None or settled != expected or proof.get("settlement_basis") != basis:
        raise ValueError("FI_LEDGER_SETTLEMENT_MODEL_UNVERIFIED")
    entitled_nominal = decimal_value(seed.initial_nominal, "initial nominal", positive=True)
    for prior in prior_events:
        if (prior.event_id != event.event_id and prior.kind == "AMORTIZATION"
                and aware_datetime(prior.effective_at) <= record_at):
            entitled_nominal -= decimal_value(prior.nominal_redeemed, "redeemed nominal", positive=True)
    if entitled_nominal != decimal_value(event.nominal_base, "entitled nominal", positive=True):
        raise ValueError("FI_LEDGER_RECORD_DATE_NOMINAL_MISMATCH")
    # The source-confirmed amount is scaled from an explicit unit convention;
    # neither a yield nor the spot quote is converted into a coupon/payment.
    per_vn = decimal_value(receipt["cash_per_nominal"], "cash per nominal", nonnegative=True)
    base = (decimal_value(event.nominal_redeemed, "redeemed nominal", positive=True)
            if event.kind == "AMORTIZATION" else
            decimal_value(event.nominal_base, "coupon nominal", positive=True))
    if decimal_value(event.cash_amount, "cash", nonnegative=True) != base * per_vn:
        raise ValueError("FI_LEDGER_PAYMENT_UNIT_MISMATCH")


def apply_verified_receipt(store, *, receipt_bytes, approved_sha256, source_bytes, applied_at):
    """Commit event and currency cash together; exact replay cannot pay twice.

    ``approved_sha256`` is the external trust anchor, not a value accepted from
    the receipt itself. ``source_bytes`` is the actual reviewed issuer/fixing
    evidence. This method does no external IO and never records a broker fill.
    A caller's missing schema is an explicit integration failure.
    """
    if (not isinstance(receipt_bytes, bytes) or not 0 < len(receipt_bytes) <= MAX_RECEIPT_BYTES
            or not isinstance(source_bytes, bytes) or not 0 < len(source_bytes) <= MAX_SOURCE_BYTES):
        raise ValueError("FI_LEDGER_EVIDENCE_SIZE_OR_TYPE_INVALID")
    if _hash(receipt_bytes) != _digest(approved_sha256):
        raise ValueError("FI_LEDGER_RECEIPT_PIN_MISMATCH")
    receipt = _json(receipt_bytes)
    if (receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("paper_only") is not True
            or receipt.get("real_routes_used") != []
            or receipt.get("review_authority") != "RELEASE_REVIEWED_ECONOMIC_EVENT"):
        raise ValueError("FI_LEDGER_REVIEW_ATTESTATION_INVALID")
    event = EconomicEvent(**receipt["event"])
    if _hash(source_bytes) != _digest(event.source_sha256):
        raise ValueError("FI_LEDGER_SOURCE_HASH_MISMATCH")
    at, booked_at = aware_datetime(applied_at), _time(applied_at)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        safety = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not safety or safety[0] != "PRODUCTION_PAPER" or type(safety[1]) is not int or safety[1] != 0:
            raise ValueError("FI_LEDGER_PAPER_SAFETY_REQUIRED")
        seed = _seed(db, event.paper_id)
        if tuple(receipt.get("identity") or ()) != (seed.symbol, seed.family, seed.market,
                                                        seed.trading_currency, seed.settlement):
            raise ValueError("FI_LEDGER_RECEIPT_IDENTITY_MISMATCH")
        seed_json = _canonical(asdict(seed))
        saved = db.execute("SELECT seed_json FROM paper_fi_lots WHERE paper_id=?", (seed.paper_id,)).fetchone()
        if saved and saved[0] != seed_json:
            raise ValueError("FI_LEDGER_IMMUTABLE_SEED_CHANGED")
        rows = db.execute("SELECT * FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id",
                          (seed.paper_id,)).fetchall()
        prior_events = tuple(EconomicEvent(**json.loads(row["event_json"])) for row in rows)
        _entitlement(seed, event, receipt, at, prior_events)
        prior = next((row for row in rows if row["event_id"] == event.event_id), None)
        if prior:
            if prior["receipt_sha256"] != approved_sha256:
                raise ValueError("FI_LEDGER_RECEIPT_REPLAY_CONFLICT")
            # A repeated read does not roll ingestion time backwards or revise cash.
            if at < max(aware_datetime(row["booked_at"]) for row in rows):
                raise ValueError("FI_LEDGER_BOOKING_CLOCK_ROLLBACK")
            return reduce_events(seed, tuple(EconomicEvent(**json.loads(row["event_json"]))
                                             for row in rows), as_of=applied_at)
        # Capacity limits new events, never an exact replay already committed.
        if len(rows) >= MAX_LOT_EVENTS:
            raise ValueError("FI_LEDGER_EVENT_BOUND_EXCEEDED")
        if rows and at < max(aware_datetime(row["booked_at"]) for row in rows):
            raise ValueError("FI_LEDGER_BOOKING_CLOCK_ROLLBACK")
        if event.kind == "AMORTIZATION":
            # The reducer orders knowledge time, not economic entitlement.
            # A newly learned earlier amortization would lower nominal at an
            # already-paid obligation's record date. Reject it atomically;
            # never revise old cash or silently preserve a now-invalid right.
            for row in rows:
                if row["kind"] == "FIXING":
                    continue
                if row["entitlement_record_at"] is None:
                    raise ValueError("FI_LEDGER_PRIOR_ENTITLEMENT_UNVERIFIED")
                if aware_datetime(event.effective_at) <= aware_datetime(row["entitlement_record_at"]):
                    raise ValueError("FI_LEDGER_RETROACTIVE_AMORTIZATION_CHANGES_ENTITLEMENT")
        events = prior_events + (event,)
        snapshot = reduce_events(seed, events, as_of=applied_at)
        db.execute("INSERT OR IGNORE INTO paper_fi_lots VALUES(?,?,?)",
                   (seed.paper_id, seed_json, seed.position_sha256))
        inserted = db.execute("""INSERT INTO paper_fi_economic_events
          (paper_id,event_id,obligation_id,kind,event_json,receipt_sha256,source_sha256,booked_at,
           entitlement_record_at)
          VALUES(?,?,?,?,?,?,?,?,?)""", (seed.paper_id, event.event_id, event.obligation_id,
          event.kind, _canonical(asdict(event)), approved_sha256, event.source_sha256, booked_at,
          _time(receipt["entitlement"]["record_at"]) if event.kind != "FIXING" else None))
        if event.kind != "FIXING":
            db.execute("INSERT INTO paper_fi_cash_entries VALUES(?,?,?,?,?,?,?,?)",
                       (inserted.lastrowid, seed.paper_id, cash_currency(event.payment_currency),
                        str(decimal_value(event.cash_amount, "event cash", nonnegative=True)), event.kind,
                        _time(event.effective_at), _time(event.recorded_at), booked_at))
        return snapshot


def cash_effect(connection, currency, as_of, *, income_only=False):
    """Actual imported PAPER cash, separated by currency and ingestion cutoff.

    Principal amortization is cash but not investment income. Consumers use
    income_only for realized income, the full amount for cash, and residual
    nominal for exposure. The original spot ledger is intentionally unchanged.
    """
    at, currency = aware_datetime(as_of), cash_currency(currency)
    rows = connection.execute("SELECT amount,kind,booked_at FROM paper_fi_cash_entries WHERE currency=?",
                              (currency,))
    return sum((decimal_value(row[0], "FI cash", nonnegative=True) for row in rows
                if aware_datetime(row[2]) <= at and (not income_only or row[1] == "COUPON")), Decimal(0))


def snapshot_at(connection, paper_id, as_of):
    """PIT nominal/cash view; late imports never rewrite the known past."""
    row = connection.execute("SELECT seed_json FROM paper_fi_lots WHERE paper_id=?", (paper_id,)).fetchone()
    if not row:
        return None
    seed = PositionSeed(**json.loads(row[0]))
    at = aware_datetime(as_of)
    events = [EconomicEvent(**json.loads(row[0])) for row in connection.execute(
        "SELECT event_json,booked_at FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id", (paper_id,))
        if aware_datetime(row[1]) <= at]
    return reduce_events(seed, events, as_of=as_of)
