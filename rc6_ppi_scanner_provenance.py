"""Bounded native PPI scanner receipt; separate PAPER mode from quote origin.

Only the observer call site immediately following ProductionMarketReader.current()
and .book() may add a quote. This file is not a discovery feed, catalog proof,
full-universe capture, order gate, or proof of a future provider session.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
        ready = (raw.get("state") == "READY" and _aware(raw.get("source_at"))
                 and _aware(raw.get("book_at")) and _aware(raw.get("source_at")) <= received
                 and _aware(raw.get("book_at")) <= received)
        row = dict(raw)
        row["source"] = "PPI"  # trusted scanner call-site provenance only
        row["state"] = "READY" if ready else "UNAVAILABLE"
        accepted.append(row)
    return accepted, {"state": "READY" if accepted else "UNAVAILABLE", "schema": SCHEMA,
                       "source": "PPI_SCANNER_RECEIPT", "source_scope": "SCANNER_ROTATED_SUBSET_ONLY",
                       "provider_clock_basis": "NATIVE_TRADE_AND_BOOK_PER_ROW",
                       "capture_clock_basis": "RECEIPT_NOT_PROVIDER_CLOCK", "provider_available": None,
                       "accepted_records": len(accepted), "rejected_records": rejected,
                       "native_unavailable_records": sum(row["state"] != "READY" for row in accepted),
                       "reason": "OK" if accepted else "SCANNER_PRIMARY_RECEIPT_NO_ELIGIBLE_RECORDS"}


class ScannerPrimaryRecorder:
    """In-memory per-session, per-five-key aggregator; one atomic write/cycle."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.records: dict[tuple[str, ...], dict[str, Any]] = {}
        self.invalid_identity = 0
        self.invalid_receipt_clock = 0
        self.capture_count = 0

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
        native_trade, native_book = _aware(quote.trade_at), _aware(quote.book_at)
        valid = bool(native_trade and native_book and native_trade <= received
                     and native_book <= received and quote.last_kind == "TRADE"
                     and quote.last > 0)
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
                os.fchmod(stream.fileno(), 0o644)
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
