"""Accounting from #778 for independently witnessed BONOS/ON PAPER sales."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import timezone
from decimal import Decimal, ROUND_DOWN

from bl_candle_engine import fingerprint, stamp
from bs_instrument_contracts import (aware_datetime, cash_currency, contract_from_metadata,
                                     decimal_value, family_name)
from cf_sale_settlement import (modeled_sale_settlement, modeled_sale_settlement_date,
                                validated_sale_settlement)
import cc_spot_liquidity as liquidity
from rc6_fixed_income_event_ledger import _canonical, _hash, _seed
from rc6_fixed_income_event_reducer import EconomicEvent
from rc6_fixed_income_portfolio_bridge import position_at
from rc6_fixed_income_runtime import _digest, _json

PAPER_ONLY = True
REAL_ROUTES_USED = ()
ENTRY_AUTHORITY = False
REAL_SALE_AUTHORITY = False
_AUTHORITY_ERROR = "FI_NATIVE_MARKET_EXIT_NOT_ADMITTED: INDEPENDENT_AUTHORITY_UNBOUND"
SCHEMA = "rc6.fi-native-paper-market-sale.v1"
BOOK_SCHEMA = "rc6.fi-ppi-normalized-book.v1"
IOL_BOOK_SCHEMA = "rc6.fi-iol-normalized-book.v1"
FEE_SCHEMA = "rc6.fi-paper-sale-fee-quote.v1"
REASON = "FI_PAPER_MARKET_SALE_MODELED_SETTLEMENT"
MAX_EVIDENCE_BYTES = 16 * 1024
_SOURCE_FIELDS = frozenset({"schema","provider","route","paper_only","identity",
    "book_at","observed_at","bid","ask","bid_size","ask_size","quantity_unit",
    "metadata_source"})
_FEE_FIELDS = frozenset({"schema","paper_only","identity","paper_id","nominal",
    "exit_price","exit_fee","currency","slippage","policy"})
_BOOK_SOURCES = {
    BOOK_SCHEMA: ("PPI_PRIMARY_READ_ONLY", "MarketData/Book", "PPI_PRIMARY_LIVE_BOOK:REST",
                  "PPI_LIVE_BOOK", "PPI_REST_READ_ONLY:MarketData/Book"),
    IOL_BOOK_SCHEMA: ("IOL_REST", "market_depth", "IOL_REST",
                      "IOL_LIVE_BOOK", "IOL_REST_READ_ONLY:market_depth"),
}


def init_schema(store):
    """Only the schema-owning parent initializes this; the fill hot path cannot."""
    with store.connect() as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS paper_fi_native_sales(
            fill_id INTEGER PRIMARY KEY REFERENCES paper_fills(id),
            event_row_id INTEGER NOT NULL UNIQUE REFERENCES paper_fi_economic_events(id),
            paper_id TEXT NOT NULL REFERENCES paper_fi_lots(paper_id),
            request_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL, fee_sha256 TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL, applied_at TEXT NOT NULL,
            book_at TEXT NOT NULL, nominal TEXT NOT NULL, price TEXT NOT NULL,
            exit_fee TEXT NOT NULL, allocated_entry_cost TEXT NOT NULL,
            gross_pnl TEXT NOT NULL, net_pnl TEXT NOT NULL,
            net_proceeds TEXT NOT NULL, available_at TEXT,
            settlement_basis TEXT NOT NULL, result_json TEXT NOT NULL,
            UNIQUE(paper_id,request_id));
        CREATE INDEX IF NOT EXISTS idx_fi_native_sale_paper
            ON paper_fi_native_sales(paper_id,applied_at);
        CREATE TRIGGER IF NOT EXISTS paper_fi_native_sale_no_update
            BEFORE UPDATE ON paper_fi_native_sales
            BEGIN SELECT RAISE(ABORT,'FI_NATIVE_SALE_RECEIPT_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS paper_fi_native_sale_no_delete
            BEFORE DELETE ON paper_fi_native_sales
            BEGIN SELECT RAISE(ABORT,'FI_NATIVE_SALE_RECEIPT_IMMUTABLE'); END;
        CREATE TRIGGER IF NOT EXISTS paper_fi_native_sale_no_replace
            BEFORE INSERT ON paper_fi_native_sales
            WHEN EXISTS(SELECT 1 FROM paper_fi_native_sales WHERE fill_id=NEW.fill_id
                OR event_row_id=NEW.event_row_id OR (paper_id=NEW.paper_id AND request_id=NEW.request_id))
            BEGIN SELECT RAISE(ABORT,'FI_NATIVE_SALE_RECEIPT_IMMUTABLE'); END;
        """)


def _table(db):
    return bool(db.execute("""SELECT 1 FROM sqlite_master WHERE type='table'
                    AND name='paper_fi_native_sales'""").fetchone())


def _sales(db, paper_id):
    return db.execute("""SELECT * FROM paper_fi_native_sales
        WHERE paper_id=? ORDER BY event_row_id""", (paper_id,)).fetchall() if _table(db) else []


