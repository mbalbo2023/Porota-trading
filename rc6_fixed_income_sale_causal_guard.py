"""Fail-closed *structural* witness for native PAPER fixed-income sales.

This deliberately does not authorize a sale or assert that PPI, a signed PAPER
decision, a broker tariff or settlement has been independently authenticated.
The caller's hashes and labels are claims, not signatures or provider proof.
It is a bounded, deterministic OFFHOST preflight which can be wired to a
trusted, persisted Book/decision/fee authority in a later coherent release.

The existing FI_NATIVE_MARKET_EXIT_NOT_ADMITTED production interlock remains
required. Nothing here calls a broker, writes SQLite or creates a PAPER fill.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

PAPER_ONLY = True
REAL_ROUTES_USED = ()
NATIVE_MARKET_SELL_AUTHORITY = False
SCHEMA = "rc6.fi-sale-causal-structural-witness.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})
_BOOK_FIELDS = frozenset({
    "identity", "provider", "route", "native_book_at", "observed_at", "known_at",
    "journal_event_sha256", "bid", "ask", "bid_size", "quantity_unit",
    "quote_price_basis",
})
_DECISION_FIELDS = frozenset({
    "identity", "side", "decided_at", "known_at", "request_id",
    "decision_receipt_sha256", "decision_signature_sha256",
})
_CONTRACT_FIELDS = frozenset({
    "identity", "quantity_step", "minimum_quantity", "cash_multiplier",
    "quote_price_basis", "payout_currency",
})
_FEE_FIELDS = frozenset({
    "identity", "nominal", "fee_currency", "fee_amount", "tariff_sha256",
    "execution_price", "slippage_fraction",
})
_SETTLEMENT_FIELDS = frozenset({
    "identity", "settlement_currency", "settlement_at",
    "net_proceeds", "settlement_policy_sha256", "fx_receipt_sha256",
})
_AUTHORITY_BLOCKERS = (
    "FI_EXIT_PPI_BOOK_JOURNAL_AUTHORITY_UNBOUND",
    "FI_EXIT_SIGNED_PAPER_DECISION_AUTHORITY_UNBOUND",
    "FI_EXIT_ALL_STATUS_IDENTITY_AUTHORITY_UNBOUND",
    "FI_EXIT_VN_AND_TARIFF_AUTHORITY_UNBOUND",
    "FI_EXIT_SETTLEMENT_AUTHORITY_UNBOUND",
)


def _object(value, expected, label):
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"FI_EXIT_{label}_SCHEMA_INVALID")
    return value


def _instant(value, label):
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"FI_EXIT_{label}_CLOCK_INVALID")
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"FI_EXIT_{label}_CLOCK_INVALID") from exc
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError(f"FI_EXIT_{label}_CLOCK_INVALID")
    return at.astimezone(timezone.utc)


def _decimal(value, label, *, positive=False):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"FI_EXIT_{label}_VALUE_INVALID")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"FI_EXIT_{label}_VALUE_INVALID") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"FI_EXIT_{label}_VALUE_INVALID")
    return number


def _sha(value, label):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"FI_EXIT_{label}_DIGEST_INVALID")
    return value


def _identity(value, label):
    if (type(value) is not list or len(value) != 5 or
            any(not isinstance(part, str) or not part or part != part.strip()
                for part in value) or value[1] not in _IDENTITY_FAMILIES):
        raise ValueError(f"FI_EXIT_{label}_FIVE_KEY_INVALID")
    return tuple(value)


def assess_causal_paper_sale(*, identity, book1, decision, book2, contract,
                             fee, settlement, requested_nominal,
                             latency_seconds, execution_at):
    """Check five-key, clocks, VN, price basis, fee and settlement equations.

    The result is ALWAYS BLOCKED: structural coherence is a necessary but
    insufficient dependency for the *independent* source/decision authority.
    Exact retries are deterministic; this function has no durable transaction.
    """
    expected = _identity(list(identity) if isinstance(identity, tuple) else identity,
                         "TARGET")
    book1 = _object(book1, _BOOK_FIELDS, "BOOK1")
    book2 = _object(book2, _BOOK_FIELDS, "BOOK2")
    decision = _object(decision, _DECISION_FIELDS, "DECISION")
    contract = _object(contract, _CONTRACT_FIELDS, "CONTRACT")
    fee = _object(fee, _FEE_FIELDS, "FEE")
    settlement = _object(settlement, _SETTLEMENT_FIELDS, "SETTLEMENT")
    for label, item in (("BOOK1", book1), ("BOOK2", book2),
                        ("DECISION", decision), ("CONTRACT", contract),
                        ("FEE", fee), ("SETTLEMENT", settlement)):
        _identity(item["identity"], label)
    b1_at = _instant(book1["native_book_at"], "BOOK1_NATIVE")
    b1_seen = _instant(book1["observed_at"], "BOOK1_OBSERVED")
    b1_known = _instant(book1["known_at"], "BOOK1_KNOWN")
    decided = _instant(decision["decided_at"], "DECIDED")
    decision_known = _instant(decision["known_at"], "DECISION_KNOWN")
    b2_at = _instant(book2["native_book_at"], "BOOK2_NATIVE")
    b2_seen = _instant(book2["observed_at"], "BOOK2_OBSERVED")
    b2_known = _instant(book2["known_at"], "BOOK2_KNOWN")
    executed = _instant(execution_at, "EXECUTION")
    settled = _instant(settlement["settlement_at"], "SETTLEMENT")
    latency = _decimal(latency_seconds, "LATENCY", positive=True)
    if latency > Decimal("3600"):
        raise ValueError("FI_EXIT_LATENCY_BOUND_EXCEEDED")
    latency_us = latency * Decimal(1_000_000)
    if latency_us != latency_us.to_integral_value():
        raise ValueError("FI_EXIT_LATENCY_SUBMICROSECOND_UNSUPPORTED")
    qty = _decimal(requested_nominal, "VN", positive=True)
    step = _decimal(contract["quantity_step"], "VN_STEP", positive=True)
    minimum = _decimal(contract["minimum_quantity"], "VN_MINIMUM", positive=True)
    multiplier = _decimal(contract["cash_multiplier"], "PRICE_MULTIPLIER", positive=True)
    price = _decimal(book2["bid"], "BOOK2_BID", positive=True)
    fee_amount = _decimal(fee["fee_amount"], "FEE")
    slippage = _decimal(fee["slippage_fraction"], "SLIPPAGE")
    if slippage >= 1:
        raise ValueError("FI_EXIT_SLIPPAGE_POLICY_INVALID")
    modeled_execution_price = (price * (1 - slippage)).quantize(Decimal("0.0001"))
    fee_execution_price = _decimal(fee["execution_price"], "EXECUTION_PRICE", positive=True)
    _sha(book1["journal_event_sha256"], "BOOK1_EVENT")
    _sha(book2["journal_event_sha256"], "BOOK2_EVENT")
    _sha(decision["decision_receipt_sha256"], "DECISION_RECEIPT")
    _sha(decision["decision_signature_sha256"], "DECISION_SIGNATURE_CLAIM")
    _sha(fee["tariff_sha256"], "TARIFF")
    _sha(settlement["settlement_policy_sha256"], "SETTLEMENT_POLICY")
    if settlement["fx_receipt_sha256"] is not None:
        _sha(settlement["fx_receipt_sha256"], "FX")
    if (not isinstance(decision["request_id"], str) or
            not 0 < len(decision["request_id"]) <= 128 or
            decision["request_id"] != decision["request_id"].strip()):
        raise ValueError("FI_EXIT_DECISION_REQUEST_ID_INVALID")

    blockers = []
    if any(tuple(item["identity"]) != expected for item in
           (book1, decision, book2, contract, fee, settlement)):
        blockers.append("FI_EXIT_FIVE_KEY_IDENTITY_CONFLICT")
    if (decision["side"] != "SELL" or
            any(b["provider"] != "PPI_PRIMARY_READ_ONLY" or
                b["route"] != "MarketData/Book" for b in (book1, book2))):
        blockers.append("FI_EXIT_SIDE_OR_PROVIDER_CLAIM_INVALID")
    if (b1_at > b1_seen or b1_seen > b1_known or b1_known > decided or
            decided > decision_known or decision_known > executed):
        blockers.append("FI_EXIT_BOOK1_DECISION_CAUSAL_CLOCK_INVALID")
    # A decision timestamp alone is not usable until the signed/recorded
    # decision was actually known. The next native book must occur strictly
    # after the later instant plus the latency budget, never before it.
    decision_usable_at = max(decided, decision_known)
    if (b2_at <= decision_usable_at + timedelta(microseconds=int(latency_us)) or
            b2_at > b2_seen or b2_seen > b2_known or b2_known > executed):
        blockers.append("FI_EXIT_BOOK2_STRICT_FUTURE_CLOCK_INVALID")
    if book1["journal_event_sha256"] == book2["journal_event_sha256"]:
        blockers.append("FI_EXIT_BOOK2_EVENT_REPLAY")
    if any(b["quantity_unit"] != "VN" for b in (book1, book2)):
        blockers.append("FI_EXIT_PPI_BOOK_QUANTITY_UNIT_UNVERIFIED")
    if (book1["quote_price_basis"] != contract["quote_price_basis"] or
            book2["quote_price_basis"] != contract["quote_price_basis"] or
            contract["quote_price_basis"] not in {"PER_100_VN", "PER_1_VN"} or
            multiplier != (Decimal("0.01") if contract["quote_price_basis"] ==
                           "PER_100_VN" else Decimal(1))):
        blockers.append("FI_EXIT_PRICE_PER_VN_MULTIPLIER_CONFLICT")
    if (qty < minimum or qty % step or
            _decimal(book2["bid_size"], "BOOK2_DEPTH") < qty or
            _decimal(book1["bid_size"], "BOOK1_DEPTH") <= 0):
        blockers.append("FI_EXIT_VN_STEP_OR_NATIVE_DEPTH_INSUFFICIENT")
    for label, item in (("BOOK1", book1), ("BOOK2", book2)):
        bid, ask = _decimal(item["bid"], label+"_BID", positive=True), _decimal(
            item["ask"], label+"_ASK", positive=True)
        if bid > ask:
            blockers.append("FI_EXIT_NATIVE_BOOK_CROSSED")
    if (_decimal(fee["nominal"], "FEE_NOMINAL") != qty or
            fee["fee_currency"] != expected[3]):
        blockers.append("FI_EXIT_TARIFF_NOMINAL_OR_CURRENCY_CONFLICT")
    if fee_execution_price != modeled_execution_price:
        blockers.append("FI_EXIT_EXECUTION_PRICE_SLIPPAGE_MISMATCH")
    if (settlement["settlement_currency"] != expected[3] or
            settled <= executed or
            _decimal(settlement["net_proceeds"], "NET_PROCEEDS") !=
            fee_execution_price*qty*multiplier-fee_amount):
        blockers.append("FI_EXIT_SETTLEMENT_CURRENCY_TIME_OR_CASH_CONFLICT")
    if (not isinstance(contract["payout_currency"],str) or
            contract["payout_currency"] not in {"ARS", "USD"}):
        blockers.append("FI_EXIT_ISSUER_PAYOUT_CURRENCY_INVALID")
    # Issuer payment currency (e.g. USD coupons for an ARS bond sale) is
    # distinct from trade/fee settlement. It must never become implicit FX.
    if settlement["fx_receipt_sha256"] is not None:
        blockers.append("FI_EXIT_UNREQUESTED_FX_AUTHORITY_UNBOUND")

    # No input boolean or caller-provided digest may satisfy these independent
    # authorities. A separate trusted producer/signature/tariff/settlement owner
    # must be bound under the same economic transaction in a future release.
    claimed = {"identity": list(expected), "book1": book1, "decision": decision,
               "book2": book2, "contract": contract, "fee": fee,
               "settlement": settlement, "nominal": requested_nominal,
               "latency_seconds": latency_seconds, "execution_at": execution_at}
    raw = json.dumps(claimed, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {
        "schema": SCHEMA, "status": "BLOCKED", "paper_only": True,
        "native_market_sell_authorized": False, "real_routes_used": [],
        "real_orders_sent": 0, "economic_side_effect": "0",
        "structural_witness": not blockers,
        "structural_blockers": blockers,
        "authority_blockers": list(_AUTHORITY_BLOCKERS),
        "blockers": blockers + list(_AUTHORITY_BLOCKERS),
        "claim_sha256": hashlib.sha256(raw).hexdigest(),
    }


def assess_complementary_fx_arithmetic(*, foreign_amount, local_amount,
                                       applied_rate, rate_convention):
    """Diagnostic only: IOL/secondary amounts cannot establish PPI/FX authority.

    The caller must name a rate convention; `ARS_PER_USD` checks arithmetic,
    not whether the upstream endpoint actually used that semantic convention.
    Even a matching rate is insufficient without independently proved units,
    currency, source and a consistent PIT instant.
    """
    foreign = _decimal(foreign_amount, "FOREIGN_AMOUNT", positive=True)
    local = _decimal(local_amount, "LOCAL_AMOUNT", positive=True)
    rate = _decimal(applied_rate, "APPLIED_RATE", positive=True)
    if rate_convention != "ARS_PER_USD":
        raise ValueError("FI_EXIT_FX_RATE_CONVENTION_UNVERIFIED")
    expected = foreign * rate
    # A currency cent is the conservative unit for this diagnostic; the
    # nonmatching AL30 example exceeds it by orders of magnitude.
    difference = abs(local - expected)
    mismatch = difference > Decimal("0.01")
    return {
        "status": "BLOCKED", "real_orders_sent": 0,
        "ppi_primary_provenance_established": False,
        "fx_authorized": False,
        "arithmetic_consistent_if_named_convention": not mismatch,
        "blockers": (["FI_EXIT_COMPLEMENTARY_FX_AMOUNT_RATE_MISMATCH"]
                     if mismatch else []) +
                    ["FI_EXIT_COMPLEMENTARY_FX_SOURCE_SEMANTICS_AND_PIT_UNVERIFIED"],
    }
