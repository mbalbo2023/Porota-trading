"""Bounded, read-only PAPER obligation witness for a controlled RC6 pause.

This module does not stop a process or close a PAPER position.  A deployment
may adopt a stopped runtime only after an independent, durable stop receipt
binds this witness to both cleanly stopped containers and held RC6 timers.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from bs_instrument_contracts import CASH_CURRENCIES, aware_datetime
from bt_caucion_paper import pending_proceeds, validate_position
from cd_spot_ledger import partition


SCHEMA = "rc6.operator-paper-obligations.v2"
MAX_ROWS_PER_TABLE = 10_000
MAX_STREAM_EVENTS = 700_000
MAX_TOTAL_ROWS = 800_000
MAX_CANONICAL_BYTES = 256 * 1024 * 1024
MAX_RETAINED_BYTES = 16 * 1024 * 1024
MAX_ROW_BYTES = 64 * 1024
READ_DEADLINE_SECONDS = 20
FETCH_BATCH = 256
TABLES = {
    "paper_positions": ("paper_id", {"paper_id", "status", "currency", "settlement"}),
    "paper_fills": ("id", {"id", "paper_id", "side"}),
    "paper_admin_cancellations": ("paper_id", {"paper_id", "authority", "cancelled_at"}),
    "paper_spot_sales": ("fill_id", {"fill_id", "paper_id", "available_at", "basis"}),
    "paper_sale_receivables": ("paper_id", {"paper_id", "currency", "available_at", "basis"}),
    "paper_cauciones": ("paper_id", {"paper_id", "status", "currency"}),
    "paper_caucion_allocations": ("request_id", {"request_id", "paper_id", "request_hash"}),
    "paper_family_lifecycle": ("lifecycle_id", {"lifecycle_id", "family", "state", "currency"}),
    "paper_family_lifecycle_events": ("event_id", {"event_id", "lifecycle_id", "family"}),
    "paper_future_positions": ("lifecycle_id", {"lifecycle_id", "status", "currency"}),
    "paper_future_marks": ("event_id", {"event_id", "lifecycle_id"}),
    "paper_exit_intents": ("paper_id", {"paper_id", "state", "cause"}),
    "paper_future_exit_intents": ("lifecycle_id", {"lifecycle_id", "state", "cause"}),
    "paper_events": ("id", {"id", "paper_id", "event_type", "event_at"}),
    "paper_learning_samples": ("paper_id", {"paper_id", "label_timestamp"}),
    "paper_notification_outbox": ("id", {"id", "event_key", "kind", "body", "state"}),
}
OUTBOX_STATIC = ("id", "event_key", "kind", "body", "priority", "created_at")
OUTBOX_STATES = {"PENDING", "SENDING", "SENT", "DEAD", "SUPPRESSED_WEEKEND"}


def _stamp(value):
    at = aware_datetime(value).astimezone(timezone.utc)
    if at > datetime.now(timezone.utc):
        raise ValueError("PAPER_PAUSE_FUTURE_AS_OF")
    return at.isoformat(timespec="microseconds")


class _Budget:
    def __init__(self, deadline):
        self.deadline = deadline
        self.bytes = self.rows = self.retained_bytes = 0

    def feed(self, digest, value, *, row=False, retained=False):
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()
        size = len(encoded) + 1
        if (time.monotonic() > self.deadline or size > MAX_ROW_BYTES or
                self.bytes + size > MAX_CANONICAL_BYTES or
                (row and self.rows + 1 > MAX_TOTAL_ROWS) or
                (retained and self.retained_bytes + size > MAX_RETAINED_BYTES)):
            raise ValueError("PAPER_PAUSE_SCAN_BOUNDS_EXCEEDED")
        self.bytes += size
        self.rows += int(row)
        self.retained_bytes += size if retained else 0
        digest.update(encoded + b"\n")


def _digest_rows(name, columns, rows, budget):
    digest = hashlib.sha256()
    budget.feed(digest, (name, tuple(columns)))
    for row in rows:
        budget.feed(digest, tuple(row[key] for key in columns))
    return digest.hexdigest()


def snapshot_paper_obligations(db_path, *, as_of):
    """Validate and hash the persisted PAPER book without exporting its rows.

    The same ``as_of`` is used when comparing a stop receipt and a later
    preinstall read.  T+1 availability is projected from immutable fills;
    an unconfirmed settlement remains pending regardless of elapsed time.
    """
    at = _stamp(as_of)
    path = Path(db_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("PAPER_PAUSE_DB_UNSAFE")
    deadline = time.monotonic() + READ_DEADLINE_SECONDS
    budget = _Budget(deadline)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.3)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=300")
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute("BEGIN")
        state = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or state["mode"] != "PRODUCTION_PAPER" or type(state["real_orders_sent"]) is not int or state["real_orders_sent"] != 0:
            raise ValueError("PAPER_PAUSE_MODE_OR_REAL_ORDERS_RED")
        witness, all_rows = {}, {}
        for name, (key, required) in TABLES.items():
            schema_rows = [tuple(row) for row in db.execute("PRAGMA table_info(" + name + ")")]
            schema = tuple(row[1] for row in schema_rows)
            if not required <= set(schema) or key not in schema:
                raise ValueError("PAPER_PAUSE_SCHEMA_RED:" + name)
            # ORDER BY a declared primary key is stable without a temp sorter.
            # A schema drift or unindexed query must fail before scanning data.
            key_info = next(row for row in schema_rows if row[1] == key)
            if key_info[5] != 1 or sum(row[5] > 0 for row in schema_rows) != 1:
                raise ValueError("PAPER_PAUSE_UNINDEXED_KEY:" + name)
            sql = "SELECT * FROM " + name + " ORDER BY " + key
            plan = db.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            if any("TEMP B-TREE" in row[3].upper() for row in plan):
                raise ValueError("PAPER_PAUSE_TEMP_SORT_RED:" + name)
            expected = db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
            max_rows = MAX_STREAM_EVENTS if name == "paper_events" else MAX_ROWS_PER_TABLE
            if type(expected) is not int or expected > max_rows or expected + budget.rows > MAX_TOTAL_ROWS:
                raise ValueError("PAPER_PAUSE_ROWS_EXCEEDED:" + name)
            digest = hashlib.sha256()
            budget.feed(digest, (name, schema))
            rows = [] if name != "paper_events" else None
            count = 0
            cursor = db.execute(sql)
            while batch := cursor.fetchmany(FETCH_BATCH):
                for source in batch:
                    budget.feed(digest, tuple(source), row=True, retained=rows is not None)
                    count += 1
                    if count > max_rows:
                        raise ValueError("PAPER_PAUSE_ROWS_EXCEEDED:" + name)
                    if rows is not None:
                        rows.append(dict(source))
            if count != expected:
                raise ValueError("PAPER_PAUSE_SNAPSHOT_COUNT_CHANGED:" + name)
            if rows is not None:
                all_rows[name] = rows
            schema_digest = hashlib.sha256()
            budget.feed(schema_digest, (name + ".schema",
                                        ("cid", "name", "type", "notnull", "default", "pk")))
            for info in schema_rows:
                budget.feed(schema_digest, info)
            witness[name] = {"count": count, "sha256": digest.hexdigest(),
                             "schema_sha256": schema_digest.hexdigest()}
        positions = all_rows["paper_positions"]
        if any(row["status"] not in {"OPEN", "CLOSED", "CANCELLED_ADMIN"} for row in positions):
            raise ValueError("PAPER_PAUSE_SPOT_STATE_RED")
        for row in positions:
            partition(db, row)
        if any(row["status"] not in {"OPEN", "MATURED"} for row in all_rows["paper_cauciones"]):
            raise ValueError("PAPER_PAUSE_CAUCION_STATE_RED")
        for row in all_rows["paper_cauciones"]:
            validate_position(row)
        if any(row["status"] not in {"ACTIVE", "CLOSED"} for row in all_rows["paper_future_positions"]):
            raise ValueError("PAPER_PAUSE_FUTURE_STATE_RED")
        if any(row["family"] not in {"FCI", "FUTUROS"} for row in all_rows["paper_family_lifecycle"]):
            raise ValueError("PAPER_PAUSE_FAMILY_STATE_RED")
        for row in all_rows["paper_exit_intents"]:
            if row["state"] not in {"CLOSED", "CANCELLED_ADMIN"}:
                raise ValueError("PAPER_PAUSE_SPOT_EXIT_PENDING")
        for row in all_rows["paper_future_exit_intents"]:
            if row["state"] != "CLOSED":
                raise ValueError("PAPER_PAUSE_FUTURE_EXIT_PENDING")
        if any(row["state"] not in OUTBOX_STATES for row in all_rows["paper_notification_outbox"]):
            raise ValueError("PAPER_PAUSE_OUTBOX_STATE_RED")
        pending = {}
        for currency in sorted(CASH_CURRENCIES):
            amount = pending_proceeds(None, at, currency, connection=db)
            if not isinstance(amount, Decimal):
                raise ValueError("PAPER_PAUSE_PENDING_PROJECTION_RED")
            pending[currency] = str(amount)
        open_counts = {
            "spot": sum(row["status"] == "OPEN" for row in positions),
            "caucion": sum(row["status"] == "OPEN" for row in all_rows["paper_cauciones"]),
            "future": sum(row["status"] == "ACTIVE" for row in all_rows["paper_future_positions"]),
            "fci": sum(row["family"] == "FCI" and row["state"] != "REDEEMED"
                       for row in all_rows["paper_family_lifecycle"]),
            "futures_lifecycle": sum(row["family"] == "FUTUROS" and
                                     row["state"] not in {"CLOSE", "EXPIRY"}
                                     for row in all_rows["paper_family_lifecycle"]),
        }
        outbox = all_rows["paper_notification_outbox"]
        outbox_static = _digest_rows("paper_notification_outbox.static", OUTBOX_STATIC,
                                     outbox, budget)
        outbox_counts = {state: sum(row["state"] == state for row in outbox)
                         for state in sorted(OUTBOX_STATES)}
        db.rollback()
    return {"schema": SCHEMA, "as_of_utc": at, "mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "tables": witness, "open_counts": open_counts,
            "pending_proceeds_by_currency": pending,
            "outbox_static_sha256": outbox_static,
            "outbox_state_counts": outbox_counts}


def assert_pause_book_preserved(original, current):
    """Compare a stop receipt with an unchanged, stopped PAPER database."""
    if (not isinstance(original, dict) or original.get("schema") != SCHEMA or
            original != current or any(original["open_counts"].values())):
        raise ValueError("PAPER_PAUSE_OBLIGATIONS_CHANGED_OR_OPEN")
    return True