def _parsed(raw, pin, schema, fields):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_EVIDENCE_BYTES:
        raise ValueError("FI_NATIVE_SALE_EVIDENCE_BYTES_REQUIRED")
    digest = _digest(pin)
    if _hash(raw) != digest:
        raise ValueError("FI_NATIVE_SALE_EVIDENCE_PIN_MISMATCH")
    value = _json(raw)
    if (set(value) != fields or value.get("schema") != schema
            or value.get("paper_only") is not True or
            raw != _canonical(value).encode("utf-8")):
        raise ValueError("FI_NATIVE_SALE_EVIDENCE_NOT_CANONICAL")
    return value, digest


def _quote_matches(source, q, seed):
    try:
        provider, route, metadata, _, _ = _BOOK_SOURCES[source["schema"]]
        identity = [seed.symbol, seed.family, seed.market,
                    seed.trading_currency, seed.settlement]
        return (source["identity"] == identity and
                source["provider"] == provider and source["route"] == route and
                source["quantity_unit"] == "VN" and
                source["metadata_source"] == q.metadata_source == metadata and
                aware_datetime(source["book_at"]) == aware_datetime(q.book_at) and
                aware_datetime(source["observed_at"]) == aware_datetime(q.observed_at) and
                all(decimal_value(source[k], k, nonnegative=True) ==
                    decimal_value(getattr(q,k), k, nonnegative=True)
                    for k in ("bid","ask","bid_size","ask_size")) and
                (q.symbol, family_name(q.asset_class), q.market, q.currency,
                 q.settlement) == tuple(identity))
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def sale_rows_checked(db, paper_id):
    """Validate each native receipt, event, fill and depth under one snapshot."""
    rows = _sales(db, paper_id)
    for row in rows:
        fill = db.execute("SELECT * FROM paper_fills WHERE id=?", (row["fill_id"],)).fetchone()
        event = db.execute("SELECT * FROM paper_fi_economic_events WHERE id=?",
                           (row["event_row_id"],)).fetchone()
        depth = db.execute("SELECT * FROM paper_book_consumption WHERE fill_id=?",
                           (row["fill_id"],)).fetchone()
        receipt = json.loads(row["result_json"])
        causal_sale = receipt.get("causal_sale")
        if (not isinstance(causal_sale, dict) or causal_sale.get("request_id") != row["request_id"]
                or causal_sale.get("paper_id") != paper_id):
            raise ValueError(_AUTHORITY_ERROR)
        source = receipt.get("book_evidence") or {}
        fee = receipt.get("fee_evidence") or {}
        evidence_sha = _hash(_canonical({"book_sha256": row["source_sha256"],
            "fee_sha256": row["fee_sha256"], "causal_sale": causal_sale}).encode())
        root = db.execute("SELECT * FROM paper_positions WHERE paper_id=?",
                          (paper_id,)).fetchone()
        book_identity = ({"symbol":root["symbol"],"family":family_name(root["asset_class"]),
                         "settlement":root["settlement"],"currency":root["currency"],
                         "market":root["market"]} if root else {})
        book_values = ({k:format(decimal_value(source[k],k,nonnegative=True).normalize(),'f')
                        for k in ("bid","ask","bid_size","ask_size")}
                       if source and all(k in source for k in (
                           "bid","ask","bid_size","ask_size")) else None)
        if (not root or not fill or not event or not depth or
                row["receipt_sha256"] != evidence_sha or row["receipt_sha256"] != event["receipt_sha256"] or
                row["source_sha256"] != event["source_sha256"] or
                _canonical(receipt) != row["result_json"] or
                _hash(_canonical(source).encode("utf-8")) != row["source_sha256"] or
                _hash(_canonical(fee).encode("utf-8")) != row["fee_sha256"] or
                receipt.get("schema") != SCHEMA or receipt.get("paper_only") is not True or
                receipt.get("real_routes_used") != [] or
                receipt.get("real_orders_sent") != 0 or
                receipt.get("broker_order_sent") is not False or
                receipt.get("settlement_proof") != "PAPER_MODELED_NOT_ACCOUNT_MOVEMENT" or
                receipt.get("fill_id") != row["fill_id"] or
                receipt.get("event_row_id") != row["event_row_id"] or
                receipt.get("request_id") != row["request_id"] or
                receipt.get("paper_id") != paper_id or
                receipt.get("applied_at") != row["applied_at"] or
                receipt.get("book_at") != row["book_at"] or
                receipt.get("nominal") != row["nominal"] or
                receipt.get("price") != row["price"] or
                receipt.get("exit_fee") != row["exit_fee"] or
                receipt.get("settlement_available_at") != row["available_at"] or
                receipt.get("net_proceeds") != row["net_proceeds"] or
                receipt.get("allocated_entry_cost") != row["allocated_entry_cost"] or
                receipt.get("reason") != REASON or
                fill["paper_id"] != paper_id or fill["source"] != "PRODUCTION_PAPER" or
                fill["side"] != "SELL_SIMULATED" or
                aware_datetime(fill["filled_at"]) != aware_datetime(row["applied_at"]) or
                event["paper_id"] != paper_id or event["kind"] != "MARKET_SALE" or
                event["entitlement_record_at"] is not None or
                event["booked_at"] != row["applied_at"] or
                depth["side"] != "SELL_SIMULATED" or
                depth["instrument_key"] != fingerprint(book_identity) or
                book_values is None or json.loads(depth["book_json"]) != book_values or
                decimal_value(depth["quantity"], "reserved VN", positive=True) !=
                    decimal_value(row["nominal"], "sold VN", positive=True) or
                depth["book_at"] != stamp(row["book_at"])):
            raise ValueError("FI_NATIVE_SALE_JOURNAL_OR_DEPTH_CONFLICT")
        event_value = EconomicEvent(**json.loads(event["event_json"]))
        source_details = _BOOK_SOURCES.get(source.get("schema"))
        if source_details is None:
            raise ValueError("FI_NATIVE_SALE_ECONOMIC_RECONCILIATION_FAILED")
        provider, route, metadata, source_kind, source_reference = source_details
        qty = decimal_value(row["nominal"], "sold VN", positive=True)
        price = decimal_value(row["price"], "exit price", positive=True)
        cost = decimal_value(row["exit_fee"], "sale fee", nonnegative=True)
        allocated = decimal_value(row["allocated_entry_cost"], "entry fee allocated", nonnegative=True)
        multiplier = decimal_value(json.loads(root["features_json"])["contract_cash_multiplier"],
                                   "FI multiplier", positive=True)
        entry = decimal_value(root["entry_price"], "entry price", positive=True)
        gross = (price - entry) * qty * multiplier
        net = gross - allocated - cost
        proceeds = price * qty * multiplier - cost
        if (event_value.kind != "MARKET_SALE" or event_value.paper_id != paper_id or
                event_value.source_sha256 != row["source_sha256"] or
                fee.get("schema") != FEE_SCHEMA or
                source.get("provider") != provider or source.get("route") != route or
                source.get("quantity_unit") != "VN" or
                source.get("metadata_source") != metadata or
                aware_datetime(source["book_at"]) != aware_datetime(row["book_at"]) or
                fee.get("paper_only") is not True or
                fee.get("policy") != "PAPER_COST_MODEL_NOT_PPI_ACCOUNT_FEE" or
                fee.get("currency") != root["currency"] or
                fee.get("identity") != source.get("identity") or
                fee.get("paper_id") != paper_id or
                fee.get("nominal") != row["nominal"] or
                fee.get("exit_price") != row["price"] or
                source.get("identity") != [
                    root["symbol"],family_name(root["asset_class"]),root["market"],
                    root["currency"],root["settlement"]] or
                event_value.event_id != "FI_PAPER_SALE:"+row["request_id"] or
                event_value.obligation_id != event_value.event_id or
                event_value.source_kind != source_kind or event_value.source_reference != source_reference or
                aware_datetime(event_value.recorded_at) != aware_datetime(row["applied_at"]) or
                aware_datetime(event_value.effective_at) != aware_datetime(row["book_at"]) or
                aware_datetime(event_value.source_at) != aware_datetime(row["book_at"]) or
                event_value.payment_currency is not None or
                event_value.cash_amount is not None or
                decimal_value(event_value.nominal_redeemed, "sold nominal", positive=True) != qty or
                decimal_value(fill["quantity"], "fill VN", positive=True) != qty or
                decimal_value(fill["price"], "fill price", positive=True) != price or
                decimal_value(fill["costs"], "fill fee", nonnegative=True) != cost or
                decimal_value(fill["slippage"], "fill slippage", nonnegative=True) !=
                    decimal_value(source["bid"], "source bid",positive=True)-price or
                Decimal(row["gross_pnl"]) != gross or Decimal(row["net_pnl"]) != net or
                Decimal(row["net_proceeds"]) != proceeds or
                fee.get("exit_fee") != str(cost) or
                receipt.get("gross_pnl") != str(gross) or
                receipt.get("net_pnl") != str(net) or
                row["available_at"] != modeled_sale_settlement(
                    root["settlement"],row["applied_at"]) or
                row["settlement_basis"] != (
                    "PAPER_CONSERVATIVE_CALENDAR" if row["available_at"] is not None
                    else "PENDING_CONFIRMATION")):
            raise ValueError("FI_NATIVE_SALE_ECONOMIC_RECONCILIATION_FAILED")
    return rows


