#!/usr/bin/env python3
"""A729: bounded, immutable SQLite schema and Porota-only backup inventory.

This is deliberately NOT a deletion utility. Historical/PIT pointers and
cross-database pins remain unproven; even a row older than 365 ART dates is
NOT a deletable row. Production DB/WAL, backups and other apps are untouched.

Host execution has no caller-supplied paths or SQL. A private runner collects
the raw JSON and publishes only the strict allowlisted sanitized receipt.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from zoneinfo import ZoneInfo


SCHEMA = "rc6.a729.db-backup-inventory-readonly.v1"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
DATA = Path("/opt/porota-trading/data")
DB_TARGETS = (
    ("HISTORY", DATA / "market_history.db", 2 * 1024**3),
    ("OBSERVER", DATA / "paper_v17/observer_v17.db", 5 * 1024**3),
)
BACKUP_TARGETS = (
    ("GENERAL", Path("/opt/porota-backups"), re.compile(r"porota-general-[A-Za-z0-9_.-]{1,80}\.tar\.gz\Z")),
    ("PAPER", DATA / "paper_v17/artifacts/observer_v17.db/backups/paper", re.compile(r"observer_[A-Za-z0-9_.-]{1,80}\.db\.gz\Z")),
    ("HISTORY", DATA / "paper_v17/artifacts/observer_v17.db/backups/history", re.compile(r"market_history_[A-Za-z0-9_.-]{1,80}\.db\.gz\Z")),
)
DATED_TABLES = frozenset({"history_versions_v2", "market_historical_ohlcv"})
BOOK_CONTRACT = {
    # Exact A726 SQL contract, publicly defined in the main-branch probe.
    "observer_state": ("id", "mode", "real_orders_sent", "process_state", "session_state"),
    "paper_positions": ("status",),
    "paper_future_positions": ("status",),
    "paper_cauciones": ("status",),
    "paper_family_lifecycle": ("family", "state"),
}
PUBLIC_TABLES = frozenset(BOOK_CONTRACT) | frozenset({
    "history_versions_v2", "market_historical_ohlcv", "historical_raw_archive",
})
MAX_TABLES = 96
MAX_BACKUP_ENTRIES = 1000
MAX_RAW_BYTES = 65536
DB_SCAN_SECONDS = 4.0
ALLOWED_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
DB_STATUSES = frozenset({"SCANNED", "SQL_PARTIAL_BOUNDED", "SCHEMA_UNAVAILABLE", "MISSING",
                         "PATH_UNSAFE", "FILE_UNSAFE", "SIZE_LIMIT", "WAL_BASE_ONLY",
                         "ROLLBACK_JOURNAL_ACTIVE", "CHANGED_DURING_SCAN", "UNAVAILABLE"})
BACKUP_STATUSES = frozenset({"SCANNED", "MISSING", "PATH_UNSAFE", "UNAVAILABLE",
                             "BOUNDED", "CHANGED_DURING_SCAN"})
TABLE_STATUSES = frozenset({"METADATA_ONLY", "TIME_BUDGET", "SQL_UNAVAILABLE"})
SIDECAR_STATES = frozenset({"REGULAR", "MISSING", "FILE_UNSAFE", "UNAVAILABLE"})
JOURNAL_STATES = frozenset({"WAL_HEADER", "ROLLBACK_HEADER", "UNKNOWN"})
CONTRACT_STATES = frozenset({"PRESENT", "MISSING_COLUMNS", "TABLE_ABSENT", "NO_VERIFICADO"})
SCHEMA_SCOPES = frozenset({"NO_VERIFICADO", "IMMUTABLE_BASE_STABLE",
                           "BASE_FILE_ONLY_WAL_UNRECONCILED"})
PRAGMA_MODES = frozenset({"wal", "delete", "truncate", "persist", "memory", "off"})
MAX_NUMBER = 2**60


class InventoryError(Exception):
    """Expected fail-closed inventory condition (never published verbatim)."""


def _anchored_directory(path: Path) -> int:
    """Anchor every absolute parent component with NOFOLLOW; reject symlinks."""
    if not path.is_absolute() or ".." in path.parts or not hasattr(os, "O_PATH"):
        raise InventoryError("PATH_UNSAFE")
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open("/", flags)
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except (OSError, ValueError) as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise InventoryError("MISSING" if isinstance(exc, FileNotFoundError)
                             else "PATH_UNSAFE") from exc


def _file_metadata(parent_fd: int, name: str) -> tuple[str, tuple | None]:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "MISSING", None
    except (OSError, ValueError):
        return "UNAVAILABLE", None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return "FILE_UNSAFE", None
    return "REGULAR", (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                       info.st_ctime_ns, info.st_nlink, info.st_mode,
                       info.st_uid, info.st_gid)


def _stable_file(parent_fd: int, name: str, before: tuple | None) -> bool:
    status, after = _file_metadata(parent_fd, name)
    return status == "REGULAR" and after == before


def _db_public(role: str, status: str, size: int | None = None) -> dict:
    return {"role": role, "status": status, "size_bytes": size,
            "wal_bytes": None, "shm_bytes": None, "journal_bytes": None,
            "wal_state": "UNAVAILABLE", "shm_state": "UNAVAILABLE",
            "journal_state": "UNAVAILABLE", "wal_owner_matches_db": None,
            "shm_owner_matches_db": None, "wal_same_group_as_db": None,
            "shm_same_group_as_db": None, "shm_owner_readable": None,
            "wal_owner_readable": None, "sqlite_header_mode": "UNKNOWN",
            "pragma_immutable_journal_mode": None,
            "schema_scope": "NO_VERIFICADO", "page_size": None,
            "page_count": None, "freelist_count": None, "tables": [],
            "tables_seen": 0, "tables_inspected": 0,
            "book_contract": [{"name": name, "state": "NO_VERIFICADO",
                               "missing_required_columns": []} for name in BOOK_CONTRACT],
            "retention_delete_allowed": False, "physical_reclaim_estimate_bytes": None}


def _integer_pragma(connection: sqlite3.Connection, name: str) -> int | None:
    value = connection.execute(f"PRAGMA {name}").fetchone()
    result = value[0] if value else None
    return result if type(result) is int and 0 <= result <= MAX_NUMBER else None


def _table_inventory(connection: sqlite3.Connection) -> tuple[list[dict], int, bool, list[dict]]:
    """Read only sqlite_schema and PRAGMA metadata; never scan table records.

    Even SELECT COUNT(*) can scan a full index on a large host DB. Since no
    current CLOSED/flat/write-quiescence proof exists, counting live records
    violates the source policy; keep exact counts and 365d rows NO_VERIFICADO.
    """
    start = time.monotonic()
    overall_end = start + DB_SCAN_SECONDS
    names = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name LIMIT ?",
        (MAX_TABLES + 1,)).fetchall()
    overflow = len(names) > MAX_TABLES
    result = []
    contracts = {name: {"name": name, "state": "TABLE_ABSENT",
                        "missing_required_columns": []} for name in BOOK_CONTRACT}
    for ordinal, row in enumerate(names[:MAX_TABLES], 1):
        actual_name = row[0]
        if not isinstance(actual_name, str) or not ALLOWED_NAME.fullmatch(actual_name):
            # Never emit uncontrolled names from a private database.
            name = f"OTHER_TABLE_{ordinal}"
            result.append({"name": name, "status": "SQL_UNAVAILABLE",
                           "rows": None, "older_than_365_art_dates": None,
                           "columns_count": None, "indexes_count": None,
                           "has_date_column": None})
            continue
        name = actual_name if actual_name in PUBLIC_TABLES else f"OTHER_TABLE_{ordinal}"
        table = {"name": name, "status": "TIME_BUDGET", "rows": None,
                 "older_than_365_art_dates": None, "columns_count": None,
                 "indexes_count": None, "has_date_column": None}
        if time.monotonic() >= overall_end:
            result.append(table)
            continue
        connection.set_progress_handler(lambda: int(time.monotonic() > overall_end), 2000)
        try:
            quoted = '"' + actual_name.replace('"', '""') + '"'
            columns = [item[1] for item in connection.execute(f"PRAGMA table_info({quoted})")]
            indexes = [item[1] for item in connection.execute(f"PRAGMA index_list({quoted})")]
            table.update(status="METADATA_ONLY", columns_count=len(columns),
                         indexes_count=len(indexes), has_date_column="date" in columns)
            if name in contracts:
                missing = sorted(set(BOOK_CONTRACT[name]) - set(columns))
                contracts[name] = {"name": name,
                                   "state": "MISSING_COLUMNS" if missing else "PRESENT",
                                   "missing_required_columns": missing}
        except sqlite3.OperationalError as exc:
            if "interrupted" not in str(exc).lower():
                table["status"] = "SQL_UNAVAILABLE"
            else:
                table["status"] = "TIME_BUDGET"
        except sqlite3.DatabaseError:
            table["status"] = "SQL_UNAVAILABLE"
        finally:
            connection.set_progress_handler(None, 0)
        result.append(table)
    if overflow:
        # A truncated table listing cannot prove that a missing expected table
        # is absent. Keep only explicitly seen table contracts.
        for item in contracts.values():
            if item["state"] == "TABLE_ABSENT":
                item["state"] = "NO_VERIFICADO"
    return result, len(names), overflow, list(contracts.values())


def inventory_db(role: str, path: Path, maximum: int, cutoff: str) -> dict:
    receipt = _db_public(role, "UNAVAILABLE")
    try:
        parent_fd = _anchored_directory(path.parent)
    except InventoryError as exc:
        receipt["status"] = "MISSING" if str(exc) == "MISSING" else "PATH_UNSAFE"
        return receipt
    try:
        status, before = _file_metadata(parent_fd, path.name)
        if status != "REGULAR":
            receipt["status"] = status if status in ("MISSING", "UNAVAILABLE") else "FILE_UNSAFE"
            return receipt
        assert before is not None
        size = before[2]
        receipt["size_bytes"] = size
        if not 4096 <= size <= maximum:
            receipt["status"] = "SIZE_LIMIT"
            return receipt
        sidecars_before = {}
        for suffix, key, statekey in (("-wal", "wal_bytes", "wal_state"),
                                      ("-shm", "shm_bytes", "shm_state"),
                                      ("-journal", "journal_bytes", "journal_state")):
            scstatus, scmarker = _file_metadata(parent_fd, path.name + suffix)
            receipt[statekey] = scstatus
            if scstatus not in ("MISSING", "REGULAR"):
                receipt["status"] = "FILE_UNSAFE"
                return receipt
            sidecars_before[suffix] = (scstatus, scmarker)
            receipt[key] = scmarker[2] if scmarker is not None else 0
            if suffix in ("-wal", "-shm") and scmarker is not None:
                prefix = "wal" if suffix == "-wal" else "shm"
                receipt[f"{prefix}_owner_matches_db"] = scmarker[7] == before[7]
                receipt[f"{prefix}_same_group_as_db"] = scmarker[8] == before[8]
                receipt[f"{prefix}_owner_readable"] = bool(scmarker[6] & stat.S_IRUSR)
        if receipt["journal_bytes"]:
            receipt["status"] = "ROLLBACK_JOURNAL_ACTIVE"
            return receipt
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=parent_fd)
        except OSError:
            receipt["status"] = "CHANGED_DURING_SCAN"
            return receipt
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_ino != before[1]:
                receipt["status"] = "CHANGED_DURING_SCAN"
                return receipt
            header = os.pread(fd, 20, 0)
            if header[:16] == b"SQLite format 3\x00":
                receipt["sqlite_header_mode"] = (
                    "WAL_HEADER" if header[18:20] == b"\x02\x02" else
                    "ROLLBACK_HEADER" if header[18:20] == b"\x01\x01" else "UNKNOWN")
            # /proc/self/fd binds SQLite to the already-opened, no-follow inode;
            # immutable=1 cannot create journal/WAL, even if source is read-only.
            uri = f"file:/proc/self/fd/{fd}?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as database:
                database.execute("PRAGMA query_only=ON")
                database.execute("PRAGMA temp_store=MEMORY")
                mode = database.execute("PRAGMA journal_mode").fetchone()
                if mode and mode[0] in ("wal", "delete", "truncate", "persist", "memory", "off"):
                    receipt["pragma_immutable_journal_mode"] = mode[0]
                receipt["page_size"] = _integer_pragma(database, "page_size")
                receipt["page_count"] = _integer_pragma(database, "page_count")
                receipt["freelist_count"] = _integer_pragma(database, "freelist_count")
                tables, seen, overflow, contracts = _table_inventory(database)
                receipt["tables"] = tables
                receipt["book_contract"] = contracts
                receipt["tables_seen"] = seen
                receipt["tables_inspected"] = sum(t["status"] == "METADATA_ONLY" for t in tables)
                receipt["schema_scope"] = ("BASE_FILE_ONLY_WAL_UNRECONCILED"
                                           if receipt["wal_bytes"] else "IMMUTABLE_BASE_STABLE")
                receipt["status"] = ("WAL_BASE_ONLY" if receipt["wal_bytes"] else
                    "SQL_PARTIAL_BOUNDED" if overflow or any(
                        t["status"] != "METADATA_ONLY" for t in tables) else "SCANNED")
        except (OSError, sqlite3.DatabaseError, sqlite3.OperationalError):
            receipt["status"] = "SCHEMA_UNAVAILABLE"
        finally:
            os.close(fd)
        if not _stable_file(parent_fd, path.name, before):
            receipt = _db_public(role, "CHANGED_DURING_SCAN")
        for suffix, (old_status, old_marker) in sidecars_before.items():
            new_status, new_marker = _file_metadata(parent_fd, path.name + suffix)
            if (old_status, old_marker) != (new_status, new_marker):
                receipt = _db_public(role, "CHANGED_DURING_SCAN")
                break
        return receipt
    finally:
        os.close(parent_fd)


def inventory_backups(role: str, root: Path, name_re: re.Pattern, cutoff: datetime) -> dict:
    receipt = {"role": role, "status": "UNAVAILABLE", "matched_files": 0,
               "matched_bytes": 0, "older_365_file_mtime_count": 0,
               "older_365_file_mtime_bytes": 0, "unexpected_entries": 0,
               "unsafe_entries": 0, "files_seen": 0, "delete_allowed": False}
    try:
        directory_fd = _anchored_directory(root)
    except InventoryError as exc:
        receipt["status"] = "MISSING" if str(exc) == "MISSING" else "PATH_UNSAFE"
        return receipt
    try:
        before = os.fstat(directory_fd)
        # The path walk uses O_PATH to reject symlinks; scandir requires an
        # independently opened read-only directory descriptor on that inode.
        read_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                          dir_fd=directory_fd)
        try:
            with os.scandir(read_fd) as entries:
                for entry in entries:
                    receipt["files_seen"] += 1
                    if receipt["files_seen"] > MAX_BACKUP_ENTRIES:
                        receipt["status"] = "BOUNDED"
                        break
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        receipt["unsafe_entries"] += 1
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        receipt["unsafe_entries"] += 1
                        continue
                    if not name_re.fullmatch(entry.name) or not 0 <= info.st_size <= MAX_NUMBER:
                        receipt["unexpected_entries"] += 1
                        continue
                    receipt["matched_files"] += 1
                    receipt["matched_bytes"] += info.st_size
                    if datetime.fromtimestamp(info.st_mtime, tz=timezone.utc) < cutoff:
                        receipt["older_365_file_mtime_count"] += 1
                        receipt["older_365_file_mtime_bytes"] += info.st_size
        finally:
            os.close(read_fd)
        if receipt["status"] != "BOUNDED":
            receipt["status"] = "SCANNED"
        after = os.fstat(directory_fd)
        if (before.st_ino, before.st_dev, before.st_mtime_ns) != (
                after.st_ino, after.st_dev, after.st_mtime_ns):
            receipt["status"] = "CHANGED_DURING_SCAN"
    except (OSError, OverflowError):
        receipt["status"] = "UNAVAILABLE"
    finally:
        os.close(directory_fd)
    return receipt


def host_inventory() -> dict:
    now = datetime.now(timezone.utc)
    cutoff_day = now.astimezone(ART).date() - timedelta(days=364)
    cutoff = datetime.combine(cutoff_day, datetime.min.time(), tzinfo=ART).astimezone(timezone.utc)
    databases = [inventory_db(role, path, maximum, cutoff_day.isoformat())
                 for role, path, maximum in DB_TARGETS]
    backups = [inventory_backups(role, path, pattern, cutoff)
               for role, path, pattern in BACKUP_TARGETS]
    return {"schema": SCHEMA, "state": "INVENTORY_ONLY", "generated_at_utc": now.isoformat(),
            "retention_cutoff_art_inclusive": cutoff_day.isoformat(),
            "db_inventory": databases, "backup_inventory": backups,
            "read_only": True, "db_immutable_ro": True,
            "host_mutations": 0, "db_rows_deleted": 0, "backup_files_deleted": 0,
            "ppi_watch_touched": False, "provider_calls": 0, "real_orders_sent_by_action": 0,
            "purge_allowed": False, "db_delete_authorized": False,
            "backup_delete_authorized": False,
            "limitations": ["PINS_AND_365D_LINEAGE_NOT_PROVEN", "NO_HOST_PAPER_LEDGER_PROOF",
                            "BACKUP_MTIME_NOT_DATA_RETENTION", "NO_HOST_FULL_TABLE_SCANS",
                            "ROW_COUNTS_NO_VERIFICADO"]}


def _int(value: object, maximum: int = MAX_NUMBER) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def public_receipt(raw: object, remote_rc: int) -> dict:
    failure = {"schema": SCHEMA, "state": "NO_VERIFICADO", "failure_class": "HOST_UNAVAILABLE",
               "read_only": True, "host_mutations": 0, "db_rows_deleted": 0,
               "backup_files_deleted": 0, "purge_allowed": False, "ppi_watch_touched": False,
               "real_orders_sent_by_action": 0}
    if remote_rc != 0 or not isinstance(raw, dict):
        return failure
    require = {"schema": SCHEMA, "state": "INVENTORY_ONLY", "read_only": True,
               "db_immutable_ro": True, "host_mutations": 0, "db_rows_deleted": 0,
               "backup_files_deleted": 0, "ppi_watch_touched": False, "provider_calls": 0,
               "real_orders_sent_by_action": 0, "purge_allowed": False,
               "db_delete_authorized": False, "backup_delete_authorized": False}
    if any(raw.get(key) != value or type(raw.get(key)) is not type(value)
           for key, value in require.items()):
        return dict(failure, failure_class="INVARIANT_NOT_PROVEN")
    try:
        timestamp = datetime.fromisoformat(raw["generated_at_utc"])
        assert timestamp.tzinfo is not None
        cutoff = raw["retention_cutoff_art_inclusive"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff)
        assert isinstance(raw["db_inventory"], list) and len(raw["db_inventory"]) == 2
        assert isinstance(raw["backup_inventory"], list) and len(raw["backup_inventory"]) == 3
        clean_dbs = []
        for value, role in zip(raw["db_inventory"], ("HISTORY", "OBSERVER")):
            assert isinstance(value, dict) and value.get("role") == role
            assert value["status"] in DB_STATUSES
            fields = ("size_bytes", "wal_bytes", "shm_bytes", "journal_bytes", "page_size",
                      "page_count", "freelist_count", "tables_seen", "tables_inspected")
            ints = {key: _int(value.get(key)) for key in fields}
            assert all(value.get(key) is None or ints[key] is not None for key in fields)
            details = {}
            for part in ("wal", "shm"):
                state = value.get(f"{part}_state")
                assert state in SIDECAR_STATES
                details[f"{part}_state"] = state
                for key in ("owner_matches_db", "same_group_as_db", "owner_readable"):
                    result = value.get(f"{part}_{key}")
                    assert result is None or type(result) is bool
                    details[f"{part}_{key}"] = result
            assert value.get("journal_state") in SIDECAR_STATES
            assert value.get("sqlite_header_mode") in JOURNAL_STATES
            assert value.get("schema_scope") in SCHEMA_SCOPES
            pragma = value.get("pragma_immutable_journal_mode")
            assert pragma is None or pragma in PRAGMA_MODES
            details.update(journal_state=value["journal_state"],
                           sqlite_header_mode=value["sqlite_header_mode"],
                           pragma_immutable_journal_mode=pragma,
                           schema_scope=value["schema_scope"])
            assert isinstance(value["tables"], list) and len(value["tables"]) <= MAX_TABLES
            tables = []
            for table in value["tables"]:
                assert isinstance(table, dict) and isinstance(table.get("name"), str)
                assert table["name"] in PUBLIC_TABLES or re.fullmatch(
                    r"OTHER_TABLE_\d{1,2}", table["name"])
                assert table.get("status") in TABLE_STATUSES
                rows = _int(table.get("rows"))
                old = _int(table.get("older_than_365_art_dates"))
                # No host full-table scans: row/age counts MUST be unknown.
                assert table.get("rows") is None and table.get("older_than_365_art_dates") is None
                cols = _int(table.get("columns_count"), 1024)
                indexes = _int(table.get("indexes_count"), 1024)
                has_date = table.get("has_date_column")
                assert has_date is None or type(has_date) is bool
                assert table["status"] != "METADATA_ONLY" or (
                    cols is not None and indexes is not None and type(has_date) is bool)
                tables.append({"name": table["name"], "status": table["status"],
                               "rows": rows, "older_than_365_art_dates": old,
                               "columns_count": cols, "indexes_count": indexes,
                               "has_date_column": has_date})
            contracts = []
            assert isinstance(value.get("book_contract"), list)
            assert len(value["book_contract"]) == len(BOOK_CONTRACT)
            for contract, name in zip(value["book_contract"], BOOK_CONTRACT):
                assert isinstance(contract, dict) and contract.get("name") == name
                assert contract.get("state") in CONTRACT_STATES
                missing = contract.get("missing_required_columns")
                assert isinstance(missing, list)
                assert len(missing) <= len(BOOK_CONTRACT[name])
                assert len(set(missing)) == len(missing)
                assert all(item in BOOK_CONTRACT[name] for item in missing)
                contracts.append({"name": name, "state": contract["state"],
                                  "missing_required_columns": missing})
            clean_dbs.append({"role": role, "status": value["status"],
                              **ints, **details, "tables": tables,
                              "book_contract": contracts, "retention_delete_allowed": False,
                              "physical_reclaim_estimate_bytes": None})
        clean_backups = []
        for value, role in zip(raw["backup_inventory"], ("GENERAL", "PAPER", "HISTORY")):
            assert isinstance(value, dict) and value.get("role") == role
            assert value.get("status") in BACKUP_STATUSES
            keys = ("matched_files", "matched_bytes", "older_365_file_mtime_count",
                    "older_365_file_mtime_bytes", "unexpected_entries", "unsafe_entries",
                    "files_seen")
            vals = {key: _int(value.get(key)) for key in keys}
            assert all(num is not None for num in vals.values())
            clean_backups.append({"role": role, "status": value["status"],
                                  **vals, "delete_allowed": False})
        return {"schema": SCHEMA, "state": "INVENTORY_ONLY",
                "generated_at_utc": timestamp.isoformat(),
                "retention_cutoff_art_inclusive": cutoff,
                "db_inventory": clean_dbs, "backup_inventory": clean_backups,
                "read_only": True, "db_immutable_ro": True, "host_mutations": 0,
                "db_rows_deleted": 0, "backup_files_deleted": 0,
                "purge_allowed": False, "ppi_watch_touched": False,
                "real_orders_sent_by_action": 0,
                "db_delete_authorized": False, "backup_delete_authorized": False,
                "limitations": ["PINS_AND_365D_LINEAGE_NOT_PROVEN",
                                "NO_HOST_PAPER_LEDGER_PROOF", "BACKUP_MTIME_NOT_DATA_RETENTION",
                                "NO_HOST_FULL_TABLE_SCANS", "ROW_COUNTS_NO_VERIFICADO"]}
    except (AssertionError, KeyError, TypeError, ValueError, OverflowError):
        return dict(failure, failure_class="MALFORMED_HOST_RECEIPT")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("host-inventory", "sanitize"))
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--remote-rc", type=int, default=255)
    args = parser.parse_args()
    if args.mode == "host-inventory":
        print(json.dumps(host_inventory(), separators=(",", ":"), sort_keys=True))
        return 0
    if args.out is None or args.raw is None:
        parser.error("sanitize requires --raw and --out")
    try:
        if args.raw.stat().st_size > MAX_RAW_BYTES:
            raise InventoryError("RAW_LIMIT")
        raw = json.loads(args.raw.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, InventoryError):
        raw = None
    clean = public_receipt(raw, args.remote_rc)
    args.out.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if clean["state"] == "INVENTORY_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
