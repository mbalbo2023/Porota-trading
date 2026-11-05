"""Private, bounded receipt for PPI Current+Book already read by the scanner.

This is a transport/capture assertion by the trusted observer call site, not
a broker authorization, new provider call, raw PIT journal or full-market feed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
DEFAULT_PATH = Path(os.getenv("DATA_DIR", "/app/data")) / "market" / FILENAME
MAX_RECORDS = 1024
MAX_BYTES = 2 * 1024 * 1024
RETENTION_SECONDS = 48 * 3600  # Diagnostic lifetime, never trading freshness.
NATIVE_QUOTE_MAX_AGE_SECONDS = 300


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if instant.tzinfo is None or instant.utcoffset() is None:
        return None
    return instant.astimezone(timezone.utc)


def _key(row: dict[str, Any]) -> tuple[str, ...] | None:
    try:
        return tuple(_canonical_identity(row))
    except (TypeError, ValueError):
        return None


def native_trade_book_ready(row: dict[str, Any], current: datetime, *,
                            require_trade_kind: bool = True) -> bool:
    """Native TRADE and BOOK clocks must independently be recent and causal."""
    if current.tzinfo is None or current.utcoffset() is None:
        return False
    received, trade, book = (_aware(row.get(field)) for field in
                             ("received_at", "trade_at", "book_at"))
    now = current.astimezone(timezone.utc)
    try:
        last = Decimal(str(row.get("last")))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return bool(
        received and trade and book and last.is_finite() and last > 0
        and (row.get("last_kind") == "TRADE" if require_trade_kind
             else row.get("last_kind") in (None, "", "TRADE"))
        and row.get("review_status") in (None, "", "NO_CURRENT_CONFLICT")
        and row.get("source_at") == row.get("trade_at")
        and trade <= received <= now and book <= received
        and 0 <= (now - trade).total_seconds() <= NATIVE_QUOTE_MAX_AGE_SECONDS
        and 0 <= (now - book).total_seconds() <= NATIVE_QUOTE_MAX_AGE_SECONDS
    )


def receipt_path(root: Path) -> Path:
    return Path(root) / FILENAME


def _safe_read(path: Path) -> dict[str, Any] | None:
    """Never follow the final file or parent symlink or read an oversized file."""
    if not path.is_absolute():
        return None
    parent_fd = file_fd = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        file_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o077 or not 2 <= before.st_size <= MAX_BYTES):
            return None
        with os.fdopen(os.dup(file_fd), "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(file_fd)
        if (len(raw) != before.st_size or len(raw) > MAX_BYTES
                or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size)
                != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size)):
            return None
        result = json.loads(raw.decode("utf-8"))
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if parent_fd is not None:
            os.close(parent_fd)


def primary_records(root: Path, current: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read trustworthy scanner assertions; invalid native time stays UNAVAILABLE."""
    path = receipt_path(root)
    unavailable = {"state": "UNAVAILABLE", "schema": SCHEMA, "producer": PRODUCER,
                   "source_scope": "SCANNER_ROTATED_SUBSET_ONLY", "provider_available": None}
    if current.tzinfo is None or current.utcoffset() is None:
        return [], {**unavailable, "reason": "CONSUMER_CLOCK_INVALID"}
    value = _safe_read(path)
    if not value:
        reason = ("SCANNER_PRIMARY_RECEIPT_NOT_PUBLISHED" if not path.exists()
                  else "SCANNER_PRIMARY_RECEIPT_INVALID_OR_UNREADABLE")
        return [], {**unavailable, "reason": reason}
    rows = value.get("records")
    captured = _aware(value.get("captured_at"))
    now = current.astimezone(timezone.utc)
    if (value.get("schema") != SCHEMA or value.get("producer") != PRODUCER
            or value.get("provider_origin") != ORIGIN or not isinstance(rows, list)
            or len(rows) > MAX_RECORDS or value.get("record_count") != len(rows)
            or not captured or captured > now):
        return [], {**unavailable, "reason": "SCANNER_PRIMARY_RECEIPT_SCHEMA_OR_CLOCK_INVALID"}
    accepted, rejected, seen = [], 0, set()
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("provider_origin") != ORIGIN:
            rejected += 1
            continue
        key, received = _key(raw), _aware(raw.get("received_at"))
        if (not key or list(key) != raw.get("canonical_identity") or not received
                or received > captured or received > now
                or (now - received).total_seconds() > RETENTION_SECONDS):
            rejected += 1
            continue
        if key in seen:
            return [], {**unavailable, "reason": "SCANNER_PRIMARY_RECEIPT_DUPLICATE_IDENTITY"}
        seen.add(key)
        ready = bool(raw.get("catalog_identity_confirmed") is True
                     and raw.get("state") == "READY" and native_trade_book_ready(raw, now))
        row = dict(raw)
        row["source"] = "PPI"  # Call-site assertion, not a wire-level signature.
        row["state"] = "READY" if ready else "UNAVAILABLE"
        accepted.append(row)
    eligible = sum(row["state"] == "READY" for row in accepted)
    return accepted, {"state": "READY" if eligible else "UNAVAILABLE", "schema": SCHEMA,
                      "source": "PPI_SCANNER_RECEIPT", "source_scope": "SCANNER_ROTATED_SUBSET_ONLY",
                      "provider_clock_basis": "NATIVE_TRADE_AND_BOOK_PER_ROW",
                      "capture_clock_basis": "RECEIPT_NOT_PROVIDER_CLOCK",
                      "provider_available": None, "accepted_records": len(accepted),
                      "eligible_records": eligible, "rejected_records": rejected,
                      "native_unavailable_records": len(accepted) - eligible,
                      "reason": "OK" if eligible else "SCANNER_PRIMARY_RECEIPT_NO_ELIGIBLE_RECORDS"}


