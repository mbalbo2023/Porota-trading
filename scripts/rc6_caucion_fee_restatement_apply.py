#!/usr/bin/env python3
"""One-shot atomic PAPER-only PUBLIC cost restatement; never real broker fees.

Designed to run under canonical deploy ownership, after quiescence and before
the restarted observer.  No provider/network calls or changes to original
PAPER positions, allocations, fills, events or source evidence.  Only six
append-only model-version receipts are inserted atomically. Failure rolls
back this *new* transaction; the historical ledger remains untouched.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from rc6_caucion_fee_model import (
    MODEL, SOURCE_SHA256, ACCOUNT_FEES_VERIFIED,
    old_public_modeled_fee, conservative_public_modeled_fee,
    restated_public_modeled_fee,
)
from scripts.rc6_caucion_fee_ro_provenance import (
    LEGACY_AUTHORITY, _amount, _invalid_constant, _strict_contract,
    _allocation_link, MAX_TERMS_BYTES,
)

SCHEMA = "RC6_PAPER_CAUCION_PUBLIC_FEE_RESTATEMENT_V1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_HISTORICAL_ROWS = 6
MAX_POSITION_ROWS = 10000


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _seal(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def init_restated_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS paper_caucion_fee_restatements (
        paper_id TEXT PRIMARY KEY REFERENCES paper_cauciones(paper_id),
        schema TEXT NOT NULL, model TEXT NOT NULL, source_pdf_sha256 TEXT NOT NULL,
        candidate_sha TEXT NOT NULL, workspace_dataset_id TEXT NOT NULL,
        original_request_fingerprint TEXT NOT NULL,
        original_terms_sha256 TEXT NOT NULL,
        original_total_fees TEXT NOT NULL, public_model_fees TEXT NOT NULL,
        effective_total_fees TEXT NOT NULL, adjustment_delta TEXT NOT NULL,
        known_at_utc TEXT NOT NULL, broker_account_fees_verified INTEGER NOT NULL CHECK(broker_account_fees_verified=0),
        seal_sha256 TEXT NOT NULL)""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS paper_caucion_fee_restatements_no_update
        BEFORE UPDATE ON paper_caucion_fee_restatements
        BEGIN SELECT RAISE(ABORT,'PAPER_FEE_RESTATEMENT_APPEND_ONLY'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS paper_caucion_fee_restatements_no_delete
        BEFORE DELETE ON paper_caucion_fee_restatements
        BEGIN SELECT RAISE(ABORT,'PAPER_FEE_RESTATEMENT_APPEND_ONLY'); END""")


def _workspace_identity(db):
    try:
        row = db.execute("""SELECT namespace,dataset_id,origin,imported_legacy
              FROM paper_workspace WHERE id=1""").fetchone()
        mode = db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if (row is None or tuple(row)[0] != "POROTA_PAPER_V17_FRESH"
                or tuple(row)[2:] != ("FRESH_EMPTY", 0)
                or mode is None or tuple(mode) != ("PRODUCTION_PAPER", 0)):
            raise ValueError("PAPER_WORKSPACE_OR_REAL_ORDER_SAFETY_RED")
        dataset_id = str(row[1])
        from uuid import UUID
        if str(UUID(dataset_id)) != dataset_id:
            raise ValueError("PAPER_DATASET_ID_INVALID")
        return dataset_id
    except (sqlite3.Error, ValueError, TypeError, IndexError) as exc:
        raise ValueError("PAPER_WORKSPACE_OR_REAL_ORDER_SAFETY_RED") from exc


