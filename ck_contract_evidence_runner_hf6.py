"""Scheduled HF6 contract evidence collection.

Runs only while the PAPER observer reports MARKET_CLOSED. It authenticates to
PPI production through the existing read-only guard and may call only the
allowlisted market/configuration endpoints plus documented Bonds/Estimate.
No account, budget, confirm, cancel or order endpoint is reachable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/app")

import ci_ppi_bond_estimate_patch_hf6  # noqa: F401 - installs GET-only extension
import ch_contract_evidence_hf6 as evidence
import cm_special_family_discovery_hf6 as discovery
from bd_ppi_readonly_guard import ProductionMarketReader


DB = os.getenv("POROTA_CONTRACT_EVIDENCE_DB", "/app/data/paper_v17/observer_v17.db")
SECRET = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c


def observer_state(store):
    with store.connect() as c:
        row = c.execute("""SELECT mode,process_state,session_state,ppi_auth,real_orders_sent
          FROM observer_state WHERE id=1""").fetchone()
    return tuple(row) if row else None


def main():
    store = Store(DB)
    before = observer_state(store)
    print("OBSERVER_BEFORE=" + repr(before))
    if not before:
        raise SystemExit("OBSERVER_STATE_MISSING")
    mode, process_state, session_state, ppi_auth, real_orders = before
    if mode != "PRODUCTION_PAPER" or int(real_orders or 0) != 0:
        raise SystemExit("FAIL_CLOSED_RUNTIME_INVARIANT")
    if session_state != "MARKET_CLOSED":
        print("STATUS=SKIPPED_MARKET_NOT_CLOSED")
        return 0

    secret = json.loads(Path(SECRET).read_text(encoding="utf-8"))
    api_key = secret.get("api_key") or ""
    api_secret = secret.get("api_secret") or ""
    if not api_key or not api_secret:
        raise SystemExit("PPI_SECRET_INCOMPLETE")

    reader = ProductionMarketReader(api_key, api_secret)
    try:
        reader.login_once()
        run_id = "contract-evidence-" + uuid.uuid4().hex
        result = evidence.collect(reader, store, run_id=run_id)
        probes = discovery.probe(reader, store)
        print("EVIDENCE_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
        print("DISCOVERY_PROBES=" + json.dumps(probes, ensure_ascii=False, sort_keys=True))
        print("PPI_READER_METRICS=" + json.dumps(reader.metrics, sort_keys=True))
    finally:
        reader.close()

    after = observer_state(store)
    print("OBSERVER_AFTER=" + repr(after))
    if not after or after[0] != "PRODUCTION_PAPER" or int(after[-1] or 0) != 0:
        raise SystemExit("FAIL_CLOSED_RUNTIME_CHANGED")

    with store.connect() as c:
        quick = c.execute("PRAGMA quick_check").fetchone()[0]
        latest = c.execute("""SELECT total,verified,blocked_porota,blocked_provider,finished_at
          FROM contract_evidence_runs ORDER BY finished_at DESC LIMIT 1""").fetchone()
    print("QUICK_CHECK=" + str(quick))
    print("LATEST_EVIDENCE_RUN=" + repr(tuple(latest) if latest else None))
    if quick != "ok":
        raise SystemExit("SQLITE_QUICK_CHECK_FAILED")
    print("STATUS=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
