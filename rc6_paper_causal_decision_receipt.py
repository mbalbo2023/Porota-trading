"""A620: PAPER-only decision freeze + independent post-COMMIT Ed25519 observer.

The engine owns the atomic frozen decision, not the private signing key. Only
an independent observer, running *after* the decision transaction COMMIT,
may record a signed `decision_known_at`. A local hash, an unsigned freeze, or
an unpinned signing key must NEVER become fill authority or PPI provenance.

No provider GET, order route, PPI Watch write, or runtime admission occurs here.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any
from urllib.parse import quote as urlquote


FREEZE_SCHEMA = "rc6.paper-decision-causal-freeze.v1"
RECEIPT_SCHEMA = "rc6.paper-decision-known-at-ed25519.v1"
SIGNER_ROLE = "INDEPENDENT_POST_COMMIT_PAPER_OBSERVER"
SHA256_RE = re.compile(r"[a-f0-9]{64}\Z")
MAX_RECEIPT_BYTES = 64_000
# Deliberately UNCONFIGURED in the candidate. These must be pinned by a
# separately reviewed signed release/policy before a runtime verifier may
# recognize a key or a release. Caller-supplied hashes alone are NOT trust.
PINNED_DECISION_SIGNER_KEY_SHA256: str | None = None
PINNED_RELEASE_SHA256: str | None = None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str | bytes) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _clock(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(timezone.utc) if value.tzinfo is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _digest_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and SHA256_RE.fullmatch(value) else None


def _paper_row_document(row: Any) -> dict[str, Any]:
    return dict(zip(("source", "strategy_version", "decision_key", "decided_at",
                     "symbol", "action", "score", "reason", "features_json"), row))


def init_schema(store: Any) -> None:
    """Parent migration owner only; never called on a hot child connection."""
    with store.connect() as connection:
        connection.executescript("""
          CREATE TABLE IF NOT EXISTS paper_causal_decision_freezes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_key TEXT NOT NULL UNIQUE,
            freeze_payload_json TEXT NOT NULL,
            freeze_sha256 TEXT NOT NULL,
            prepared_before_commit_at TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS paper_causal_decision_receipts (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            signature_b64 TEXT NOT NULL,
            signer_key_id TEXT NOT NULL,
            decision_known_at TEXT NOT NULL,
            previous_receipt_sha256 TEXT);
          CREATE TRIGGER IF NOT EXISTS rc6_causal_freezes_no_update
            BEFORE UPDATE ON paper_causal_decision_freezes
            BEGIN SELECT RAISE(ABORT,'RC6_CAUSAL_FREEZE_IMMUTABLE'); END;
          CREATE TRIGGER IF NOT EXISTS rc6_causal_freezes_no_delete
            BEFORE DELETE ON paper_causal_decision_freezes
            BEGIN SELECT RAISE(ABORT,'RC6_CAUSAL_FREEZE_IMMUTABLE'); END;
          CREATE TRIGGER IF NOT EXISTS rc6_causal_receipts_no_update
            BEFORE UPDATE ON paper_causal_decision_receipts
            BEGIN SELECT RAISE(ABORT,'RC6_CAUSAL_RECEIPT_IMMUTABLE'); END;
          CREATE TRIGGER IF NOT EXISTS rc6_causal_receipts_no_delete
            BEFORE DELETE ON paper_causal_decision_receipts
            BEGIN SELECT RAISE(ABORT,'RC6_CAUSAL_RECEIPT_IMMUTABLE'); END;
        """)


def freeze_in_decision_transaction(connection: sqlite3.Connection, *,
                                   decision_key: str, quote: Any, action: str,
                                   score: str, reason: str,
                                   features: dict[str, Any],
                                   features_json: str,
                                   source: str, strategy_version: str) -> str:
    """Freeze the exact PAPER decision in its already-open caller transaction.

    `prepared_before_commit_at` is NOT `decision_known_at`. A crash before
    COMMIT rolls back both the original decision and this immutable freeze.
    Missing release/Book1 references are retained as null, never inferred.
    """
    row = connection.execute("SELECT source,strategy_version,decision_key,decided_at,"
                             "symbol,action,score,reason,features_json "
                             "FROM paper_decisions WHERE decision_key=?", (decision_key,)).fetchone()
    if row is None:
        raise ValueError("CAUSAL_DECISION_NOT_IN_SAME_TRANSACTION")
    recorded_row = _paper_row_document(row)
    if (recorded_row["source"] != source or recorded_row["strategy_version"] != strategy_version
            or recorded_row["action"] != action or recorded_row["score"] != score
            or recorded_row["reason"] != reason or recorded_row["features_json"] != features_json):
        raise ValueError("CAUSAL_FROZEN_DECISION_ROW_MISMATCH")
    lineage = features.get("performance_lineage") if isinstance(features, dict) else None
    claimed_decision_at = lineage.get("decision_at") if isinstance(lineage, dict) else None
    release_claim = _digest_or_none(os.environ.get("POROTA_RC6_RELEASE_SHA256"))
    book1_claim = _digest_or_none(features.get("ppi_book1_event_sha256"))
    identity = [getattr(quote, name, None) for name in
                ("symbol", "asset_class", "market", "currency", "settlement")]
    freeze = {
        "schema": FREEZE_SCHEMA,
        "source": "PRODUCTION_PAPER",
        "execution": "SIMULATED_ONLY",
        "real_money_authorized": False,
        "real_routes_used": [],
        "decision_key": decision_key,
        "action": action,
        "identity": identity,
        "claimed_decision_at": claimed_decision_at,
        "quote_observed_at": getattr(quote, "observed_at", None),
        "quote_native_book_at": getattr(quote, "book_at", None),
        "quote_native_trade_at": getattr(quote, "trade_at", None),
        "book1_event_sha256_claim": book1_claim,
        "quote_used": {name: str(getattr(quote, name, "")) for name in
                       ("last", "bid", "ask", "bid_size", "ask_size")},
        "strategy_version": strategy_version,
        "release_sha256_claim": release_claim,
        "paper_decision_row_sha256": _sha(_json(recorded_row)),
        "features_json_sha256": _sha(features_json),
        "score": score,
    }
    raw = _json(freeze)
    if len(raw.encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ValueError("CAUSAL_DECISION_FREEZE_CAPACITY_HOLD")
    digest = _sha(raw)
    connection.execute("INSERT INTO paper_causal_decision_freezes "
                       "(decision_key,freeze_payload_json,freeze_sha256,prepared_before_commit_at) "
                       "VALUES(?,?,?,?)", (decision_key, raw, digest, _now()))
    return digest


def _read_verified_freeze(connection: sqlite3.Connection, decision_key: str
                          ) -> tuple[dict[str, Any] | None, str | None, str]:
    row = connection.execute("SELECT freeze_payload_json,freeze_sha256 "
                             "FROM paper_causal_decision_freezes WHERE decision_key=?",
                             (decision_key,)).fetchone()
    if row is None:
        return None, None, "CAUSAL_FREEZE_ABSENT"
    raw, sha = row
    if (not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RECEIPT_BYTES
            or not isinstance(sha, str) or not hmac.compare_digest(_sha(raw), sha)):
        return None, None, "CAUSAL_FREEZE_INTEGRITY_HOLD"
    try:
        frozen = json.loads(raw)
    except json.JSONDecodeError:
        return None, None, "CAUSAL_FREEZE_JSON_HOLD"
    if (not isinstance(frozen, dict) or frozen.get("schema") != FREEZE_SCHEMA
            or frozen.get("decision_key") != decision_key
            or frozen.get("source") != "PRODUCTION_PAPER"
            or frozen.get("real_money_authorized") is not False
            or frozen.get("real_routes_used") != []):
        return None, None, "CAUSAL_FREEZE_SCOPE_HOLD"
    paper_row = connection.execute("SELECT source,strategy_version,decision_key,decided_at,"
                                   "symbol,action,score,reason,features_json "
                                   "FROM paper_decisions WHERE decision_key=?", (decision_key,)).fetchone()
    if (paper_row is None or _sha(_json(_paper_row_document(paper_row))) !=
            frozen.get("paper_decision_row_sha256")):
        return None, None, "CAUSAL_FREEZE_PAPER_DECISION_LINK_HOLD"
    return frozen, sha, ""


def _private_ed25519_key(path: Path):
    """Load only a private 0600 RAW key in the independent observer process."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o077):
            raise ValueError("CAUSAL_SIGNER_PRIVATE_KEY_PERMISSION_HOLD")
        raw = stream.read(33)
    if len(raw) != 32:
        raise ValueError("CAUSAL_SIGNER_PRIVATE_KEY_FORMAT_HOLD")
    return Ed25519PrivateKey.from_private_bytes(raw)