class ScannerPrimaryRecorder:
    """One in-memory five-key batch, written atomically once per scanner cycle."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.records: dict[tuple[str, ...], dict[str, Any]] = {}
        self.capture_count = 0
        self.invalid_identity = 0
        self.invalid_receipt_clock = 0

    def capture(self, quote: Any, *, request: tuple[str, str, str]) -> None:
        if (quote.symbol, quote.asset_class, quote.settlement) != tuple(request):
            self.invalid_identity += 1
            return
        try:
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
        # Current+Book attests transport for a 3-part SDK request. Market and
        # currency come from the catalog, not from the wire response. A PAPER
        # opening hold (including unverified PPI identity) must never turn
        # that locally enriched 5-part key into eligible source evidence.
        catalog_bound = str(getattr(quote, "metadata_source", "") or "").startswith("PPI_CATALOG:")
        opening_hold = bool(str(getattr(quote, "opening_block_reason", "") or "").strip())
        row["catalog_identity_confirmed"] = bool(catalog_bound and not opening_hold)
        key, received = _key(row), _aware(quote.observed_at)
        if not key:
            self.invalid_identity += 1
            return
        if not received:
            self.invalid_receipt_clock += 1
            return
        row["canonical_identity"] = list(key)
        row["state"] = ("READY" if row["catalog_identity_confirmed"]
                        and native_trade_book_ready(row, received)
                        else "UNAVAILABLE")
        previous = self.records.get(key)
        if previous:
            previous_received = _aware(previous.get("received_at"))
            if previous_received and previous_received > received:
                return
            if previous_received == received and (
                previous.get("review_status") == "CONFLICT_REVIEW_REQUIRED"
                or any(previous.get(field) != row.get(field) for field in
                       ("last", "bid", "ask", "trade_at", "book_at", "state"))
            ):
                row["review_status"] = "CONFLICT_REVIEW_REQUIRED"
                row["state"] = "UNAVAILABLE"
        self.records[key] = row
        self.capture_count += 1

    def publish(self, *, current: datetime | None = None) -> dict[str, Any]:
        now = current or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("SCANNER_PRIMARY_RECEIPT_CLOCK_INVALID")
        now = now.astimezone(timezone.utc)
        alive = {key: row for key, row in self.records.items()
                 if (received := _aware(row.get("received_at"))) is not None
                 and 0 <= (now - received).total_seconds() <= RETENTION_SECONDS}
        chosen = sorted(alive.items(), key=lambda pair: (pair[1]["received_at"], pair[0]),
                        reverse=True)[:MAX_RECORDS]
        payload = {"schema": SCHEMA, "producer": PRODUCER, "provider_origin": ORIGIN,
                   "source_scope": "SCANNER_ROTATED_SUBSET_ONLY",
                   "captured_at": now.isoformat(), "records": [row for _, row in chosen],
                   "record_count": len(chosen), "cycle_capture_count": self.capture_count,
                   "overflow_records": max(0, len(alive) - len(chosen)),
                   "native_unavailable_records": sum(row["state"] != "READY" for _, row in chosen),
                   "invalid_identity": self.invalid_identity,
                   "invalid_receipt_clock": self.invalid_receipt_clock}
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > MAX_BYTES:
            raise ValueError("SCANNER_PRIMARY_RECEIPT_SIZE_LIMIT")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.is_absolute() or self.path.parent.is_symlink():
            raise ValueError("SCANNER_PRIMARY_RECEIPT_PATH_INVALID")
        try:
            prior = os.lstat(self.path)
        except FileNotFoundError:
            pass
        else:
            if (not stat.S_ISREG(prior.st_mode) or prior.st_nlink != 1
                    or prior.st_uid != os.geteuid()):
                raise ValueError("SCANNER_PRIMARY_RECEIPT_TARGET_UNSAFE")
        fd, temporary = tempfile.mkstemp(prefix=".ppi-scanner-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            parent_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.records = dict(chosen)
        result = {"status": "PUBLISHED", "records": len(chosen),
                  "overflow_records": payload["overflow_records"],
                  "invalid_identity": self.invalid_identity,
                  "invalid_receipt_clock": self.invalid_receipt_clock}
        self.capture_count = self.invalid_identity = self.invalid_receipt_clock = 0
        return result
