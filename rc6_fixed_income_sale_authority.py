"""Independently witnessed PAPER market exits for BONOS and ON.

The engine freezes a SELL with the native REST Book1. The existing independent
observer signs its post-COMMIT ACK and witnesses the first eligible Book2 in
the pinned gateway generation. Release policy supplies the units and fee basis.
"""
from __future__ import annotations

import base64
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
import os
from pathlib import Path
import re

from bs_instrument_contracts import aware_datetime, decimal_value, family_name
from rc6_fixed_income_runtime import _digest, _json, _read
from rc6_fixed_income_iol_evidence import (
    POLICY_PIN as IOL_POLICY_PIN, PROVIDER as IOL_PROVIDER,
    iol_native_book_fields, iol_quote_book_fields, iol_reviewed_policy,
)
import rc6_paper_causal_decision_receipt as causal
from rc6_ppi_shared_feed import NATIVE_READ_SCHEMA, ReadOnlyUnixFeedClient, _sha as native_sha


SALE_FAMILIES = frozenset({"BONOS", "OBLIGACIONES"})
POLICY_SCHEMA = "rc6.fi-market-sale-policy.v2"
ECONOMICS_SCHEMA = "rc6.fi-market-sale-ppi-economics.v1"
WITNESS_SCHEMA = "rc6.fi-market-sale-book2-ed25519.v1"
POLICY_PIN = "POROTA_FI_MARKET_SALE_POLICY_SHA256"
HISTORICAL_PAPER_ENABLE = "POROTA_FI_HISTORICAL_PAPER_SALE"
HISTORICAL_MODEL = "rc6.fi-sale-historical-paper-model.v1"
HISTORICAL_PATH = Path(__file__).resolve().parent / "ops/policy/rc6-fi-sale-paper-model.json"
MAX_BOOK_BYTES = 20_000


def sale_release_anchor(source_sha, source_tree):
    """Bind PAPER policy and signer to the exact sealed Deploy V2 coordinates."""
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
               for value in (source_sha, source_tree)):
        raise ValueError("FI_PAPER_RELEASE_COORDINATES_UNAVAILABLE")
    return causal._sha(causal._json([HISTORICAL_MODEL, source_sha, source_tree]))


@lru_cache(maxsize=1)
def packaged_historical_model():
    """Read the immutable operator-approved PAPER assumptions in the image."""
    from bu_instrument_catalog import normalize_record

    raw = HISTORICAL_PATH.read_bytes()
    source = _json(raw)
    if (source.get("schema") != HISTORICAL_MODEL or source.get("paper_only") is not True
            or source.get("quantity_model") != "NORMALIZED_HISTORICAL_PPI_VN"
            or source.get("settlement_model") != "PAPER_CONSERVATIVE_BYMA_SPOT"
            or type(source.get("catalogue_receipt")) is not int):
        raise ValueError("FI_PAPER_HISTORICAL_MODEL_UNVERIFIED")
    known_at = aware_datetime(source["known_at"])
    rows = {}
    for row in source["instruments"]:
        identity = tuple(row["identity"])
        if (len(identity) != 5 or identity[1] not in SALE_FAMILIES or identity in rows
                or row["settlement_evidence"] != "REQUEST_CANDIDATE"):
            raise ValueError("FI_PAPER_HISTORICAL_IDENTITY_UNBOUND")
        discovery = normalize_record(row["ppi_discovery"], identity[4],
            row["ppi_discovery_at"], "FI_HISTORICAL_PAPER_SALE")
        observed = tuple(discovery[field] for field in (
            "ticker", "instrument_type", "market", "currency", "settlement"))
        if (observed != identity or discovery["settlement_source"] != "REQUEST_CANDIDATE"
                or aware_datetime(row["ppi_discovery_at"]) > known_at):
            raise ValueError("FI_PAPER_HISTORICAL_PPI_DISCOVERY_CONFLICT")
        _digest(row["ppi_metadata_sha256"])
        values = {name: decimal_value(row[name], name, positive=True)
                  for name in ("cash_multiplier", "minimum_quantity", "quantity_step")}
        if values["minimum_quantity"] % values["quantity_step"]:
            raise ValueError("FI_PAPER_HISTORICAL_QUANTITY_GRID_INVALID")
        rows[identity] = values
    if not rows:
        raise ValueError("FI_PAPER_HISTORICAL_MODEL_EMPTY")
    return rows, known_at, causal._sha(raw)


