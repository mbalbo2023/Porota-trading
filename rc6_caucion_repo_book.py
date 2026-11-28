"""PAPER intake for a colocadora book attested by an independent publisher.

The IOL reference cache and HTTP envelope have no book clock. The publisher
must have independent access to a native book and exact costs. This consumer
only reads a scoped attestation; it has no producer, API client or order route.
"""
from __future__ import annotations

import os
import re
import sqlite3
import stat
from collections import Counter
from contextlib import closing
from hashlib import sha256

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ak_byma_calendar import es_dia_habil_operativo
from bs_instrument_contracts import aware_datetime
from bt_caucion_paper import CaucionOffer, TZ
from cg_paper_workspace import artifact_root
from rc6_cauciones_contract import parse_ticker
from rc6_ppi_native_request_receipt import release_coordinates
from rc6_ppi_shared_feed import _canonical_bytes


SCHEMA = "rc6.caucion-repo-book.v1"
AUTHORITY_SCHEMA = "rc6.caucion-repo-book-authority.v1"
AUTHORITY_NAME = "paper-caucion-repo-book-authority-v1.json"
RECEIPT_NAME = "caucion-repo-book-v1.json"
CLOCK_UNVERIFIED = "IOL_REPO_BOOK_CLOCK_UNVERIFIED"
AUTHORITY_MISSING = "CAUCION_REPO_BOOK_AUTHORITY_MISSING"
RECEIPT_MISSING = "CAUCION_REPO_BOOK_RECEIPT_MISSING"
MAX_BOOK_AGE_SECONDS = 90
_SOURCES = {"IOL_REST", "BYMA_STRUCTURED_API"}
_BOOK_COUNT = 32
_MAX_RECEIPT_BYTES = 32768
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,95}\Z")
_BOOK_FIELDS = {
    "identity", "provider_instrument_id", "provider_payload_sha256", "side", "term_days",
    "freshness_basis", "provider_book_at", "received_at", "maturity_at", "annual_rate_fraction",
    "available_principal", "minimum_principal", "principal_step", "day_count_basis", "fee_payment",
    "quoted_total_fees", "fee_quote_principal",
}
_OFFER_FIELDS = (
    "annual_rate_fraction", "available_principal", "minimum_principal", "principal_step",
    "day_count_basis", "fee_payment", "quoted_total_fees", "fee_quote_principal",
)
_REQUALIFY = {
    "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED", "PPI_NATIVE_SETTLEMENT_RECEIPT_INVALID",
    "CAPABILITY:NEEDS_CAUCION_TERMS",
}


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _read_signed_books(store):
    from scripts.rc6_operator_paper_rearm import (
        OPERATOR_DIR_RUNTIME, _root_owned_receipt, _strict_object,
    )

    # The PAPER data owner can publish the file, but cannot grant its signer
    # authority. Only the release owner writes the separate operator pin.
    try:
        pin = _root_owned_receipt(OPERATOR_DIR_RUNTIME, receipt_name=AUTHORITY_NAME)
    except FileNotFoundError:
        raise ValueError(AUTHORITY_MISSING) from None
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("CAUCION_REPO_BOOK_AUTHORITY_INVALID") from exc
    try:
        root = artifact_root(store.path)
        with os.fdopen(os.open(root / RECEIPT_NAME,
                              os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not 0 < info.st_size <= _MAX_RECEIPT_BYTES):
                raise ValueError("CAUCION_REPO_BOOK_RECEIPT_INVALID")
            raw = stream.read(_MAX_RECEIPT_BYTES + 1)
            if len(raw) != info.st_size:
                raise ValueError("CAUCION_REPO_BOOK_RECEIPT_INVALID")
        receipt = _strict_object(raw)
    except FileNotFoundError:
        raise ValueError(RECEIPT_MISSING) from None
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("CAUCION_REPO_BOOK_RECEIPT_INVALID") from exc

    release = release_coordinates()
    if (set(pin) != {"schema", "release", "provider", "signer_sha256"}
            or pin["schema"] != AUTHORITY_SCHEMA or not release or pin["release"] != release
            or not isinstance(pin["provider"], str) or pin["provider"] not in _SOURCES
            or not _matches(_HEX, pin["signer_sha256"])):
        raise ValueError("CAUCION_REPO_BOOK_AUTHORITY_INVALID")
    if (set(receipt) != {"claims", "public_key_hex", "signature_hex"}
            or not _matches(_HEX, receipt["public_key_hex"])
            or not _matches(_SIGNATURE, receipt["signature_hex"])):
        raise ValueError("CAUCION_REPO_BOOK_RECEIPT_INVALID")
    claims = receipt["claims"]
    if (not isinstance(claims, dict) or set(claims) != {"schema", "release", "provider", "books"}
            or claims["schema"] != SCHEMA or claims["release"] != release
            or claims["provider"] != pin["provider"] or not isinstance(claims["books"], list)
            or not 0 < len(claims["books"]) <= _BOOK_COUNT):
        raise ValueError("CAUCION_REPO_BOOK_RECEIPT_INVALID")
    public = bytes.fromhex(receipt["public_key_hex"])
    if sha256(public).hexdigest() != pin["signer_sha256"]:
        raise ValueError("CAUCION_REPO_BOOK_SIGNER_UNVERIFIED")
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(
            bytes.fromhex(receipt["signature_hex"]), _canonical_bytes(claims))
    except (InvalidSignature, TypeError, ValueError, RecursionError):
        raise ValueError("CAUCION_REPO_BOOK_SIGNATURE_INVALID") from None
    return claims


def _offer(book, *, at, provider):
    if not isinstance(book, dict) or set(book) != _BOOK_FIELDS:
        raise ValueError("CAUCION_REPO_BOOK_FIELDS_INVALID")
    if book["freshness_basis"] != "PROVIDER_BOOK_TIMESTAMP":
        raise ValueError(CLOCK_UNVERIFIED)
    key = book["identity"]
    if (not isinstance(key, list) or len(key) != 5 or not isinstance(key[0], str)
            or key[1:] != ["CAUCIONES", "BYMA", "ARS", "INMEDIATA"]
            or not re.fullmatch(r"PESOS[1-9][0-9]{0,2}", key[0])
            or not _matches(_PROVIDER_ID, book["provider_instrument_id"])
            or not _matches(_HEX, book["provider_payload_sha256"]) or book["side"] != "COLOCADORA"):
        raise ValueError("CAUCION_REPO_BOOK_IDENTITY_OR_SIDE_INVALID")
    tenor = parse_ticker(key[0]).term_days
    if type(book["term_days"]) is not int or book["term_days"] != tenor:
        raise ValueError("CAUCION_TERM_IDENTITY_MISMATCH")
    try:
        quoted, received = (aware_datetime(book[field]) for field in (
            "provider_book_at", "received_at"))
    except ValueError:
        raise ValueError(CLOCK_UNVERIFIED) from None
    if not quoted <= received <= at or (at - quoted).total_seconds() > MAX_BOOK_AGE_SECONDS:
        raise ValueError("CAUCION_REPO_BOOK_STALE_OR_FUTURE")
    try:
        maturity = aware_datetime(book["maturity_at"])
    except ValueError:
        raise ValueError("CAUCION_REPO_BOOK_MATURITY_UNVERIFIED") from None
    start = quoted.astimezone(TZ).date()
    due = maturity.astimezone(TZ).date()
    if (start != at.astimezone(TZ).date() or (due - start).days != tenor
            or not es_dia_habil_operativo(due)):
        raise ValueError("CAUCION_REPO_BOOK_MATURITY_UNVERIFIED")
    if book["quoted_total_fees"] is None or book["fee_quote_principal"] is None:
        raise ValueError("CAUCION_REPO_BOOK_EXACT_FEE_REQUIRED")
    digest = sha256(_canonical_bytes({"provider": provider, **book})).hexdigest()
    return CaucionOffer(
        instrument_id=key[0], currency=key[3], start_date=start.isoformat(),
        maturity_at=maturity.isoformat(), quoted_at=quoted.isoformat(),
        metadata_source=f"{SCHEMA}:{provider}:{digest}:{book['provider_payload_sha256']}",
        **{field: book[field] for field in _OFFER_FIELDS},
    )


def _catalog_reason(connection, key, *, at, pending):
    from bu_instrument_catalog import _candidate_retry_ambiguities, _iol_spot_primary
    from scripts.rc6_operator_paper_rearm import _strict_object

    if (key[1], key[0], *key[2:]) in pending:
        return "CHANGED_REVIEW_REQUIRED"
    candidates = connection.execute("""SELECT * FROM financial_instrument_catalog
      WHERE ticker=? AND instrument_type=? AND status IN ('AVAILABLE','STALE')""", key[:2]).fetchall()
    if len(candidates) != 1 or tuple(candidates[0][field] for field in (
            "ticker", "instrument_type", "market", "currency", "settlement")) != key:
        return "CAUCION_REPO_BOOK_PPI_IDENTITY_NOT_UNIQUE"
    record = dict(candidates[0])
    record["raw"] = _strict_object(record["metadata_json"])
    if (record["status"] != "AVAILABLE" or not _iol_spot_primary(record)
            or aware_datetime(record["last_seen_at"]) > at):
        return "CAUCION_REPO_BOOK_PPI_IDENTITY_UNVERIFIED"
    if key in _candidate_retry_ambiguities(connection, key[:2]):
        return "RETRY_IDENTITY_AMBIGUOUS"
    ledger = connection.execute("""SELECT can_simulate,status,detail FROM candidate_identity_v2
      WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?""", key).fetchone()
    if ledger and ledger["status"] == "AVAILABLE":
        if ledger["can_simulate"] == 1 and record["capability"] == "READY_PAPER_CAUCION_PLACING":
            return ""
        reasons = set(ledger["detail"].split(";"))
        if ledger["can_simulate"] == 0 and reasons <= _REQUALIFY:
            return ""
    return "CAUCION_REPO_BOOK_CANDIDATE_BLOCKED"


def iol_offers_from_store(store, *, at, connection=None):
    """Use the same source and exact catalog checks in planning and placement."""
    try:
        claims = _read_signed_books(store)
    except ValueError as exc:
        return [], [str(exc)]
    if connection is None:
        with closing(store.connect()) as db:
            db.execute("BEGIN")
            return _qualified_offers(db, claims, aware_datetime(at))
    return _qualified_offers(connection, claims, aware_datetime(at))


def _qualified_offers(connection, claims, at):
    from cp_contract_evidence_v2_hf6 import pending_material_changes

    offers, rejected = [], set()
    identities = Counter(
        tuple(book["identity"]) for book in claims["books"]
        if isinstance(book, dict) and isinstance(book.get("identity"), list)
        and all(isinstance(part, str) for part in book["identity"])
    )
    try:
        pending = pending_material_changes(connection, family="CAUCIONES")
        for book in claims["books"]:
            try:
                offer = _offer(book, at=at, provider=claims["provider"])
                key = tuple(book["identity"])
                if identities[key] != 1:
                    rejected.add("CAUCION_REPO_BOOK_DUPLICATE_IDENTITY")
                    continue
                reason = _catalog_reason(connection, key, at=at, pending=pending)
                if reason:
                    rejected.add(reason)
                else:
                    offers.append(offer)
            except (TypeError, ValueError, ArithmeticError) as exc:
                code = str(exc)
                rejected.add(code if re.fullmatch(r"[A-Z][A-Z0-9_]{1,125}", code)
                             else "CAUCION_REPO_BOOK_FIELDS_INVALID")
    except sqlite3.Error:
        return [], ["CAUCION_REPO_BOOK_CATALOG_UNAVAILABLE"]
    return offers, sorted(rejected)
