"""A320: offhost PPI read-only book capture and independently attested PIT/PAPER review.

No import of trading/order/host modules is permitted in this verifier. It
never submits a broker order, mutates History v2 or promotes a READY catalog.
Only an authenticated, contemporary, two-provider capture and a *separately
attested* PAPER decision can yield a positive production-evidence receipt.

Trust boundary: distinct HMAC-SHA256 keys are provisioned only into a future
reviewed main-branch offhost workflow by independent capture/decision owners.
An arbitrary JSON document (even internally consistent) is NOT authority.
Synthetic fixtures can test a candidate route but cannot create real verdicts.
"""

from __future__ import annotations

import argparse
import base64
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time


SCHEMA = "rc6.a320.pit.paper.offhost.v1"
FAMILIES = (
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
    "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
)
MAX_CAPTURE_TARGETS = 10
MAX_CAPTURE_BYTES = 128 * 1024
MAX_BUNDLE_BYTES = 2 * 1024 * 1024
MAX_LEVELS = 20
MAX_QUOTE_AGE_MS = 120000
MAX_CAPTURE_CLOCK_SKEW_MS = 30000
MAX_CROSS_SOURCE_DEVIATION = Decimal("0.10")
CAPTURE_PAUSE_SECONDS = 0.35
SHA = re.compile(r"[a-f0-9]{64}\Z")
SHA_COMMIT = re.compile(r"[a-f0-9]{40}\Z")
SYMBOL = re.compile(r"[A-Z0-9./_-]{1,40}\Z")
SOURCES = frozenset({"PPI", "IOL", "IOL_CONNECTOR", "BYMA", "A3", "A3_CEM",
                     "CAFCI", "CNV"})
PRIMARY_FOR_FAMILY = {
    **{name: frozenset({"PPI"}) for name in FAMILIES},
    "FUTUROS": frozenset({"A3", "A3_CEM", "PPI"}),
    "ETFS": frozenset({"BYMA", "PPI"}),
    "FCI": frozenset({"CAFCI", "CNV", "PPI"}),
    "CAUCIONES": frozenset({"BYMA", "A3", "PPI"}),
}
CONTRACT_DETAIL = {
    "ACCIONES": ("lot_size", "tick_size"),
    "CEDEARS": ("lot_size", "tick_size", "cedear_ratio", "underlying_session_open_utc"),
    "ETFS": ("lot_size", "tick_size", "listing_from", "nav_reference_only"),
    "BONOS": ("nominal_basis", "accrued_interest", "issuer_isin", "native_exit_verified"),
    "LETRAS": ("nominal_basis", "issuer_isin", "fixing_contract", "native_maturity_verified"),
    "OBLIGACIONES": ("nominal_basis", "issuer_isin", "coupon_contract", "native_exit_verified"),
    "OPCIONES": ("strike", "option_kind", "expiry_day", "contract_multiplier", "exercise_risk_verified"),
    "FUTUROS": ("expiry_day", "contract_multiplier", "margin_per_contract",
                "variation_mark_verified"),
    "CAUCIONES": ("term_days", "maturity_day", "day_count_basis", "principal_unit"),
    "FCI": ("fund_id", "fund_class_id", "fund_cutoff_at_utc", "redemption_days",
            "nav_publication_contract"),
}
VOLUME_KIND_BY_FAMILY = {
    "ACCIONES": "QUANTITY", "CEDEARS": "QUANTITY", "ETFS": "QUANTITY",
    "BONOS": "NOMINAL", "LETRAS": "NOMINAL", "OBLIGACIONES": "NOMINAL",
    "OPCIONES": "CONTRACTS", "FUTUROS": "CONTRACTS",
    "CAUCIONES": "MONEY", "FCI": "MONEY",
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value):
    return sha256(_canonical(value)).hexdigest()


def _utc(raw, name):
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("A320_" + name + "_CLOCK_INVALID") from None


def _num(value, name, *, positive=True):
    if isinstance(value, bool) or value is None:
        raise ValueError("A320_" + name + "_MISSING")
    try:
        result = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise ValueError("A320_" + name + "_NONNUMERIC") from None
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError("A320_" + name + "_INVALID")
    return result


def _hash_string(value, name):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("A320_" + name + "_SHA_UNVERIFIED")
    return value


def _date(raw, name):
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        raise ValueError("A320_" + name + "_DATE_INVALID") from None


def _read_json_bounded(path, limit):
    target = Path(path).absolute()
    if target.resolve() != target or not target.is_file():
        raise ValueError("A320_SOURCE_PATH_UNSAFE")
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("A320_SOURCE_PATH_UNSAFE")
    before = sha256(target.read_bytes()).hexdigest()
    source = json.loads(target.read_text(encoding="utf-8"))
    if before != sha256(target.read_bytes()).hexdigest():
        raise ValueError("A320_SOURCE_MUTATED")
    return source, before