def deployed_sale_model():
    if os.getenv(HISTORICAL_PAPER_ENABLE) != "1":
        return None
    if (os.getenv("DASHBOARD_OPERATION_MODE") != "PRODUCTION_PAPER"
            or os.getenv("EXECUTION_MODE") != "SIMULATION"
            or os.getenv("REAL_ORDER_CAPABILITY") != "BLOCKED"
            or os.getenv("ALLOW_REAL_ORDERS", "").lower() not in {"", "false", "0"}):
        raise ValueError("FI_PAPER_HISTORICAL_MODEL_MODE_BLOCKED")
    release = sale_release_anchor(os.getenv("POROTA_BUILD_SHA"), os.getenv("POROTA_CANDIDATE_TREE_SHA"))
    rows, known_at, source_sha = packaged_historical_model()
    pin = causal._sha(causal._json([release, source_sha]))
    return {"release": release, "policy": pin, "source": source_sha,
            "known_at": known_at, "rows": rows, "quantity_model": "NORMALIZED_HISTORICAL_PPI_VN"}


def deployed_exit_signing_model():
    """Pin the independent EXIT child for the configured read-only provider."""
    if os.getenv("POROTA_RC6_MARKET_PROVIDER") != IOL_PROVIDER:
        return deployed_sale_model()
    pin = os.getenv(IOL_POLICY_PIN, "")
    if not pin:
        return None
    if (os.getenv("DASHBOARD_OPERATION_MODE") != "PRODUCTION_PAPER"
            or os.getenv("EXECUTION_MODE") != "SIMULATION"
            or os.getenv("REAL_ORDER_CAPABILITY") != "BLOCKED"
            or os.getenv("ALLOW_REAL_ORDERS", "").lower() not in {"", "false", "0"}):
        raise ValueError("FI_IOL_EXIT_SIGNER_MODE_BLOCKED")
    return {"release": sale_release_anchor(os.getenv("POROTA_BUILD_SHA"),
                                          os.getenv("POROTA_CANDIDATE_TREE_SHA")),
            "policy": _digest(pin), "quantity_model": "RELEASE_REVIEWED_IOL_REST_VN"}


def native_market_sale_enabled(identity):
    model = deployed_sale_model()
    return identity in model["rows"] if model else bool(os.getenv(POLICY_PIN))


def _historical_market_sale_policy(model, identity, contract, at):
    from rc6_performance.costs import paper_cost_contract

    if (causal.PINNED_RELEASE_SHA256 != model["release"]
            or causal._digest_or_none(causal.PINNED_DECISION_SIGNER_KEY_SHA256) is None):
        raise ValueError("FI_PAPER_EXIT_SIGNER_PENDING")
    if identity not in model["rows"] or aware_datetime(at) < model["known_at"]:
        raise ValueError("FI_MARKET_SALE_POLICY_IDENTITY_UNBOUND")
    terms = model["rows"][identity]
    if contract.key != identity or any(getattr(contract, field) != value for field, value in terms.items()):
        raise ValueError("FI_MARKET_SALE_UNITS_COST_OR_SETTLEMENT_UNVERIFIED")
    costs = paper_cost_contract(identity[1], identity[3], at, symbol=identity[0],
                                market=identity[2], settlement=identity[4])
    if costs.binding_error(at):
        raise ValueError("FI_MARKET_SALE_PAPER_COST_UNVERIFIED")
    row = {"minimum_post_ack_ms": 0, "fee_policy_sha256": costs.diagnostic()["policy_sha256"],
           "economic_policy_authority": "NORMALIZED_HISTORICAL_PPI_PAPER_ASSUMPTIONS",
           "release_sha256": model["release"]}
    return row, costs, model["policy"]


