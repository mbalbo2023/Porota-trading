#!/usr/bin/env python3
"""A626: reconcile financial contract gaps on an externally staged SQLite copy.

Only an independently staged, immutable, checkpointed OFFHOST copy is allowed.
The supplied copy receipt is an untrusted claim, not runtime WAL/custody proof.
No API/provider/broker interaction or database writes.
Output consists only of aggregate counts and canonical field names.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import time

from cq_family_contract_rules_hf6 import FAMILY_CONTRACT_FIELDS

SCHEMA = "rc6.a626.offhost-contract-gap-join.v1"
COPY_SCHEMA = "rc6.a626.wal-safe-offhost-copy.v1"
FAMILIES = ("BONOS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
PPI_SOURCES = frozenset({"PPI_FIELD", "REQUEST_CANDIDATE"})
ACTIVE = frozenset({"AVAILABLE", "ACTIVE", "READY"})
MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_ROWS = 60_000
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_FIELD_BYTES = 32 * 1024
MAX_CATALOG_METADATA_BYTES = 128 * 1024 * 1024
HEXDIGEST = re.compile(r"^[a-f0-9]{64}$")


class Rejected(Exception):
    pass


def _family(value):
    raw = str(value or "").strip().upper()
    return {"ON": "OBLIGACIONES", "FCI_LOCAL": "FCI"}.get(raw, raw)


def _key(row):
    return (_family(row["family"]), str(row["ticker"] or "").strip().upper(),
            str(row["market"] or "").strip().upper(),
            str(row["currency"] or "").strip().upper(),
            str(row["settlement"] or "").strip().upper())


def _complete_key(key):
    return len(key) == 5 and all(part not in ("", "UNKNOWN", "*", "NULL")
                                 for part in key)


def _catalog_primary_claim(row):
    """Catalog-side provenance claim, not a fresh provider authorization."""
    source = str(row["settlement_source"] or "").strip().upper()
    if source in PPI_SOURCES:
        return True
    try:
        value = json.loads(row["metadata_json"] or "{}")
    except (ValueError, TypeError):
        return False
    if not isinstance(value, dict):
        return False
    discovery = str(value.get("_discovery_source") or "").strip().upper()
    availability = str(value.get("_availability_source") or "").strip().upper()
    return discovery == "PPI_PRIMARY" or availability.startswith("PPI_PRIMARY")


def _assert_copy(path: Path, manifest: dict) -> str:
    if (not isinstance(manifest, dict) or manifest.get("schema") != COPY_SCHEMA
            or manifest.get("copy_complete") is not True
            or manifest.get("wal_safe_quiescence_verified") is not True
            or manifest.get("runtime_source") is not False
            or not isinstance(manifest.get("sha256"), str)
            or not HEXDIGEST.fullmatch(manifest["sha256"])):
        raise Rejected("COPY_CUSTODY_RECEIPT_INVALID")
    if not path.is_absolute() or ".." in path.parts or path.is_symlink():
        raise Rejected("COPY_PATH_UNSAFE")
    if any(p.is_symlink() for p in path.parents):
        raise Rejected("COPY_PARENT_ALIAS_UNSAFE")
    if ("/opt/porota-trading/" in str(path) or str(path).startswith("/app/data/")):
        raise Rejected("RUNTIME_DB_PATH_FORBIDDEN")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise Rejected("COPY_UNCHECKPOINTED_SIDECAR_PRESENT")
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise Rejected("COPY_REGULAR_FILE_REQUIRED")
        if not 0 < before.st_size <= MAX_SOURCE_BYTES:
            raise Rejected("COPY_SIZE_OUT_OF_BOUND")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = path.lstat()
    except OSError as exc:
        raise Rejected("COPY_UNAVAILABLE") from exc
    if (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns):
        raise Rejected("COPY_CHANGED_DURING_HASH")
    if manifest.get("bytes") != before.st_size or digest.hexdigest() != manifest["sha256"]:
        raise Rejected("COPY_DIGEST_MISMATCH")
    return digest.hexdigest()


def _schema_ok(conn, table, fields):
    names = {str(x["name"]) for x in conn.execute("PRAGMA table_info(" + table + ")")}
    if not fields <= names:
        raise Rejected("COPY_SCHEMA_MISSING_" + table.upper())


def _current_rows(conn):
    query = """SELECT c.family,c.ticker,c.market,c.currency,c.settlement,
        c.source_class,c.snapshot_id,c.evidence_hash,c.observed_at,
        s.family AS snap_family,s.ticker AS snap_ticker,s.market AS snap_market,
        s.currency AS snap_currency,s.settlement AS snap_settlement,
        s.source_class AS snap_source_class,s.evidence_hash AS snap_hash,
        s.observed_at AS snap_observed_at,
        s.evidence_json
      FROM contract_evidence_v2_current c
      JOIN contract_evidence_v2_snapshots s ON s.snapshot_id=c.snapshot_id"""
    rows = []
    total_bytes = 0
    expected = conn.execute("SELECT COUNT(*) FROM contract_evidence_v2_current").fetchone()[0]
    if expected > MAX_ROWS:
        raise Rejected("V2_CURRENT_ROWS_OUT_OF_BOUND")
    for value in conn.execute(query):
        row = dict(value)
        if len(rows) >= MAX_ROWS:
            raise Rejected("V2_CURRENT_ROWS_OUT_OF_BOUND")
        raw = row["evidence_json"]
        if not isinstance(raw, str) or len(raw) > MAX_FIELD_BYTES:
            raise Rejected("V2_EVIDENCE_FIELD_OUT_OF_BOUND")
        total_bytes += len(raw)
        if total_bytes > MAX_EVIDENCE_BYTES:
            raise Rejected("V2_EVIDENCE_BUDGET_EXHAUSTED")
        if (_key(row) != _key({
                "family": row["snap_family"], "ticker": row["snap_ticker"],
                "market": row["snap_market"], "currency": row["snap_currency"],
                "settlement": row["snap_settlement"]})
                or row["source_class"] != row["snap_source_class"]
                or row["observed_at"] != row["snap_observed_at"]
                or row["evidence_hash"] != row["snap_hash"]):
            raise Rejected("V2_CURRENT_SNAPSHOT_IDENTITY_MISMATCH")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            raise Rejected("V2_EVIDENCE_JSON_INVALID") from None
        if not isinstance(parsed, dict):
            raise Rejected("V2_EVIDENCE_JSON_INVALID")
        canonical = json.dumps(parsed, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"))
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != row["evidence_hash"]:
            raise Rejected("V2_EVIDENCE_HASH_MISMATCH")
        row["parsed_evidence"] = parsed
        rows.append(row)
    if len(rows) != expected:
        raise Rejected("V2_CURRENT_SNAPSHOT_ORPHAN")
    return rows


def _bucket():
    return {"catalog_active": 0, "catalog_prelim_ready": 0,
            "catalog_primary_claimed": 0, "catalog_complementary_only": 0,
            "capability_gaps": 0, "ppi_ticker_ambiguous": 0,
            "ppi_catalog_identity_absent": 0, "ppi_symbol_multiple": 0,
            "gap_catalog_primary_claimed": 0, "gap_catalog_complementary_only": 0,
            "catalog_incomplete_five_key": 0,
            "exact_v2_ppi": 0, "exact_v2_complement_only": 0,
            "ticker_only_v2_mismatch": 0, "v2_absent": 0,
            "ppi_partial_or_unverified": 0, "ppi_missing_required_fields": 0,
            "ppi_field_conflicts": 0, "ppi_fields_present_not_accredited": 0,
            "missing_contract_fields": Counter(), "gap_capability": Counter()}


def _analyze(conn):
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    needed = {"financial_instrument_catalog", "contract_evidence_v2_current",
              "contract_evidence_v2_snapshots"}
    if not needed <= tables:
        raise Rejected("COPY_TABLES_MISSING")
    _schema_ok(conn, "financial_instrument_catalog", {
        "ticker", "instrument_type", "market", "currency", "settlement",
        "settlement_source", "status", "capability", "metadata_json"})
    for table in ("contract_evidence_v2_current", "contract_evidence_v2_snapshots"):
        _schema_ok(conn, table, {"family", "ticker", "market", "currency", "settlement",
                                "source_class", "evidence_hash"})
    _schema_ok(conn, "contract_evidence_v2_current", {"snapshot_id", "observed_at"})
    _schema_ok(conn, "contract_evidence_v2_snapshots", {"snapshot_id", "evidence_json"})
    catalog = [dict(row) for row in conn.execute(
        """SELECT ticker,instrument_type AS family,market,currency,settlement,
            settlement_source,status,capability,metadata_json
            FROM financial_instrument_catalog""")]
    if not 0 < len(catalog) <= MAX_ROWS:
        raise Rejected("CATALOG_DENOMINATOR_UNVERIFIED")
    if sum(len(str(r["metadata_json"] or "")) for r in catalog) > MAX_CATALOG_METADATA_BYTES:
        raise Rejected("CATALOG_METADATA_BUDGET_EXHAUSTED")
    current = _current_rows(conn)
    ppi_by_symbol = Counter()
    for row in catalog:
        row["_catalog_primary_claim"] = _catalog_primary_claim(row)
        if row["_catalog_primary_claim"]:
            ppi_by_symbol[str(row["ticker"] or "").strip().upper()] += 1
    by_key = defaultdict(list)
    by_symbol = Counter()
    v2_unkeyed_rows = 0
    for row in current:
        key = _key(row)
        if _complete_key(key):
            by_key[key].append(row)
        else:
            v2_unkeyed_rows += 1
        by_symbol[key[1]] += 1
    totals = {fam: _bucket() for fam in FAMILIES}
    for row in catalog:
        key = _key(row)
        fam = key[0]
        if fam not in totals or str(row["status"] or "").upper() not in ACTIVE:
            continue
        result = totals[fam]
        result["catalog_active"] += 1
        primary_claim = row["_catalog_primary_claim"]
        result["catalog_primary_claimed" if primary_claim
               else "catalog_complementary_only"] += 1
        if str(row["capability"] or "").startswith("READY_PAPER_"):
            result["catalog_prelim_ready"] += 1
            continue
        result["capability_gaps"] += 1
        result["gap_catalog_primary_claimed" if primary_claim
               else "gap_catalog_complementary_only"] += 1
        capability = str(row["capability"] or "")
        result["gap_capability"][capability if re.fullmatch(r"[A-Z_]{2,65}", capability)
                                 else "OTHER_OR_UNSAFE_CODE"] += 1
        if ppi_by_symbol[key[1]] != 1:
            result["ppi_ticker_ambiguous"] += 1
        if not ppi_by_symbol[key[1]]:
            result["ppi_catalog_identity_absent"] += 1
        if ppi_by_symbol[key[1]] > 1:
            result["ppi_symbol_multiple"] += 1
        if not _complete_key(key):
            result["catalog_incomplete_five_key"] += 1
            continue
        candidates = by_key.get(key, [])
        primary = [r for r in candidates if str(r["source_class"]).startswith("PPI_")]
        if not primary:
            if candidates:
                result["exact_v2_complement_only"] += 1
            elif by_symbol[key[1]]:
                result["ticker_only_v2_mismatch"] += 1
            else:
                result["v2_absent"] += 1
            continue
        result["exact_v2_ppi"] += 1
        merged = {}
        values = defaultdict(set)
        partial = False
        for item in primary:
            fields = item["parsed_evidence"]
            if (str(fields.get("evidence_scope") or "").startswith("PARTIAL")
                    or str(fields.get("readiness_guard") or "").startswith("NO_AUTO")
                    or str(fields.get("book_quantity_unit_status") or "").endswith("UNVERIFIED")):
                partial = True
            for field, value in fields.items():
                if field not in FAMILY_CONTRACT_FIELDS.get(fam, ()):
                    continue
                if value in (None, "", [], {}):
                    continue
                values[field].add(json.dumps(value, ensure_ascii=True, sort_keys=True))
                merged[field] = value
        required = FAMILY_CONTRACT_FIELDS[fam]
        # The V2 primary snapshot's five-key identity establishes only these
        # three identity terms, never its economic, tariff or lifecycle terms.
        if primary_claim:
            for identity_field, identity_value in zip(
                    ("market", "currency", "settlement"), key[2:]):
                if identity_field in required:
                    merged.setdefault(identity_field, identity_value)
        missing = sorted(k for k in required if k not in merged)
        conflict = any(len(values[k]) > 1 for k in required)
        if conflict:
            result["ppi_field_conflicts"] += 1
        if missing:
            result["ppi_missing_required_fields"] += 1
            result["missing_contract_fields"].update(missing)
        if partial:
            result["ppi_partial_or_unverified"] += 1
        if not missing and not conflict and not partial:
            result["ppi_fields_present_not_accredited"] += 1
    return totals, len(catalog), len(current), v2_unkeyed_rows


def inspect(copy_path: Path, receipt_path: Path, *, deadline_seconds=20.0):
    try:
        receipt_stat = receipt_path.lstat()
        if (not stat.S_ISREG(receipt_stat.st_mode) or receipt_path.is_symlink()
                or receipt_stat.st_size > 16 * 1024):
            raise Rejected("COPY_CUSTODY_RECEIPT_INVALID")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Rejected("COPY_CUSTODY_RECEIPT_UNAVAILABLE") from None
    digest = _assert_copy(copy_path, receipt)
    conn = None
    try:
        conn = sqlite3.connect(copy_path.as_uri() + "?mode=ro&immutable=1",
                               uri=True, timeout=0.05)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        started = time.monotonic()
        conn.set_progress_handler(lambda: int(time.monotonic() - started > deadline_seconds), 1000)
        if conn.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise Rejected("COPY_SQLITE_INTEGRITY_INVALID")
        totals, catalog_size, current_size, unkeyed_size = _analyze(conn)
    except sqlite3.Error:
        raise Rejected("COPY_SQLITE_READ_FAILED") from None
    finally:
        if conn is not None:
            conn.close()
    result = []
    for family in FAMILIES:
        bucket = totals[family]
        result.append({k: (dict(sorted(v.items())) if isinstance(v, Counter) else v)
                       for k, v in {"family": family, **bucket}.items()})
    return {"schema": SCHEMA, "status": "DIAGNOSTIC_ONLY", "read_only": True,
            "source_copy_receipt_claim_only": True, "runtime_verified": False,
            "ppi_provenance_authenticity_verified": False,
            "v2_evidence_hash_integrity_checked": True,
            "identity_five_key_join": True, "snapshot_join": "EXACT_ID_HASH_SOURCE",
            "source_sha256": digest, "catalog_rows": catalog_size,
            "v2_current_rows": current_size, "families": result,
            "v2_unkeyed_identity_rows": unkeyed_size,
            "capability_gaps_measured": sum(r["capability_gaps"] for r in result),
            "historical_819_gaps_match": sum(r["capability_gaps"] for r in result) == 819,
            "paper_auto_enabled": False, "real_orders_sent": 0,
            "provider_gets": 0, "host_writes": 0}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--offhost-db-copy", type=Path, required=True)
    parser.add_argument("--copy-receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect(args.offhost_db_copy, args.copy_receipt),
                         sort_keys=True, separators=(",", ":"), ensure_ascii=True))
        return 0
    except Rejected as exc:
        print(json.dumps({"schema": SCHEMA, "status": "BLOQUEADO",
                          "reason": str(exc), "read_only": True,
                          "runtime_verified": False, "provider_gets": 0,
                          "host_writes": 0}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