def _write_private_once(path, payload):
    target = Path(path).absolute()
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        raise ValueError("A320_PRIVATE_DESTINATION_UNSAFE")
    handle, scratch = tempfile.mkstemp(prefix=".rc6-a320-", dir=target.parent)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "wb") as output:
            output.write(_canonical(payload) + b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(scratch, target)
    finally:
        Path(scratch).unlink(missing_ok=True)


def _sign(body, key, key_id):
    if (not isinstance(key, bytes) or len(key) < 32
            or not isinstance(key_id, str) or not SYMBOL.fullmatch(key_id)):
        raise ValueError("A320_SIGNING_KEY_UNTRUSTED")
    message = dict(body)
    message["attestation"] = {
        "algorithm": "HMAC-SHA256",
        "key_id": key_id,
        "mac": hmac.new(key, _canonical(body), sha256).hexdigest(),
    }
    return message


def _attestation(body, key, key_id):
    if not isinstance(body, dict) or not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("A320_AUTHENTICATED_ATTESTATION_ABSENT")
    signature = body.get("attestation")
    if not isinstance(signature, dict) or signature.get("algorithm") != "HMAC-SHA256":
        raise ValueError("A320_AUTHENTICATED_ATTESTATION_ABSENT")
    if signature.get("key_id") != key_id:
        raise ValueError("A320_ATTESTATION_SIGNER_MISMATCH")
    mac = signature.get("mac")
    if not isinstance(mac, str) or not SHA.fullmatch(mac):
        raise ValueError("A320_ATTESTATION_SIGNATURE_MISSING")
    unsigned = {k: v for k, v in body.items() if k != "attestation"}
    expected = hmac.new(key, _canonical(unsigned), sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("A320_ATTESTATION_SIGNATURE_INVALID")
    return unsigned


def _identity(value, family):
    if not isinstance(value, dict) or value.get("family") != family:
        raise ValueError("A320_FAMILY_IDENTITY_MISMATCH")
    symbol = value.get("symbol")
    kind = value.get("instrument_type")
    if (not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol)
            or not isinstance(kind, str) or not SYMBOL.fullmatch(kind)):
        raise ValueError("A320_SYMBOL_INVALID")
    if (value.get("market") not in {"BYMA", "BCBA", "NYSE", "NASDAQ", "A3", "ROFEX", "CAFCI"}
            or value.get("currency") not in {"ARS", "USD"}
            or not isinstance(value.get("settlement"), str)
            or not value["settlement"].strip()):
        raise ValueError("A320_MONETARY_OR_SETTLEMENT_UNVERIFIED")
    return tuple(value[name] for name in ("symbol", "instrument_type", "market", "currency",
                                          "settlement"))


def _contract(contract, family, identity, economic_day):
    if not isinstance(contract, dict) or _identity(contract, family) != identity:
        raise ValueError("A320_CONTRACT_IDENTITY_UNVERIFIED")
    _hash_string(contract.get("official_source_sha256"), "CONTRACT_SOURCE")
    _hash_string(contract.get("calendar_official_source_sha256"), "CALENDAR_SOURCE")
    if contract.get("price_basis") != "RAW" or contract.get("adjustment_basis") != "RAW_NO_ADJUSTMENT":
        raise ValueError("A320_PRICE_BASIS_UNVERIFIED")
    unit = _num(contract.get("unit_multiplier"), "UNIT_MULTIPLIER")
    if contract.get("volume_kind") != VOLUME_KIND_BY_FAMILY[family]:
        raise ValueError("A320_VOLUME_UNIT_UNVERIFIED")
    if not _date(contract.get("valid_from"), "CONTRACT_FROM") <= economic_day <= _date(
            contract.get("valid_through"), "CONTRACT_THROUGH"):
        raise ValueError("A320_CONTRACT_EFFECTIVITY_MISMATCH")
    missing = [key for key in CONTRACT_DETAIL[family] if contract.get(key) is None]
    if missing:
        raise ValueError("A320_FAMILY_CONTRACT_FIELDS_MISSING")
    if family == "ETFS":
        if identity[2] != "BYMA" or economic_day < _date(contract["listing_from"], "ETF_LISTING"):
            raise ValueError("A320_ETF_VENUE_OR_LISTING_UNVERIFIED")
        if contract["nav_reference_only"] is not True:
            raise ValueError("A320_ETF_NAV_EXECUTION_CONFUSED")
    if family == "CEDEARS" and _num(contract["cedear_ratio"], "CEDEAR_RATIO") <= 0:
        raise ValueError("A320_CEDEAR_RATIO_INVALID")
    if family in {"BONOS", "LETRAS", "OBLIGACIONES"}:
        if _num(contract["nominal_basis"], "NOMINAL_BASIS") != 100:
            raise ValueError("A320_FI_NOMINAL_BASIS_UNVERIFIED")
        if not isinstance(contract["issuer_isin"], str) or len(contract["issuer_isin"]) != 12:
            raise ValueError("A320_FI_ISIN_UNVERIFIED")
        if contract.get("native_exit_verified") is False or contract.get("native_maturity_verified") is False:
            raise ValueError("A320_FI_NATIVE_EXIT_NOT_VERIFIED")
    if family in {"OPCIONES", "FUTUROS"}:
        if _date(contract["expiry_day"], "DERIVATIVE_EXPIRY") < economic_day:
            raise ValueError("A320_DERIVATIVE_EXPIRED")
        _num(contract["contract_multiplier"], "CONTRACT_MULTIPLIER")
    if family == "OPCIONES":
        _num(contract["strike"], "OPTION_STRIKE")
        if contract["option_kind"] not in {"CALL", "PUT"} or contract["exercise_risk_verified"] is not True:
            raise ValueError("A320_OPTION_EXERCISE_UNVERIFIED")
    if family == "FUTUROS":
        _num(contract["margin_per_contract"], "FUTURE_MARGIN")
        if contract["variation_mark_verified"] is not True:
            raise ValueError("A320_FUTURE_MARK_UNVERIFIED")
    if family == "CAUCIONES":
        term = contract["term_days"]
        if type(term) is not int or not 1 <= term <= 120:
            raise ValueError("A320_CAUCION_TERM_UNVERIFIED")
        if _date(contract["maturity_day"], "CAUCION_MATURITY") <= economic_day:
            raise ValueError("A320_CAUCION_MATURITY_INVALID")
        if contract["day_count_basis"] not in {360, 365}:
            raise ValueError("A320_CAUCION_DAYCOUNT_UNVERIFIED")
        _num(contract["principal_unit"], "CAUCION_PRINCIPAL_UNIT")
        if contract.get("quote_unit") != "TNA_PERCENT":
            raise ValueError("A320_CAUCION_QUOTE_UNIT_UNVERIFIED")
    if family == "FCI":
        if any(not isinstance(contract[key], str) or not contract[key].strip()
               for key in ("fund_id", "fund_class_id", "nav_publication_contract")):
            raise ValueError("A320_FCI_CLASS_UNVERIFIED")
        _utc(contract["fund_cutoff_at_utc"], "FCI_CUTOFF")
        if type(contract["redemption_days"]) is not int or contract["redemption_days"] < 0:
            raise ValueError("A320_FCI_REDEMPTION_TERM_INVALID")
    return unit


def capture_ppi_book(targets, fetch, *, now=None, pause=None, signer_key=None,
                     signer_id=None, provenance="AUTHENTIC_READONLY_CAPTURE"):
    """Capture one bounded PPI SDK parsed Book per exact request, no other API.

    The returned raw Book does not contain row-level currency/price basis. The
    request tuple is evidence of intent, never a substitute for an independently
    authenticated contract. Caller must use the reviewed ProductionMarketReader.
    """
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_CAPTURE_TARGETS:
        raise ValueError("A320_CAPTURE_TARGET_BUDGET_INVALID")
    clock = now or (lambda: datetime.now(timezone.utc))
    wait = pause or time.sleep
    records = []
    seen = set()
    for ordinal, target in enumerate(targets):
        if not isinstance(target, dict) or target.get("family") not in FAMILIES:
            raise ValueError("A320_CAPTURE_TARGET_INVALID")
        identity = _identity(target, target["family"])
        if identity in seen:
            raise ValueError("A320_CAPTURE_DUPLICATE_TARGET")
        seen.add(identity)
        if ordinal:
            wait(CAPTURE_PAUSE_SECONDS)
        start = _utc(clock(), "CAPTURE_START")
        # The only permitted provider function is the read-only Book facade.
        fetched = fetch(identity[0], identity[1], identity[4])
        received = _utc(clock(), "FIRST_KNOWN")
        if received < start:
            raise ValueError("A320_CAPTURE_CLOCK_REGRESSION")
        direct_http = (isinstance(fetched, tuple) and len(fetched) == 2
                       and isinstance(fetched[1], bytes))
        raw = fetched[0] if direct_http else fetched
        if not isinstance(raw, dict) or "date" not in raw:
            raise ValueError("A320_PPI_BOOK_SHAPE_INVALID")
        if len(_canonical(raw)) > MAX_CAPTURE_BYTES:
            raise ValueError("A320_PPI_BOOK_BUDGET_EXCEEDED")
        wire = fetched[1] if direct_http else None
        if wire is not None:
            if len(wire) > MAX_CAPTURE_BYTES or json.loads(wire) != raw:
                raise ValueError("A320_PPI_TRANSPORT_RAW_MISMATCH")
        record = {
            "schema": "rc6.a320.provider.capture.v1",
            "source": "PPI", "capture_kind": "PPI_DIRECT_REST_BOOK" if direct_http else "SDK_PARSED_BOOK_UNVERIFIED",
            "provenance": provenance, "identity": target,
            "request": {"Ticker": identity[0], "Type": identity[1],
                        "Settlement": identity[4]},
            "started_at_utc": start.isoformat(),
            "first_known_at_utc": received.isoformat(),
            "provider_at_utc": raw["date"],
            "raw_book": raw, "raw_book_sha256": _sha(raw),
            "raw_http_body_sha256": sha256(wire).hexdigest() if wire is not None else None,
            "raw_http_body_base64": base64.b64encode(wire).decode("ascii") if wire is not None else None,
            "source_url": "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book",
            "row_currency_status": "NOT_IN_PPI_BOOK_RESPONSE",
            "row_basis_status": "NOT_IN_PPI_BOOK_RESPONSE",
            "history_v2_writes": 0, "orders_sent": 0,
        }
        if signer_key is not None and signer_id is not None:
            record = _sign(record, signer_key, signer_id)
        records.append(record)
    return {"schema": "rc6.a320.ppi.book.capture.v1", "record_count": len(records),
            "captures": records, "host_access": False, "real_orders_sent": 0,
            "history_v2_writes": 0}


SOURCE_DOMAINS = {
    "IOL": ("api.invertironline.com",),
    "IOL_CONNECTOR": ("api.invertironline.com",),
    "BYMA": ("byma.com.ar",),
    "A3": ("a3mercados.com.ar",),
    "A3_CEM": ("a3mercados.com.ar",),
    "CAFCI": ("cafci.org.ar",),
    "CNV": ("cnv.gov.ar",),
}


def _source_lineage(source):
    return {"IOL_CONNECTOR": "IOL", "A3_CEM": "A3"}.get(source, source)


def _authenticated(record, key_pair, label):
    if not isinstance(key_pair, tuple) or len(key_pair) != 2:
        raise ValueError("A320_" + label + "_TRUST_ROOT_ABSENT")
    return _attestation(record, key_pair[0], key_pair[1])


def _capture_temporal(record, *, cutoff=None, max_age_ms=MAX_QUOTE_AGE_MS):
    first = _utc(record.get("first_known_at_utc"), "FIRST_KNOWN")
    started = _utc(record.get("started_at_utc"), "CAPTURE_STARTED")
    published = _utc(record.get("provider_at_utc"), "PROVIDER")
    if published > first or started > first:
        raise ValueError("A320_PROVIDER_LOOKAHEAD_OR_UNBOUNDED_CLOCK")
    elapsed = (first - started).total_seconds() * 1000
    if elapsed < 0 or elapsed > MAX_CAPTURE_CLOCK_SKEW_MS:
        raise ValueError("A320_CAPTURE_LATENCY_UNBOUNDED")
    if cutoff is not None:
        if first > cutoff:
            raise ValueError("A320_SOURCE_KNOWN_AFTER_DECISION")
        age = (cutoff - first).total_seconds() * 1000
        if age > max_age_ms:
            raise ValueError("A320_SOURCE_STALE_FOR_DECISION")
    return first, published


def _book_provider_fresh_at(record, asof, family):
    """Receiving an old quote *now* never refreshes its market timestamp.

    FCI prior class NAV is not an intraday executable book: its prior known
    value is checked through class/cutoff and a later separately published NAV.
    """
    if family == "FCI":
        return
    published = _utc(record.get("provider_at_utc"), "QUOTE_PROVIDER")
    age_ms = (asof - published).total_seconds() * 1000
    if not 0 <= age_ms <= MAX_QUOTE_AGE_MS:
        raise ValueError("A320_MARKET_BOOK_PROVIDER_TIMESTAMP_STALE_OR_FUTURE")


def _source_url(record):
    from urllib.parse import urlsplit
    source = record.get("source")
    if source not in SOURCE_DOMAINS:
        raise ValueError("A320_INDEPENDENT_SOURCE_UNSUPPORTED")
    try:
        location = urlsplit(record["source_url"])
        if (location.scheme != "https" or location.username or location.password
                or not location.hostname or location.fragment):
            raise ValueError()
    except (TypeError, KeyError, ValueError):
        raise ValueError("A320_INDEPENDENT_SOURCE_URL_UNVERIFIED") from None
    if not any(location.hostname == domain or location.hostname.endswith("." + domain)
               for domain in SOURCE_DOMAINS[source]):
        raise ValueError("A320_INDEPENDENT_SOURCE_DOMAIN_UNVERIFIED")


def _book_quote(raw):
    if not isinstance(raw, dict):
        raise ValueError("A320_BOOK_RAW_MISSING")
    def level(name, *, minimum):
        values = raw.get(name)
        if not isinstance(values, list) or not values or len(values) > MAX_LEVELS:
            raise ValueError("A320_BOOK_DEPTH_UNVERIFIED")
        options = []
        for part in values:
            if not isinstance(part, dict):
                raise ValueError("A320_BOOK_LEVEL_UNVERIFIED")
            options.append((_num(part.get("price"), "BOOK_PRICE"),
                            _num(part.get("quantity"), "BOOK_QUANTITY")))
        return min(options, key=lambda v: v[0]) if minimum else max(
            options, key=lambda v: v[0])
    bid, bid_qty = level("bids", minimum=False)
    ask, ask_qty = level("offers", minimum=True)
    if bid > ask:
        raise ValueError("A320_BOOK_CROSSED_OR_MISPRICED")
    return {"bid": bid, "ask": ask, "bid_quantity": bid_qty,
            "ask_quantity": ask_qty}


def _normalized_quote(raw, family):
    if not isinstance(raw, dict):
        raise ValueError("A320_OFFICIAL_QUOTE_MISSING")
    if family == "FCI":
        return {"nav": _num(raw.get("nav"), "FCI_PRIOR_NAV")}
    result = {name: _num(raw.get(name), "INDEPENDENT_" + name.upper())
              for name in ("bid", "ask", "bid_quantity", "ask_quantity")}
    if result["bid"] > result["ask"]:
        raise ValueError("A320_INDEPENDENT_BOOK_CROSSED")
    return result


def _verify_primary(record, family, identity, key_pair, decision_at):
    body = _authenticated(record, key_pair, "PRIMARY")
    if _identity(body.get("identity"), family) != identity:
        raise ValueError("A320_PRIMARY_IDENTITY_MISMATCH")
    source = body.get("source")
    if source not in PRIMARY_FOR_FAMILY[family] or body.get("provenance") != "AUTHENTIC_READONLY_CAPTURE":
        raise ValueError("A320_PRIMARY_CAPTURE_ORIGIN_UNVERIFIED")
    _capture_temporal(body, cutoff=decision_at)
    _book_provider_fresh_at(body, decision_at, family)
    if body.get("orders_sent") != 0 or body.get("history_v2_writes") != 0:
        raise ValueError("A320_PRIMARY_NOT_READONLY")
    if source == "PPI":
        if body.get("capture_kind") != "PPI_DIRECT_REST_BOOK":
            raise ValueError("A320_PPI_SOURCE_IS_NOT_LIVE_BOOK")
        if body.get("request") != {"Ticker": identity[0], "Type": identity[1],
                                    "Settlement": identity[4]}:
            raise ValueError("A320_PPI_BOOK_REQUEST_KEY_MISMATCH")
        raw = body.get("raw_book")
        if _hash_string(body.get("raw_book_sha256"), "PPI_BOOK") != _sha(raw):
            raise ValueError("A320_PPI_RAW_BOOK_HASH_MISMATCH")
        try:
            wire = base64.b64decode(body.get("raw_http_body_base64"), validate=True)
            if (len(wire) > MAX_CAPTURE_BYTES
                    or sha256(wire).hexdigest() != _hash_string(
                        body.get("raw_http_body_sha256"), "PPI_HTTP_BODY")
                    or json.loads(wire) != raw):
                raise ValueError()
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ValueError("A320_PPI_WIRE_RAW_HASH_MISMATCH") from None
        if body.get("source_url") != "https://clientapi.portfoliopersonal.com/api/1.0/MarketData/Book":
            raise ValueError("A320_PPI_HTTP_ENDPOINT_UNVERIFIED")
        if _utc(raw.get("date"), "PPI_BOOK_DATE") != _utc(body.get("provider_at_utc"), "PPI_PROVIDER"):
            raise ValueError("A320_PPI_PROVIDER_CLOCK_MISMATCH")
        if body.get("row_currency_status") != "NOT_IN_PPI_BOOK_RESPONSE" or body.get(
                "row_basis_status") != "NOT_IN_PPI_BOOK_RESPONSE":
            raise ValueError("A320_PPI_ROW_METADATA_FABRICATED")
        return source, _book_quote(raw)
    _source_url(body)
    if body.get("capture_kind") != "OFFICIAL_READONLY_CAPTURE" or body.get(
            "upstream_method") != "GET" or body.get("license_state") != "AUTHORIZED":
        raise ValueError("A320_OFFICIAL_PRIMARY_TRANSPORT_UNVERIFIED")
    raw = body.get("raw_payload")
    if _hash_string(body.get("raw_payload_sha256"), "OFFICIAL_PRIMARY") != _sha(raw):
        raise ValueError("A320_OFFICIAL_PRIMARY_RAW_HASH_MISMATCH")
    if not isinstance(raw, dict) or raw.get("identity") != body.get("identity"):
        raise ValueError("A320_OFFICIAL_PRIMARY_PAYLOAD_IDENTITY_MISMATCH")
    return source, _normalized_quote(raw.get("quote"), family)


def _verify_reference(record, family, identity, primary_source, primary_record,
                      key_pair, decision_at):
    body = _authenticated(record, key_pair, "REFERENCE")
    if _identity(body.get("identity"), family) != identity:
        raise ValueError("A320_REFERENCE_IDENTITY_MISMATCH")
    if (_source_lineage(body.get("source")) == _source_lineage(primary_source)
            or body.get("source") == "PPI"):
        raise ValueError("A320_REFERENCE_NOT_INDEPENDENT_PROVIDER")
    _source_url(body)
    if (body.get("capture_kind") != "INDEPENDENT_OFFICIAL_READONLY_CAPTURE"
            or body.get("upstream_method") != "GET"
            or body.get("license_state") != "AUTHORIZED"
            or body.get("provenance") != "AUTHENTIC_READONLY_CAPTURE"
            or body.get("sensor_id") == primary_record.get("sensor_id")
            or not body.get("sensor_id")
            or body.get("history_v2_writes") != 0 or body.get("orders_sent") != 0):
        raise ValueError("A320_REFERENCE_CAPTURE_INDEPENDENCE_UNVERIFIED")
    _capture_temporal(body, cutoff=decision_at)
    _book_provider_fresh_at(body, decision_at, family)
    raw = body.get("raw_payload")
    if _hash_string(body.get("raw_payload_sha256"), "REFERENCE_RAW") != _sha(raw):
        raise ValueError("A320_REFERENCE_RAW_HASH_MISMATCH")
    if not isinstance(raw, dict) or raw.get("identity") != body.get("identity"):
        raise ValueError("A320_REFERENCE_RAW_IDENTITY_MISMATCH")
    official_document = raw.get("official_contract_document")
    calendar_document = raw.get("official_calendar_document")
    contract = raw.get("contract")
    if (not isinstance(official_document, (dict, str))
            or not isinstance(calendar_document, (dict, str))
            or not isinstance(contract, dict)):
        raise ValueError("A320_OFFICIAL_CONTRACT_DOCUMENT_ABSENT")
    if _hash_string(contract.get("official_source_sha256"), "CONTRACT") != _sha(official_document):
        raise ValueError("A320_CONTRACT_DOCUMENT_HASH_MISMATCH")
    if (_hash_string(contract.get("calendar_official_source_sha256"), "CALENDAR")
            != _sha(calendar_document)):
        raise ValueError("A320_OFFICIAL_CALENDAR_DOCUMENT_HASH_MISMATCH")
    return body.get("source"), _normalized_quote(raw.get("quote"), family), contract


def _calendar(contract, decision_at, provider_at, family):
    effective = _date(contract.get("calendar_valid_from"), "CALENDAR_FROM")
    end = _date(contract.get("calendar_valid_through"), "CALENDAR_THROUGH")
    if not effective <= decision_at.date() <= end:
        raise ValueError("A320_WRONG_EFFECTIVE_SESSION_CALENDAR")
    if _utc(contract.get("calendar_first_known_at_utc"), "CALENDAR_FIRST_KNOWN") > decision_at:
        raise ValueError("A320_FUTURE_CALENDAR_LOOKAHEAD")
    if family == "FCI":
        return
    opened = _utc(contract.get("session_open_utc"), "SESSION_OPEN")
    closed = _utc(contract.get("session_close_utc"), "SESSION_CLOSE")
    if opened.date() != decision_at.date() or not opened <= provider_at <= decision_at <= closed:
        raise ValueError("A320_OUTSIDE_EFFECTIVE_TRADING_SESSION")


def _same_price_basis(primary_quote, reference_quote, family):
    names = ("nav",) if family == "FCI" else ("bid", "ask")
    for key in names:
        lhs = primary_quote[key]
        rhs = reference_quote[key]
        if abs(lhs - rhs) / max(lhs, rhs) > MAX_CROSS_SOURCE_DEVIATION:
            raise ValueError("A320_INDEPENDENT_PRICE_DISAGREEMENT")


def _verify_fee(record, family, identity, fee_key_pair, decision_at):
    body = _authenticated(record, fee_key_pair, "FEE")
    if (_identity(body.get("identity"), family) != identity
            or body.get("account_rate_status") != "ACCOUNT_EXACT_VERIFIED"
            or body.get("fee_source_kind") != "BROKER_ACCOUNT_CONTRACT_READONLY"
            or body.get("orders_sent") != 0
            or _utc(body.get("first_known_at_utc"), "FEE_FIRST_KNOWN") > decision_at
            or not _date(body.get("valid_from"), "FEE_VALID_FROM") <= decision_at.date()
            <= _date(body.get("valid_through"), "FEE_VALID_THROUGH")):
        raise ValueError("A320_EXACT_FEES_UNVERIFIED")
    _hash_string(body.get("broker_fee_contract_sha256"), "BROKER_FEE_CONTRACT")
    required_method = ("CAUCION_ANNUALIZED_COMMISSION" if family == "CAUCIONES"
                       else "FCI_SUBSCRIPTION" if family == "FCI" else "BOOK_NOTIONAL")
    if body.get("calculation_method") != required_method:
        raise ValueError("A320_FEE_CALCULATION_BASIS_UNVERIFIED")
    values = {}
    for name in ("commission_pct", "vat_on_commission_pct", "market_rights_pct",
                 "min_commission", "other_charges_amount"):
        values[name] = _num(body.get(name), "FEE_" + name.upper(), positive=False)
    if values["commission_pct"] > Decimal("0.1") or values[
            "vat_on_commission_pct"] > Decimal("0.5") or values[
            "market_rights_pct"] > Decimal("0.1"):
        raise ValueError("A320_FEE_RATE_OUT_OF_BOUND")
    return values


def _fees(gross, schedule, *, commission_time_factor=Decimal(1)):
    commission = max(gross * commission_time_factor * schedule["commission_pct"],
                     schedule["min_commission"])
    vat = commission * schedule["vat_on_commission_pct"]
    rights = gross * schedule["market_rights_pct"]
    return commission + vat + rights + schedule["other_charges_amount"]


def _verify_decision(record, family, identity, role_key, first_known, primary, reference):
    body = _authenticated(record, role_key, "DECISION")
    if (_identity(body.get("identity"), family) != identity
            or body.get("intent") != "PAPER_ONLY"
            or body.get("orders_sent") != 0
            or body.get("real_order_id") is not None
            or body.get("inputs_sha256") != {"primary": _sha(primary),
                                               "reference": _sha(reference)}):
        raise ValueError("A320_PAPER_DECISION_OR_INPUTS_UNVERIFIED")
    decision_at = _utc(body.get("decision_at_utc"), "DECISION")
    if (decision_at < first_known
            or (decision_at - first_known).total_seconds() * 1000 > MAX_QUOTE_AGE_MS
            or _utc(body.get("features_last_known_at_utc"), "DECISION_FEATURES") > decision_at):
        raise ValueError("A320_PAPER_DECISION_LOOKAHEAD_OR_STALE")
    qty = _num(body.get("quantity"), "PAPER_QUANTITY")
    if body.get("side") not in ({"PLACE"} if family == "CAUCIONES" else {"SUBSCRIBE"}
                                 if family == "FCI" else {"BUY", "SELL"}):
        raise ValueError("A320_PAPER_SIDE_UNSUPPORTED")
    _num(body.get("cash_available"), "PAPER_CASH_AVAILABLE", positive=False)
    _num(body.get("margin_available"), "PAPER_MARGIN_AVAILABLE", positive=False)
    if body.get("risk_approval") != "SIGNED_PAPER_ONLY_LIMITS":
        raise ValueError("A320_PAPER_RISK_SIGNOFF_ABSENT")
    if body["side"] == "SELL" and _num(body.get("position_available"),
                                        "PAPER_POSITION_AVAILABLE", positive=False) < qty:
        raise ValueError("A320_PAPER_SELL_WITHOUT_VERIFIED_INVENTORY")
    return body, decision_at, qty


def _fci_post_allocation(record, family, identity, contract, key_pair, decision_at):
    if family != "FCI":
        raise ValueError("A320_POST_ALLOCATION_ONLY_FCI")
    body = _authenticated(record, key_pair, "ALLOCATION")
    if (_identity(body.get("identity"), family) != identity
            or body.get("source") not in {"CAFCI", "CNV"}
            or body.get("capture_kind") != "INDEPENDENT_OFFICIAL_NAV_PUBLICATION"
            or body.get("upstream_method") != "GET"
            or body.get("license_state") != "AUTHORIZED"
            or body.get("orders_sent") != 0):
        raise ValueError("A320_FCI_POST_ALLOCATION_PROVENANCE_UNVERIFIED")
    _source_url(body)
    first, published = _capture_temporal(body)
    cutoff = _utc(contract["fund_cutoff_at_utc"], "FCI_CUTOFF")
    if not (decision_at < cutoff < published <= first):
        raise ValueError("A320_FCI_NAV_PUBLICATION_LOOKAHEAD")
    raw = body.get("raw_payload")
    if (_hash_string(body.get("raw_payload_sha256"), "ALLOCATION_PAYLOAD") != _sha(raw)
            or not isinstance(raw, dict) or raw.get("identity") != body["identity"]
            or raw.get("fund_class_id") != contract["fund_class_id"]):
        raise ValueError("A320_FCI_ALLOCATION_CLASS_OR_HASH_MISMATCH")
    return _num(raw.get("nav"), "FCI_ALLOCATION_NAV"), first


def _simulate_paper(family, primary_quote, contract, qty, decision, schedule,
                    allocation=None):
    unit = _num(contract.get("unit_multiplier"), "PAPER_UNIT_MULTIPLIER")
    side = decision["side"]
    if family == "FCI":
        if allocation is None:
            raise ValueError("A320_FCI_ACTUAL_NAV_PUBLICATION_REQUIRED")
        gross = qty
        charge = _fees(gross, schedule)
        if charge >= gross:
            raise ValueError("A320_FCI_SUBSCRIPTION_FEES_EXCEED_CASH")
        paper_units = (gross - charge) / (allocation * unit)
        if Decimal(str(decision["cash_available"])) < gross:
            raise ValueError("A320_PAPER_INSUFFICIENT_CASH")
        return {"entry_convention": "FCI_POST_CUTOFF_PUBLISHED_NAV",
                "value": str(gross), "verified_costs": str(charge),
                "illustrative_units": str(paper_units),
                "cash_required": str(gross), "no_pre_cutoff_nav_lookahead": True}
    if family == "CAUCIONES":
        if contract.get("quote_unit") != "TNA_PERCENT" or contract.get(
                "collateral_regime_verified") is not True or contract.get(
                "counterparty_verified") is not True:
            raise ValueError("A320_CAUCION_TNA_OR_COLLATERAL_UNVERIFIED")
        principal = qty * _num(contract["principal_unit"], "CAUCION_PRINCIPAL")
        rate = primary_quote["bid"] / 100
        term = Decimal(contract["term_days"])
        interest = principal * rate * term / Decimal(contract["day_count_basis"])
        charge = _fees(principal, schedule,
                       commission_time_factor=term / Decimal(contract["day_count_basis"]))
        required = principal + charge
        if qty > primary_quote["bid_quantity"] or Decimal(str(decision[
                "cash_available"])) < required:
            raise ValueError("A320_CAUCION_BOOK_DEPTH_OR_CASH_INSUFFICIENT")
        return {"entry_convention": "CAUCION_TNA_BID_PLACE_PAPER",
                "principal": str(principal), "interest_if_held_to_maturity": str(interest),
                "verified_costs": str(charge), "cash_required": str(required)}
    best = primary_quote["ask"] if side == "BUY" else primary_quote["bid"]
    depth = primary_quote["ask_quantity"] if side == "BUY" else primary_quote["bid_quantity"]
    if qty > depth:
        raise ValueError("A320_PAPER_BOOK_DEPTH_INSUFFICIENT")
    factor = Decimal("0.01") if family in {"BONOS", "LETRAS", "OBLIGACIONES"} else Decimal(1)
    if family in {"OPCIONES", "FUTUROS"}:
        factor *= _num(contract["contract_multiplier"], "DERIVATIVE_CONTRACT_MULTIPLIER")
    gross = qty * unit * best * factor
    if family in {"BONOS", "LETRAS", "OBLIGACIONES"}:
        gross += qty * _num(contract.get("accrued_interest", "0"), "ACCRUED_INTEREST",
                            positive=False)
    charge = _fees(gross, schedule)
    required = gross + charge if side == "BUY" else charge
    if family == "FUTUROS":
        required = qty * _num(contract["margin_per_contract"], "FUTURES_MARGIN") + charge
        if Decimal(str(decision["margin_available"])) < required:
            raise ValueError("A320_FUTURE_MARGIN_INSUFFICIENT")
    elif Decimal(str(decision["cash_available"])) < required:
        raise ValueError("A320_PAPER_INSUFFICIENT_CASH")
    return {"entry_convention": "BEST_DISPLAYED_ASK" if side == "BUY" else "BEST_DISPLAYED_BID",
            "best_displayed_price": str(best), "gross_notional": str(gross),
            "verified_costs": str(charge), "cash_or_margin_required": str(required),
            "entry_simulation_only": True,
            "mark_to_market": "NOT_EVALUATED_WITHOUT_POST_DECISION_EXIT"}


def _one_case(case, keys):
    family = case.get("family")
    if family not in FAMILIES:
        raise ValueError("A320_FAMILY_UNSUPPORTED")
    identity = _identity(case.get("identity"), family)
    primary = case.get("primary_capture")
    reference = case.get("independent_capture")
    if not isinstance(primary, dict) or not isinstance(reference, dict):
        raise ValueError("A320_TWO_SOURCE_RAW_RECEIPTS_REQUIRED")
    provider_keys = keys.get("providers") or {}
    p_role = provider_keys.get(_source_lineage(primary.get("source")))
    r_role = provider_keys.get(_source_lineage(reference.get("source")))
    if not p_role or not r_role or p_role[0] == r_role[0] or p_role[1] == r_role[1]:
        raise ValueError("A320_PROVIDER_TRUST_ROOTS_NOT_INDEPENDENT")
    p_body = _authenticated(primary, p_role, "PRIMARY")
    r_body = _authenticated(reference, r_role, "REFERENCE")
    first_p = _utc(p_body.get("first_known_at_utc"), "PRIMARY_FIRST_KNOWN")
    first_r = _utc(r_body.get("first_known_at_utc"), "REFERENCE_FIRST_KNOWN")
    evaluated_at = max(first_p, first_r)
    source, p_quote = _verify_primary(primary, family, identity, p_role, evaluated_at)
    independent, r_quote, contract = _verify_reference(
        reference, family, identity, source, p_body, r_role, evaluated_at)
    day = evaluated_at.date()
    _contract(contract, family, identity, day)
    _calendar(contract, evaluated_at, _utc(p_body["provider_at_utc"], "QUOTE_PROVIDER"), family)
    _same_price_basis(p_quote, r_quote, family)
    source_result = {"family": family, "identity": list(identity),
                     "status": "SOURCE_PIT_CANDIDATE", "source": source,
                     "independent_source": independent,
                     "first_known_at_utc": evaluated_at.isoformat(),
                     "primary_receipt_sha256": _sha(primary),
                     "independent_receipt_sha256": _sha(reference),
                     "contract_document_sha256": contract["official_source_sha256"]}
    try:
        d_role = keys.get("decision")
        if (not d_role or d_role[0] in {p_role[0], r_role[0]}
                or d_role[1] in {p_role[1], r_role[1]}):
            raise ValueError("A320_DECISION_TRUST_ROOT_NOT_INDEPENDENT")
        decision, decision_at, qty = _verify_decision(
            case.get("paper_decision"), family, identity, keys.get("decision"),
            evaluated_at, primary, reference)
        _book_provider_fresh_at(p_body, decision_at, family)
        _book_provider_fresh_at(r_body, decision_at, family)
        _calendar(contract, decision_at, _utc(p_body["provider_at_utc"], "QUOTE_PROVIDER"), family)
        fee_role = keys.get("fees")
        if (not fee_role or fee_role[0] in {p_role[0], r_role[0], keys["decision"][0]}
                or fee_role[1] in {p_role[1], r_role[1], keys["decision"][1]}):
            raise ValueError("A320_FEE_ROOT_NOT_INDEPENDENT")
        schedule = _verify_fee(case.get("fee_receipt"), family, identity, fee_role,
                               decision_at)
        allocation = None
        allocation_first = None
        if family == "FCI":
            allocation_receipt = case.get("post_cutoff_nav")
            a_source = allocation_receipt.get("source") if isinstance(
                allocation_receipt, dict) else None
            a_role = provider_keys.get(_source_lineage(a_source))
            allocation, allocation_first = _fci_post_allocation(
                allocation_receipt, family, identity, contract, a_role,
                decision_at)
        economics = _simulate_paper(family, p_quote, contract, qty, decision,
                                    schedule, allocation)
        paper = {"status": "PAPER_SIM_CANDIDATE", "decision_at_utc": decision_at.isoformat(),
                 "decision_receipt_sha256": _sha(case["paper_decision"]),
                 "fee_receipt_sha256": _sha(case["fee_receipt"]),
                 "side": decision["side"], "quantity": str(qty),
                 "economics": economics, "simulation_only": True,
                 "real_order_id": None, "real_orders_sent": 0,
                 "paper_fill_is_not_a_real_fill": True}
        if allocation_first is not None:
            paper["post_cutoff_nav_first_known_at_utc"] = allocation_first.isoformat()
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        paper = {"status": "NOT_VERIFIED", "reason": str(error),
                 "real_orders_sent": 0}
    return source_result, paper


def review_bundle(bundle, keys=None, *, evidence_mode="OFFHOST_SYNTHETIC",
                  trusted_main_run=False):
    """Each family's denominators, fail-closed source/PAPER independently.

    Synthetic unit fixtures may prove contract paths, but real `*_VERIFIED`
    statuses require signed authentic captures AND the approved main runner.
    """
    if evidence_mode not in {"OFFHOST_SYNTHETIC", "AUTHENTIC_OFFHOST_EVIDENCE"}:
        raise ValueError("A320_EVIDENCE_MODE_UNSUPPORTED")
    if evidence_mode == "AUTHENTIC_OFFHOST_EVIDENCE" and not trusted_main_run:
        raise ValueError("A320_PRODUCTION_EVIDENCE_REQUIRES_PROTECTED_MAIN_WORKFLOW")
    if not isinstance(bundle, dict) or bundle.get("schema") != SCHEMA:
        raise ValueError("A320_INPUT_SCHEMA_MISMATCH")
    if evidence_mode == "AUTHENTIC_OFFHOST_EVIDENCE":
        _main_only("A320_PIT_PAPER_VERIFY_ENABLED")
        if b"OFFHOST_SYNTHETIC" in _canonical(bundle):
            raise ValueError("A320_SYNTHETIC_SOURCE_CANNOT_BECOME_PRODUCTION_EVIDENCE")
    cases = bundle.get("cases")
    if not isinstance(cases, list) or len(cases) > 100:
        raise ValueError("A320_INPUT_CASE_BUDGET_EXCEEDED")
    if any(not isinstance(case, dict) or case.get("family") not in FAMILIES
           for case in cases):
        raise ValueError("A320_UNASSIGNED_OR_INVALID_FAMILY_CASE")
    keys = keys or {}
    family_results = {}
    # This first version proves fail-closed contract paths and can authenticate
    # a PPI raw Book captured by our read-only runner. HMAC verification here
    # is NOT sufficient to establish external independence: the same verifier
    # necessarily holds each symmetric secret. Until reviewed external sensors
    # supply independently timestamped Ed25519 signatures with private keys
    # kept *off* the verification runner, no production row may be promoted.
    sensor_public_verifier_ready = False
    for family in FAMILIES:
        entries = [case for case in cases if isinstance(case, dict)
                   and case.get("family") == family]
        source_candidates = paper_candidates = 0
        duplicate_case_count = invalid_case_count = 0
        reasons = []
        receipts = []
        seen_source_pairs = set()
        seen_paper_decisions = set()
        seen_exact_cases = set()
        for case in entries:
            try:
                if evidence_mode == "AUTHENTIC_OFFHOST_EVIDENCE" and not sensor_public_verifier_ready:
                    raise ValueError("A320_INDEPENDENT_SENSOR_PUBLIC_SIGNATURE_VERIFICATION_REQUIRED")
                source, paper = _one_case(case, keys)
                source_pair = (source["primary_receipt_sha256"],
                               source["independent_receipt_sha256"])
                paper_sha = paper.get("decision_receipt_sha256")
                exact_case_id = (source_pair, _sha(case.get("paper_decision")))
                if exact_case_id in seen_exact_cases:
                    duplicate_case_count += 1
                    reasons.append("A320_EXACT_CASE_DUPLICATE_NO_NEW_EVIDENCE")
                seen_exact_cases.add(exact_case_id)
                if source_pair in seen_source_pairs:
                    source["status"] = "DUPLICATE_SOURCE_RECEIPT_NO_NEW_EVIDENCE"
                    reasons.append("A320_DUPLICATE_SOURCE_RECEIPT_PAIR")
                else:
                    seen_source_pairs.add(source_pair)
                    source_candidates += 1
                if paper["status"] == "PAPER_SIM_CANDIDATE":
                    if paper_sha in seen_paper_decisions:
                        paper["status"] = "DUPLICATE_PAPER_DECISION_NO_NEW_EVIDENCE"
                        reasons.append("A320_DUPLICATE_SIGNED_PAPER_DECISION")
                    else:
                        seen_paper_decisions.add(paper_sha)
                        paper_candidates += 1
                if paper["status"] != "PAPER_SIM_CANDIDATE":
                    if "reason" in paper:
                        reasons.append(paper["reason"])
                receipts.append({"source": source, "paper": paper})
            except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as error:
                invalid_case_count += 1
                reasons.append(str(error))
        approved = (evidence_mode == "AUTHENTIC_OFFHOST_EVIDENCE"
                    and trusted_main_run and sensor_public_verifier_ready)
        family_results[family] = {
            "case_count": len(entries),
            "unique_source_receipt_pairs": len(seen_source_pairs),
            "unique_signed_paper_decisions": len(seen_paper_decisions),
            "duplicate_case_count": duplicate_case_count,
            "invalid_case_count": invalid_case_count,
            "source_pit_rows_verified": source_candidates if approved else 0,
            "paper_sim_rows_verified": paper_candidates if approved else 0,
            "synthetic_source_contract_candidates": source_candidates if not approved else 0,
            "synthetic_paper_contract_candidates": paper_candidates if not approved else 0,
            "source_status": "SOURCE_PIT_VERIFIED" if approved and source_candidates else "NO_VERIFICADO",
            "paper_status": "PAPER_SIM_VERIFIED" if approved and paper_candidates else "NO_VERIFICADO",
            "rejection_reasons": sorted(set(reasons)), "receipt_evaluations": receipts,
        }
    return {"schema": "rc6.a320.pit.paper.report.v1", "evidence_mode": evidence_mode,
            "trusted_main_run": trusted_main_run, "input_case_count": len(cases),
            "independent_public_sensor_verifier": "NOT_INTEGRATED_FAIL_CLOSED",
            "source_families_verified": sum(bool(value["source_pit_rows_verified"])
                                            for value in family_results.values()),
            "paper_families_verified": sum(bool(value["paper_sim_rows_verified"])
                                           for value in family_results.values()),
            "families": family_results, "real_orders_sent": 0,
            "history_v2_writes": 0, "predeploy_or_deploy_dispatches": 0}


def _main_only(name):
    if (os.environ.get("GITHUB_REPOSITORY") != "mbalbo2023/Porota-trading"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}
            or os.environ.get(name) != "YES"):
        raise ValueError("A320_PROTECTED_MAIN_EXECUTION_AND_OPERATOR_OPT_IN_REQUIRED")