def sale_cash_effect(db, currency, as_of, *, pending=False):
    """Return the PAPER receivable either before or after modeled settlement."""
    at = aware_datetime(as_of)
    currency = cash_currency(currency)
    total = Decimal(0)
    if not _table(db):
        return total
    paper_ids = [r[0] for r in db.execute(
        "SELECT DISTINCT paper_id FROM paper_fi_native_sales ORDER BY paper_id")]
    for paper_id in paper_ids:
        root = db.execute("SELECT currency,settlement,asset_class,market FROM paper_positions WHERE paper_id=?",
                          (paper_id,)).fetchone()
        if root is None:
            raise ValueError("FI_NATIVE_SALE_ORPHAN_POSITION")
        for row in sale_rows_checked(db, paper_id):
            if root["currency"] != currency or aware_datetime(row["applied_at"]) > at:
                continue
            release = validated_sale_settlement(
                root["settlement"],row["applied_at"],
                row["available_at"],row["settlement_basis"],
                family=root["asset_class"], market=root["market"], currency=root["currency"])
            waiting = release is None or release > at
            if pending is None or waiting is pending:
                total += Decimal(row["net_proceeds"])
    return total


def native_partition(db, root, as_of=None):
    """Overlay native FI sale lots without treating them as generic spot SELL.

    The original BUY and its currency commitment remain immutable. The
    position's current quantity is the issuer-reduced VN less *native* sales;
    the realized slices carry only their own sale fee and basis allocation.
    """
    from rc6_fixed_income_portfolio_bridge import position_at
    p = dict(root)
    when = aware_datetime(as_of) if as_of is not None else None
    rows = sale_rows_checked(db, p["paper_id"])
    if not rows or p["close_reason"] not in (None,REASON):
        raise ValueError("FI_NATIVE_SALE_POSITION_OR_RECEIPT_INVALID")
    start = aware_datetime(p["opened_at"])
    visible = [r for r in rows if when is None or aware_datetime(r["applied_at"]) <= when]
    last = rows[-1]
    if p["status"] == "CLOSED":
        if (p["close_reason"] != REASON or
                aware_datetime(p["closed_at"]) != aware_datetime(last["applied_at"]) or
                sum((Decimal(r["net_pnl"]) for r in rows),Decimal(0)) !=
                  decimal_value(p["net_pnl"],"terminal sale PnL") or
                sum((Decimal(r["gross_pnl"]) for r in rows),Decimal(0)) !=
                  decimal_value(p["gross_pnl"],"terminal sale gross") or
                sum((Decimal(r["exit_fee"]) for r in rows),Decimal(0)) !=
                  decimal_value(p["exit_cost"],"terminal sale fee",nonnegative=True)):
            raise ValueError("FI_NATIVE_SALE_TERMINAL_LEDGER_MISMATCH")
    elif p["status"] != "OPEN" or p["closed_at"] is not None or p["close_reason"] is not None:
        raise ValueError("FI_NATIVE_SALE_OPEN_LEDGER_MISMATCH")
    latest = db.execute("SELECT booked_at FROM paper_fi_economic_events WHERE paper_id=? ORDER BY id DESC LIMIT 1",
                        (p["paper_id"],)).fetchone()
    cutoff = when or aware_datetime(latest[0])
    if cutoff < start:
        return None, []
    view = position_at(db,p["paper_id"],cutoff.isoformat())
    if view is None:
        raise ValueError("FI_NATIVE_SALE_IMMUTABLE_VN_MISSING")
    original = Decimal(p["quantity"])
    realized = []
    for row in visible:
        realized.append(dict(p,status="CLOSED",quantity=row["nominal"],
            entry_cost=row["allocated_entry_cost"],closed_at=row["applied_at"],
            exit_price=row["price"],exit_cost=row["exit_fee"],
            gross_pnl=row["gross_pnl"],net_pnl=row["net_pnl"],
            close_reason=REASON,sale_fill_id=row["fill_id"]))
    if view.residual_nominal == 0:
        if p["status"] == "CLOSED" and (when is None or
                when >= aware_datetime(p["closed_at"])):
            return None,realized
        # The issuer may have fully redeemed the remaining VN after a partial
        # sale. Only its independent final-redemption reviewer may close it;
        # the market-sale ledger cannot claim that payment or fake a SELL.
        if p["status"] == "OPEN":
            # Retain the original unsold slice solely as a lifecycle placeholder
            # until the issuer's reviewed terminal receipt is committed.  The
            # FI valuation/reducer independently reports zero economic VN; no
            # market-sale proceeds or issuer payout are manufactured here.
            waiting_vn = original-sum((Decimal(row["nominal"]) for row in visible),Decimal(0))
            if not 0 < waiting_vn <= original:
                raise ValueError("FI_NATIVE_SALE_REMAINDER_AWAITS_ISSUER_REVIEW")
            p.update(status="OPEN",quantity=str(waiting_vn),
                entry_cost=str(Decimal(p["entry_cost"])*waiting_vn/original),
                original_quantity=str(original),
                fi_economic_residual_nominal="0")
            return p,realized
    if p["status"] == "CLOSED" and when is not None and when >= aware_datetime(p["closed_at"]):
        raise ValueError("FI_NATIVE_SALE_TERMINAL_HAS_RESIDUAL_VN")
    p.update(status="OPEN",quantity=str(view.residual_nominal),
             entry_cost=str(Decimal(p["entry_cost"])*view.residual_nominal/original),
             closed_at=None,exit_price=None,exit_cost=None,gross_pnl=None,
             net_pnl=None,close_reason=None,original_quantity=str(original))
    return p,realized


