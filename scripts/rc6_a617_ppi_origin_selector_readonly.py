#!/usr/bin/env python3
"""A617: bounded host-only reconciliation of PPI provenance and COLD selection.

No provider client, network, runtime command, database write, WAL copy, sidecar
mutation or order path is imported. The output contains aggregate counters only.
An observed mode/source label or an on-disk assertion is NOT PPI authentication,
paper readiness, an EXIT reservation or a causal fill.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import uuid
from urllib.parse import quote


SCHEMA = "rc6.a617.ppi-origin-selector-readonly.v1"
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
MARKET_ROOT = Path("/opt/porota-trading/data/market")
RECEIPT = "rc6_ppi_scanner_primary_latest.json"
JOURNAL = "rc6_ppi_native_book_causal_private.json"
RECEIPT_SCHEMA = "rc6.ppi-scanner-primary-receipt.v1"
JOURNAL_SCHEMA = "rc6.ppi-native-book-causal-journal.v1"
PRODUCER = "bf_production_paper_observer._read_scanner_quote"
ORIGIN = "PPI_READONLY_SDK_CURRENT_AND_BOOK"
FAMILIES = ("ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
            "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
MAX_SNAPSHOT_ROWS = 4096
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_JOURNAL_BYTES = 8 * 1024 * 1024


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _exact(row):
    parts = (row.get("symbol"), row.get("asset_class"), row.get("market"),
             row.get("currency"), row.get("settlement"))
    if any(not isinstance(value, str) or not value.strip() or
           value.upper() in {"UNKNOWN", "NO_VERIFICADO"} for value in parts):
        return None
    return tuple(value.strip().upper() for value in parts)


def _private_document(path: Path, maximum: int, expected_schema: str):
    """No symlinks, no loose permissions, no changing/unbounded reads."""
    summary = {"state": "NO_VERIFICADO", "reason": "FILE_NOT_READ"}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, {"state": "ABSENT", "reason": "PRIVATE_FILE_ABSENT"}
    except OSError:
        return None, {**summary, "reason": "PRIVATE_FILE_OPEN_HOLD"}
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_mode & 0o077 or not 2 <= before.st_size <= maximum):
            return None, {**summary, "reason": "PRIVATE_FILE_PERMISSIONS_OR_SIZE_HOLD"}
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or
                (before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_ino, after.st_size, after.st_mtime_ns)):
            return None, {**summary, "reason": "PRIVATE_FILE_MUTATED_DURING_READ"}
    except OSError:
        return None, {**summary, "reason": "PRIVATE_FILE_READ_HOLD"}
    finally:
        os.close(fd)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return None, {**summary, "reason": "PRIVATE_FILE_JSON_HOLD"}
    if (not isinstance(document, dict) or document.get("schema") != expected_schema
            or document.get("producer") != PRODUCER or
            document.get("provider_origin") != ORIGIN or
            not isinstance(document.get("records"), list)):
        return None, {**summary, "reason": "PRIVATE_FILE_CONTRACT_HOLD"}
    return document, {"state": "SCHEMA_ASSERTED", "reason": "ORIGIN_ASSERTION_NOT_PROVIDER_ATTESTATION"}


def _receipt_report(root: Path):
    doc, report = _private_document(root / RECEIPT, MAX_RECEIPT_BYTES, RECEIPT_SCHEMA)
    result = dict(report, record_count_asserted=0, exact_rows_structural=0,
                  fresh_native_trade_book_rows_structural=0,
                  independently_authenticated=False)
    if doc is None:
        return result, []
    rows = doc["records"]
    if len(rows) > 1024:
        return {**result, "state": "NO_VERIFICADO", "reason": "RECEIPT_CAPACITY_HOLD"}, []
    at = datetime.now(timezone.utc)
    valid = []
    for row in rows:
        if not isinstance(row, dict) or row.get("provider_origin") != ORIGIN:
            continue
        key = _exact(row)
        if key is None or row.get("canonical_identity") != list(key):
            continue
        valid.append(row)
        receipt = _stamp(row.get("received_at"))
        book = _stamp(row.get("book_at"))
        trade = _stamp(row.get("trade_at"))
        if (row.get("state") == "READY" and row.get("last_kind") == "TRADE"
                and receipt and book and trade and
                0 <= (at - trade).total_seconds() <= 300 and
                0 <= (at - book).total_seconds() <= 300 and
                trade <= receipt <= at and book <= receipt):
            result["fresh_native_trade_book_rows_structural"] += 1
    result["record_count_asserted"] = len(rows)
    result["exact_rows_structural"] = len(valid)
    return result, valid


def _digest(value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False) + "\n").encode()
    return hashlib.sha256(raw).hexdigest()


def _journal_report(root: Path):
    doc, report = _private_document(root / JOURNAL, MAX_JOURNAL_BYTES, JOURNAL_SCHEMA)
    result = dict(report, record_count_asserted=0, hash_chain_structurally_valid=False,
                  independently_authenticated=False, fill_authority=False)
    if doc is None:
        return result
    rows = doc["records"]
    result["record_count_asserted"] = len(rows)
    if len(rows) > 4096 or doc.get("record_count") != len(rows):
        return {**result, "state": "NO_VERIFICADO", "reason": "JOURNAL_CAPACITY_OR_COUNT_HOLD"}
    prior = None
    latest = {}
    try:
        for sequence, row in enumerate(rows, 1):
            key = _exact(row) if isinstance(row, dict) else None
            book = row.get("book") if isinstance(row, dict) else None
            known = _stamp(row.get("known_at")) if isinstance(row, dict) else None
            native = _stamp(row.get("native_book_at")) if isinstance(row, dict) else None
            trade = _stamp(row.get("native_trade_at")) if isinstance(row, dict) else None
            if (key is None or list(key) != row.get("canonical_identity") or
                    row.get("source") != "PPI" or row.get("provider_origin") != ORIGIN or
                    not isinstance(book, dict) or not known or not native or not trade or
                    native > known or trade > known or
                    row.get("known_at") != row.get("observed_at") or
                    row.get("normalized_book_sha256") != _digest(book) or
                    row.get("previous_sha256") != prior or row.get("sequence") != sequence or
                    row.get("event_sha256") != _digest({k: v for k, v in row.items()
                                                      if k != "event_sha256"}) or
                    row.get("quantity_unit") != "NO_VERIFICADO" or
                    row.get("price_unit") != "NO_VERIFICADO" or
                    (key in latest and (native <= latest[key][0] or known <= latest[key][1]))):
                raise ValueError("JOURNAL_CHAIN_HOLD")
            latest[key] = (native, known)
            prior = row["event_sha256"]
    except (TypeError, ValueError, OverflowError, KeyError):
        return {**result, "state": "NO_VERIFICADO", "reason": "JOURNAL_CHAIN_HOLD"}
    return {**result, "state": "STRUCTURAL_HASH_CHAIN_VALIDATED",
            "reason": "HASH_CHAIN_NOT_PROVIDER_SIGNATURE",
            "hash_chain_structurally_valid": True}


def _tables(conn):
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _mount_is_readonly(path: Path):
    """Only query SQLite after the host Action creates a private RO bind mount.

    SQLite URI mode=ro alone does NOT prove zero SHM writes in WAL mode. Never
    use immutable=1 against a live WAL database. The mount guard must hold
    for the longest matching mount point inside the *current* namespace.
    """
    chosen_length = -1
    readonly = False
    try:
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            fields = line.split(" - ", 1)[0].split()
            if len(fields) < 6:
                continue
            mount = fields[4].replace("\\040", " ")
            if (mount != "/" and path != Path(mount) and
                    not str(path).startswith(mount.rstrip("/") + "/")):
                continue
            if len(mount) > chosen_length:
                chosen_length = len(mount)
                readonly = "ro" in fields[5].split(",")
    except (OSError, UnicodeError, ValueError):
        return False
    return chosen_length >= 0 and readonly


def _verified_paper_db(conn, tables):
    """Verify the paper workspace marker; never export its private UUID."""
    if "paper_workspace" not in tables:
        return False
    names = {str(row[1]) for row in conn.execute("PRAGMA table_info(paper_workspace)")}
    required = {"id", "namespace", "origin", "imported_legacy", "dataset_id"}
    if not required <= names:
        return False
    rows = conn.execute("""SELECT id,namespace,origin,imported_legacy,dataset_id
      FROM paper_workspace LIMIT 2""").fetchall()
    if len(rows) != 1:
        return False
    row = rows[0]
    try:
        return (row[:4] == (1, "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0)
                and str(uuid.UUID(row[4])) == row[4])
    except (TypeError, ValueError, AttributeError):
        return False


def _columns(conn, table):
    if table not in {"candidate_identity_v2", "candidate_universe", "market_snapshots",
                     "financial_instrument_catalog", "universe_cycle_metrics"}:
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _selector_report(conn, tables):
    result = {"state": "NO_VERIFICADO", "table": "NONE", "eligible_sql_exact_request": None,
              "source_authority_qualified": False, "bootstrap_network_budget": "NOT_ATTESTED"}
    table = "candidate_identity_v2" if "candidate_identity_v2" in tables else (
        "candidate_universe" if "candidate_universe" in tables else None)
    if not table or not {"ticker", "instrument_type", "settlement", "can_simulate", "status"} <= _columns(conn, table):
        return {**result, "state": "SCHEMA_HOLD"}
    marks = ",".join("?" for _ in FAMILIES)
    sql = f"""SELECT COUNT(*) FROM (
      SELECT ticker,instrument_type,settlement FROM {table}
      WHERE can_simulate=1 AND status='AVAILABLE'
        AND UPPER(instrument_type) IN ({marks})
      GROUP BY ticker,instrument_type,settlement HAVING COUNT(*)=1)"""
    count = int(conn.execute(sql, FAMILIES).fetchone()[0])
    return {**result, "state": "READ_ONLY_OBSERVED", "table": table,
            "eligible_sql_exact_request": count}


def _snapshot_report(conn, tables, receipt_rows):
    result = {"state": "NO_VERIFICADO", "reason": "TABLE_UNAVAILABLE",
              "recent_sampled": 0, "recent_sample_truncated": None,
              "paper_mode_rows": 0, "ppi_source_label_rows": 0,
              "other_or_unknown_source_rows": 0, "paper_mode_native_clock_pairs": 0,
              "paper_mode_exact_receipt_correlations": 0,
              "paper_mode_provider_attested_rows": 0,
              "ppi_source_label_provider_attested_rows": 0}
    if "market_snapshots" not in tables:
        return result
    columns = _columns(conn, "market_snapshots")
    if not {"source", "symbol"} <= columns:
        return {**result, "reason": "SNAPSHOT_SCHEMA_HOLD"}
    fields = [name for name in ("source", "symbol", "asset_class", "market",
              "currency", "settlement", "observed_at", "trade_at", "book_at",
              "last_kind", "last", "bid", "ask", "bid_size", "ask_size") if name in columns]
    order = "id" if "id" in columns else "rowid"
    rows = [dict(zip(fields, value)) for value in conn.execute(
        f"SELECT {','.join(fields)} FROM market_snapshots ORDER BY {order} DESC LIMIT ?",
        (MAX_SNAPSHOT_ROWS + 1,)).fetchall()]
    truncated = len(rows) > MAX_SNAPSHOT_ROWS
    rows = rows[:MAX_SNAPSHOT_ROWS]
    by_key = {}
    for record in receipt_rows:
        if key := _exact(record):
            by_key.setdefault(key, []).append(record)
    for row in rows:
        source = str(row.get("source") or "").upper()
        if source == "PRODUCTION_PAPER":
            result["paper_mode_rows"] += 1
            if _stamp(row.get("trade_at")) and _stamp(row.get("book_at")):
                result["paper_mode_native_clock_pairs"] += 1
            key = _exact(row)
            for candidate in by_key.get(key, ()) if key else ():
                if row.get("last_kind") != "TRADE" or candidate.get("state") != "READY":
                    continue
                if any(_stamp(row.get(field if field != "observed_at" else "observed_at")) !=
                       _stamp(candidate.get("received_at" if field == "observed_at" else field))
                       for field in ("observed_at", "trade_at", "book_at")):
                    continue
                try:
                    if any(Decimal(str(row.get(field))) != Decimal(str(candidate.get(field)))
                           for field in ("last", "bid", "ask", "bid_size", "ask_size")):
                        continue
                except (TypeError, ValueError, InvalidOperation):
                    continue
                result["paper_mode_exact_receipt_correlations"] += 1
                break
        elif source in {"PPI", "PPI_PRODUCTION", "PPI_SCANNER_PRIMARY"}:
            result["ppi_source_label_rows"] += 1
        else:
            result["other_or_unknown_source_rows"] += 1
    return {**result, "state": "READ_ONLY_OBSERVED",
            "reason": "SOURCE_LABEL_OR_CORRELATION_NOT_PROVIDER_SIGNATURE",
            "recent_sampled": len(rows), "recent_sample_truncated": truncated}


def _cycle_report(conn, tables):
    result = {"state": "NO_VERIFICADO", "reason": "NO_CYCLE_RECEIPT",
              "reported_cold_slots": None, "reported_active_budget_state": "UNKNOWN",
              "reported_selector_state": "UNKNOWN", "reported_cold_bootstrap_state": "UNKNOWN",
              "reported_dynamic_capacity_state": "UNKNOWN"}
    if "universe_cycle_metrics" not in tables or "detail" not in _columns(conn, "universe_cycle_metrics"):
        return {**result, "reason": "CYCLE_TABLE_UNAVAILABLE"}
    row = conn.execute("SELECT detail FROM universe_cycle_metrics ORDER BY id DESC LIMIT 1").fetchone()
    if not row or not isinstance(row[0], str) or len(row[0]) > 128 * 1024:
        return {**result, "reason": "CYCLE_DETAIL_ABSENT_OR_UNBOUNDED"}
    try:
        detail = json.loads(row[0][row[0].index("{"):])
        plan = detail.get("active_plan", {})
        cold = plan.get("cold")
        evidence = plan.get("ppi_cold_bootstrap_diagnostic", {})
        if not isinstance(plan, dict) or not isinstance(evidence, dict):
            raise ValueError("INVALID_CYCLE_SCHEMA")
        status = evidence.get("status")
        if status not in {"NO_COLD_BOOTSTRAP_AUTHORITY", "NO_BASELINE_COLD_SLOT",
                          "BASELINE_COLD_SLOT_AVAILABLE", "ELIGIBLE_SELECTOR_READ_HOLD"}:
            status = "UNKNOWN"
        budget = plan.get("active_budget", {})
        budget_status = budget.get("status") if isinstance(budget, dict) else None
        if budget_status not in {"FEASIBLE", "INFEASIBLE"}:
            budget_status = "UNKNOWN"
        capacity = evidence.get("capacity_status")
        if capacity not in {"OFF_BASELINE", "SHADOW_BASELINE", "APPROVED_DYNAMIC"}:
            capacity = "UNKNOWN"
        return {**result, "state": "READ_ONLY_OBSERVED", "reason": "CYCLE_NOT_QUOTA_AUTHORITY",
                "reported_cold_slots": len(cold) if isinstance(cold, list) else None,
                "reported_active_budget_state": budget_status,
                "reported_selector_state": evidence.get("selector_status")
                 if evidence.get("selector_status") in {"QUERY_OK", "READ_HOLD"} else "UNKNOWN",
                "reported_cold_bootstrap_state": status,
                "reported_dynamic_capacity_state": capacity}
    except (ValueError, TypeError, AttributeError, IndexError):
        return {**result, "reason": "CYCLE_DETAIL_PARSE_HOLD"}


def diagnose(db: Path = DB, root: Path = MARKET_ROOT, *, enforce_ro_mount=True):
    report = {"schema": SCHEMA, "read_only": True, "db_opened": False,
              "provider_gets": 0, "orders_called": False, "admission_authority": False,
              "causal_fill_verified": False, "runtime_sha_verified": False,
              "effective_ppi_lease": "NO_VERIFICADO", "exit_budget_reserved": "NO_VERIFICADO",
              "runtime_db_binding_verified": False,
              "db_fixed_path_identity_verified": False,
              "sqlite_ro_mount_protected": False,
              "source_provenance_authorized": False,
              "state": "NO_VERIFICADO", "reason": "DB_RO_UNAVAILABLE"}
    receipt, receipt_rows = _receipt_report(root)
    journal = _journal_report(root)
    report["scanner_receipt"] = receipt
    report["native_book_journal"] = journal
    conn = None
    try:
        if not db.is_absolute() or db.is_symlink():
            raise ValueError("DB_PATH_HOLD")
        # The Action must establish a PRIVATE read-only bind mount before any
        # SQLite access. A writable host mount is a hard stop even for URI ro:
        # WAL readers can create/update -shm; immutable=1 is never a fix.
        if enforce_ro_mount and not _mount_is_readonly(db):
            report["reason"] = "SQLITE_READONLY_MOUNT_REQUIRED"
            return report
        report["sqlite_ro_mount_protected"] = bool(enforce_ro_mount)
        uri = "file:" + quote(str(db), safe="/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=0.35, isolation_level=None)
        conn.execute("PRAGMA query_only=ON")
        tables = _tables(conn)
        report["db_opened"] = True
        if not _verified_paper_db(conn, tables):
            report["reason"] = "PAPER_V17_DATABASE_IDENTITY_HOLD"
            return report
        report["db_fixed_path_identity_verified"] = True
        report["selector"] = _selector_report(conn, tables)
        report["snapshots"] = _snapshot_report(conn, tables, receipt_rows)
        report["scanner_cycle"] = _cycle_report(conn, tables)
        report["state"] = "READ_ONLY_OBSERVED"
        report["reason"] = "SOURCE_AND_SELECTOR_AGGREGATES_NOT_PAPER_AUTHORITY"
    except (sqlite3.Error, OSError, ValueError):
        report["state"] = "NO_VERIFICADO"
        report["reason"] = "DB_RO_QUERY_UNAVAILABLE"
    finally:
        if conn is not None:
            conn.close()
    return report


def main(argv=None):
    if (sys.argv[1:] if argv is None else argv) != ["host-probe"]:
        raise SystemExit("A617_HOST_PROBE_ONLY")
    print(json.dumps(diagnose(), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
