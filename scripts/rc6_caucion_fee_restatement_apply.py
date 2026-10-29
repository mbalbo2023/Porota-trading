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
import base64
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
from scripts.rc6_caucion_fee_restatement_continuity import validate_prior_receipt

SCHEMA = "RC6_PAPER_CAUCION_PUBLIC_FEE_RESTATEMENT_V1"
ORIGIN_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_DEPLOY_ORIGIN_V2"
CONTINUITY_SCHEMA = "RC6_PAPER_CAUCION_FEE_RESTATEMENT_CONTINUITY_V1"
INTRADAY_MODES = frozenset({"OPEN", "A151_HELD_OPEN", "A151_HELD_STOPPED",
                            "CLOSED_A171_PENDING_FIX_FORWARD",
                            "CLOSED_A174_PENDING_FIX_FORWARD",
                            "CLOSED_A175_PENDING_FIX_FORWARD"})
QUIESCENCE_MODES = INTRADAY_MODES | frozenset({
    "CLOSED", "DEGRADED_CLOSED_20261026", "CLOSED_STOPPED_FAILED_RESCUE",
    "CLOSED_A143_PENDING_FIX_FORWARD",
})
SHA = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
POSITIVE = re.compile(r"[1-9][0-9]*\Z")
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
    db.execute("""CREATE TABLE IF NOT EXISTS paper_caucion_fee_restatement_origin (
        id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL,
        candidate_sha TEXT NOT NULL, candidate_tree TEXT NOT NULL,
        deploy_sha TEXT NOT NULL, run_id TEXT NOT NULL, run_attempt TEXT NOT NULL,
        quiescence_mode TEXT NOT NULL,
        owner_approval_comment_id INTEGER NOT NULL,
        owner_deferred_comment_id INTEGER NOT NULL,
        owner_intraday_comment_id INTEGER,
        combined_receipt_sha256 TEXT NOT NULL, seal_sha256 TEXT NOT NULL)""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS paper_caucion_fee_restatement_origin_no_update
        BEFORE UPDATE ON paper_caucion_fee_restatement_origin
        BEGIN SELECT RAISE(ABORT,'PAPER_FEE_ORIGIN_APPEND_ONLY'); END""")
    db.execute("""CREATE TRIGGER IF NOT EXISTS paper_caucion_fee_restatement_origin_no_delete
        BEFORE DELETE ON paper_caucion_fee_restatement_origin
        BEGIN SELECT RAISE(ABORT,'PAPER_FEE_ORIGIN_APPEND_ONLY'); END""")


def _combined(receipts):
    return _seal([r["seal_sha256"] for r in receipts])


def _origin_payload(origin, candidate_sha, receipts):
    expected = {"candidate_sha", "candidate_tree", "deploy_sha", "run_id",
                "run_attempt", "quiescence_mode", "owner_approval_comment_id",
                "owner_deferred_comment_id", "owner_intraday_comment_id"}
    if type(origin) is not dict or set(origin) != expected or origin["candidate_sha"] != candidate_sha:
        raise ValueError("PAPER_RESTATEMENT_DEPLOY_ORIGIN_REQUIRED")
    if any(type(origin[k]) is not str or SHA.fullmatch(origin[k]) is None
           for k in ("candidate_sha", "candidate_tree", "deploy_sha")) or any(
               type(origin[k]) is not str or POSITIVE.fullmatch(origin[k]) is None
               for k in ("run_id", "run_attempt")) or any(
                   type(origin[k]) is not int or origin[k] <= 0
                   for k in ("owner_approval_comment_id", "owner_deferred_comment_id")) or (
                       origin["owner_approval_comment_id"] == origin["owner_deferred_comment_id"] or
            type(origin["quiescence_mode"]) is not str or
            origin["quiescence_mode"] not in QUIESCENCE_MODES):
        raise ValueError("PAPER_RESTATEMENT_DEPLOY_ORIGIN_INVALID")
    third = origin["owner_intraday_comment_id"]
    if origin["quiescence_mode"] in INTRADAY_MODES:
        if (type(third) is not int or third <= 0 or
                third in {origin["owner_approval_comment_id"],
                          origin["owner_deferred_comment_id"]}):
            raise ValueError("PAPER_RESTATEMENT_INTRADAY_OWNER_REQUIRED")
    elif third is not None:
        raise ValueError("PAPER_RESTATEMENT_UNEXPECTED_INTRADAY_OWNER")
    payload = dict(origin, id=1, schema=ORIGIN_SCHEMA,
                   combined_receipt_sha256=_combined(receipts))
    payload["seal_sha256"] = _seal(payload)
    return payload