def apply_paper_market_sale(store, broker, *, paper_id, request_id=None, nominal=None,
                            applied_at, witness=None, quote=None, book_bytes=None,
                            approved_book_sha256=None, fee_bytes=None, approved_fee_sha256=None):
    """Use authenticated Book2 and reviewed economics for one atomic PAPER sale."""
    if witness is None:
        raise ValueError(_AUTHORITY_ERROR)
    from be_paper_engine import Quote
    from rc6_fixed_income_sale_authority import market_sale_policy, verify_market_sale_book2

    intent, fields, proof = verify_market_sale_book2(store, witness, applied_at)
    if (paper_id != intent["paper_id"] or request_id not in (None, intent["request_id"])
            or proof["decision_key"] != "FI_SELL:" + intent["request_id"]):
        raise ValueError("FI_NATIVE_SALE_SIGNED_REQUEST_MISMATCH")
    request_id = intent["request_id"]
    with store.connect() as db:
        row = db.execute("SELECT asset_class,features_json FROM paper_positions WHERE paper_id=?",
                         (paper_id,)).fetchone()
    if row is None:
        raise ValueError("FI_NATIVE_SALE_OPEN_LOT_REQUIRED")
    identity = tuple(intent["identity"])
    symbol, family, market, currency, settlement = identity
    contract = contract_from_metadata(symbol, family, json.loads(row["features_json"])["financial_contract"])
    is_iol = proof["book2"]["source"] == "IOL_REST"
    provider = "IOL_REST" if is_iol else "PPI"
    policy, costs, policy_pin = market_sale_policy(store, identity, contract, applied_at, provider=provider)
    if policy_pin != intent["policy_sha256"] or policy["minimum_post_ack_ms"] != intent["minimum_post_ack_ms"]:
        raise ValueError("FI_NATIVE_SALE_SIGNED_POLICY_MISMATCH")
    max_qty = contract.quantity(intent["maximum_vn"])
    by_book = ((fields["bid_size"] * broker.participation) / contract.quantity_step).to_integral_value(
        rounding=ROUND_DOWN) * contract.quantity_step
    if nominal is None and by_book < contract.minimum_quantity:
        raise ValueError("FI_NATIVE_SALE_INSUFFICIENT_EXECUTABLE_DEPTH")
    qty = contract.quantity(nominal if nominal is not None else min(max_qty, by_book))
    if qty > max_qty:
        raise ValueError("FI_NATIVE_SALE_SIGNED_VN_EXCEEDED")
    book_schema = IOL_BOOK_SCHEMA if is_iol else BOOK_SCHEMA
    source_provider, route, metadata, _, _ = _BOOK_SOURCES[book_schema]
    observed = Quote(symbol, row["asset_class"], settlement, Decimal(0), contract=contract,
        currency=currency, market=market, metadata_source=metadata,
        last_kind="UNAVAILABLE", **fields)
    if quote is not None and (quote.contract != contract or any(
            getattr(quote, name) != value for name, value in fields.items())):
        raise ValueError("FI_NATIVE_SALE_CALLER_QUOTE_MISMATCH")
    price = broker._spot_fill_price(observed.bid, side="SELL", contract=contract, at=applied_at)
    fee = costs.leg_cost(price * contract.cash_multiplier * qty, decision_at=applied_at)
    source = {"schema": book_schema, "provider": source_provider, "route": route,
              "paper_only": True, "identity": list(identity), "quantity_unit": "VN",
              "metadata_source": observed.metadata_source,
              **{key: str(value) for key, value in fields.items()}}
    fee_quote = {"schema": FEE_SCHEMA, "paper_only": True, "identity": list(identity),
                 "paper_id": paper_id, "nominal": str(qty), "exit_price": str(price),
                 "exit_fee": str(fee), "currency": currency, "slippage": str(broker.slippage),
                 "policy": "PAPER_COST_MODEL_NOT_PPI_ACCOUNT_FEE"}
    verified_book, verified_fee = _canonical(source).encode(), _canonical(fee_quote).encode()
    if (book_bytes not in (None, verified_book) or approved_book_sha256 not in (None, _hash(verified_book))
            or fee_bytes not in (None, verified_fee) or approved_fee_sha256 not in (None, _hash(verified_fee))):
        raise ValueError("FI_NATIVE_SALE_CALLER_TERMS_MISMATCH")
    causal_sale = {"paper_id": paper_id, "request_id": request_id,
                   "decision_key": proof["decision_key"], "freeze_sha256": proof["freeze_sha256"],
                   "decision_receipt_sha256": proof["decision_receipt_sha256"],
                   "book1_sha256": intent["book1"]["receipt_sha256"],
                   "book2_sha256": proof["book2"]["receipt_sha256"], "policy_sha256": policy_pin,
                   "fee_policy_sha256": policy["fee_policy_sha256"],
                   "witness": witness}
    if "economic_policy_authority" in policy:
        causal_sale["economic_policy_authority"] = policy["economic_policy_authority"]
    return _commit_reviewed_sale(store, broker, paper_id=paper_id, request_id=request_id,
        nominal=qty, quote=observed, book_bytes=verified_book, approved_book_sha256=_hash(verified_book),
        fee_bytes=verified_fee, approved_fee_sha256=_hash(verified_fee), applied_at=applied_at,
        causal_sale=causal_sale)


