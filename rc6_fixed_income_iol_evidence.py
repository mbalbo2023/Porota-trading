"""Exact IOL REST PAPER observations and reviewed nominal economics."""
from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from bs_instrument_contracts import aware_datetime, contract_from_metadata, decimal_value
from fe_fixed_income_nominal_contract_rc6 import FAMILIES
from rc6_fixed_income_runtime import MAX_SOURCE_BYTES, _digest, _json, _read, _window

PROVIDER = "IOL_REST"
BOOK_SCHEMA = "rc6.fi-iol-gateway-book.v1"
POLICY_SCHEMA = "rc6.fi-iol-market-policy.v1"
ECONOMICS_SCHEMA = "rc6.fi-iol-nominal-economics.v1"
POLICY_PIN = "POROTA_FI_IOL_MARKET_POLICY_SHA256"
ECONOMICS_AUTHORITY = "RELEASE_REVIEWED_IOL_REST_PRICE_AND_DEPTH"
MAX_BOOK_BYTES = 16_000
MAX_BOOK_AGE_SECONDS = 120
BA = ZoneInfo("America/Argentina/Buenos_Aires")


def iol_contract_reason(identity, contract):
    """Report whether the stable five-part nominal contract can bind an IOL observation."""
    from rc6_iol_market_data import TERMS

    if len(identity) != 5 or identity[1] not in FAMILIES:
        return "FI_IOL_FAMILY_UNSUPPORTED"
    if identity[2] != "BYMA" or identity[3] not in {"ARS", "USD"} or identity[4] not in TERMS:
        return "FI_IOL_EXACT_VARIANT_UNSUPPORTED"
    if contract is None or contract.key != tuple(identity):
        return "FI_IOL_EXACT_NOMINAL_CONTRACT_REQUIRED"
    if (contract.minimum_quantity is None or
            contract.quantity_step != contract.quantity_step.to_integral_value() or
            contract.minimum_quantity != contract.minimum_quantity.to_integral_value()):
        return "FI_IOL_NOMINAL_QUANTITY_GRID_UNVERIFIED"
    return ""


def _level_fields(depth):
    """Keep the returned prices and sizes, without unrelated provider fields."""
    return {key: [{name: level.get(name) for name in ("price", "size")} for level in depth[key]]
            for key in ("bids", "asks")}


def create_iol_book(identity, *, asset, asset_at, depth, depth_at, depth_monotonic_ns,
                    generation, consumer, depth_sha256):
    """Capture provider fields at the authenticated IOL Gateway boundary."""
    from rc6_ppi_shared_feed import _sha, _canonical_bytes

    book = {"schema": BOOK_SCHEMA, "source": PROVIDER, "consumer": consumer,
            "identity": list(identity), "session_fingerprint": generation,
            "asset": {key: asset[key] for key in ("symbol", "market", "currency", "type")},
            "asset_received_at_utc": asset_at, "received_at_utc": depth_at,
            "receive_monotonic_ns": depth_monotonic_ns,
            "raw_depth_sha256": depth_sha256, "paper_ready": False, "order_route": "NOT_PRESENT",
            "depth": {key: depth.get(key) for key in (
                "symbol", "market", "settlement_term", "currency", "last_updated")}}
    book["depth"].update(_level_fields(depth))
    if len(_canonical_bytes(book)) > MAX_BOOK_BYTES:
        raise ValueError("FI_IOL_GATEWAY_BOOK_OVERSIZE")
    book["receipt_sha256"] = _sha(book)
    iol_native_book_fields(book, identity)
    return book


