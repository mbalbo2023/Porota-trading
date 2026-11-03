#!/usr/bin/env python3
"""Bounded offhost audit of an already acquired PAPER SQLite + PPI journal.

Does not connect to a droplet, read a credential, call PPI/IOL, write SQLite,
build an artifact, or activate SELL. An authorized GitHub Action may supply a
read-only consistent copy and upload this JSON diagnostic as evidence.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.parse import quote

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rc6_fi_sell_persisted_evidence import inspect_persisted_fi_exit


MAX_POSITIONS = 100


class ReadOnlySnapshotStore:
    def __init__(self, path):
        self.path = Path(path)
        if (not self.path.is_absolute() or self.path.is_symlink()
                or not self.path.is_file()
                or not stat.S_ISREG(self.path.stat().st_mode)):
            raise ValueError("FI_EXIT_AUDIT_SQLITE_ABSOLUTE_REGULAR_COPY_REQUIRED")

    def connect(self):
        # URI mode=ro refuses write transactions and does not initialize/migrate
        # the runtime schema. PRAGMA query_only adds a second restriction.
        uri = "file:" + quote(os.fspath(self.path), safe="/") + "?mode=ro"
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db


def audit_snapshot(store, *, at, journal_path, paper_id=None, max_positions=25):
    if not 1 <= max_positions <= MAX_POSITIONS:
        raise ValueError("FI_EXIT_AUDIT_POSITION_BUDGET_INVALID")
    if paper_id is None:
        with closing(store.connect()) as db:
            rows = db.execute("""SELECT paper_id FROM paper_positions
                WHERE status='OPEN' AND asset_class IN ('BONOS','OBLIGACIONES')
                ORDER BY opened_at,paper_id LIMIT ?""", (max_positions + 1,)).fetchall()
        if len(rows) > max_positions:
            raise ValueError("FI_EXIT_AUDIT_POSITION_BUDGET_EXCEEDED")
        ids = [row[0] for row in rows]
    else:
        ids = [paper_id]
    results = [inspect_persisted_fi_exit(store, paper, at=at,
                                         journal_path=journal_path) for paper in ids]
    return {"schema": "rc6.fi-sell-offhost-ro-snapshot-audit.v1",
            "status": "BLOCKED", "paper_only": True, "runtime_assertion": False,
            "observer_or_provider_contacted": False, "db_write_attempted": False,
            "predeploy_or_deploy_executed": False, "real_routes_used": [],
            "scanned_paper_positions": len(results), "position_budget": max_positions,
            "evaluations": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="absolute path to consistent read-only SQLite copy")
    parser.add_argument("--journal", required=True, help="absolute path to private native PPI journal copy")
    parser.add_argument("--at", required=True, help="aware audit cutoff, ISO 8601")
    parser.add_argument("--paper-id", help="optional exact single PAPER position")
    parser.add_argument("--max-positions", type=int, default=25)
    args = parser.parse_args()
    try:
        result = audit_snapshot(ReadOnlySnapshotStore(args.db),at=args.at,
                                journal_path=args.journal,paper_id=args.paper_id,
                                max_positions=args.max_positions)
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = {"schema": "rc6.fi-sell-offhost-ro-snapshot-audit.v1",
                  "status": "HOLD", "paper_only": True,"runtime_assertion": False,
                  "real_routes_used": [],"reason": (str(exc) if isinstance(exc,ValueError)
                                                         and str(exc).startswith("FI_EXIT_")
                                                         else "FI_EXIT_AUDIT_SNAPSHOT_UNREADABLE")}
        print(json.dumps(result,sort_keys=True))
        return 2
    print(json.dumps(result,sort_keys=True,separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