def _key_environment(name):
    value = os.environ.get(name)
    if not value or not re.fullmatch(r"[a-fA-F0-9]{64,}", value) or len(value) % 2:
        raise ValueError("A320_ROLE_SECRET_MISSING_OR_INVALID:" + name)
    secret = bytes.fromhex(value)
    if len(secret) < 32:
        raise ValueError("A320_ROLE_SECRET_TOO_SHORT")
    return secret


def _keys_from_environment():
    names = {"PPI": "A320_PPI", "IOL": "A320_IOL", "BYMA": "A320_BYMA",
             "A3": "A320_A3", "CAFCI": "A320_CAFCI", "CNV": "A320_CNV",
             "decision": "A320_DECISION", "fees": "A320_FEES"}
    roles = {}
    for role, prefix in names.items():
        key_id = os.environ.get(prefix + "_KEY_ID")
        secret_present = bool(os.environ.get(prefix + "_HMAC_KEY_HEX"))
        if not key_id and not secret_present:
            continue
        if not key_id or not SYMBOL.fullmatch(key_id):
            raise ValueError("A320_PROTECTED_ROLE_KEY_ID_MISSING")
        roles[role] = (_key_environment(prefix + "_HMAC_KEY_HEX"), key_id)
    if (len({key[0] for key in roles.values()}) != len(roles)
            or len({key[1] for key in roles.values()}) != len(roles)):
        raise ValueError("A320_AUTHORITY_ROLES_ARE_NOT_INDEPENDENT")
    return {"providers": {key: value for key, value in roles.items()
                          if key in {"PPI", "IOL", "BYMA", "A3", "CAFCI", "CNV"}},
            "decision": roles.get("decision"), "fees": roles.get("fees")}