def iol_native_book_fields(book, identity):
    """Verify captured identity, frame digest, venue clock and two-sided depth."""
    from rc6_iol_market_data import MARKETS, TERMS, _best_level, _capture
    from rc6_multisource_discovery import canonical_family
    from rc6_ppi_shared_feed import _sha, _canonical_bytes
    from iol_rest_readonly_rc6 import IOLRestError

    if (not isinstance(book, dict) or book.get("schema") != BOOK_SCHEMA
            or book.get("source") != PROVIDER or book.get("identity") != list(identity)
            or len(identity) != 5 or identity[1] not in FAMILIES
            or book.get("consumer") not in {"scanner", "exit_reader"}
            or book.get("paper_ready") is not False or book.get("order_route") != "NOT_PRESENT"
            or len(_canonical_bytes(book)) > MAX_BOOK_BYTES + 100
            or book.get("receipt_sha256") != _sha(
                {key: value for key, value in book.items() if key != "receipt_sha256"})):
        raise ValueError("FI_IOL_GATEWAY_BOOK_RECEIPT_UNVERIFIED")
    _digest(book.get("session_fingerprint"))
    _digest(book.get("raw_depth_sha256"))
    symbol, family, market, currency, term = identity
    venue = (symbol, MARKETS[market], TERMS[term])
    asset, depth = book["asset"], book["depth"]
    if ((asset["symbol"], asset["market"], asset["currency"]) != (*venue[:2], currency)
            or canonical_family(asset["type"]) != family):
        raise ValueError("FI_IOL_GATEWAY_BOOK_ASSET_MISMATCH")
    seen, asset_seen = (aware_datetime(book[key]) for key in
                        ("received_at_utc", "asset_received_at_utc"))
    ns = book.get("receive_monotonic_ns")
    if type(ns) is not int or ns <= 0 or not seen - timedelta(seconds=30) <= asset_seen <= seen:
        raise ValueError("FI_IOL_GATEWAY_BOOK_CAPTURE_CLOCK_UNVERIFIED")
    try:
        captured = _capture(depth, received=seen, venue_key=venue, currency=currency,
                            source_time=depth.get("last_updated"))
        bid, bid_size = _best_level(depth["bids"], bid=True)
        ask, ask_size = _best_level(depth["asks"], bid=False)
    except IOLRestError as exc:
        raise ValueError("FI_IOL_GATEWAY_BOOK_DEPTH_OR_CLOCK_UNVERIFIED") from exc
    if seen - aware_datetime(captured.provider_at) > timedelta(seconds=MAX_BOOK_AGE_SECONDS):
        raise ValueError("FI_IOL_GATEWAY_BOOK_STALE")
    if bid > ask:
        raise ValueError("FI_IOL_GATEWAY_BOOK_CROSSED")
    return {"book_at": captured.provider_at, "observed_at": captured.received_at,
            "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size}


def iol_quote_book_fields(quote, identity, *, consumer):
    receipt = quote.iol_native_rest_book
    fields = iol_native_book_fields(receipt, identity)
    if (quote.metadata_source != PROVIDER or receipt["consumer"] != consumer or
            any(aware_datetime(getattr(quote, key)) != aware_datetime(fields[key])
                for key in ("book_at", "observed_at")) or
            any(getattr(quote, key) != fields[key] for key in ("bid", "ask", "bid_size", "ask_size"))):
        raise ValueError("FI_IOL_QUOTE_BOOK_MISMATCH")
    return fields


def iol_reviewed_position_book(store, position, quote, at):
    """Check nominal pricing against the immutable contract used for the held lot."""
    identity = tuple(position[key] for key in ("symbol", "asset_class", "market", "currency", "settlement"))
    terms = json.loads(position["features_json"])["financial_contract"]
    held = contract_from_metadata(position["symbol"], position["asset_class"], terms)
    if held.key != identity or quote.contract != held:
        raise ValueError("FI_IOL_HELD_NOMINAL_CONTRACT_MISMATCH")
    receipt = quote.iol_native_rest_book
    consumer = receipt.get("consumer") if isinstance(receipt, dict) else None
    iol_quote_book_fields(quote, identity, consumer=consumer)
    iol_reviewed_policy(store, identity, held, at)


def iol_entry_market_evidence(quote, identity, at):
    """Bind the independent IOL stream trade and depth to the same OPEN session."""
    from co_market_sessions_hf6 import byma_paper_spot_phase

    at = aware_datetime(at)
    reason = iol_contract_reason(identity, quote.contract)
    if reason:
        raise ValueError(reason)
    iol_quote_book_fields(quote, identity, consumer="scanner")
    if quote.opening_block_reason or quote.time_error(at, require_trade=True):
        raise ValueError("FI_IOL_ENTRY_BOOK_OR_TRADE_STALE")
    proof = quote.iol_trade_evidence
    book = quote.iol_native_rest_book
    if (not isinstance(proof, dict) or proof.get("identity") != list(identity)
            or proof.get("gateway_generation") != book["session_fingerprint"]):
        raise ValueError("FI_IOL_ENTRY_TRADE_GENERATION_UNVERIFIED")
    trade_at, book_at = (aware_datetime(getattr(quote, key)) for key in ("trade_at", "book_at"))
    if (aware_datetime(proof.get("trade_at")) != trade_at
            or not trade_at <= aware_datetime(proof.get("received_at")) <=
            aware_datetime(proof.get("quote_received_at")) <= aware_datetime(quote.observed_at)
            or decimal_value(proof.get("price"), "IOL trade price", positive=True) != quote.last):
        raise ValueError("FI_IOL_ENTRY_TRADE_PROOF_MISMATCH")
    if any(stamp.astimezone(BA).date() != at.astimezone(BA).date()
           or byma_paper_spot_phase(stamp) != "OPEN" for stamp in (trade_at, book_at, at)):
        raise ValueError("FI_IOL_ENTRY_WRONG_MARKET_SESSION")
    return book["receipt_sha256"]


def iol_reviewed_policy(store, identity, contract, at):
    """Require reviewed IOL price and VN depth units for the exact variant."""
    from rc6_fixed_income_sale_authority import sale_release_anchor
    from rc6_performance.costs import paper_cost_contract

    reason = iol_contract_reason(identity, contract)
    if reason:
        raise ValueError(reason)
    pin = os.getenv(POLICY_PIN, "")
    if not pin:
        raise ValueError("FI_IOL_PRICE_AND_DEPTH_POLICY_PIN_MISSING")
    pin = _digest(pin)
    release = sale_release_anchor(os.getenv("POROTA_BUILD_SHA"), os.getenv("POROTA_CANDIDATE_TREE_SHA"))
    root = Path(os.getenv("POROTA_FI_EVIDENCE_DIRECTORY",
                          str(Path(store.path).parent / "fixed-income-evidence")))
    if root.is_symlink() or not root.is_dir():
        raise ValueError("FI_IOL_ECONOMICS_DIRECTORY_UNAVAILABLE")
    doc = _json(_read(root, "iol-market-policy.json", pin))
    if (doc.get("schema") != POLICY_SCHEMA or doc.get("paper_only") is not True
            or doc.get("real_routes_used") != [] or doc.get("release_sha256") != release
            or doc.get("source_authority") != ECONOMICS_AUTHORITY
            or not isinstance(doc.get("instruments"), list)):
        raise ValueError("FI_IOL_MARKET_POLICY_UNVERIFIED")
    at = aware_datetime(at)
    _window(doc, at)
    rows = [row for row in doc["instruments"]
            if isinstance(row, dict) and row.get("identity") == list(identity)]
    if len(rows) != 1:
        raise ValueError("FI_IOL_MARKET_POLICY_EXACT_IDENTITY_UNVERIFIED")
    source_pin = _digest(rows[0].get("economics_sha256"))
    source = _json(_read(root, f"iol-economics-{source_pin}.json", source_pin))
    if (source.get("schema") != ECONOMICS_SCHEMA or source.get("identity") != list(identity)
            or source.get("provider") != PROVIDER or source.get("price_basis") != "CONTRACT_QUOTE_PRICE"
            or source.get("depth_size_unit") != "VN" or source.get("order_quantity_unit") != "VN"):
        raise ValueError("FI_IOL_PRICE_OR_DEPTH_UNITS_UNVERIFIED")
    _window(source, at)
    if aware_datetime(source["known_at"]) > aware_datetime(doc["known_at"]):
        raise ValueError("FI_IOL_MARKET_POLICY_REVIEW_CLOCK_INVALID")
    provider_pin = _digest(source.get("provider_evidence_sha256"))
    _read(root, f"iol-source-{provider_pin}.bin", provider_pin, limit=MAX_SOURCE_BYTES)
    for key in ("cash_multiplier", "minimum_quantity", "quantity_step"):
        if decimal_value(source.get(key), key, positive=True) != getattr(contract, key):
            raise ValueError("FI_IOL_PRICE_OR_DEPTH_CONTRACT_CONFLICT")
    costs = paper_cost_contract(identity[1], identity[3], at, symbol=identity[0],
                                market=identity[2], settlement=identity[4])
    if costs.binding_error(at):
        raise ValueError("FI_MARKET_SALE_PAPER_COST_UNVERIFIED")
    row = {"minimum_post_ack_ms": 0, "fee_policy_sha256": costs.diagnostic()["policy_sha256"],
           "economic_policy_authority": ECONOMICS_AUTHORITY, "release_sha256": release}
    return row, costs, pin
