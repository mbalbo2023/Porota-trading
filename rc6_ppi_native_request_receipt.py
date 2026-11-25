"""PAPER settlement evidence signed by the real, guarded PPI REST owner."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from rc6_ppi_shared_feed import _canonical_bytes, _five, _sha


SOURCE = "PPI_NATIVE_REQUEST_RECEIPT"
FIELD = "_ppi_native_request_receipt"
SCHEMA = "rc6.ppi.native-settlement-receipt.v1"
CAUCION_BOOK_SCHEMA = "rc6.ppi.caucion-book-settlement-receipt.v1"
_PIN_SCHEMA = "rc6.ppi.native-settlement-controller-pin.v1"
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA64 = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE = re.compile(r"[0-9a-f]{128}\Z")
_PIN_MAX_BYTES = 16 * 1024
_MAX_RETIRED_PINS = 64
_IDENTITY_LKG_SECONDS = 14 * 86400
_PAIR_MAX_SECONDS = 120
_ENDPOINTS = ("current", "book")
_REFERENCE_ONLY = frozenset({"CAUCIONES", "FCI", "INDICES"})
_MATERIAL_FIELDS = {
    "schema", "five_key", "release", "catalog_sha256", "gateway_generation",
    "current", "book", "signature",
}
_CAUCION_BOOK_FIELDS = _MATERIAL_FIELDS - {"current"}
_WIRE_FIELDS = {
    "guard_sha256", "body_sha256", "lease_sha256",
    "native_at", "received_at", "monotonic_ns",
}


def release_coordinates():
    coordinates = [os.getenv("POROTA_BUILD_SHA", ""), os.getenv("POROTA_CANDIDATE_TREE_SHA", "")]
    return coordinates if all(_SHA40.fullmatch(item) for item in coordinates) else None


def _clock(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("PPI_NATIVE_SETTLEMENT_CLOCK_INVALID")
    point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if point.utcoffset() is None:
        raise ValueError("PPI_NATIVE_SETTLEMENT_CLOCK_INVALID")
    return point.astimezone(timezone.utc)


def _positive(value):
    number = Decimal(str(value))
    return number.is_finite() and number > 0


def _caucion_book_scope(identity):
    from rc6_cauciones_contract import parse_ticker

    return (
        identity[1:] == ("CAUCIONES", "BYMA", "ARS", "INMEDIATA")
        and parse_ticker(identity[0]).currency_prefix == "PESOS"
    )


def _wire_part(capture, endpoint, identity, *, priority="DISCOVERY"):
    from rc6_ppi_global_budget import assert_native_ppi_response_identity
    from rc6_ppi_shared_rest_producer import _validate_guard_witness

    native = capture["native_receipt"]
    payload = native["raw_payload"]
    guard = capture["guard_wire_receipt"]
    guard_hash, received, monotonic = _validate_guard_witness(
        guard, method=endpoint, priority=priority, five_key=identity,
        raw_sha256=native["raw_sha256"],
    )
    assert_native_ppi_response_identity(payload, identity)
    if (native["ppi_five_key"] != list(identity)
            or native["raw_sha256"] != _sha(payload)
            or _clock(native["native_at_utc"]) != _clock(payload["date"])
            or native["guard_budget_receipt_sha256"] != guard_hash
            or _clock(native["received_at_utc"]) != received
            or native["receive_monotonic_ns"] != monotonic):
        raise ValueError("PPI_NATIVE_SETTLEMENT_WIRE_CONTEXT_MISMATCH")
    if endpoint == "current":
        usable = _positive(payload["price"])
    elif priority == "WARM" and _caucion_book_scope(identity):
        from rc6_cauciones_contract import placed_side_book_levels, quote_from_book_level
        from rc6_ppi_global_budget import native_ppi_response_settlement

        status, _ = native_ppi_response_settlement(payload)
        levels = list(placed_side_book_levels(payload))
        quote = quote_from_book_level(identity[0], levels[0]) if levels else None
        usable = status == "EXPLICIT" and quote is not None and (
            quote.tna_percent > 0 and quote.quantity > 0
        )
    else:
        bid = payload["bids"][0]
        offer = payload["offers"][0]
        usable = (all(_positive(level[field]) for level in (bid, offer)
                      for field in ("price", "quantity"))
                  and Decimal(str(bid["price"])) <= Decimal(str(offer["price"])))
    if not usable:
        raise ValueError("PPI_NATIVE_SETTLEMENT_EMPTY_MARKET_DATA")
    return {
        "guard_sha256": guard_hash, "body_sha256": guard["raw_body_sha256"],
        "lease_sha256": guard["budget_lease_sha256"],
        "native_at": _clock(native["native_at_utc"]).isoformat(),
        "received_at": received.isoformat(), "monotonic_ns": monotonic,
    }


def _valid_wire(wire):
    return (
        isinstance(wire, dict) and set(wire) == _WIRE_FIELDS
        and all(isinstance(wire[h], str) and _SHA64.fullmatch(wire[h])
                for h in ("guard_sha256", "body_sha256", "lease_sha256"))
        and type(wire["monotonic_ns"]) is int and wire["monotonic_ns"] > 0
        and 0 <= (_clock(wire["received_at"]) - _clock(wire["native_at"])).total_seconds()
        <= _PAIR_MAX_SECONDS
    )


def _valid_pair(current, book):
    if not _valid_wire(current) or not _valid_wire(book):
        return False
    span = (_clock(book["received_at"]) - _clock(current["received_at"])).total_seconds()
    return (0 <= span <= _PAIR_MAX_SECONDS
            and book["monotonic_ns"] > current["monotonic_ns"]
            and book["guard_sha256"] != current["guard_sha256"]
            and book["lease_sha256"] != current["lease_sha256"])


class SettlementReceiptSigner:
    """Sign native settlement evidence with the Gateway key pinned by its parent."""

    def __init__(self, release):
        if (not isinstance(release, list) or len(release) != 2
                or any(not isinstance(item, str) or not _SHA40.fullmatch(item) for item in release)):
            raise ValueError("PPI_NATIVE_SETTLEMENT_RELEASE_INVALID")
        self.release = list(release)
        self._key = Ed25519PrivateKey.generate()
        self.public_key = self._key.public_key().public_bytes_raw().hex()
        self._current = None

    def observe(self, endpoint, five_key, capture, *, generation, catalog_digest, priority="DISCOVERY"):
        ready_current = self._current
        if priority == "DISCOVERY":
            self._current = None
        try:
            identity = _five(five_key)
            if (endpoint not in _ENDPOINTS
                    or not _SHA64.fullmatch(str(generation))
                    or not _SHA64.fullmatch(str(catalog_digest))):
                return None
            context = {
                "five_key": list(identity), "release": self.release,
                "gateway_generation": generation, "catalog_sha256": catalog_digest,
            }
            if priority == "WARM" and endpoint == "book" and _caucion_book_scope(identity):
                wire = _wire_part(capture, endpoint, identity, priority=priority)
                if not _valid_wire(wire):
                    return None
                payload = {**context, "schema": CAUCION_BOOK_SCHEMA, "book": wire}
                return {**payload, "signature": self._key.sign(_canonical_bytes(payload)).hex()}

            if priority != "DISCOVERY" or identity[1] in _REFERENCE_ONLY:
                return None
            wire = _wire_part(capture, endpoint, identity)
            coordinate = (identity, generation, catalog_digest)
            if endpoint == "current":
                self._current = (coordinate, wire)
                return None
            if not ready_current or ready_current[0] != coordinate or not _valid_pair(ready_current[1], wire):
                return None
            payload = {**context, "schema": SCHEMA, "current": ready_current[1], "book": wire}
            return {**payload, "signature": self._key.sign(_canonical_bytes(payload)).hex()}
        except (ValueError, TypeError, ArithmeticError, KeyError, IndexError, RuntimeError):
            # No contract proof from an unusable response. The native read
            # and its diagnostics still reach their existing consumer.
            return None


def _pin_path(database):
    from cg_paper_workspace import artifact_root

    return artifact_root(database) / "ppi-gateway" / "settlement-verifier.json"


def _read_pin(path: Path):
    if path.parent.is_symlink():
        raise ValueError("PPI_NATIVE_SETTLEMENT_PIN_CUSTODY")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o022 or not 0 < info.st_size <= _PIN_MAX_BYTES):
            raise ValueError("PPI_NATIVE_SETTLEMENT_PIN_CUSTODY")
        pin = json.loads(stream.read(_PIN_MAX_BYTES + 1))
    if not isinstance(pin, dict) or pin.get("schema") != _PIN_SCHEMA or not _valid_pin_key(pin):
        raise ValueError("PPI_NATIVE_SETTLEMENT_PIN_INVALID")
    retired = pin.get("retired", [])
    if (not isinstance(retired, list) or len(retired) > _MAX_RETIRED_PINS
            or any(not isinstance(item, dict) or not _valid_pin_key(item)
                   or not isinstance(item.get("until"), str) for item in retired)):
        raise ValueError("PPI_NATIVE_SETTLEMENT_PIN_INVALID")
    for item in retired:
        _clock(item["until"])
    return pin


def _valid_pin_key(pin):
    release = pin.get("release")
    public = pin.get("public_key")
    return (isinstance(public, str) and bool(_SHA64.fullmatch(public))
            and isinstance(release, list) and len(release) == 2
            and all(isinstance(item, str) and _SHA40.fullmatch(item) for item in release))


def pin_gateway_signer(database, status, *, expected_pid, release=None):
    """Pin only a public key obtained from the controller's authenticated child."""
    release = release if release is not None else release_coordinates()
    public = status.get("settlement_signer_public_key")
    if (status.get("state") != "OPEN" or status.get("owner_pid") != expected_pid
            or type(expected_pid) is not int or expected_pid <= 0
            or status.get("settlement_signer_release") != release
            or not isinstance(public, str) or not _SHA64.fullmatch(public)
            or not release):
        raise ValueError("PPI_NATIVE_SETTLEMENT_SIGNER_UNPINNED")
    path = _pin_path(database)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("PPI_NATIVE_SETTLEMENT_PIN_CUSTODY")
    pin = {"schema": _PIN_SCHEMA, "public_key": public, "release": release}
    try:
        previous = _read_pin(path)
    except FileNotFoundError:
        previous = None
    if previous:
        if previous["public_key"] == public and previous["release"] == release:
            return
        now = datetime.now(timezone.utc)
        floor = now - timedelta(seconds=_IDENTITY_LKG_SECONDS)
        retired = [{"public_key": previous["public_key"], "release": previous["release"],
                    "until": now.isoformat()}]
        retired.extend(item for item in previous.get("retired", [])
                       if floor <= _clock(item["until"]) <= now)
        pin["retired"] = retired[:_MAX_RETIRED_PINS]
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".settlement-", delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(_canonical_bytes(pin))
            f.flush()
            os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def verified_receipt(proof, five_key, *, database=None, now=None,
                     maximum_age_seconds=_IDENTITY_LKG_SECONDS, require_active=False):
    """Verify the exact contract. Only the active signer may add a new proof."""
    try:
        identity = _five(five_key)
        if (not isinstance(proof, dict) or proof.get("five_key") != list(identity)
                or not isinstance(proof.get("signature"), str) or not _SIGNATURE.fullmatch(proof["signature"])
                or any(not isinstance(proof.get(name), str) or not _SHA64.fullmatch(proof[name])
                       for name in ("catalog_sha256", "gateway_generation"))):
            return False
        if proof.get("schema") == CAUCION_BOOK_SCHEMA:
            valid = (set(proof) == _CAUCION_BOOK_FIELDS and _caucion_book_scope(identity)
                     and _valid_wire(proof["book"]))
        else:
            valid = (proof.get("schema") == SCHEMA and set(proof) == _MATERIAL_FIELDS
                     and identity[1] not in _REFERENCE_ONLY
                     and _valid_pair(proof["current"], proof["book"]))
        if not valid:
            return False
        point = datetime.now(timezone.utc) if now is None else (
            _clock(now) if isinstance(now, str) else _clock(now.isoformat()))
        known = _clock(proof["book"]["received_at"])
        if not 0 <= (point - known).total_seconds() <= maximum_age_seconds:
            return False
        pin = _read_pin(_pin_path(database))
        release = release_coordinates()
        if release and release != pin["release"]:
            return False
        material = {k: v for k, v in proof.items() if k != "signature"}
        signature, signed = bytes.fromhex(proof["signature"]), _canonical_bytes(material)
        candidates = [pin]
        if not require_active:
            candidates.extend(item for item in pin.get("retired", [])
                              if known <= _clock(item["until"]) <= point)
        for signer in candidates:
            if proof["release"] != signer["release"]:
                continue
            try:
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(signer["public_key"])).verify(
                    signature, signed)
                return True
            except InvalidSignature:
                continue
        return False
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return False