def _verified_origin(db, receipts):
    table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                       "AND name='paper_caucion_fee_restatement_origin'").fetchone()
    if table is None:
        raise ValueError("PAPER_RESTATEMENT_ORIGIN_MISSING")
    rows = db.execute("SELECT * FROM paper_caucion_fee_restatement_origin LIMIT 2").fetchall()
    if len(rows) != 1:
        raise ValueError("PAPER_RESTATEMENT_ORIGIN_COUNT_RED")
    row = dict(rows[0])
    keys = {"id", "schema", "combined_receipt_sha256", "seal_sha256",
            "candidate_sha", "candidate_tree", "deploy_sha", "run_id", "run_attempt",
            "quiescence_mode", "owner_approval_comment_id", "owner_deferred_comment_id",
            "owner_intraday_comment_id"}
    if set(row) != keys:
        raise ValueError("PAPER_RESTATEMENT_ORIGIN_COLUMNS_RED")
    payload = _origin_payload({k: row[k] for k in keys if k not in
                              {"id", "schema", "combined_receipt_sha256", "seal_sha256"}},
                              row["candidate_sha"], receipts)
    if row != payload or any(r["candidate_sha"] != row["candidate_sha"] for r in receipts):
        raise ValueError("PAPER_RESTATEMENT_ORIGIN_TAMPERED")
    return row


def _verify_continuity(origin, candidate_sha, proof, receipt_dir, known_at):
    if (type(proof) is not dict or set(proof) != {"schema", "new_candidate_sha",
            "prior_origin_seal_sha256", "prior_receipt_sha256", "prior_run_conclusion",
            "prior_workflow_path"} or proof.get("schema") != CONTINUITY_SCHEMA or
            proof.get("new_candidate_sha") != candidate_sha or
            proof.get("prior_origin_seal_sha256") != origin["seal_sha256"] or
            proof.get("prior_run_conclusion") != "failure" or
            proof.get("prior_workflow_path") != ".github/workflows/porota-deploy-v2-promote.yml" or
            type(proof.get("prior_receipt_sha256")) is not str or
            SHA256.fullmatch(proof["prior_receipt_sha256"]) is None):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_DEPLOY_PROOF_RED")
    directory = Path(receipt_dir) if receipt_dir is not None else None
    path = directory / ("quiescence-" + origin["candidate_sha"] + ".json") if directory else None
    if (path is None or path.is_symlink() or not path.is_file() or
            path.stat().st_size > 65536 or path.stat().st_nlink != 1):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_RED")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != proof["prior_receipt_sha256"]:
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_RECEIPT_CHANGED")
    receipt = json.loads(raw, parse_constant=_invalid_constant)
    at = validate_prior_receipt(receipt, origin["candidate_sha"],
                                origin["quiescence_mode"])
    if at < datetime.fromisoformat(known_at):
        raise ValueError("PAPER_RESTATEMENT_PRIOR_FAILED_HOST_CLOCK_RED")


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
        origin_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                  "AND name='paper_caucion_fee_restatement_origin'").fetchone()
        if bool(table) != bool(origin_table):
            raise ValueError("RESTATEMENT_ORIGIN_TABLE_PARTIAL")
        if not legacy_count and table is None:
            return {}
        if table is None:
            raise ValueError("RESTATEMENT_TABLE_MISSING")
        records = db.execute("SELECT * FROM paper_caucion_fee_restatements "
                             "ORDER BY paper_id LIMIT 7").fetchall()
        if not legacy_count and not records:
            if db.execute("SELECT 1 FROM paper_caucion_fee_restatement_origin LIMIT 1").fetchone():
                raise ValueError("RESTATEMENT_ORPHAN_ORIGIN")
            return {}
        if legacy_count != EXPECTED_HISTORICAL_ROWS or len(records) != EXPECTED_HISTORICAL_ROWS:
            raise ValueError("RESTATEMENT_INCOMPLETE")
        first = records[0]
        receipt_sha, known_at = first["candidate_sha"], first["known_at_utc"]
        wanted = expected_restated_receipts(db, receipt_sha, known_at, require_flat=False)
        if not _rows_match(records, wanted):
            raise ValueError("RESTATEMENT_TAMPERED_OR_WRONG_VERSION")
        _verified_origin(db, wanted)
        return {r["paper_id"]: dict(r) for r in records}
    except (sqlite3.Error, TypeError, ValueError, KeyError, OverflowError) as exc:
        raise ValueError("CAUCION_RESTATEMENT_INVALID: " + str(exc)) from exc


