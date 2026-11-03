"""Bounded native PPI scanner receipt; separate PAPER mode from quote origin.

Only the observer call site immediately following ProductionMarketReader.current()
and .book() may add a quote. This file is not a discovery feed, catalog proof,
full-universe capture, order gate, or proof of a future provider session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any

from rc6_dynamic_universe.sources import _canonical_identity


SCHEMA = "rc6.ppi-scanner-primary-receipt.v1"
PRODUCER = "bf_production_paper_observer._read_scanner_quote"
ORIGIN = "PPI_READONLY_SDK_CURRENT_AND_BOOK"
FILENAME = "rc6_ppi_scanner_primary_latest.json"
# The immutable observer has a read-only rootfs and a persistent data mount.
# Production mounts host /opt/porota-trading/data at container DATA_DIR=/app/data.
DEFAULT_PATH = Path(os.getenv("DATA_DIR", "/app/data")) / "market" / FILENAME
MAX_RECORDS = 1024
MAX_BYTES = 2 * 1024 * 1024
# Preserve after-hours diagnostics without making old values fresh. The two
# consuming pipelines independently apply their own native-clock freshness.
RETENTION_SECONDS = 48 * 3600
# The IOL scanner primary reader already required both native clocks within
# 300 seconds. Share that stricter bound with the scanner and ledger so a
# stale book cannot become PAPER-ready through another consumer.
NATIVE_QUOTE_MAX_AGE_SECONDS = 300

# A556: private, append-preserving, integrity-checked native PPI Book evidence.
# This is a finite capture budget, *not* retention or authority to delete. Once
# it is full a separate governed archival decision is required (issue #478).
PIT_JOURNAL_SCHEMA = "rc6.ppi-native-book-causal-journal.v1"
PIT_JOURNAL_FILENAME = "rc6_ppi_native_book_causal_private.json"
PIT_JOURNAL_MAX_RECORDS = 4096
PIT_JOURNAL_MAX_BYTES = 8 * 1024 * 1024
PIT_SESSION_MAX_RECORDS = 1024
PIT_SESSION_MAX_BYTES = 2 * 1024 * 1024
PIT_EVENT_MAX_BYTES = 32 * 1024
PIT_BOOK_MAX_DEPTH_PER_SIDE = 64


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return result.astimezone(timezone.utc) if result.tzinfo and result.utcoffset() is not None else None


def _key(row: dict[str, Any]) -> tuple[str, ...] | None:
    try:
        return tuple(_canonical_identity(row))
    except (TypeError, ValueError):
        return None


def _positive_last(value: Any) -> bool:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return amount.is_finite() and amount > 0


def native_trade_book_ready(row: dict[str, Any], current: datetime, *,
                            receipt: Any = None, require_trade_kind: bool = False) -> bool:
    """Pure PPI selection prerequisite, never an execution authority.

    Native TRADE and BOOK clocks are independently recent at the consumer's
    cutoff. A publication/receipt clock cannot stand in for either one.
    """
    if current.tzinfo is None or current.utcoffset() is None:
        return False
    received = _aware(row.get("received_at") if receipt is None else receipt)
    trade_at = _aware(row.get("trade_at"))
    declared_source_at = row.get("source_at")
    source_at = _aware(declared_source_at)
    source_consistent = bool(source_at == trade_at if declared_source_at not in (None, "")
                             else not require_trade_kind)
    book_at = _aware(row.get("book_at"))
    at = current.astimezone(timezone.utc)
    kind = row.get("last_kind")
    return bool(received and trade_at and source_consistent and book_at
                and (kind == "TRADE" if require_trade_kind else kind in (None, "", "TRADE"))
                and row.get("review_status") in (None, "", "NO_CURRENT_CONFLICT")
                and _positive_last(row.get("last"))
                and trade_at <= received <= at and book_at <= received
                and 0 <= (at - trade_at).total_seconds() <= NATIVE_QUOTE_MAX_AGE_SECONDS
                and 0 <= (at - book_at).total_seconds() <= NATIVE_QUOTE_MAX_AGE_SECONDS)


def _safe_read(path: Path) -> dict[str, Any] | None:
    """Never traverse a final symlink or consume an unbounded, changing file."""
    if not path.is_absolute():
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size < 2 or before.st_size > MAX_BYTES):
                return None
            with os.fdopen(os.dup(fd), "rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(fd)
            if (len(raw) != before.st_size or len(raw) > MAX_BYTES
                    or (before.st_ino, before.st_mtime_ns, before.st_size)
                    != (after.st_ino, after.st_mtime_ns, after.st_size)):
                return None
        finally:
            os.close(fd)
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def receipt_path(root: Path) -> Path:
    # Both readers use their already configured market-data root. There is no
    # independent override whose differing path could silently turn a valid
    # scanner receipt into PPI.present=0 on the host.
    return Path(root) / FILENAME


def primary_records(root: Path, current: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load exact scanner assertions, never synthesize PPI from catalog tags.

    Observations without native trade/book time remain visible for diagnosis
    only, with state=UNAVAILABLE. They cannot enable PAPER admission.
    """
    path = receipt_path(root)
    value = _safe_read(path)
    unavailable = {"state": "UNAVAILABLE", "schema": SCHEMA, "producer": PRODUCER,
                   "provider_available": None, "source_scope": "SCANNER_ROTATED_SUBSET_ONLY"}
    if (not value or value.get("schema") != SCHEMA or value.get("producer") != PRODUCER
            or value.get("provider_origin") != ORIGIN):
        reason = ("SCANNER_PRIMARY_RECEIPT_NOT_PUBLISHED" if not path.exists()
                  else "SCANNER_PRIMARY_RECEIPT_INVALID_OR_UNREADABLE")
        return [], {**unavailable, "reason": reason}
    rows = value.get("records")
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        return [], {**unavailable, "reason": "SCANNER_PRIMARY_RECEIPT_RECORDS_INVALID"}
    accepted, rejected = [], 0
    at = current.astimezone(timezone.utc)
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("provider_origin") != ORIGIN:
            rejected += 1
            continue
        key = _key(raw)
        received = _aware(raw.get("received_at"))
        if (not key or list(key) != raw.get("canonical_identity") or not received
                or received > at or (at - received).total_seconds() > RETENTION_SECONDS):
            rejected += 1
            continue
        # The native quote/event time is never replaced with the receipt or
        # atomic publication time. An invalid provider clock stays invalid.
        ready = bool(raw.get("state") == "READY"
                     and native_trade_book_ready(raw, at, require_trade_kind=True))
        row = dict(raw)
        row["source"] = "PPI"  # trusted scanner call-site provenance only
        row["state"] = "READY" if ready else "UNAVAILABLE"
        accepted.append(row)
    eligible_count = sum(row["state"] == "READY" for row in accepted)
    return accepted, {"state": "READY" if eligible_count else "UNAVAILABLE", "schema": SCHEMA,
                       "source": "PPI_SCANNER_RECEIPT", "source_scope": "SCANNER_ROTATED_SUBSET_ONLY",
                       "provider_clock_basis": "NATIVE_TRADE_AND_BOOK_PER_ROW",
                       "capture_clock_basis": "RECEIPT_NOT_PROVIDER_CLOCK", "provider_available": None,
                       "accepted_records": len(accepted), "eligible_records": eligible_count,
                       "rejected_records": rejected,
                       "native_unavailable_records": len(accepted) - eligible_count,
                       "reason": "OK" if eligible_count else "SCANNER_PRIMARY_RECEIPT_NO_ELIGIBLE_RECORDS"}