class _PpiReadonlyRestBook:
    """Tightly scoped official PPI REST transport; never imports trading SDK.

    POST is allowed only for `/Account/LoginApi`, GET only for
    `/MarketData/Book`. Header credentials are never logged/serialized.
    Redirects and proxies are disallowed. No Account balance or Order routes.
    """
    URL = "https://clientapi.portfoliopersonal.com/api/1.0"

    def __init__(self, authorized_client, client_key, api_key, api_secret):
        from urllib.request import HTTPSHandler, ProxyHandler, build_opener
        self._public_headers = {"AuthorizedClient": authorized_client,
                                "ClientKey": client_key}
        self._login_headers = {"ApiKey": api_key, "ApiSecret": api_secret}
        from urllib.request import HTTPRedirectHandler
        class BlockRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise ValueError("A320_PPI_HTTP_REDIRECT_DENIED")
        self._opener = build_opener(ProxyHandler({}), HTTPSHandler(), BlockRedirect())
        self._token = None

    def _fetch(self, method, path, query=None):
        from urllib.parse import urlencode
        from urllib.request import Request
        if ((method, path) not in {("POST", "/Account/LoginApi"),
                                   ("GET", "/MarketData/Book")}
                or method == "GET" and set(query or {}) != {"Ticker", "Type", "Settlement"}
                or method == "POST" and query):
            raise ValueError("A320_PPI_HTTP_ROUTE_DENIED")
        url = self.URL + path + ("?" + urlencode(query) if query else "")
        headers = {**self._public_headers, "Accept": "application/json"}
        if method == "POST":
            headers.update(self._login_headers)
        else:
            if not self._token:
                raise ValueError("A320_PPI_LOGIN_REQUIRED")
            headers["Authorization"] = "Bearer " + self._token
        request = Request(url, headers=headers, method=method,
                          data=b"" if method == "POST" else None)
        try:
            with self._opener.open(request, timeout=8) as response:
                if response.status != 200:
                    raise ValueError("A320_PPI_HTTP_STATUS_NOT_OK")
                wire = response.read(MAX_CAPTURE_BYTES + 1)
        except Exception as exc:
            # Never leak credential-bearing request or response details.
            if isinstance(exc, ValueError) and str(exc).startswith("A320_"):
                raise
            raise ValueError("A320_PPI_READONLY_NETWORK_OR_AUTH_FAILURE") from None
        if len(wire) > MAX_CAPTURE_BYTES:
            raise ValueError("A320_PPI_RESPONSE_TOO_LARGE")
        try:
            return json.loads(wire), wire
        except (TypeError, ValueError):
            raise ValueError("A320_PPI_RESPONSE_NOT_JSON") from None

    def login(self):
        data, _ = self._fetch("POST", "/Account/LoginApi")
        if isinstance(data, list) and len(data) == 1:
            data = data[0]
        if not isinstance(data, dict):
            raise ValueError("A320_PPI_LOGIN_SHAPE_INVALID")
        token = data.get("accessToken")
        if not isinstance(token, str) or len(token) < 10:
            raise ValueError("A320_PPI_LOGIN_TOKEN_MISSING")
        self._token = token

    def book(self, ticker, instrument_type, settlement):
        if any(not isinstance(v, str) or not SYMBOL.fullmatch(v)
               for v in (ticker, instrument_type, settlement)):
            raise ValueError("A320_PPI_EXACT_BOOK_KEY_INVALID")
        result, raw = self._fetch("GET", "/MarketData/Book",
                                  {"Ticker": ticker, "Type": instrument_type,
                                   "Settlement": settlement})
        if not isinstance(result, dict):
            raise ValueError("A320_PPI_BOOK_SHAPE_INVALID")
        return result, raw

    def close(self):
        self._token = None
        self._login_headers.clear()


