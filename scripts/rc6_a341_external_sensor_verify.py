"""Ed25519 trust-boundary audit for independent RC6 market-data sensors.

This module never grants PAPER readiness or emits provider data. A valid
signature proves only possession of a pinned public key's private counterpart;
it does not by itself prove source entitlement, licenced-data provenance,
clock accuracy, decision-time causality, independent administration, or fills.

It is intentionally separate from A320 while that PR has another WRITE_OWNER.
An A320 integration must require its own authenticated evidence and review.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import stat


REGISTRY_SCHEMA = "rc6.a341.sensor.trust-registry.v1"
RECEIPT_SCHEMA = "rc6.a341.sensor.attestation.v1"
REPORT_SCHEMA = "rc6.a341.sensor.crypto-review.v1"
FAMILIES = frozenset((
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
))
SOURCE_LINEAGE = {"PPI": "PPI", "IOL": "IOL", "IOL_CONNECTOR": "IOL",
                  "BYMA": "BYMA", "A3": "A3", "A3_CEM": "A3",
                  "CAFCI": "CAFCI", "CNV": "CNV"}
HASH = re.compile(r"[0-9a-f]{64}\Z")
SYMBOL = re.compile(r"[A-Z0-9./_-]{1,40}\Z")
NAME = re.compile(r"[A-Za-z0-9_.:-]{3,80}\Z")
MAX_RECEIPT_BYTES = 128 * 1024
MAX_REGISTRY_BYTES = 16 * 1024
MAX_QUOTE_AGE_SECONDS = 120
REGISTRY_FIELDS = frozenset({"schema", "sensors"})
SENSOR_FIELDS = frozenset({"sensor_id", "source", "key_id", "operator_id",
                           "trust_domain", "public_key_b64", "valid_from_utc",
                           "valid_until_utc"})
RECEIPT_FIELDS = frozenset({"schema", "sensor_id", "source", "identity",
                            "provider_at_utc", "first_known_at_utc",
                            "payload_sha256", "payload_format", "attestation"})
SIGNATURE_FIELDS = frozenset({"algorithm", "key_id", "signature_b64"})
PAYLOAD_FORMATS = frozenset({"SDK_PARSED", "RAW_HTTP_ATTESTED", "INDEPENDENT_FEED"})


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value):
    return sha256(_canonical(value)).hexdigest()


def _decode_b64(value, expected_bytes, reason):
    if not isinstance(value, str) or len(value) > 256:
        raise ValueError(reason)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError(reason) from None
    if len(decoded) != expected_bytes or base64.b64encode(decoded).decode() != value:
        raise ValueError(reason)
    return decoded


def _utc(value, reason):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError(reason)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError(reason) from None


def _identity(value):
    if (not isinstance(value, list) or len(value) != 5
            or any(not isinstance(v, str) or not v or len(v) > 40 for v in value)):
        raise ValueError("A341_FULL_KEY_INVALID")
    symbol, family, market, currency, settlement = value
    if (not SYMBOL.fullmatch(symbol) or family not in FAMILIES
            or market not in {"BYMA", "BCBA", "A3", "ROFEX", "NYSE", "NASDAQ", "CAFCI"}
            or currency not in {"ARS", "USD"} or not SYMBOL.fullmatch(settlement)):
        raise ValueError("A341_FULL_KEY_INVALID")
    return tuple(value)


def _registry(registry, expected_sha256):
    if (not isinstance(expected_sha256, str) or not HASH.fullmatch(expected_sha256)
            or not isinstance(registry, dict) or set(registry) != REGISTRY_FIELDS
            or registry.get("schema") != REGISTRY_SCHEMA
            or _digest(registry) != expected_sha256):
        raise ValueError("A341_TRUST_REGISTRY_PIN_UNVERIFIED")
    sensors = registry.get("sensors")
    if not isinstance(sensors, list) or not 2 <= len(sensors) <= 16:
        raise ValueError("A341_TRUST_REGISTRY_SHAPE_INVALID")
    index = {}
    for sensor in sensors:
        if not isinstance(sensor, dict) or set(sensor) != SENSOR_FIELDS:
            raise ValueError("A341_TRUST_SENSOR_SHAPE_INVALID")
        for field in ("sensor_id", "key_id", "operator_id", "trust_domain"):
            if not isinstance(sensor[field], str) or not NAME.fullmatch(sensor[field]):
                raise ValueError("A341_TRUST_SENSOR_ID_INVALID")
        if (sensor["source"] not in SOURCE_LINEAGE
                or sensor["sensor_id"] in index):
            raise ValueError("A341_TRUST_SENSOR_NOT_UNIQUE")
        _decode_b64(sensor["public_key_b64"], 32, "A341_TRUST_PUBLIC_KEY_INVALID")
        valid_from = _utc(sensor["valid_from_utc"], "A341_TRUST_CLOCK_INVALID")
        valid_until = _utc(sensor["valid_until_utc"], "A341_TRUST_CLOCK_INVALID")
        if valid_from >= valid_until:
            raise ValueError("A341_TRUST_CLOCK_INVALID")
        index[sensor["sensor_id"]] = sensor
    return index


def _check_receipt(receipt, sensors, identity, now):
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
        raise ValueError("A341_RECEIPT_SHAPE_INVALID")
    if receipt["schema"] != RECEIPT_SCHEMA or _identity(receipt["identity"]) != identity:
        raise ValueError("A341_RECEIPT_IDENTITY_INVALID")
    sensor = sensors.get(receipt["sensor_id"])
    if sensor is None or receipt["source"] != sensor["source"]:
        raise ValueError("A341_RECEIPT_SENSOR_MISMATCH")
    if (receipt["payload_format"] not in PAYLOAD_FORMATS
            or not isinstance(receipt["payload_sha256"], str)
            or not HASH.fullmatch(receipt["payload_sha256"])):
        raise ValueError("A341_PAYLOAD_PROVENANCE_UNVERIFIED")
    stamp = _utc(receipt["provider_at_utc"], "A341_PROVIDER_CLOCK_INVALID")
    received = _utc(receipt["first_known_at_utc"], "A341_FIRST_KNOWN_CLOCK_INVALID")
    if (not 0 <= (received - stamp).total_seconds() <= MAX_QUOTE_AGE_SECONDS
            or received > now or stamp > now):
        raise ValueError("A341_QUOTE_STALE_OR_FUTURE")
    if not (_utc(sensor["valid_from_utc"], "A341_TRUST_CLOCK_INVALID")
            <= received < _utc(sensor["valid_until_utc"], "A341_TRUST_CLOCK_INVALID")):
        raise ValueError("A341_SIGNER_OUTSIDE_VALIDITY")
    attestation = receipt["attestation"]
    if (not isinstance(attestation, dict) or set(attestation) != SIGNATURE_FIELDS
            or attestation["algorithm"] != "Ed25519"
            or attestation["key_id"] != sensor["key_id"]):
        raise ValueError("A341_SIGNATURE_TYPE_OR_KEY_UNVERIFIED")
    signature = _decode_b64(attestation["signature_b64"], 64,
                            "A341_SIGNATURE_LENGTH_INVALID")
    public_key = _decode_b64(sensor["public_key_b64"], 32,
                             "A341_TRUST_PUBLIC_KEY_INVALID")
    # The private signing key MUST be off this verification runner. Neither
    # key generation nor signing is available in the production code path.
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, _canonical({key: value for key, value in receipt.items()
                                   if key != "attestation"}))
    except (InvalidSignature, ValueError):
        raise ValueError("A341_SIGNATURE_INVALID") from None
    return sensor


def verify_pair(registry, expected_registry_sha256, pair, identity, *, now=None):
    """Cryptographic review only; deliberately never promotes PIT or PAPER."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("A341_REVIEW_CLOCK_INVALID")
    now = now.astimezone(timezone.utc)
    full_key = _identity(identity)
    sensors = _registry(registry, expected_registry_sha256)
    if not isinstance(pair, list) or len(pair) != 2:
        raise ValueError("A341_EXACT_TWO_SENSORS_REQUIRED")
    first = _check_receipt(pair[0], sensors, full_key, now)
    second = _check_receipt(pair[1], sensors, full_key, now)
    if (first["sensor_id"] == second["sensor_id"]
            or first["key_id"] == second["key_id"]
            or first["public_key_b64"] == second["public_key_b64"]
            or first["operator_id"] == second["operator_id"]
            or first["trust_domain"] == second["trust_domain"]
            or SOURCE_LINEAGE[first["source"]] == SOURCE_LINEAGE[second["source"]]):
        raise ValueError("A341_SENSORS_NOT_INDEPENDENT_BY_DECLARED_REGISTRY")
    return {"schema": REPORT_SCHEMA, "cryptographic_pair_valid": True,
            "signed_receipts": 2, "identity_digest": _digest(full_key),
            "receipt_digests": sorted(_digest(receipt) for receipt in pair),
            "registry_sha256": expected_registry_sha256,
            "source_lineages": sorted((SOURCE_LINEAGE[first["source"]],
                                       SOURCE_LINEAGE[second["source"]])),
            "external_independence_attested": False,
            "source_pit_verified": 0, "paper_sim_verified": 0,
            "real_orders_sent": 0, "history_v2_writes": 0,
            "ready_implication": "NONE", "evidence_state": "NO_VERIFICADO"}


