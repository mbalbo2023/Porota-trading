"""Isolated PAPER ledgers for fund and futures lifecycle design.

This module has no broker client and no real-order route.  It records explicit,
idempotent transitions in a caller-supplied SQLite store so missing executors
remain distinguishable from missing contract evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


PAPER_ONLY = True
REAL_ROUTES_USED = ()

TRANSITIONS = {
    "FCI": {
        None: {"SUBSCRIBE"},
        "SUBSCRIBE": {"PENDING"},
        "PENDING": {"NAV_APPLIED"},
        "NAV_APPLIED": {"SETTLED"},
        "SETTLED": {"REDEEM_REQUESTED"},
        "REDEEM_REQUESTED": {"REDEEMED"},
    },
    "FUTUROS": {
        None: {"OPEN"},
        "OPEN": {"MARGIN_RESERVED"},
        "MARGIN_RESERVED": {"DAILY_VARIATION", "CLOSE", "EXPIRY"},
        "DAILY_VARIATION": {"MARGIN_OK", "MARGIN_DEFICIT", "CLOSE", "EXPIRY"},
        "MARGIN_OK": {"DAILY_VARIATION", "CLOSE", "EXPIRY"},
        "MARGIN_DEFICIT": {"DAILY_VARIATION", "CLOSE", "EXPIRY"},
    },
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def init_schema(store):
    with store.connect() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS paper_family_lifecycle(
          lifecycle_id TEXT PRIMARY KEY,
          family TEXT NOT NULL,
          instrument TEXT NOT NULL,
          currency TEXT NOT NULL,
          state TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          ledger_total TEXT NOT NULL DEFAULT '0',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS paper_family_lifecycle_events(
          event_id TEXT PRIMARY KEY,
          lifecycle_id TEXT NOT NULL,
          family TEXT NOT NULL,
          from_state TEXT,
          to_state TEXT NOT NULL,
          amount TEXT NOT NULL,
          occurred_at TEXT NOT NULL,
          detail_json TEXT NOT NULL,
          FOREIGN KEY(lifecycle_id) REFERENCES paper_family_lifecycle(lifecycle_id)
        );
        CREATE INDEX IF NOT EXISTS idx_paper_family_lifecycle_state
          ON paper_family_lifecycle(family,state,updated_at);
        """)


def apply_paper_event(store, *, lifecycle_id, event_id, family, instrument,
                      currency, to_state, amount="0", occurred_at=None,
                      detail=None):
    """Apply one idempotent PAPER transition or fail closed.

    A repeated event_id returns the original result without a second ledger
    mutation.  No transition calls a broker or infers NAV/margin/settlement.
    """
    family = str(family or "").upper()
    to_state = str(to_state or "").upper()
    if family not in TRANSITIONS:
        raise ValueError("PAPER_LIFECYCLE_FAMILY_UNSUPPORTED")
    init_schema(store)
    stamp = occurred_at or _now()
    with store.connect() as connection:
        existing_event = connection.execute(
            "SELECT * FROM paper_family_lifecycle_events WHERE event_id=?",
            (str(event_id),),
        ).fetchone()
        if existing_event:
            row = dict(existing_event)
            if row["lifecycle_id"] != str(lifecycle_id):
                raise ValueError("PAPER_LIFECYCLE_EVENT_ID_COLLISION")
            return {"idempotent": True, "state": row["to_state"],
                    "real_routes_used": [], "paper_only": True}

        current = connection.execute(
            "SELECT * FROM paper_family_lifecycle WHERE lifecycle_id=?",
            (str(lifecycle_id),),
        ).fetchone()
        from_state = current["state"] if current else None
        if current and (current["family"] != family
                        or current["instrument"] != str(instrument)
                        or current["currency"] != str(currency).upper()):
            raise ValueError("PAPER_LIFECYCLE_IDENTITY_MISMATCH")
        if to_state not in TRANSITIONS[family].get(from_state, set()):
            raise ValueError(f"PAPER_LIFECYCLE_INVALID_TRANSITION:{from_state}->{to_state}")

        try:
            delta = __import__("decimal").Decimal(str(amount))
            previous = __import__("decimal").Decimal(str(current["ledger_total"])) if current else 0
        except __import__("decimal").InvalidOperation as exc:
            raise ValueError("PAPER_LIFECYCLE_AMOUNT_INVALID") from exc
        total = previous + delta
        payload = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
        connection.execute("""INSERT INTO paper_family_lifecycle
          (lifecycle_id,family,instrument,currency,state,updated_at,ledger_total,metadata_json)
          VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(lifecycle_id) DO UPDATE SET state=excluded.state,
            updated_at=excluded.updated_at,ledger_total=excluded.ledger_total,
            metadata_json=excluded.metadata_json""",
          (str(lifecycle_id), family, str(instrument), str(currency).upper(),
           to_state, stamp, str(total), payload))
        connection.execute("""INSERT INTO paper_family_lifecycle_events
          (event_id,lifecycle_id,family,from_state,to_state,amount,occurred_at,detail_json)
          VALUES(?,?,?,?,?,?,?,?)""",
          (str(event_id), str(lifecycle_id), family, from_state, to_state,
           str(delta), stamp, payload))
    return {"idempotent": False, "state": to_state, "ledger_total": str(total),
            "real_routes_used": [], "paper_only": True}


def lifecycle_state(store, lifecycle_id):
    init_schema(store)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT * FROM paper_family_lifecycle WHERE lifecycle_id=?",
            (str(lifecycle_id),),
        ).fetchone()
    return dict(row) if row else None