def apply_restatement(db_path, candidate_sha, *, now=None, origin=None,
                      continuity_proof=None, receipt_dir=None):
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
        existing = db.execute("SELECT * FROM paper_caucion_fee_restatements "
                              "ORDER BY paper_id LIMIT 7").fetchall()
        if existing:
            if len(existing) != EXPECTED_HISTORICAL_ROWS:
                raise ValueError("PAPER_RESTATEMENT_PARTIAL_RED")
            # Idempotent verification uses original known_at and SHA. Never
            # silently rebook a newer model or change known-at chronology.
            first = existing[0]
            wanted = expected_restated_receipts(
                db, first["candidate_sha"], first["known_at_utc"])
            if not _rows_match(existing, wanted):
                raise ValueError("PAPER_RESTATEMENT_EXISTING_RECEIPT_INVALID")
            saved_origin = _verified_origin(db, wanted)
            if first["candidate_sha"] != candidate_sha:
                _verify_continuity(saved_origin, candidate_sha, continuity_proof,
                                   receipt_dir, wanted[0]["known_at_utc"])
            elif continuity_proof is not None:
                raise ValueError("PAPER_RESTATEMENT_UNNEEDED_CONTINUITY_PROOF")
            inserted = 0
        else:
            if continuity_proof is not None:
                raise ValueError("PAPER_RESTATEMENT_UNNEEDED_CONTINUITY_PROOF")
            wanted = expected_restated_receipts(db, candidate_sha, now.isoformat())
            saved_origin = _origin_payload(origin, candidate_sha, wanted)
            columns = tuple(wanted[0])
            statement = ("INSERT INTO paper_caucion_fee_restatements (" + ",".join(columns)
                         + ") VALUES(" + ",".join("?" for _ in columns) + ")")
            for receipt in wanted:
                db.execute(statement, tuple(receipt[name] for name in columns))
            origin_columns = tuple(saved_origin)
            db.execute("INSERT INTO paper_caucion_fee_restatement_origin (" +
                       ",".join(origin_columns) + ") VALUES(" +
                       ",".join("?" for _ in origin_columns) + ")",
                       tuple(saved_origin[name] for name in origin_columns))
            inserted = len(wanted)
        result = {"schema": SCHEMA, "status": "GREEN", "receipts": len(wanted),
                  "inserted": inserted, "old_positions_untouched": True,
                  "origin_candidate_sha": saved_origin["candidate_sha"],
                  "continuity_adopted": saved_origin["candidate_sha"] != candidate_sha,
                  "broker_account_fees_verified": False,
                  "modeled_fee_adjustments_nonnegative": True,
                  "combined_receipt_sha256": _combined(wanted)}
    return result


def preflight_restatement(db_path, candidate_sha, *, now=None,
                          continuity_proof=None, receipt_dir=None):
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
        origin_candidate = candidate_sha
        if existing:
            origin_candidate = next(iter(existing.values()))["candidate_sha"]
            origin = _verified_origin(db, list(existing.values()))
            if origin_candidate != candidate_sha:
                _verify_continuity(origin, candidate_sha, continuity_proof,
                                   receipt_dir, next(iter(existing.values()))["known_at_utc"])
            elif continuity_proof is not None:
                raise ValueError("PAPER_RESTATEMENT_UNNEEDED_CONTINUITY_PROOF")
        elif continuity_proof is not None:
            raise ValueError("PAPER_RESTATEMENT_UNNEEDED_CONTINUITY_PROOF")
        return {"schema": SCHEMA + "_PREFLIGHT", "status": "GREEN", "read_only": True,
                "receipts_expected": len(wanted), "previously_applied": bool(existing),
                "origin_candidate_sha": origin_candidate,
                "continuity_adopted": origin_candidate != candidate_sha,
                "paper_book_flat": True, "broker_account_fees_verified": False,
                "modeled_fee_adjustments_nonnegative": True,
                "source_pdf_sha256": SOURCE_SHA256}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--deploy-sha")
    parser.add_argument("--candidate-tree")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--quiescence-mode")
    parser.add_argument("--owner-approval-comment-id", type=int)
    parser.add_argument("--owner-deferred-comment-id", type=int)
    parser.add_argument("--owner-intraday-comment-id", type=int)
    parser.add_argument("--continuity-proof-b64", default="")
    parser.add_argument("--receipt-dir")
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--apply", action="store_true")
    command.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if len(args.continuity_proof_b64) > 8192:
        raise ValueError("PAPER_RESTATEMENT_CONTINUITY_PROOF_BOUND")
    proof = json.loads(base64.b64decode(args.continuity_proof_b64, validate=True)) if (
        args.continuity_proof_b64) else None
    origin = {"candidate_sha": args.candidate_sha, "candidate_tree": args.candidate_tree,
              "deploy_sha": args.deploy_sha, "run_id": args.run_id,
              "run_attempt": args.run_attempt, "quiescence_mode": args.quiescence_mode,
              "owner_approval_comment_id": args.owner_approval_comment_id,
              "owner_deferred_comment_id": args.owner_deferred_comment_id,
              "owner_intraday_comment_id": args.owner_intraday_comment_id}
    result = (apply_restatement(args.db, args.candidate_sha, origin=origin,
                                continuity_proof=proof, receipt_dir=args.receipt_dir)
              if args.apply else preflight_restatement(
                  args.db, args.candidate_sha, continuity_proof=proof,
                  receipt_dir=args.receipt_dir))
    print(json.dumps(result, sort_keys=True))