def _reviewed_row(source_bytes, at, valid_until):
    """Bind one released per-variant model to reviewed PPI economics bytes."""
    from bu_instrument_catalog import normalize_record, ppi_full_key_evidence_reason
    from rc6_performance.costs import paper_cost_contract

    source = _json(source_bytes)
    identity = source.get("identity")
    raw = source.get("ppi_identity")
    known_at = source.get("known_at")
    if (source.get("schema") != ECONOMICS_SCHEMA or not isinstance(identity, list)
            or len(identity) != 5 or identity[1] not in SALE_FAMILIES
            or not isinstance(raw, dict) or not aware_datetime(known_at) <= at < valid_until
            or source.get("quantity_unit") != "VN" or source.get("bid_size_unit") != "VN"):
        raise ValueError("FI_MARKET_SALE_PPI_ECONOMICS_UNVERIFIED")
    record = normalize_record(raw, None, known_at, "FI_RELEASE_POLICY_EVIDENCE")
    observed = [record[field] for field in ("ticker", "instrument_type", "market", "currency", "settlement")]
    if observed != identity or ppi_full_key_evidence_reason(record):
        raise ValueError("FI_MARKET_SALE_PPI_VARIANT_OR_TERM_UNVERIFIED")
    _digest(source.get("ppi_capture_sha256"))
    costs = paper_cost_contract(identity[1], identity[3], at, symbol=identity[0],
                                market=identity[2], settlement=identity[4])
    if costs.binding_error(at):
        raise ValueError("FI_MARKET_SALE_PAPER_COST_UNVERIFIED")
    terms = {field: str(decimal_value(source[field], field, positive=True))
             for field in ("cash_multiplier", "minimum_quantity", "quantity_step")}
    if Decimal(terms["minimum_quantity"]) % Decimal(terms["quantity_step"]):
        raise ValueError("FI_MARKET_SALE_PPI_QUANTITY_GRID_INVALID")
    return {"identity": identity, "known_at": aware_datetime(known_at).isoformat(),
            "valid_until": valid_until.isoformat(), "quantity_unit": "VN",
            "fee_currency": identity[3], "settlement_model": "PAPER_CONSERVATIVE_BYMA_SPOT",
            **terms, "fee_policy_sha256": costs.diagnostic()["policy_sha256"],
            "minimum_post_ack_ms": 0, "ppi_economics_source_sha256": causal._sha(source_bytes)}, costs


def build_market_sale_policy(sources, *, release_sha256, at, valid_until):
    """Prepare release policy bytes from reviewed sources, one row per five-key."""
    release = _digest(release_sha256)
    now, until = aware_datetime(at), aware_datetime(valid_until)
    rows = [_reviewed_row(raw, now, until)[0] for raw in sources]
    if not rows or len({tuple(row["identity"]) for row in rows}) != len(rows):
        raise ValueError("FI_MARKET_SALE_POLICY_IDENTITY_UNBOUND")
    return causal._json({"schema": POLICY_SCHEMA, "paper_only": True,
        "release_sha256": release, "review_authority": "RELEASE_REVIEWED_PPI_UNITS_AND_PAPER_COST",
        "instruments": sorted(rows, key=lambda row: row["identity"])}).encode()


def market_sale_policy(store, identity, contract, at, *, policy_bytes=None, provider="PPI"):
    """Require reviewed executable book units for this provider and exact identity."""
    if provider != os.getenv("POROTA_RC6_MARKET_PROVIDER", "PPI"):
        raise ValueError("FI_MARKET_SALE_ACTIVE_PROVIDER_MISMATCH")
    if provider == IOL_PROVIDER:
        if identity[1] not in SALE_FAMILIES:
            raise ValueError("FI_MARKET_SALE_POLICY_IDENTITY_UNBOUND")
        row, costs, pin = iol_reviewed_policy(store, identity, contract, at)
        if (causal.PINNED_RELEASE_SHA256 != row["release_sha256"]
                or causal._digest_or_none(causal.PINNED_DECISION_SIGNER_KEY_SHA256) is None):
            raise ValueError("FI_PAPER_EXIT_SIGNER_PENDING")
        return row, costs, pin
    if provider != "PPI":
        raise ValueError("FI_MARKET_SALE_ACTIVE_PROVIDER_MISMATCH")
    model = deployed_sale_model()
    if model is not None:
        return _historical_market_sale_policy(model, identity, contract, at)
    pin = _digest(os.getenv(POLICY_PIN))
    root = Path(os.getenv("POROTA_FI_EVIDENCE_DIRECTORY",
                          str(Path(store.path).parent / "fixed-income-evidence")))
    if root.is_symlink() or not root.is_dir():
        raise ValueError("FI_MARKET_SALE_POLICY_DIRECTORY_UNAVAILABLE")
    if policy_bytes is None:
        policy_bytes = _read(root, "market-sale-policy.json", pin)
    if not isinstance(policy_bytes, bytes) or causal._sha(policy_bytes) != pin:
        raise ValueError("FI_MARKET_SALE_POLICY_HASH_MISMATCH")
    document = _json(policy_bytes)
    if (document.get("schema") != POLICY_SCHEMA or document.get("paper_only") is not True
            or document.get("review_authority") != "RELEASE_REVIEWED_PPI_UNITS_AND_PAPER_COST"
            or _digest(document.get("release_sha256")) != causal.PINNED_RELEASE_SHA256
            or not isinstance(document.get("instruments"), list)):
        raise ValueError("FI_MARKET_SALE_POLICY_UNVERIFIED")
    at = aware_datetime(at)
    rows = [row for row in document["instruments"]
            if isinstance(row, dict) and row.get("identity") == list(identity)]
    if len(rows) != 1 or identity[1] not in SALE_FAMILIES:
        raise ValueError("FI_MARKET_SALE_POLICY_IDENTITY_UNBOUND")
    row = rows[0]
    source_pin = _digest(row.get("ppi_economics_source_sha256"))
    source = _read(root, f"ppi-economics-{source_pin}.json", source_pin)
    reviewed, costs = _reviewed_row(source, at, aware_datetime(row["valid_until"]))
    if (row != reviewed
            or contract.key != identity or contract.minimum_quantity is None
            or decimal_value(row["cash_multiplier"], "price basis", positive=True) != contract.cash_multiplier
            or decimal_value(row["minimum_quantity"], "VN minimum", positive=True) != contract.minimum_quantity
            or decimal_value(row["quantity_step"], "VN step", positive=True) != contract.quantity_step):
        raise ValueError("FI_MARKET_SALE_UNITS_COST_OR_SETTLEMENT_UNVERIFIED")
    return row, costs, pin