def expected_restated_receipts(db, candidate_sha, known_at, *, require_flat=True):
    """Build six full, sealed receipts only from a consistent locked snapshot."""
    if not isinstance(candidate_sha, str) or SHA.fullmatch(candidate_sha) is None:
        raise ValueError("PAPER_RESTATEMENT_CANDIDATE_SHA_RED")
    at = datetime.fromisoformat(known_at)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("PAPER_RESTATEMENT_NAIVE_KNOWN_AT")
    known_at = at.astimezone(timezone.utc).isoformat(timespec="seconds")
    dataset = _workspace_identity(db)
    all_rows = db.execute("SELECT * FROM paper_cauciones LIMIT ?", (MAX_POSITION_ROWS + 1,)).fetchall()
    if len(all_rows) > MAX_POSITION_ROWS:
        raise ValueError("PAPER_RESTATEMENT_UNBOUNDED_LEDGER")
    rows = []
    for row in all_rows:
        if not isinstance(row["terms_json"], str):
            raise ValueError("PAPER_RESTATEMENT_TERMS_INVALID")
        terms = json.loads(row["terms_json"], parse_constant=_invalid_constant)
        if isinstance(terms, dict) and terms.get("fee_authority") == LEGACY_AUTHORITY:
            rows.append(row)
    if len(rows) != EXPECTED_HISTORICAL_ROWS:
        raise ValueError("PAPER_RESTATEMENT_EXACT_SIX_REQUIRED")
    if require_flat:
        if db.execute("SELECT COUNT(*) FROM paper_positions "
                      "WHERE status='OPEN'").fetchone()[0] != 0:
            raise ValueError("PAPER_RESTATEMENT_SPOT_NOT_FLAT")
        if db.execute("SELECT COUNT(*) FROM paper_cauciones "
                      "WHERE status='OPEN'").fetchone()[0] != 0:
            raise ValueError("PAPER_RESTATEMENT_CAUCION_NOT_FLAT")
        future_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                   "AND name='paper_future_positions'").fetchone()
        if future_exists and db.execute("SELECT COUNT(*) FROM paper_future_positions "
                                        "WHERE status='ACTIVE'").fetchone()[0] != 0:
            raise ValueError("PAPER_RESTATEMENT_FUTURES_NOT_FLAT")
    receipts = []
    for row in rows:
        raw = row["terms_json"]
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_TERMS_BYTES:
            raise ValueError("PAPER_RESTATEMENT_TERMS_INVALID")
        terms = json.loads(raw, parse_constant=_invalid_constant)
        if not isinstance(terms, dict) or terms.get("fee_authority") != LEGACY_AUTHORITY:
            raise ValueError("PAPER_RESTATEMENT_HISTORICAL_AUTHORITY_MISMATCH")
        principal = _amount(row["principal"])
        interest = _amount(row["gross_interest"])
        saved = _amount(row["total_fees"])
        days = row["interest_days"]
        if (row["status"] != "MATURED" or not _strict_contract(row, terms, principal, interest)
                or hashlib.sha256((raw + "|" + format(principal.normalize(), "f")).encode()).hexdigest()
                   != row["request_fingerprint"]
                or old_public_modeled_fee(principal, days) != saved
                or _allocation_link(db, row, terms) != "LINKED"):
            raise ValueError("PAPER_RESTATEMENT_HISTORICAL_REPLAY_RED")
        public = conservative_public_modeled_fee(principal, interest, days)
        effective = restated_public_modeled_fee(principal, interest, saved, days)
        if effective < saved:
            raise ValueError("PAPER_RESTATEMENT_COULD_INCREASE_PNL")
        # This is a model correction, not a backdated real fill.
        payload = {
            "paper_id": row["paper_id"], "schema": SCHEMA,
            "model": MODEL, "source_pdf_sha256": SOURCE_SHA256,
            "candidate_sha": candidate_sha, "workspace_dataset_id": dataset,
            "original_request_fingerprint": row["request_fingerprint"],
            "original_terms_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "original_total_fees": str(saved), "public_model_fees": str(public),
            "effective_total_fees": str(effective),
            "adjustment_delta": str(effective - saved),
            "known_at_utc": known_at,
            "broker_account_fees_verified": 0,
        }
        payload["seal_sha256"] = _seal(payload)
        receipts.append(payload)
    if ACCOUNT_FEES_VERIFIED is not False:
        raise ValueError("PAPER_RESTATEMENT_BROKER_AUTHORITY_UNSAFE")
    return sorted(receipts, key=lambda r: r["paper_id"])


def _rows_match(existing, wanted):
    return [dict(x) for x in existing] == wanted


def read_verified_restated_fees(db):
    """Non-mutating all-or-none check for a live ledger containing old rows.

    No receipt, partial receipt, changed original row, source/model drift,
    changed table or corrupted seal can unlock the PAPER economic consumers.
    Current-fee-only synthetic ledgers require no migration at all.
    """
    try:
        observed = db.execute("SELECT terms_json FROM paper_cauciones LIMIT ?",
                              (MAX_POSITION_ROWS + 1,)).fetchall()
        if len(observed) > MAX_POSITION_ROWS:
            raise ValueError("UNBOUNDED_LEDGER")
        legacy_count = 0
        for row in observed:
            raw = row[0]
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_TERMS_BYTES:
                raise ValueError("TERMS_INVALID")
            payload = json.loads(raw, parse_constant=_invalid_constant)
            if not isinstance(payload, dict):
                raise ValueError("TERMS_INVALID")
            legacy_count += payload.get("fee_authority") == LEGACY_AUTHORITY
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='paper_caucion_fee_restatements'").fetchone()
        if not legacy_count and table is None:
            return {}
        if table is None:
            raise ValueError("RESTATEMENT_TABLE_MISSING")
        records = db.execute("SELECT * FROM paper_caucion_fee_restatements ORDER BY paper_id").fetchall()
        if not legacy_count and not records:
            return {}
        if legacy_count != EXPECTED_HISTORICAL_ROWS or len(records) != EXPECTED_HISTORICAL_ROWS:
            raise ValueError("RESTATEMENT_INCOMPLETE")
        first = records[0]
        receipt_sha, known_at = first["candidate_sha"], first["known_at_utc"]
        wanted = expected_restated_receipts(db, receipt_sha, known_at, require_flat=False)
        if not _rows_match(records, wanted):
            raise ValueError("RESTATEMENT_TAMPERED_OR_WRONG_VERSION")
        return {r["paper_id"]: dict(r) for r in records}
    except (sqlite3.Error, TypeError, ValueError, KeyError, OverflowError) as exc:
        raise ValueError("CAUCION_RESTATEMENT_INVALID: " + str(exc)) from exc