class ScannerPrimaryRecorder:
    """In-memory per-session, per-five-key aggregator; one atomic write/cycle."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.records: dict[tuple[str, ...], dict[str, Any]] = {}
        self.invalid_identity = 0
        self.invalid_receipt_clock = 0
        self.capture_count = 0

    def capture_raw_book(self, quote: Any, *, raw_book: dict[str, Any],
                         request: tuple[str, str, str], identity: tuple[str, ...],
                         observed_at: str) -> dict[str, Any]:
        """Persist raw-depth PPI Book already read by the trusted scanner.

        The observer call site is an A550/A556 *separate* integration gate.
        No provider call, catalog lookup, unit inference, order, or publish log.
        """
        return capture_native_book_journal(
            self.path.with_name(PIT_JOURNAL_FILENAME), quote,
            raw_book=raw_book, request=request, identity=identity,
            observed_at=observed_at)

    def capture(self, quote: Any, *, request: tuple[str, str, str]) -> None:
        if (quote.symbol, quote.asset_class, quote.settlement) != tuple(request):
            self.invalid_identity += 1
            return
        try:
            # A catalog-derived monetary identity cannot contradict the
            # quote's own bound contract, even when all five strings exist.
            quote.monetary_identity()
        except (TypeError, ValueError):
            self.invalid_identity += 1
            return
        row = {"symbol": str(quote.symbol), "asset_class": str(quote.asset_class),
               "settlement": str(quote.settlement), "market": quote.market,
               "currency": quote.currency, "last": str(quote.last),
               "bid": str(quote.bid), "ask": str(quote.ask),
               "bid_size": str(quote.bid_size), "ask_size": str(quote.ask_size),
               "trade_at": quote.trade_at, "book_at": quote.book_at,
               "source_at": quote.trade_at, "received_at": quote.observed_at,
               "last_kind": quote.last_kind, "provider_origin": ORIGIN,
               "source_path": "PPI:SCANNER_CURRENT_AND_BOOK"}
        key = _key(row)
        if not key:
            self.invalid_identity += 1
            return
        received = _aware(quote.observed_at)
        if not received:
            self.invalid_receipt_clock += 1
            return
        valid = native_trade_book_ready(row, received, require_trade_kind=True)
        row["canonical_identity"] = list(key)
        row["state"] = "READY" if valid else "UNAVAILABLE"
        previous = self.records.get(key)
        previous_received = _aware(previous.get("received_at")) if previous else None
        if previous_received and previous_received > received:
            return
        if (previous_received == received and previous is not None
                and any(previous.get(field) != row.get(field)
                        for field in ("last", "bid", "ask", "trade_at", "book_at", "state"))):
            row["review_status"] = "CONFLICT_REVIEW_REQUIRED"
            row["state"] = "UNAVAILABLE"
        self.records[key] = row
        self.capture_count += 1

    def publish(self, *, current: datetime | None = None) -> dict[str, Any]:
        now = (current or datetime.now(timezone.utc)).astimezone(timezone.utc)
        alive = {key: row for key, row in self.records.items()
                 if (received := _aware(row.get("received_at"))) is not None
                 and 0 <= (now - received).total_seconds() <= RETENTION_SECONDS}
        chosen = sorted(alive.items(), key=lambda pair: (pair[1]["received_at"], pair[0]), reverse=True)[:MAX_RECORDS]
        self.records = dict(chosen)
        payload = {"schema": SCHEMA, "producer": PRODUCER, "provider_origin": ORIGIN,
                   "source_scope": "SCANNER_ROTATED_SUBSET_ONLY",
                   "captured_at": now.isoformat(), "records": [row for _, row in chosen],
                   "record_count": len(chosen), "cycle_capture_count": self.capture_count,
                   "native_unavailable_records": sum(row["state"] != "READY" for _, row in chosen),
                   "invalid_identity": self.invalid_identity,
                   "invalid_receipt_clock": self.invalid_receipt_clock}
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("SCANNER_PRIMARY_RECEIPT_SIZE_LIMIT")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".ppi-scanner-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                # The packaged host source-cycle unit has no User= override;
                # the observer writer uses botuser (UID 1000). A deployment
                # must recheck host overrides before consuming this receipt.
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.capture_count = 0
        return {"status": "PUBLISHED", "records": len(chosen),
                "invalid_identity": self.invalid_identity,
                "invalid_receipt_clock": self.invalid_receipt_clock}


def _pit_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _pit_hash(value: Any) -> str:
    return hashlib.sha256(_pit_bytes(value)).hexdigest()


def _pit_decimal(value: Any, *, positive: bool) -> str:
    if isinstance(value, bool):
        raise ValueError("RAW_BOOK_NUMERIC_INVALID")
    try:
        number = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError("RAW_BOOK_NUMERIC_INVALID") from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError("RAW_BOOK_NUMERIC_INVALID")
    return format(number, "f")


def _pit_depth(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > PIT_BOOK_MAX_DEPTH_PER_SIDE:
        raise ValueError("RAW_BOOK_DEPTH_INVALID_OR_CAPACITY_HOLD")
    result = []
    positions = set()
    for original in raw:
        if not isinstance(original, dict) or "price" not in original or "quantity" not in original:
            raise ValueError("RAW_BOOK_LEVEL_CONTRACT_INVALID")
        position = original.get("position")
        if position is not None:
            if (type(position) is not int or not 1 <= position <= PIT_BOOK_MAX_DEPTH_PER_SIDE
                    or position in positions):
                raise ValueError("RAW_BOOK_LEVEL_POSITION_INVALID")
            positions.add(position)
        result.append({"position": position, "price": _pit_decimal(original["price"], positive=True),
                       "quantity": _pit_decimal(original["quantity"], positive=False)})
    return result


def _pit_event(quote: Any, *, raw_book: dict[str, Any], request: tuple[str, str, str],
               identity: tuple[str, ...], observed_at: str) -> dict[str, Any]:
    if ((quote.symbol, quote.asset_class, quote.settlement) != tuple(request)
            or observed_at != quote.observed_at):
        raise ValueError("PPI_BOOK_REQUEST_OR_RECEIPT_IDENTITY_MISMATCH")
    try:
        quote.monetary_identity()
    except (TypeError, ValueError) as exc:
        raise ValueError("PPI_BOOK_MONETARY_IDENTITY_INVALID") from exc
    row = {"symbol": str(quote.symbol), "asset_class": str(quote.asset_class),
           "market": quote.market, "currency": quote.currency,
           "settlement": str(quote.settlement)}
    key = _key(row)
    if (not key or type(identity) not in (tuple, list) or tuple(identity) != key):
        raise ValueError("PPI_BOOK_FIVE_KEY_MISMATCH")
    if (not isinstance(raw_book, dict) or not isinstance(raw_book.get("date"), str)
            or "bids" not in raw_book or "offers" not in raw_book):
        raise ValueError("PPI_BOOK_NATIVE_SHAPE_UNVERIFIED")
    provider_book = _aware(raw_book["date"])
    native_book = _aware(quote.book_at)
    native_trade = _aware(quote.trade_at)
    received = _aware(observed_at)
    if (not provider_book or not native_book or not native_trade or not received
            or provider_book != native_book or native_book > received or native_trade > received):
        raise ValueError("PPI_BOOK_NATIVE_PIT_CLOCK_INVALID")
    # Preserve every provider depth level. Missing units remain explicitly
    # UNVERIFIED; the specialized execution engine must never assume nominal.
    bids = _pit_depth(raw_book["bids"])
    offers = _pit_depth(raw_book["offers"])
    if not bids and not offers:
        raise ValueError("PPI_BOOK_NO_DEPTH")
    book = {"native_date": raw_book["date"], "bids": bids, "offers": offers}
    if len(_pit_bytes(book)) > PIT_EVENT_MAX_BYTES:
        raise ValueError("PPI_BOOK_EVENT_CAPACITY_HOLD")
    event = {**row, "canonical_identity": list(key), "source": "PPI",
            "provider_origin": ORIGIN, "native_book_at": quote.book_at,
            "native_trade_at": quote.trade_at, "observed_at": observed_at,
            "known_at": observed_at, "session_art": received.astimezone(
                timezone(timedelta(hours=-3))).date().isoformat(),
            "book": book, "raw_book_sha256": _pit_hash(book),
            "quantity_unit": "NO_VERIFICADO", "price_unit": "NO_VERIFICADO",
            "depth_levels": {"bids": len(bids), "offers": len(offers)}}
    if len(_pit_bytes(event)) > PIT_EVENT_MAX_BYTES:
        raise ValueError("PPI_BOOK_EVENT_CAPACITY_HOLD")
    return event


def _pit_verified_rows(doc: Any) -> list[dict[str, Any]]:
    if (not isinstance(doc, dict) or doc.get("schema") != PIT_JOURNAL_SCHEMA
            or doc.get("producer") != PRODUCER or doc.get("provider_origin") != ORIGIN
            or not isinstance(doc.get("records"), list)
            or len(doc["records"]) > PIT_JOURNAL_MAX_RECORDS
            or doc.get("record_count") != len(doc["records"])):
        raise ValueError("PPI_BOOK_JOURNAL_SCHEMA_HOLD")
    prior_hash = None
    seen = set()
    latest_per_identity: dict[tuple[str, ...], tuple[datetime, datetime]] = {}
    for sequence, event in enumerate(doc["records"], start=1):
        if not isinstance(event, dict):
            raise ValueError("PPI_BOOK_JOURNAL_EVENT_INVALID")
        expected = _pit_hash({k: v for k, v in event.items() if k != "event_sha256"})
        if (event.get("sequence") != sequence or event.get("previous_sha256") != prior_hash
                or event.get("event_sha256") != expected
                or event.get("provider_origin") != ORIGIN or event.get("source") != "PPI"
                or _key(event) != tuple(event.get("canonical_identity") or ())
                or event.get("known_at") != event.get("observed_at")
                or not (_aware(event.get("native_book_at")) and _aware(event.get("native_trade_at"))
                         and _aware(event.get("observed_at")))
                or _aware(event["native_book_at"]) > _aware(event["observed_at"])
                or _aware(event["native_trade_at"]) > _aware(event["observed_at"])
                or not isinstance(event.get("book"), dict)
                or event.get("raw_book_sha256") != _pit_hash(event["book"])
                or event.get("quantity_unit") != "NO_VERIFICADO"
                or event.get("price_unit") != "NO_VERIFICADO"):
            raise ValueError("PPI_BOOK_JOURNAL_CHAIN_OR_IDENTITY_HOLD")
        book = event["book"]
        try:
            bids, offers = _pit_depth(book["bids"]), _pit_depth(book["offers"])
        except (ValueError, TypeError, KeyError):
            raise ValueError("PPI_BOOK_JOURNAL_DEPTH_INVALID") from None
        if (not bids and not offers or bids != book["bids"] or offers != book["offers"]
                or _aware(book.get("native_date")) != _aware(event["native_book_at"])
                or event.get("depth_levels") != {"bids": len(bids), "offers": len(offers)}):
            raise ValueError("PPI_BOOK_JOURNAL_DEPTH_INVALID")
        clock = (tuple(event["canonical_identity"]), _aware(event["native_book_at"]))
        if clock in seen:
            raise ValueError("PPI_BOOK_JOURNAL_DUPLICATE_CLOCK_HOLD")
        seen.add(clock)
        key = tuple(event["canonical_identity"])
        book_clock, known_clock = _aware(event["native_book_at"]), _aware(event["known_at"])
        previous = latest_per_identity.get(key)
        if ((previous and (book_clock <= previous[0] or known_clock <= previous[1]))
                or event.get("session_art") != known_clock.astimezone(
                    timezone(timedelta(hours=-3))).date().isoformat()):
            raise ValueError("PPI_BOOK_JOURNAL_CAUSAL_ORDER_HOLD")
        latest_per_identity[key] = (book_clock, known_clock)
        prior_hash = expected
    return doc["records"]


def _pit_read_private(path: Path) -> dict[str, Any] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o077 or before.st_size > PIT_JOURNAL_MAX_BYTES):
            raise ValueError("PPI_BOOK_JOURNAL_PERMISSION_OR_CAPACITY_HOLD")
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(PIT_JOURNAL_MAX_BYTES + 1)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or len(raw) > PIT_JOURNAL_MAX_BYTES
                or (before.st_ino, before.st_mtime_ns, before.st_size)
                != (after.st_ino, after.st_mtime_ns, after.st_size)):
            raise ValueError("PPI_BOOK_JOURNAL_CHANGED_OR_CAPACITY_HOLD")
    finally:
        os.close(fd)
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("PPI_BOOK_JOURNAL_UNREADABLE_HOLD") from None
    _pit_verified_rows(result)
    return result


@contextmanager
def _pit_exclusive_lock(path: Path):
    if not path.is_absolute():
        raise ValueError("PPI_BOOK_JOURNAL_PATH_NOT_ABSOLUTE")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(path.parent.lstat().st_mode):
        raise ValueError("PPI_BOOK_JOURNAL_DIRECTORY_UNTRUSTED")
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError("PPI_BOOK_JOURNAL_LOCK_UNTRUSTED")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def capture_native_book_journal(path: Path, quote: Any, *, raw_book: dict[str, Any],
                                request: tuple[str, str, str], identity: tuple[str, ...],
                                observed_at: str) -> dict[str, Any]:
    """Bounded, private, atomic, lock-held PIT evidence; never authorizes fills."""
    try:
        event = _pit_event(quote, raw_book=raw_book, request=request,
                           identity=identity, observed_at=observed_at)
    except (ValueError, TypeError, AttributeError) as exc:
        return {"status": "CAPTURE_INVALID_HOLD", "reason": str(exc)}
    try:
        with _pit_exclusive_lock(Path(path)):
            doc = _pit_read_private(Path(path))
            if doc is None:
                doc = {"schema": PIT_JOURNAL_SCHEMA, "producer": PRODUCER,
                       "provider_origin": ORIGIN, "records": [], "record_count": 0}
            rows = doc["records"]
            native_at = _aware(event["native_book_at"])
            key = tuple(event["canonical_identity"])
            for previous in reversed(rows):
                if tuple(previous["canonical_identity"]) != key:
                    continue
                previous_at = _aware(previous["native_book_at"])
                if previous_at == native_at:
                    if previous["raw_book_sha256"] == event["raw_book_sha256"]:
                        return {"status": "DEDUPLICATED", "records": len(rows),
                                "event_sha256": previous["event_sha256"]}
                    return {"status": "CAPTURE_INVALID_HOLD", "reason": "PPI_BOOK_NATIVE_CLOCK_CONFLICT"}
                if native_at < previous_at or _aware(event["known_at"]) <= _aware(previous["known_at"]):
                    return {"status": "CAPTURE_INVALID_HOLD", "reason": "PPI_BOOK_CLOCK_REGRESSION_OR_AMBIGUOUS_RECEIPT"}
                break
            session = event["session_art"]
            same_session = [row for row in rows if row["session_art"] == session]
            if (len(rows) >= PIT_JOURNAL_MAX_RECORDS
                    or len(same_session) >= PIT_SESSION_MAX_RECORDS
                    or sum(len(_pit_bytes(row)) for row in same_session) + len(_pit_bytes(event)) > PIT_SESSION_MAX_BYTES):
                return {"status": "CAPTURE_CAPACITY_HOLD", "reason": "PPI_BOOK_ROWS_OR_SESSION_BYTES_BUDGET"}
            event["sequence"] = len(rows) + 1
            event["previous_sha256"] = rows[-1]["event_sha256"] if rows else None
            event["event_sha256"] = _pit_hash(event)
            candidate = {**doc, "records": [*rows, event], "record_count": len(rows) + 1}
            encoded = _pit_bytes(candidate)
            if len(encoded) > PIT_JOURNAL_MAX_BYTES:
                return {"status": "CAPTURE_CAPACITY_HOLD", "reason": "PPI_BOOK_TOTAL_BYTES_BUDGET"}
            fd, temporary = tempfile.mkstemp(prefix=".ppi-book-pit-", dir=Path(path).parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                parentfd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(parentfd)
                finally:
                    os.close(parentfd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return {"status": "CAPTURED", "records": len(rows) + 1,
                    "event_sha256": event["event_sha256"]}
    except (OSError, ValueError) as exc:
        # Error codes only; never return confidential levels or provider body.
        return {"status": "CAPTURE_INTEGRITY_HOLD", "reason": (
            str(exc) if isinstance(exc, ValueError) else "PPI_BOOK_JOURNAL_IO_HOLD")}


def verified_native_book_journal(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read-only verified chain for OFFHOST audit; no fill or READY assertion."""
    try:
        doc = _pit_read_private(Path(path))
    except (OSError, ValueError) as exc:
        return [], {"state": "HOLD", "reason": (
            str(exc) if isinstance(exc, ValueError) else "PPI_BOOK_JOURNAL_IO_HOLD")}
    if doc is None:
        return [], {"state": "NO_VERIFICADO", "reason": "PPI_BOOK_JOURNAL_ABSENT"}
    return doc["records"], {"state": "HASH_CHAIN_VERIFIED", "records": doc["record_count"],
                            "latest_sha256": doc["records"][-1]["event_sha256"] if doc["records"] else None}


def strictly_future_native_books(path: Path, *, identity: tuple[str, ...], decision_at: str,
                                 latency_seconds: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Only nominate post-decision native books, NEVER infer a fill/contract."""
    decision = _aware(decision_at)
    if (not decision or isinstance(latency_seconds, bool)
            or not isinstance(latency_seconds, (int, float))
            or not 0 <= latency_seconds <= 300):
        return [], {"state": "HOLD", "reason": "PPI_BOOK_DECISION_OR_LATENCY_INVALID"}
    rows, receipt = verified_native_book_journal(path)
    if receipt["state"] != "HASH_CHAIN_VERIFIED":
        return [], receipt
    matching = [row for row in rows if tuple(row["canonical_identity"]) == tuple(identity)
                and _aware(row["native_book_at"]) > decision + timedelta(seconds=latency_seconds)
                and _aware(row["known_at"]) > decision]
    return matching, {**receipt, "eligible_future_books": len(matching),
                      "fill_accredited": False, "quantity_unit": "NO_VERIFICADO"}