def _read_json(path, limit):
    file = Path(path).absolute()
    if file.is_symlink() or not file.is_file():
        raise ValueError("A341_INPUT_FILE_UNSAFE")
    metadata = file.stat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= limit):
        raise ValueError("A341_INPUT_FILE_UNSAFE")
    raw = file.read_bytes()
    if len(raw) != metadata.st_size:
        raise ValueError("A341_INPUT_CHANGED")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("A341_DUPLICATE_JSON_KEYS")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ValueError("A341_NONFINITE_JSON_NUMBER")))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("A341_INPUT_JSON_INVALID") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Public-key-only RC6 sensor audit")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--registry-sha256", required=True)
    parser.add_argument("--receipts", required=True)
    parser.add_argument("--identity", required=True, help="JSON full five-field key")
    args = parser.parse_args(argv)
    try:
        registry = _read_json(args.registry, MAX_REGISTRY_BYTES)
        receipts = _read_json(args.receipts, MAX_RECEIPT_BYTES)
        identity = json.loads(args.identity)
        result = verify_pair(registry, args.registry_sha256, receipts, identity)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        # No signer, payload, key, broker exception or path leaks to stdout.
        print(json.dumps({"schema": REPORT_SCHEMA, "evidence_state": "NO_VERIFICADO",
                          "cryptographic_pair_valid": False, "source_pit_verified": 0,
                          "paper_sim_verified": 0, "real_orders_sent": 0,
                          "reason": "A341_FAIL_CLOSED"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