def native_book_fields(receipt, identity):
    """Parse the retained REST book without granting it execution authority."""
    if isinstance(receipt, dict) and receipt.get("source") == IOL_PROVIDER:
        if receipt.get("consumer") != "exit_reader":
            raise ValueError("FI_IOL_BOOK_NOT_EXIT_READER")
        return iol_native_book_fields(receipt, identity)
    from bf_production_paper_observer import _level, _levels

    if (not isinstance(receipt, dict) or receipt.get("schema") != NATIVE_READ_SCHEMA
            or receipt.get("method") != "book" or receipt.get("capture_type") != "REST_BOOK"
            or receipt.get("source") != "PPI" or receipt.get("ppi_five_key") != list(identity)
            or receipt.get("paper_ready") is not False or receipt.get("order_route") != "NOT_PRESENT"
            or len(causal._json(receipt).encode()) > MAX_BOOK_BYTES
            or receipt.get("receipt_sha256") != native_sha(
                {key: value for key, value in receipt.items() if key != "receipt_sha256"})
            or receipt.get("raw_sha256") != native_sha(receipt.get("raw_payload"))):
        raise ValueError("FI_MARKET_SALE_NATIVE_BOOK_INVALID")
    raw = receipt["raw_payload"]
    seen = aware_datetime(receipt["received_at_utc"])
    native = aware_datetime(receipt["native_at_utc"])
    monotonic_ns = receipt.get("receive_monotonic_ns")
    if (not isinstance(raw, dict) or aware_datetime(raw.get("date")) != native
            or native > seen or type(monotonic_ns) is not int or monotonic_ns <= 0):
        raise ValueError("FI_MARKET_SALE_NATIVE_BOOK_CLOCK_INVALID")
    _digest(receipt.get("session_fingerprint"))
    _digest(receipt.get("guard_budget_receipt_sha256"))
    bids, asks = _levels(raw, "bid"), _levels(raw, "ask")
    bid, bid_size = _level(bids[0]) if bids else (Decimal(0), Decimal(0))
    ask, ask_size = _level(asks[0]) if asks else (Decimal(0), Decimal(0))
    if not 0 < bid <= ask or bid_size <= 0 or ask_size <= 0:
        raise ValueError("FI_MARKET_SALE_NATIVE_DEPTH_INVALID")
    return {"book_at": native.isoformat(), "observed_at": seen.isoformat(),
            "bid": bid, "ask": ask, "bid_size": bid_size, "ask_size": ask_size}