def attest_after_commit(db_path: Path, *, decision_key: str,
                        signing_key_path: Path | None,
                        expected_release_sha256: str | None) -> dict[str, Any]:
    """Independent observer: read already COMMITTED decision, sign at *read* time.

    Never invoke from be_paper_engine. A missing key or release binding leaves
    a durable freeze and NO attestation, recoverable by retry after a crash.
    """
    failed = {"state": "NO_VERIFICADO", "decision_known_at": None,
              "signature_created": False, "fill_accredited": False,
              "real_orders_sent": 0}
    if (not isinstance(decision_key, str) or not decision_key
            or signing_key_path is None
            or _digest_or_none(expected_release_sha256) is None):
        return {**failed, "reason": "CAUSAL_SIGNER_OR_RELEASE_TRUST_UNAVAILABLE"}
    try:
        signer = _private_ed25519_key(Path(signing_key_path))
        from cryptography.hazmat.primitives import serialization
        public_key = signer.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw)
        key_id = _sha(public_key)
        with closing(sqlite3.connect(str(db_path), timeout=5)) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            frozen, sha, error = _read_verified_freeze(connection, decision_key)
            if error:
                connection.rollback()
                return {**failed, "reason": error}
            if (frozen.get("release_sha256_claim") != expected_release_sha256
                    or frozen.get("action") not in {"BUY", "SELL"}
                    or len(frozen.get("identity") or []) != 5
                    or any(not isinstance(v, str) or not v.strip()
                           for v in frozen["identity"])
                    or _clock(frozen.get("claimed_decision_at")) is None
                    or _clock(frozen.get("quote_observed_at")) is None
                    or _clock(frozen["quote_observed_at"]) > _clock(frozen["claimed_decision_at"])):
                connection.rollback()
                return {**failed, "reason": "CAUSAL_DECISION_CLOCK_IDENTITY_OR_RELEASE_HOLD"}
            duplicate = connection.execute("SELECT 1 FROM paper_causal_decision_receipts "
                                           "WHERE decision_key=?", (decision_key,)).fetchone()
            if duplicate:
                connection.rollback()
                return {**failed, "reason": "CAUSAL_RECEIPT_ALREADY_COMMITTED_NO_REPLAY"}
            previous = connection.execute("SELECT sequence,payload_sha256 FROM "
                                          "paper_causal_decision_receipts ORDER BY sequence DESC LIMIT 1").fetchone()
            sequence = previous[0] + 1 if previous else 1
            # This clock is sampled by the signer *after* observing the prior
            # transaction's committed freeze through a fresh connection.
            known_at = _now()
            if _clock(known_at) < _clock(frozen["claimed_decision_at"]):
                connection.rollback()
                return {**failed, "reason": "CAUSAL_OBSERVER_CLOCK_BEFORE_DECISION_HOLD"}
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "role": SIGNER_ROLE,
                "decision_key": decision_key,
                "freeze_sha256": sha,
                "decision_at": frozen["claimed_decision_at"],
                "decision_known_at": known_at,
                "identity": frozen["identity"],
                "action": frozen["action"],
                "release_sha256": expected_release_sha256,
                "book1_event_sha256_claim": frozen["book1_event_sha256_claim"],
                "signer_key_id": key_id,
                "sequence": sequence,
                "previous_receipt_sha256": previous[1] if previous else None,
                "real_money_authorized": False,
                "real_routes_used": [],
            }
            raw = _json(receipt)
            signed = base64.b64encode(signer.sign(raw.encode("utf-8"))).decode("ascii")
            digest = _sha(raw)
            connection.execute("INSERT INTO paper_causal_decision_receipts VALUES(?,?,?,?,?,?,?,?)",
                               (sequence, decision_key, raw, digest, signed,
                                key_id, known_at, receipt["previous_receipt_sha256"]))
            connection.commit()
            return {**failed, "state": "RECORDED_UNVERIFIED_UNTIL_PINNED_VERIFIER",
                    "reason": "CAUSAL_SIGNED_RECEIPT_NEEDS_INDEPENDENT_VERIFICATION",
                    "receipt_sha256": digest, "decision_known_at": known_at,
                    "signature_created": True, "signer_key_id": key_id}
    except (OSError, ValueError, sqlite3.Error, ImportError) as exc:
        return {**failed, "reason": (str(exc) if isinstance(exc, ValueError)
                else "CAUSAL_OBSERVER_FAILED:" + type(exc).__name__)}


