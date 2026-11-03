#!/usr/bin/env python3
"""A626: aggregate six financial families on a private read-only host mount.

Only execute via the dedicated Actions SSH step after a private mount namespace
has remounted /opt/porota-trading/data read-only. Never copy the live database,
create files, fetch providers, inspect secrets or promote a PAPER capability.
The live WAL snapshot is not a PIT causal receipt or provider authorization.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

DATA_ROOT = Path("/opt/porota-trading/data")
DB = DATA_ROOT / "paper_v17" / "observer_v17.db"
NAMESPACE = "POROTA_PAPER_V17_FRESH"
SCHEMA = "rc6.a626.host-contract-aggregates-ro.v1"
FAMILIES = ("BONOS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
ACTIVE = frozenset({"AVAILABLE", "ACTIVE", "READY"})
PPI_CATALOG = frozenset({"PPI_FIELD", "REQUEST_CANDIDATE"})
MAX_CATALOG_ROWS = 60_000
MAX_CURRENT_ROWS = 60_000
MAX_METADATA_BYTES = 128 * 1024 * 1024
MAX_HISTORY_ROWS = 200_000
MAX_HISTORY_IDENTITY_BYTES = 24 * 1024 * 1024
MAX_SECONDS = 14.0


class Blocked(Exception):
    pass


def _family(value):
    """Reporting bucket only; never use a family alias for evidence identity."""
    value = str(value or "").strip().upper()
    return {"ON": "OBLIGACIONES", "FCI_LOCAL": "FCI"}.get(value, value)


def _key(row):
    # Physical 5-key equality must be literal across catalog/current/snapshot/
    # historical provenance. Display aliases cannot accredit PPI evidence.
    return (str(row["family"] or "").strip().upper(),
            str(row["ticker"] or "").strip().upper(),
            str(row["market"] or "").strip().upper(),
            str(row["currency"] or "").strip().upper(),
            str(row["settlement"] or "").strip().upper())


def _complete(key):
    return len(key) == 5 and all(x not in ("", "UNKNOWN", "*", "NULL") for x in key)


def _mount_field(value):
    # Linux /proc/self/mountinfo escapes literal whitespace/backslash.
    return re.sub(r"\\([0-7]{3})",
                  lambda m: chr(int(m.group(1), 8)), value)


def private_ro_mount_verified(directory=DATA_ROOT):
    """No DB open when the longest covering mount is not the exact RO bind."""
    try:
        # A RO mount in the host's initial namespace would not be isolated.
        if os.stat("/proc/self/ns/mnt").st_ino == os.stat("/proc/1/ns/mnt").st_ino:
            return False
        directory = Path(directory).absolute()
        if directory.is_symlink() or directory.resolve(strict=True) != directory:
            return False
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            left = line.split(" - ", 1)[0].split()
            if len(left) < 6:
                continue
            point = Path(_mount_field(left[4]))
            opts = set(left[5].split(","))
            shared = any(flag.startswith("shared:") for flag in left[6:])
            mounts.append((point, opts, shared))
        # Actions' unshare+bind+remount must create an exact RO mount here.
        if not any(point == directory and "ro" in opts and not shared
                   for point, opts, shared in mounts):
            return False
        db = directory / "paper_v17" / "observer_v17.db"
        covering = [(len(str(point)), opts, shared) for point, opts, shared in mounts
                    if point == db or point in db.parents]
        if not covering:
            return False
        _, opts, shared = max(covering, key=lambda v: v[0])
        return "ro" in opts and not shared
    except (OSError, ValueError):
        return False


def _safe_files(directory, db):
    directory = Path(directory).absolute()
    db = Path(db).absolute()
    if (db != directory / "paper_v17" / "observer_v17.db"
            or ".." in db.parts or directory.is_symlink()
            or directory.resolve(strict=True) != directory):
        raise Blocked("HOST_DB_PATH_NOT_CANONICAL")
    if db.is_symlink() or db.resolve(strict=True) != db:
        raise Blocked("HOST_DB_ALIAS_UNSAFE")
    st = db.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size <= 0:
        raise Blocked("HOST_DB_FILE_IDENTITY_UNVERIFIED")
    found = {}
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(db) + suffix)
        if sidecar.is_symlink():
            raise Blocked("HOST_DB_SIDECAR_ALIAS_UNSAFE")
        if sidecar.exists():
            st = sidecar.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise Blocked("HOST_DB_SIDECAR_UNVERIFIED")
            found[suffix] = True
        else:
            found[suffix] = False
    if found["-wal"] and not found["-shm"]:
        raise Blocked("HOST_WAL_SHM_MISSING_FAILCLOSED")
    if found["-journal"]:
        raise Blocked("HOST_ROLLBACK_JOURNAL_UNVERIFIED")
    return {"wal_present": found["-wal"], "shm_present": found["-shm"],
            "journal_present": found["-journal"],
            "db_bytes": db.stat().st_size}


def _columns(conn, table, required):
    names = {str(row[1]) for row in conn.execute("PRAGMA table_info(" + table + ")")}
    if not required <= names:
        raise Blocked("HOST_SCHEMA_MISSING_" + table.upper())


def _metadata(value):
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _primary_claim(row, meta):
    return (str(row["settlement_source"] or "").strip().upper() in PPI_CATALOG
            or str(meta.get("_discovery_source") or "").upper() == "PPI_PRIMARY"
            or str(meta.get("_availability_source") or "").upper().startswith("PPI_PRIMARY"))


def _historical_ppi_memberships(conn, wanted):
    """One bounded streaming pass, no per-gap SQL, DISTINCT temp-B-tree or disk."""
    if not wanted:
        return set(), 0, 0
    matches = set()
    rows_scanned = identity_bytes = 0
    cursor = conn.execute("""SELECT family,ticker,market,currency,settlement
       FROM contract_evidence_v2_snapshots
       WHERE substr(source_class,1,4)='PPI_' LIMIT ?""",
                          (MAX_HISTORY_ROWS + 1,))
    for row in cursor:
        rows_scanned += 1
        if rows_scanned > MAX_HISTORY_ROWS:
            raise Blocked("HOST_HISTORY_PPI_ROWS_BUDGET")
        key = _key(row)
        identity_bytes += sum(len(str(part).encode("utf-8")) for part in key)
        if identity_bytes > MAX_HISTORY_IDENTITY_BYTES:
            raise Blocked("HOST_HISTORY_PPI_IDENTITY_BYTES_BUDGET")
        if key in wanted and _complete(key):
            matches.add(key)
    return matches, rows_scanned, identity_bytes


def _bucket():
    return {"catalog_active": 0, "catalog_prelim_ready": 0,
            "capability_gaps": 0, "gap_ppi_catalog_claimed": 0,
            "gap_complement_catalog_only": 0, "gap_ticker_ambiguous": 0,
            "gap_incomplete_five_key": 0, "exact_current_v2_ppi": 0,
            "exact_current_v2_complement_only": 0,
            "ticker_only_v2_mismatch": 0, "v2_current_absent": 0,
            "historic_ppi_exact_without_current": 0,
            "catalog_financial_contract_v17_declared": 0,
            "catalog_fund_terms_v1_declared": 0,
            "catalog_caucion_terms_v1_declared": 0,
            "catalog_contract_conflicts_declared": 0,
            "capability_codes": Counter()}


def _aggregate(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"paper_workspace", "financial_instrument_catalog",
                "contract_evidence_v2_current", "contract_evidence_v2_snapshots"}
    if not required <= names:
        raise Blocked("HOST_REQUIRED_TABLES_MISSING")
    _columns(conn, "paper_workspace", {
        "id", "namespace", "dataset_id", "origin", "imported_legacy", "created_at"})
    _columns(conn, "financial_instrument_catalog", {
        "ticker", "instrument_type", "market", "currency", "settlement",
        "settlement_source", "status", "capability", "metadata_json"})
    _columns(conn, "contract_evidence_v2_current", {
        "family", "ticker", "market", "currency", "settlement",
        "source_class", "snapshot_id", "evidence_hash", "observed_at"})
    _columns(conn, "contract_evidence_v2_snapshots", {
        "snapshot_id", "family", "ticker", "market", "currency", "settlement",
        "source_class", "evidence_hash", "observed_at"})
    identity = conn.execute("""SELECT id,namespace,dataset_id,origin,imported_legacy,
           created_at FROM paper_workspace LIMIT 2""").fetchall()
    if len(identity) != 1:
        raise Blocked("HOST_PAPER_WORKSPACE_UNVERIFIED")
    wid, ns, dataset, origin, imported, created = identity[0]
    try:
        valid_uuid = str(uuid.UUID(str(dataset))) == str(dataset)
        valid_created = datetime.fromisoformat(str(created)).tzinfo is not None
    except (TypeError, ValueError):
        valid_uuid = valid_created = False
    if not (wid == 1 and ns == NAMESPACE and origin == "FRESH_EMPTY"
            and imported == 0 and valid_uuid and valid_created):
        raise Blocked("HOST_PAPER_WORKSPACE_UNVERIFIED")
    rows = [dict(row) for row in conn.execute("""SELECT ticker,
        instrument_type AS family, market,currency,settlement,
        settlement_source,status,capability,metadata_json
        FROM financial_instrument_catalog LIMIT ?""", (MAX_CATALOG_ROWS + 1,))]
    if not 0 < len(rows) <= MAX_CATALOG_ROWS:
        raise Blocked("HOST_CATALOG_DENOMINATOR_INVALID")
    if sum(len(str(r["metadata_json"] or "")) for r in rows) > MAX_METADATA_BYTES:
        raise Blocked("HOST_CATALOG_METADATA_BUDGET")
    declared_current = conn.execute(
        "SELECT COUNT(*) FROM contract_evidence_v2_current").fetchone()[0]
    if declared_current > MAX_CURRENT_ROWS:
        raise Blocked("HOST_V2_CURRENT_BUDGET")
    current = [dict(row) for row in conn.execute("""SELECT
          c.family,c.ticker,c.market,c.currency,c.settlement,c.source_class,
          c.evidence_hash,c.observed_at,c.snapshot_id,
          s.family AS sf,s.ticker AS st,s.market AS sm,s.currency AS sc,
          s.settlement AS ss,s.source_class AS so,
          s.evidence_hash AS sh,s.observed_at AS sa
        FROM contract_evidence_v2_current c
        LEFT JOIN contract_evidence_v2_snapshots s
          ON s.snapshot_id=c.snapshot_id LIMIT ?""", (MAX_CURRENT_ROWS + 1,))]
    if len(current) != declared_current:
        raise Blocked("HOST_V2_ORPHAN_OR_BOUND")
    by_key = defaultdict(set)
    by_ticker = Counter()
    for item in current:
        if (item["sf"] is None or _key(item) != _key({
                "family": item["sf"], "ticker": item["st"],
                "market": item["sm"], "currency": item["sc"],
                "settlement": item["ss"]})
                or item["source_class"] != item["so"]
                or item["evidence_hash"] != item["sh"]
                or item["observed_at"] != item["sa"]):
            raise Blocked("HOST_V2_CURRENT_SNAPSHOT_INTEGRITY_INVALID")
        key = _key(item)
        by_ticker[key[1]] += 1
        if _complete(key):
            by_key[key].add(str(item["source_class"]))
    ppi_symbol = Counter()
    for row in rows:
        meta = _metadata(row["metadata_json"])
        row["_meta"] = meta
        row["_ppi_claim"] = _primary_claim(row, meta)
        if row["_ppi_claim"]:
            ppi_symbol[str(row["ticker"] or "").strip().upper()] += 1
    out = {family: _bucket() for family in FAMILIES}
    pending_historical = []
    for row in rows:
        key = _key(row)
        bucket = _family(row["family"])
        if bucket not in out or str(row["status"] or "").upper() not in ACTIVE:
            continue
        item = out[bucket]
        item["catalog_active"] += 1
        if str(row["capability"] or "").startswith("READY_PAPER_"):
            item["catalog_prelim_ready"] += 1
            continue
        item["capability_gaps"] += 1
        item["gap_ppi_catalog_claimed" if row["_ppi_claim"]
             else "gap_complement_catalog_only"] += 1
        if ppi_symbol[key[1]] != 1:
            item["gap_ticker_ambiguous"] += 1
        meta = row["_meta"]
        if isinstance(meta.get("financial_contract_v17"), dict) and meta["financial_contract_v17"]:
            item["catalog_financial_contract_v17_declared"] += 1
        if isinstance(meta.get("paper_family_contract_v1"), dict) and meta["paper_family_contract_v1"]:
            item["catalog_fund_terms_v1_declared"] += 1
        if isinstance(meta.get("paper_caucion_contract_v1"), dict) and meta["paper_caucion_contract_v1"]:
            item["catalog_caucion_terms_v1_declared"] += 1
        if meta.get("_contract_conflicts"):
            item["catalog_contract_conflicts_declared"] += 1
        code = str(row["capability"] or "")
        code = code if re.fullmatch(r"[A-Z_]{2,65}", code) else "OTHER_OR_UNSAFE_CODE"
        item["capability_codes"][code] += 1
        if not _complete(key):
            item["gap_incomplete_five_key"] += 1
            continue
        classes = by_key.get(key, set())
        primary = any(x.startswith("PPI_") for x in classes)
        if primary:
            item["exact_current_v2_ppi"] += 1
        elif classes:
            item["exact_current_v2_complement_only"] += 1
        elif by_ticker[key[1]]:
            item["ticker_only_v2_mismatch"] += 1
        else:
            item["v2_current_absent"] += 1
        if not primary:
            pending_historical.append((key, item))
    historic, historical_rows, historical_bytes = _historical_ppi_memberships(
        conn, {key for key, _ in pending_historical})
    for key, item in pending_historical:
        if key in historic:
            item["historic_ppi_exact_without_current"] += 1
    families = []
    for family in FAMILIES:
        families.append({name: (dict(sorted(value.items())) if isinstance(value, Counter)
                                else value)
                         for name, value in {"family": family, **out[family]}.items()})
    return len(rows), len(current), historical_rows, historical_bytes, families


def probe(*, data_root=DATA_ROOT, db=DB, guard=private_ro_mount_verified):
    if not guard(data_root):
        raise Blocked("HOST_PRIVATE_RO_MOUNT_UNVERIFIED")
    sidecars = _safe_files(data_root, db)
    conn = None
    try:
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        start = time.monotonic()
        conn.set_progress_handler(lambda: int(time.monotonic() - start > MAX_SECONDS), 1000)
        conn.execute("BEGIN")
        catalog_rows, current_rows, historical_rows, historical_bytes, families = _aggregate(conn)
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        raise Blocked("HOST_SQLITE_READ_FAILED_OR_WAL_PERMISSION") from None
    finally:
        if conn is not None:
            conn.close()
    return {"schema": SCHEMA, "status": "DIAGNOSTIC_ONLY",
            "runtime_path": "PAPER_V17_CANONICAL_RO", "private_ro_mount_verified": True,
            "paper_workspace_identity_verified": True,
            "ppi_identity_authority_verified": False,
            "source_contract_terms_fresh_verified": False,
            "causal_pit_verified": False, "v2_payload_hash_checked": False,
            "host_db_writes": 0, "provider_gets": 0, "real_orders_sent_by_probe": 0,
            "paper_auto_enabled": False, "host_runtime_order_count": "NO_VERIFICADO",
            "catalog_rows": catalog_rows, "v2_current_rows": current_rows,
            "v2_history_ppi_rows_scanned": historical_rows,
            "v2_history_identity_bytes_scanned": historical_bytes,
            "historical_819_gap_match": sum(x["capability_gaps"] for x in families) == 819,
            "capability_gaps_measured": sum(x["capability_gaps"] for x in families),
            "families": families, **sidecars,
            "measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def main():
    try:
        result = probe()
        print(json.dumps(result, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")))
        return 0
    except (Blocked, OSError) as exc:
        reason = str(exc) if isinstance(exc, Blocked) else "HOST_FS_READ_UNAVAILABLE"
        print(json.dumps({"schema": SCHEMA, "status": "NO_VERIFICADO",
                          "reason": reason, "host_db_writes": 0,
                          "provider_gets": 0, "paper_auto_enabled": False},
                         sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