def prepare_market_sale(store, position, quote, cause, at):
    """Record the ordinary PAPER decision and A620 freeze in one transaction."""
    from rc6_fixed_income_sale_witness import active_decision, sale_scope

    at = aware_datetime(at)
    identity = tuple(position[key] for key in ("symbol", "asset_class", "market", "currency", "settlement"))
    identity = (identity[0], family_name(identity[1]), *identity[2:])
    if not isinstance(cause, str) or not cause.strip() or len(cause) > 80 or quote.contract is None:
        raise ValueError("FI_MARKET_SALE_INTENT_NOT_VALID")
    if quote.metadata_source == IOL_PROVIDER:
        policy, _, pin = market_sale_policy(store, identity, quote.contract, at, provider=IOL_PROVIDER)
        book = quote.iol_native_rest_book
        fields = iol_quote_book_fields(quote, identity, consumer="exit_reader")
    else:
        policy, _, pin = market_sale_policy(store, identity, quote.contract, at)
        book = quote.ppi_native_rest_book
        if isinstance(book, dict) and book.get("source") == IOL_PROVIDER:
            raise ValueError("FI_MARKET_SALE_PROVIDER_BOOK_MISMATCH")
        fields = native_book_fields(book, identity)
    if (quote.time_error(at) or any(getattr(quote, name) != fields[name]
                                  for name in ("bid", "ask", "bid_size", "ask_size"))
            or aware_datetime(quote.book_at) != aware_datetime(fields["book_at"])
            or aware_datetime(fields["observed_at"]) > aware_datetime(quote.observed_at)):
        raise ValueError("FI_MARKET_SALE_INTENT_BOOK1_MISMATCH")
    quantity = quote.contract.quantity(position["quantity"])
    intent = {"paper_id": position["paper_id"], "cause": cause,
              "identity": list(identity), "maximum_vn": str(quantity), "book1": book,
              "policy_sha256": pin, "minimum_post_ack_ms": policy["minimum_post_ack_ms"]}
    if "release_sha256" in policy:
        intent["release_sha256"] = policy["release_sha256"]
    scope = sale_scope(intent)
    active = active_decision(store, position["paper_id"], scope)
    if active:
        return active
    request = causal._sha(causal._json([position["paper_id"], scope, book["receipt_sha256"]]))
    intent["request_id"] = request
    key = "FI_SELL:" + request
    store.record_decision(key, quote, "SELL", Decimal(0), cause, {
        "family": identity[1], "paper_only": True, "real_routes_used": [],
        "performance_lineage": {"decision_at": at.isoformat()}, "fixed_income_market_sale": intent,
    })
    return active_decision(store, position["paper_id"], scope) or key


def _ack(db_path, decision_key, public_key, release):
    proof = causal.verify_signed_receipt(db_path, decision_key=decision_key,
        trusted_public_key_raw=public_key, trusted_public_key_sha256=causal._sha(public_key),
        trusted_release_sha256=release)
    if proof["state"] != "SIGNED_DECISION_KNOWN_AT_VERIFIED" or "fixed_income_market_sale" not in proof:
        raise ValueError(proof["reason"])
    return proof


def _book2_cutoff(proof):
    intent = proof["fixed_income_market_sale"]
    at = aware_datetime(proof["decision_known_at"])
    ns = proof["decision_known_monotonic_ns"]
    latency = intent["minimum_post_ack_ms"]
    if type(latency) is not int or not 0 <= latency <= 1_000:
        raise ValueError("FI_MARKET_SALE_ACK_LATENCY_INVALID")
    book1 = intent["book1"]
    native_book_fields(book1, intent["identity"])
    if (aware_datetime(book1["received_at_utc"]) >= at or book1["receive_monotonic_ns"] >= ns):
        raise ValueError("IOL_FI_BOOK1_NOT_PRE_COMMIT" if book1["source"] == IOL_PROVIDER
                         else "PPI_PIT_BOOK1_NOT_PRE_COMMIT")
    return at + timedelta(milliseconds=latency), ns + latency * 1_000_000


def _read_first_book2(db_path, intent, cut_at, cut_ns):
    book1 = intent["book1"]
    if book1["source"] == IOL_PROVIDER:
        from rc6_iol_market_gateway import IOLGatewayClient, iol_gateway_socket_path

        client = IOLGatewayClient(socket_path=iol_gateway_socket_path(db_path), timeout_seconds=2)
        client.owner_status()
        return client.first_sale_book2(book1, cutoff_at=cut_at, cutoff_ns=cut_ns)
    from rc6_ppi_gateway_reader_adapter import gateway_socket_path

    client = ReadOnlyUnixFeedClient(socket_path=gateway_socket_path(db_path), timeout_seconds=2)
    status = client.owner_status()
    if status["session_generation"] != intent["book1"]["session_fingerprint"]:
        raise ValueError("PPI_PIT_BOOK2_SESSION_CHANGED")
    return client.read_native("exit_reader", method="book", five_key=intent["identity"],
        book1_receipt_sha256=intent["book1"]["receipt_sha256"], commit_at=cut_at,
        commit_monotonic_ns=cut_ns, require_owner_identity=True)