def verify_signed_receipt(db_path: Path, *, decision_key: str,
                          trusted_public_key_raw: bytes | None,
                          trusted_public_key_sha256: str | None,
                          trusted_release_sha256: str | None) -> dict[str, Any]:
    """Independent pinned verifier. No key/release pin -> no known-at authority.

    The supplied trust pins must originate in protected release/host policy,
    never from the unsigned receipt itself or an untrusted caller claim.
    """
    result = {"state": "NO_VERIFICADO", "reason": "CAUSAL_TRUST_ANCHOR_UNAVAILABLE",
              "decision_known_at": None, "decision_known_at_verified": False,
              "decision_signature_verified": False,
              "ppi_source_capture_attested": False,
              "unit_contract_verified": False, "tariff_contract_verified": False,
              "fill_accredited": False, "real_orders_sent": 0}
    if (not isinstance(trusted_public_key_raw, bytes)
            or len(trusted_public_key_raw) != 32
            or _digest_or_none(trusted_public_key_sha256) is None
            or _digest_or_none(trusted_release_sha256) is None
            or _digest_or_none(PINNED_DECISION_SIGNER_KEY_SHA256) is None
            or _digest_or_none(PINNED_RELEASE_SHA256) is None
            or trusted_public_key_sha256 != PINNED_DECISION_SIGNER_KEY_SHA256
            or trusted_release_sha256 != PINNED_RELEASE_SHA256
            or not hmac.compare_digest(_sha(trusted_public_key_raw), trusted_public_key_sha256)):
        return result
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        with closing(sqlite3.connect("file:" + urlquote(str(Path(db_path).absolute()), safe="/") + "?mode=ro",
                                     uri=True, timeout=5)) as connection:
            frozen, freeze_sha, error = _read_verified_freeze(connection, decision_key)
            if error:
                return {**result, "state": "BLOQUEADO", "reason": error}
            row = connection.execute("SELECT sequence,payload_json,payload_sha256,"
                                     "signature_b64,signer_key_id,decision_known_at,"
                                     "previous_receipt_sha256 FROM paper_causal_decision_receipts "
                                     "WHERE decision_key=?", (decision_key,)).fetchone()
            if row is None:
                return {**result, "reason": "CAUSAL_SIGNED_RECEIPT_ABSENT"}
            sequence, raw, digest, signature, key_id, known_at, previous_hash = row
            if (not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_RECEIPT_BYTES
                    or not hmac.compare_digest(_sha(raw), digest)):
                return {**result, "state": "BLOQUEADO", "reason": "CAUSAL_RECEIPT_INTEGRITY_HOLD"}
            receipt = json.loads(raw)
            prev = connection.execute("SELECT payload_sha256 FROM paper_causal_decision_receipts "
                                      "WHERE sequence=?", (sequence-1,)).fetchone() if sequence > 1 else None
            if (not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA
                    or receipt.get("role") != SIGNER_ROLE
                    or receipt.get("decision_key") != decision_key
                    or receipt.get("freeze_sha256") != freeze_sha
                    or receipt.get("identity") != frozen.get("identity")
                    or receipt.get("action") != frozen.get("action")
                    or receipt.get("book1_event_sha256_claim") != frozen.get("book1_event_sha256_claim")
                    or receipt.get("release_sha256") != trusted_release_sha256
                    or frozen.get("release_sha256_claim") != trusted_release_sha256
                    or receipt.get("signer_key_id") != trusted_public_key_sha256
                    or key_id != trusted_public_key_sha256
                    or receipt.get("decision_known_at") != known_at
                    or receipt.get("sequence") != sequence
                    or receipt.get("previous_receipt_sha256") != previous_hash
                    or previous_hash != (prev[0] if prev else None)
                    or receipt.get("real_money_authorized") is not False
                    or receipt.get("real_routes_used") != []
                    or _clock(known_at) is None
                    or _clock(known_at) < _clock(receipt.get("decision_at"))
                    or receipt.get("decision_at") != frozen.get("claimed_decision_at")):
                return {**result, "state": "BLOQUEADO", "reason": "CAUSAL_RECEIPT_BINDING_OR_CLOCK_HOLD"}
            Ed25519PublicKey.from_public_bytes(trusted_public_key_raw).verify(
                base64.b64decode(signature, validate=True), raw.encode("utf-8"))
            return {**result, "state": "SIGNED_DECISION_KNOWN_AT_VERIFIED",
                    "reason": "DECISION_TIMING_VERIFIED_ONLY_SOURCE_UNITS_AND_FILL_UNVERIFIED",
                    "decision_known_at": known_at,
                    "decision_known_at_verified": True,
                    "decision_signature_verified": True,
                    "freeze_sha256": freeze_sha,
                    "receipt_sha256": digest,
                    "sequence": sequence}
    except Exception as exc:
        return {**result, "state": "BLOQUEADO", "reason": (
            "CAUSAL_SIGNATURE_OR_RECEIPT_INVALID:" + type(exc).__name__)}