def apply_restatement(db_path, candidate_sha, *, now=None):
    """Atomic idempotent insertion. Returns counters/hashes, never IDs or cash."""
    path = Path(db_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("PAPER_RESTATEMENT_DATABASE_UNAVAILABLE")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with sqlite3.connect(path.resolve(), timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=2000")
        db.execute("BEGIN IMMEDIATE")
        # DDL is transactional too: a failed proof leaves NO partial table or
        # receipts behind, unlike sqlite3.executescript's implicit commit.
        init_restated_schema(db)
        existing = db.execute("SELECT * FROM paper_caucion_fee_restatements ORDER BY paper_id").fetchall()
        if existing:
            if len(existing) != EXPECTED_HISTORICAL_ROWS:
                raise ValueError("PAPER_RESTATEMENT_PARTIAL_RED")
            # Idempotent verification uses original known_at and SHA. Never
            # silently rebook a newer model or change known-at chronology.
            first = existing[0]
            if first["candidate_sha"] != candidate_sha:
                raise ValueError("PAPER_RESTATEMENT_EXACT_CANDIDATE_REQUIRED")
            wanted = expected_restated_receipts(
                db, first["candidate_sha"], first["known_at_utc"])
            if not _rows_match(existing, wanted):
                raise ValueError("PAPER_RESTATEMENT_EXISTING_RECEIPT_INVALID")
            inserted = 0
        else:
            wanted = expected_restated_receipts(db, candidate_sha, now.isoformat())
            columns = tuple(wanted[0])
            statement = ("INSERT INTO paper_caucion_fee_restatements (" + ",".join(columns)
                         + ") VALUES(" + ",".join("?" for _ in columns) + ")")
            for receipt in wanted:
                db.execute(statement, tuple(receipt[name] for name in columns))
            inserted = len(wanted)
        result = {"schema": SCHEMA, "status": "GREEN", "receipts": len(wanted),
                  "inserted": inserted, "old_positions_untouched": True,
                  "broker_account_fees_verified": False,
                  "modeled_fee_adjustments_nonnegative": True,
                  "combined_receipt_sha256": _seal([r["seal_sha256"] for r in wanted])}
    return result


def preflight_restatement(db_path, candidate_sha, *, now=None):
    """Strict SQLite READ ONLY proof before any receipt-writing transition.

    This is an incident-specific six-row gate, not a PPI tariff certificate.
    Even an earlier complete application must pass original-row, model and
    current flat-book verification under the same snapshot.
    """
    path = Path(db_path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("PAPER_RESTATEMENT_DATABASE_UNAVAILABLE")
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=2000")
        db.execute("BEGIN")
        wanted = expected_restated_receipts(db, candidate_sha, now.isoformat())
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='paper_caucion_fee_restatements'").fetchone()
        existing = read_verified_restated_fees(db) if table else {}
        if existing and set(existing) != {receipt['paper_id'] for receipt in wanted}:
            raise ValueError("PAPER_RESTATEMENT_EXISTING_SET_MISMATCH")
        if existing and any(row['candidate_sha'] != candidate_sha for row in existing.values()):
            # A future candidate needs an explicit versioned adoption contract,
            # never silent reuse of another SHA's financial mutation receipt.
            raise ValueError("PAPER_RESTATEMENT_EXACT_CANDIDATE_REQUIRED")
        return {"schema": SCHEMA + "_PREFLIGHT", "status": "GREEN", "read_only": True,
                "receipts_expected": len(wanted), "previously_applied": bool(existing),
                "paper_book_flat": True, "broker_account_fees_verified": False,
                "modeled_fee_adjustments_nonnegative": True,
                "source_pdf_sha256": SOURCE_SHA256}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--candidate-sha", required=True)
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--apply", action="store_true")
    command.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    result = (apply_restatement(args.db, args.candidate_sha) if args.apply
              else preflight_restatement(args.db, args.candidate_sha))
    print(json.dumps(result, sort_keys=True))
