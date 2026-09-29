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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")

import ci_ppi_bond_estimate_patch_hf6  # noqa: F401 - installs GET-only extension
import ch_contract_evidence_hf6 as evidence
import cm_special_family_discovery_hf6 as discovery
import cr_contract_evidence_v2_mass_hf6 as evidence_v2_mass
import rc6_official_source_adapters as official_sources
from bd_ppi_readonly_guard import ProductionMarketReader


DB = os.getenv("POROTA_CONTRACT_EVIDENCE_DB", "/app/data/paper_v17/observer_v17.db")
SECRET = os.getenv("PPI_PRODUCTION_SECRET_FILE", "/run/secrets/ppi_production.json")
CONTRACT_EVIDENCE_MODE = os.getenv("POROTA_CONTRACT_EVIDENCE_MODE", "ENABLED").upper()


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    def event(self, event, detail):
        with self.connect() as c:
            c.execute("INSERT INTO paper_events VALUES(NULL,?,?,?,?,?)", (
                datetime.now(timezone.utc).isoformat(), "PAPER_ENGINE",
                str(event), None, str(detail)[:2000]))


def observer_state(store):
    with store.connect() as c:
        row = c.execute("""SELECT mode,process_state,session_state,ppi_auth,real_orders_sent
          FROM observer_state WHERE id=1""").fetchone()
    return tuple(row) if row else None


def readiness_counts(store):
    with store.connect() as c:
        total = c.execute("""SELECT COUNT(*) FROM financial_instrument_catalog
          WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER_%'""").fetchone()[0]
        by_family = dict(c.execute("""SELECT instrument_type,COUNT(*)
          FROM financial_instrument_catalog
          WHERE status='AVAILABLE' AND capability LIKE 'READY_PAPER_%'
          GROUP BY instrument_type ORDER BY instrument_type""").fetchall())
    return {"total": int(total), "by_family": {str(k): int(v) for k, v in by_family.items()}}


def main():
    if CONTRACT_EVIDENCE_MODE != "ENABLED":
        print("STATUS=SKIPPED_SOURCE_UNAVAILABLE_BY_SCOPE")
        print("CONTRACT_EVIDENCE_MODE=" + CONTRACT_EVIDENCE_MODE)
        print("PROFILE_PRESERVED=YES")
        return 0

    store = Store(DB)
    before = observer_state(store)
    print("OBSERVER_BEFORE=" + repr(before))
    if not before:
        raise SystemExit("OBSERVER_STATE_MISSING")
    mode, process_state, session_state, ppi_auth, real_orders = before
    if mode != "PRODUCTION_PAPER" or int(real_orders or 0) != 0:
        raise SystemExit("FAIL_CLOSED_RUNTIME_INVARIANT")
    if session_state not in {"MARKET_CLOSED", "CLOSED"}:
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
        run_id = "contract-evidence-v1-" + uuid.uuid4().hex
        result = evidence.collect(reader, store, run_id=run_id)
        probes = discovery.probe(reader, store)
        official = official_sources.collect(store)
        print("OFFICIAL_SOURCE_RESULT=" + json.dumps(official, ensure_ascii=False, sort_keys=True))
        print("EVIDENCE_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
        print("DISCOVERY_PROBES=" + json.dumps(probes, ensure_ascii=False, sort_keys=True))
        print("PPI_READER_METRICS=" + json.dumps(reader.metrics, sort_keys=True))
    finally:
        reader.close()

    ready_before = readiness_counts(store)
    v2_run_id = "contract-evidence-v2-mass-" + uuid.uuid4().hex
    mass_result = evidence_v2_mass.collect(store, run_id=v2_run_id)
    from bf_production_paper_observer import _reconcile_complementary_catalog
    promoted = _reconcile_complementary_catalog(store)
    ready_after = readiness_counts(store)
    print("EVIDENCE_V2_MASS_RESULT=" + json.dumps(mass_result, ensure_ascii=False, sort_keys=True))
    print("CATALOG_RECONCILIATION=" + json.dumps({
        "promoted_this_run": promoted,
        "ready_before": ready_before,
        "ready_after": ready_after,
    }, ensure_ascii=False, sort_keys=True))

    after = observer_state(store)
    print("OBSERVER_AFTER=" + repr(after))
    if not after or after[0] != "PRODUCTION_PAPER" or int(after[-1] or 0) != 0:
        raise SystemExit("FAIL_CLOSED_RUNTIME_CHANGED")

    with store.connect() as c:
        quick = c.execute("PRAGMA quick_check").fetchone()[0]
        latest = c.execute("""SELECT total,verified,blocked_porota,blocked_provider,finished_at
          FROM contract_evidence_runs ORDER BY finished_at DESC LIMIT 1""").fetchone()
        latest_v2 = c.execute("""SELECT job_key,state,records,changed,conflicts,finished_at
          FROM contract_evidence_v2_runs WHERE run_id=?""", (v2_run_id,)).fetchone()
    print("QUICK_CHECK=" + str(quick))
    print("LATEST_EVIDENCE_RUN=" + repr(tuple(latest) if latest else None))
    print("LATEST_EVIDENCE_V2_RUN=" + repr(tuple(latest_v2) if latest_v2 else None))
    if quick != "ok":
        raise SystemExit("SQLITE_QUICK_CHECK_FAILED")
    if not latest_v2 or latest_v2[1] != "OK":
        raise SystemExit("EVIDENCE_V2_MASS_RUN_FAILED")
    print("STATUS=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