def _commit_reviewed_sale(store, broker, *, paper_id, request_id, nominal,
                          quote, book_bytes, approved_book_sha256,
                          fee_bytes, approved_fee_sha256, applied_at, causal_sale):
    """Atomic VN, PAPER fill, book depth, cost, event and receivable booking."""
    from be_paper_engine import Quote
    if (not isinstance(paper_id, str) or not paper_id.startswith("PAPER-") or
            not isinstance(request_id, str) or not 0 < len(request_id) <= 128 or
            request_id != request_id.strip() or not isinstance(quote, Quote)):
        raise ValueError("FI_NATIVE_SALE_REQUEST_INVALID")
    qty = decimal_value(nominal, "FI nominal", positive=True)
    is_iol = quote.metadata_source == "IOL_REST"
    book_schema = IOL_BOOK_SCHEMA if is_iol else BOOK_SCHEMA
    _, _, _, event_source, event_reference = _BOOK_SOURCES[book_schema]
    source, source_sha = _parsed(book_bytes, approved_book_sha256, book_schema, _SOURCE_FIELDS)
    fee, fee_sha = _parsed(fee_bytes, approved_fee_sha256, FEE_SCHEMA, _FEE_FIELDS)
    at = aware_datetime(applied_at)
    at_iso = at.astimezone(timezone.utc).isoformat()
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if not _table(db):
            raise ValueError("FI_NATIVE_SALE_SCHEMA_MISSING")
        safety = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if (safety is None or safety[0] != "PRODUCTION_PAPER" or
                type(safety[1]) is not int or safety[1] != 0):
            raise ValueError("FI_NATIVE_SALE_PAPER_ZERO_ORDERS_REQUIRED")
        from rc6_paper_runtime_safety import runtime_admission_context
        _, runtime_error = runtime_admission_context(store, connection=db)
        if runtime_error:
            raise ValueError(runtime_error)
        causal_row = db.execute("""SELECT f.freeze_sha256,r.payload_sha256 FROM paper_causal_decision_freezes f
            JOIN paper_causal_decision_receipts r USING(decision_key) WHERE decision_key=?""",
            (causal_sale["decision_key"],)).fetchone()
        if (causal_row is None or tuple(causal_row) != (causal_sale["freeze_sha256"],
                causal_sale["decision_receipt_sha256"])):
            raise ValueError("FI_NATIVE_SALE_LOCKED_WRITER_ACK_MISMATCH")
        prior = db.execute("""SELECT * FROM paper_fi_native_sales
            WHERE paper_id=? AND request_id=?""", (paper_id,request_id)).fetchone()
        if prior:
            if (prior["source_sha256"] != source_sha or prior["fee_sha256"] != fee_sha or
                    Decimal(prior["nominal"]) != qty or
                    json.loads(prior["result_json"])["causal_sale"] != causal_sale):
                raise ValueError("FI_NATIVE_SALE_REPLAY_CONFLICT")
            if at < aware_datetime(prior["applied_at"]):
                raise ValueError("FI_NATIVE_SALE_REPLAY_CLOCK_ROLLBACK")
            sale_rows_checked(db, paper_id)
            return json.loads(prior["result_json"])
        seed = _seed(db, paper_id)
        root = db.execute("SELECT * FROM paper_positions WHERE paper_id=?",
                          (paper_id,)).fetchone()
        if root["status"] != "OPEN" or root["closed_at"] is not None:
            raise ValueError("FI_NATIVE_SALE_OPEN_LOT_REQUIRED")
        # A PAPER buy without coupons has not necessarily seeded the event
        # ledger. Pin its intact immutable BUY inside this transaction.
        seed_json = _canonical(asdict(seed))
        registered = db.execute("SELECT seed_json FROM paper_fi_lots WHERE paper_id=?",
                                (paper_id,)).fetchone()
        if registered and registered[0] != seed_json:
            raise ValueError("FI_NATIVE_SALE_SEED_DRIFT")
        db.execute("INSERT OR IGNORE INTO paper_fi_lots VALUES(?,?,?)",
                   (paper_id, seed_json,seed.position_sha256))
        # Matching BOOK time alone is insufficient for a historical retry:
        # a fresh request may not insert an earlier decision behind already
        # committed issuer, sale, or fixing events in the immutable journal.
        newest = db.execute("""SELECT booked_at FROM paper_fi_economic_events
            WHERE paper_id=? ORDER BY id DESC LIMIT 1""",(paper_id,)).fetchone()
        if newest and at < aware_datetime(newest[0]):
            raise ValueError("FI_NATIVE_SALE_BOOKING_CLOCK_ROLLBACK")
        if (not _quote_matches(source, quote, seed) or
                quote.time_error(at,max_age_seconds=broker.quote_max_age_seconds)):
            raise ValueError("FI_NATIVE_SALE_EXACT_IOL_BOOK_PIT_REQUIRED" if is_iol
                             else "FI_NATIVE_SALE_EXACT_PPI_BOOK_PIT_REQUIRED")
        if (quote.monetary_identity() != (seed.trading_currency,seed.market) or
                not quote.contract or quote.contract.key != (
                    seed.symbol,seed.family,seed.market,seed.trading_currency,seed.settlement) or
                quote.contract != contract_from_metadata(seed.symbol, seed.family,
                    json.loads(root["features_json"])["financial_contract"])):
            raise ValueError("FI_NATIVE_SALE_QUOTE_CONTRACT_IDENTITY_CONFLICT")
        view = position_at(db, paper_id, at_iso)
        if view is None or not 0 < qty <= view.residual_nominal:
            raise ValueError("FI_NATIVE_SALE_EXCEEDS_OWNED_RESIDUAL_VN")
        if (qty < quote.contract.minimum_quantity or qty % quote.contract.quantity_step
                or qty > liquidity.available(db,quote,"SELL_SIMULATED",broker.participation)):
            raise ValueError("FI_NATIVE_SALE_STEP_OR_SHARED_BOOK_DEPTH_EXCEEDED")
        from bq_exit_policy import PaperSessionPolicy
        policy = broker.session_policy or PaperSessionPolicy()
        if policy.execution_error(dict(root), at):
            raise ValueError("FI_NATIVE_SALE_PAPER_SESSION_NOT_OPEN")
        if not Decimal(0) <= broker.slippage < Decimal(1):
            raise ValueError("FI_NATIVE_SALE_SLIPPAGE_POLICY_INVALID")
        price = broker._spot_fill_price(quote.bid, side="SELL", contract=quote.contract, at=at)
        if price <= 0:
            raise ValueError("FI_NATIVE_SALE_EXECUTION_PRICE_INVALID")
        multiplier = decimal_value(quote.contract.cash_multiplier, "FI multiplier", positive=True)
        from rc6_fixed_income_sale_authority import market_sale_policy
        _, versioned_cost, policy_pin = market_sale_policy(
            store, quote.contract.key, quote.contract, at, provider="IOL_REST" if is_iol else "PPI")
        if (policy_pin != causal_sale["policy_sha256"] or versioned_cost.diagnostic()["policy_sha256"]
                != causal_sale["fee_policy_sha256"]):
            raise ValueError("FI_NATIVE_SALE_LOCKED_POLICY_CHANGED")
        exit_fee = versioned_cost.leg_cost(price * multiplier * qty, decision_at=at)
        expected_fee = {
            "schema": FEE_SCHEMA, "paper_only": True,
            "identity": [seed.symbol,seed.family,seed.market,seed.trading_currency,seed.settlement],
            "paper_id": paper_id, "nominal": str(qty), "exit_price": str(price),
            "exit_fee": str(exit_fee), "currency": seed.trading_currency,
            "slippage": str(broker.slippage), "policy": "PAPER_COST_MODEL_NOT_PPI_ACCOUNT_FEE",
        }
        if fee != expected_fee:
            raise ValueError("FI_NATIVE_SALE_FEE_MODEL_RECEIPT_MISMATCH")
        from cd_spot_ledger import allocated_entry_cost
        previous = sale_rows_checked(db, paper_id)
        sold = sum((Decimal(r["nominal"]) for r in previous), Decimal(0))
        allocated_prior = sum((Decimal(r["allocated_entry_cost"]) for r in previous), Decimal(0))
        entry_cost = allocated_entry_cost(
            Decimal(root["quantity"]), Decimal(root["entry_cost"]),
            sold, allocated_prior, qty)
        gross = (price - Decimal(root["entry_price"])) * qty * multiplier
        net = gross - entry_cost - exit_fee
        proceeds = price * qty * multiplier - exit_fee
        available = modeled_sale_settlement(root["settlement"], at_iso)
        if modeled_sale_settlement_date(root["settlement"],at_iso, family=seed.family,
                                        market=seed.market, currency=seed.trading_currency) is None:
            raise ValueError("FI_NATIVE_SALE_SETTLEMENT_CALENDAR_UNVERIFIED")
        basis = "PAPER_CONSERVATIVE_CALENDAR" if available else "PENDING_CONFIRMATION"
        event = EconomicEvent(
            event_id="FI_PAPER_SALE:"+request_id, obligation_id="FI_PAPER_SALE:"+request_id,
            paper_id=paper_id, kind="MARKET_SALE", effective_at=stamp(quote.book_at),
            recorded_at=at_iso, source_kind=event_source,
            source_reference=event_reference, source_sha256=source_sha,
            source_at=stamp(quote.book_at), nominal_base=str(view.residual_nominal),
            nominal_redeemed=str(qty))
        evidence_sha = _hash(_canonical({"book_sha256": source_sha, "fee_sha256": fee_sha,
                                         "causal_sale": causal_sale}).encode())
        event_row = db.execute("""INSERT INTO paper_fi_economic_events
            (paper_id,event_id,obligation_id,kind,event_json,receipt_sha256,
             source_sha256,booked_at,entitlement_record_at)
            VALUES (?,?,?,?,?,?,?,?,NULL)""",
            (paper_id,event.event_id,event.obligation_id,event.kind,
             _canonical(asdict(event)),evidence_sha,source_sha,at_iso))
        fill = db.execute("""INSERT INTO paper_fills
            (paper_id,source,side,filled_at,quantity,price,costs,slippage)
            VALUES (?,?,?,?,?,?,?,?)""", (paper_id,"PRODUCTION_PAPER",
                "SELL_SIMULATED",at_iso,str(qty),str(price),str(exit_fee),
                str(quote.bid-price)))
        liquidity.record(db,fill.lastrowid,quote)
        final = qty == view.residual_nominal
        receipt = {
            "schema": SCHEMA, "paper_only": True, "real_routes_used": [],
            "real_orders_sent": 0, "broker_order_sent": False,
            "settlement_proof": "PAPER_MODELED_NOT_ACCOUNT_MOVEMENT",
            "reason": REASON, "paper_id": paper_id, "request_id": request_id,
            "fill_id": fill.lastrowid, "event_row_id": event_row.lastrowid,
            "applied_at": at_iso, "book_at": stamp(quote.book_at),
            "nominal": str(qty), "residual_after": str(view.residual_nominal-qty),
            "price": str(price), "exit_fee": str(exit_fee),
            "allocated_entry_cost": str(entry_cost), "gross_pnl": str(gross),
            "net_pnl": str(net), "net_proceeds": str(proceeds),
            "settlement_available_at": available,
            "book_evidence": source, "fee_evidence": fee, "closed_paper": final,
            "causal_sale": causal_sale,
        }
        db.execute("""INSERT INTO paper_fi_native_sales(
          fill_id,event_row_id,paper_id,request_id,source_sha256,fee_sha256,
          receipt_sha256,applied_at,book_at,nominal,price,exit_fee,
          allocated_entry_cost,gross_pnl,net_pnl,net_proceeds,available_at,
          settlement_basis,result_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (fill.lastrowid,event_row.lastrowid,paper_id,request_id,
           source_sha,fee_sha,evidence_sha,at_iso,stamp(quote.book_at),
           str(qty),str(price),str(exit_fee),str(entry_cost),str(gross),str(net),
           str(proceeds),available,basis,_canonical(receipt)))
        if final:
            total_qty = sold + qty
            previous_price = sum((Decimal(r["price"]) * Decimal(r["nominal"])
                                  for r in previous),Decimal(0))
            # A partial amortization reduces issuer VN without a sale. The
            # aggregate quote price divides by sold VN only, not initial VN.
            avg_sale = (previous_price + price*qty)/total_qty
            db.execute("""UPDATE paper_positions SET status='CLOSED',closed_at=?,
                close_reason=?,exit_price=?,exit_cost=?,gross_pnl=?,net_pnl=?
                WHERE paper_id=? AND status='OPEN'""",
                (at_iso,REASON,str(avg_sale),
                 str(sum((Decimal(r["exit_fee"]) for r in previous),Decimal(0))+exit_fee),
                 str(sum((Decimal(r["gross_pnl"]) for r in previous),Decimal(0))+gross),
                 str(sum((Decimal(r["net_pnl"]) for r in previous),Decimal(0))+net),
                 paper_id))
            if db.execute("SELECT changes()").fetchone()[0] != 1:
                raise ValueError("FI_NATIVE_SALE_TERMINAL_WRITE_CONFLICT")
        db.execute("""INSERT INTO paper_events
            (event_at,source,event_type,paper_id,detail) VALUES(?,?,?,?,?)""",
            (at_iso,"PRODUCTION_PAPER","FI_NATIVE_PAPER_MARKET_SALE",paper_id,
             "PAPER simulated sell, not broker execution; issuer cash unmodified"))
        checked = sale_rows_checked(db, paper_id)
        if not checked or checked[-1]["fill_id"] != fill.lastrowid:
            raise ValueError("FI_NATIVE_SALE_ATOMIC_RECONCILIATION_FAILED")
        return receipt