def _check_book2(proof, book2):
    intent = proof["fixed_income_market_sale"]
    fields = native_book_fields(book2, intent["identity"])
    cut_at, cut_ns = _book2_cutoff(proof)
    if book2["source"] != intent["book1"]["source"]:
        raise ValueError("FI_MARKET_SALE_BOOK2_PROVIDER_MISMATCH")
    if book2["session_fingerprint"] != intent["book1"]["session_fingerprint"]:
        raise ValueError("IOL_FI_BOOK2_SESSION_CHANGED" if book2["source"] == IOL_PROVIDER
                         else "PPI_PIT_BOOK2_SESSION_CHANGED")
    if (aware_datetime(fields["book_at"]) <= cut_at or aware_datetime(fields["observed_at"]) <= cut_at
            or book2["receive_monotonic_ns"] <= cut_ns
            or book2["receipt_sha256"] == intent["book1"]["receipt_sha256"]):
        raise ValueError("FI_MARKET_SALE_BOOK2_NOT_STRICTLY_AFTER_ACK")
    return fields


def attest_market_sale_book2(db_path, *, decision_key, signing_key_path=None, release,
                             exit_signer=None):
    """Existing independent observer, acting through the registered gateway reader."""
    from cryptography.hazmat.primitives import serialization

    if (signing_key_path is None) == (exit_signer is None):
        raise ValueError("FI_MARKET_SALE_WITNESS_SIGNER_UNBOUND")
    signer = exit_signer if exit_signer is not None else causal._private_ed25519_key(Path(signing_key_path))
    public = signer.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                               format=serialization.PublicFormat.Raw)
    proof = _ack(db_path, decision_key, public, release)
    cut_at, cut_ns = _book2_cutoff(proof)
    result = _read_first_book2(db_path, proof["fixed_income_market_sale"], cut_at, cut_ns)
    if result.get("state") != "OK" or result.get("provider_calls_by_rpc") != 0:
        raise ValueError(result.get("state", "NO_AUTHENTIC_NATIVE_RECEIPT"))
    book2 = result.get("receipt")
    _check_book2(proof, book2)
    payload = {"schema": WITNESS_SCHEMA, "decision_key": decision_key,
               "decision_receipt_sha256": proof["receipt_sha256"], "freeze_sha256": proof["freeze_sha256"],
               "release_sha256": release, "book2": book2, "signer_key_id": causal._sha(public)}
    return {"payload": payload, "signature_b64": base64.b64encode(
            signer.sign(causal._json(payload).encode())).decode(),
            "public_key_b64": base64.b64encode(public).decode()}


def verify_market_sale_book2(store, witness, at):
    """Verify the witness, the durable A620 receipt, and exact native clocks."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        payload = witness["payload"]
        public = base64.b64decode(witness["public_key_b64"], validate=True)
        raw = causal._json(payload).encode()
        if (payload["schema"] != WITNESS_SCHEMA or len(raw) > MAX_BOOK_BYTES + 2_000
                or causal._sha(public) != causal.PINNED_DECISION_SIGNER_KEY_SHA256
                or payload["signer_key_id"] != causal.PINNED_DECISION_SIGNER_KEY_SHA256
                or payload["release_sha256"] != causal.PINNED_RELEASE_SHA256):
            raise ValueError("FI_MARKET_SALE_BOOK2_TRUST_UNBOUND")
        Ed25519PublicKey.from_public_bytes(public).verify(
            base64.b64decode(witness["signature_b64"], validate=True), raw)
    except (InvalidSignature, KeyError, TypeError, AttributeError) as exc:
        raise ValueError("FI_MARKET_SALE_BOOK2_SIGNATURE_INVALID") from exc
    proof = _ack(store.path, payload["decision_key"], public, payload["release_sha256"])
    if (payload["decision_receipt_sha256"] != proof["receipt_sha256"]
            or payload["freeze_sha256"] != proof["freeze_sha256"]):
        raise ValueError("FI_MARKET_SALE_BOOK2_ACK_MISMATCH")
    fields = _check_book2(proof, payload["book2"])
    if aware_datetime(fields["observed_at"]) > aware_datetime(at):
        raise ValueError("FI_MARKET_SALE_BOOK2_NOT_KNOWN_AT_FILL")
    return proof["fixed_income_market_sale"], fields, payload
