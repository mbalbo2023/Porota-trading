#!/usr/bin/env python3
"""Bounded read-only SQLite metadata probe inside the existing PAPER container.

No table scan, user rows, PRAGMA integrity_check, checkpoint or service change.
"""
import json
import os
from pathlib import Path
import sqlite3
import time

DB = Path("/app/data/paper_v17/observer_v17.db")
start = time.monotonic()
deadline = start + 12


def safe_count(value):
    return value if isinstance(value, int) and value >= 0 else None


def main():
    if DB.is_symlink() or not DB.is_file():
        raise SystemExit("PAPER_DB_NOT_REGULAR")
    files = {suffix: (DB.with_name(DB.name + suffix).stat().st_size
                      if DB.with_name(DB.name + suffix).is_file() else None)
             for suffix in ("", "-wal", "-shm")}
    result = {"schema": "RC6_732_SQLITE_METADATA_READONLY_V1", "at_unix": int(time.time()),
              "source": "paper_v17/observer_v17.db", "bytes": files,
              "bound": "12s, metadata only, no table row scan or checkpoint"}
    uri = DB.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=0.25)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        for key in ("page_size", "page_count", "freelist_count", "schema_version", "user_version"):
            result[key] = safe_count(connection.execute("PRAGMA " + key).fetchone()[0])
        schema = connection.execute("SELECT name,type,tbl_name FROM sqlite_master WHERE type IN ('table','index') ORDER BY type,name LIMIT 1000").fetchall()
        result["schema_objects_seen"] = len(schema)
        result["schema_object_limit_reached"] = len(schema) == 1000
        result["tables"] = [row[0] for row in schema if row[1] == "table"]
        result["index_count"] = sum(row[1] == "index" for row in schema)
        result["indexes_by_table"] = {name: sum(row[1] == "index" and row[2] == name for row in schema)
                                      for name in result["tables"]}
        result["freelist_bytes"] = result["page_size"] * result["freelist_count"]
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    finally:
        connection.close()
    print("RC6_732_SQLITE_METADATA=" + json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