def main(argv=None):
    parser = argparse.ArgumentParser(description="A320 offhost PIT/PAPER verifier (no orders)")
    parser.add_argument("operation", choices=("capture-ppi-book-readonly", "verify-bundle"))
    parser.add_argument("--targets", help="Private JSON list of exact PPI Book requests")
    parser.add_argument("--bundle", help="Private JSON attestation bundle")
    parser.add_argument("--output", required=True, help="New private 0600 evidence file")
    parser.add_argument("--mode", choices=("OFFHOST_SYNTHETIC", "AUTHENTIC_OFFHOST_EVIDENCE"),
                        default="OFFHOST_SYNTHETIC")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.operation == "capture-ppi-book-readonly":
            _main_only("A320_PPI_READONLY_CAPTURE_ENABLED")
            if os.environ.get("A320_PPI_MARKET_DATA_ENTITLEMENT") != "AUTHORIZED":
                raise ValueError("A320_PPI_BOOK_ENTITLEMENT_UNVERIFIED")
            if not args.targets or args.bundle:
                raise ValueError("A320_CAPTURE_TARGET_ARGUMENTS_INVALID")
            targets, _ = _read_json_bounded(args.targets, MAX_BUNDLE_BYTES)
            names = ("PPI_AUTHORIZED_CLIENT", "PPI_CLIENT_KEY", "PPI_API_KEY",
                     "PPI_API_SECRET")
            credentials = [os.environ.get(name) for name in names]
            if not all(credentials):
                raise ValueError("A320_PPI_READONLY_CREDENTIALS_MISSING")
            caller = _PpiReadonlyRestBook(*credentials)
            try:
                caller.login()
                receipt = capture_ppi_book(
                    targets, caller.book,
                    signer_key=_key_environment("A320_PPI_HMAC_KEY_HEX"),
                    signer_id=os.environ.get("A320_PPI_KEY_ID"))
            finally:
                caller.close()
            _write_private_once(args.output, receipt)
            print(json.dumps({"operation": args.operation,
                              "count": receipt["record_count"], "real_orders_sent": 0,
                              "evidence_sha256": _sha(receipt)}, sort_keys=True))
        else:
            if not args.bundle or args.targets:
                raise ValueError("A320_VERIFY_ARGUMENTS_INVALID")
            trusted = args.mode == "AUTHENTIC_OFFHOST_EVIDENCE"
            if trusted:
                _main_only("A320_PIT_PAPER_VERIFY_ENABLED")
            data, input_sha = _read_json_bounded(args.bundle, MAX_BUNDLE_BYTES)
            receipt = review_bundle(data, _keys_from_environment(),
                                    evidence_mode=args.mode, trusted_main_run=trusted)
            receipt["input_sha256"] = input_sha
            _write_private_once(args.output, receipt)
            print(json.dumps({"operation": args.operation,
                              "source_families_verified": receipt["source_families_verified"],
                              "paper_families_verified": receipt["paper_families_verified"],
                              "report_sha256": _sha(receipt), "real_orders_sent": 0},
                             sort_keys=True))
        return 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        # Sanitized codes only: secrets and raw market data never enter logs.
        message = str(error)
        print(message if message.startswith("A320_") else "A320_FAIL_CLOSED", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
