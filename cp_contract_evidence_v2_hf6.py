"""Versioned, append-only Contract Evidence v2 storage.

Snapshots and changes preserve provider-backed evidence.  RC4 also records a
sanitized run ledger so Scheduler/System can explain when collection ran, what
source class was used, what changed and why a run is blocked.  No secret,
cookie, token, account id or raw HTML belongs in this schema.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

SCHEMA = "porota-contract-evidence-v2"
SOURCE_RANK = {
    "PPI_STRUCTURED_API": 10,
    "PPI_AUTHENTICATED_XHR": 20,
    "PPI_OFFICIAL_DOCUMENTATION": 30,
    "PPI_AUTHENTICATED_WEB": 40,
    "PPI_SUPPORT": 50,
    "A3_PRIMARY_API": 10,
    "A3_RISK_POSTTRADE": 10,
    "A3_OFFICIAL_DOCUMENTATION": 30,
}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def evidence_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_snapshots(
          snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          source_class TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          effective_at TEXT,
          evidence_hash TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          UNIQUE(family,ticker,market,source_class,evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_current(
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          source_class TEXT NOT NULL,
          snapshot_id INTEGER NOT NULL REFERENCES contract_evidence_v2_snapshots(snapshot_id),
          evidence_hash TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          PRIMARY KEY(family,ticker,market,source_class)
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_changes(
          change_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          source_class TEXT NOT NULL,
          previous_hash TEXT,
          current_hash TEXT NOT NULL,
          detected_at TEXT NOT NULL,
          status TEXT NOT NULL,
          detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_runs(
          run_id TEXT PRIMARY KEY,
          job_key TEXT NOT NULL,
          source_class TEXT NOT NULL,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          state TEXT NOT NULL,
          auth_state TEXT NOT NULL,
          observed INTEGER NOT NULL DEFAULT 0,
          recorded INTEGER NOT NULL DEFAULT 0,
          changed INTEGER NOT NULL DEFAULT 0,
          conflicts INTEGER NOT NULL DEFAULT 0,
          blocked INTEGER NOT NULL DEFAULT 0,
          errors INTEGER NOT NULL DEFAULT 0,
          detail TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_contract_v2_snapshot_key
          ON contract_evidence_v2_snapshots(family,ticker,market,observed_at);
        CREATE INDEX IF NOT EXISTS idx_contract_v2_changes_key
          ON contract_evidence_v2_changes(family,ticker,market,detected_at);
        CREATE INDEX IF NOT EXISTS idx_contract_v2_runs_job
          ON contract_evidence_v2_runs(job_key,started_at);
        """)


def _safe_text(value, limit=1000):
    text=str(value or "").replace("\n"," ").replace("\r"," ").strip()
    return text[:limit]


def start_run(store, *, run_id, job_key, source_class, auth_state="NOT_REQUIRED",
              started_at=None, detail=""):
    if source_class not in SOURCE_RANK:
        raise ValueError("CONTRACT_V2_UNSUPPORTED_SOURCE")
    if not str(run_id or "").strip() or not str(job_key or "").strip():
        raise ValueError("CONTRACT_V2_RUN_ID_REQUIRED")
    init_schema(store)
    started_at=started_at or now_iso()
    with store.connect() as c:
        c.execute("""INSERT INTO contract_evidence_v2_runs
          (run_id,job_key,source_class,started_at,finished_at,state,auth_state,
           observed,recorded,changed,conflicts,blocked,errors,detail)
          VALUES(?,?,?,?,NULL,'RUNNING',?,0,0,0,0,0,0,?)""",
          (str(run_id),str(job_key),source_class,started_at,_safe_text(auth_state,120),
           _safe_text(detail)))
    return str(run_id)


def finish_run(store, *, run_id, state, auth_state=None, observed=0, recorded=0,
               changed=0, conflicts=0, blocked=0, errors=0, detail="",
               finished_at=None):
    """Finish a sanitized run. Counts must be non-negative integers."""
    counts=[observed,recorded,changed,conflicts,blocked,errors]
    try:
        counts=[int(value) for value in counts]
    except (TypeError,ValueError) as exc:
        raise ValueError("CONTRACT_V2_INVALID_RUN_COUNT") from exc
    if any(value < 0 for value in counts):
        raise ValueError("CONTRACT_V2_INVALID_RUN_COUNT")
    finished_at=finished_at or now_iso()
    init_schema(store)
    with store.connect() as c:
        row=c.execute("SELECT auth_state FROM contract_evidence_v2_runs WHERE run_id=?",
                      (str(run_id),)).fetchone()
        if not row:
            raise ValueError("CONTRACT_V2_RUN_NOT_STARTED")
        current_auth=row[0]
        c.execute("""UPDATE contract_evidence_v2_runs SET
          finished_at=?,state=?,auth_state=?,observed=?,recorded=?,changed=?,
          conflicts=?,blocked=?,errors=?,detail=? WHERE run_id=?""",
          (finished_at,_safe_text(state,80),_safe_text(auth_state or current_auth,120),
           *counts,_safe_text(detail),str(run_id)))


def record_snapshot(store, *, family, ticker, market, source_class,
                    source_ref, evidence, observed_at=None, effective_at=None):
    """Append provider-backed evidence and report whether the contract changed."""
    if source_class not in SOURCE_RANK:
        raise ValueError("CONTRACT_V2_UNSUPPORTED_SOURCE")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("CONTRACT_V2_EMPTY_EVIDENCE")

    family = str(family or "UNKNOWN").upper()
    ticker = str(ticker or "*").upper()
    market = str(market or "UNKNOWN").upper()
    observed_at = observed_at or now_iso()
    digest = evidence_hash(evidence)
    payload = canonical_json(evidence)

    init_schema(store)
    with store.connect() as c:
        previous = c.execute("""SELECT snapshot_id,evidence_hash,observed_at
          FROM contract_evidence_v2_current
          WHERE family=? AND ticker=? AND market=? AND source_class=?""",
          (family,ticker,market,source_class)).fetchone()

        c.execute("""INSERT OR IGNORE INTO contract_evidence_v2_snapshots
          (family,ticker,market,source_class,source_ref,observed_at,effective_at,
           evidence_hash,evidence_json) VALUES(?,?,?,?,?,?,?,?,?)""",
          (family,ticker,market,source_class,str(source_ref),observed_at,
           effective_at,digest,payload))
        snap = c.execute("""SELECT snapshot_id FROM contract_evidence_v2_snapshots
          WHERE family=? AND ticker=? AND market=? AND source_class=? AND evidence_hash=?""",
          (family,ticker,market,source_class,digest)).fetchone()
        snapshot_id = int(snap[0])

        previous_hash = previous[1] if previous else None
        changed = bool(previous_hash and previous_hash != digest)
        first_seen = previous is None

        c.execute("""INSERT INTO contract_evidence_v2_current
          (family,ticker,market,source_class,snapshot_id,evidence_hash,observed_at)
          VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(family,ticker,market,source_class) DO UPDATE SET
            snapshot_id=excluded.snapshot_id,
            evidence_hash=excluded.evidence_hash,
            observed_at=excluded.observed_at""",
          (family,ticker,market,source_class,snapshot_id,digest,observed_at))

        if first_seen or changed:
            status = "FIRST_SEEN" if first_seen else "CHANGED_REVIEW_REQUIRED"
            c.execute("""INSERT INTO contract_evidence_v2_changes
              (family,ticker,market,source_class,previous_hash,current_hash,
               detected_at,status,detail) VALUES(?,?,?,?,?,?,?,?,?)""",
              (family,ticker,market,source_class,previous_hash,digest,observed_at,
               status,
               "Nueva evidencia contractual." if first_seen else
               "El hash contractual cambió; no promover ni ejecutar hasta revisión."))

    return {
        "schema": SCHEMA,
        "family": family,
        "ticker": ticker,
        "market": market,
        "source_class": source_class,
        "snapshot_id": snapshot_id,
        "evidence_hash": digest,
        "first_seen": first_seen,
        "changed": changed,
        "status": "CHANGED_REVIEW_REQUIRED" if changed else "RECORDED",
    }


def source_conflict(records):
    """Detect contradictory non-empty values, respecting source provenance."""
    values = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        source = record.get("source_class")
        evidence = record.get("evidence") or {}
        if source not in SOURCE_RANK or not isinstance(evidence, dict):
            continue
        for field, value in evidence.items():
            if value in (None, "", [], {}):
                continue
            values.setdefault(field, []).append((SOURCE_RANK[source], source, value))

    conflicts={}
    for field, entries in values.items():
        rendered={canonical_json(v) for _,_,v in entries}
        if len(rendered)>1:
            conflicts[field]=sorted(entries,key=lambda x:x[0])
    return conflicts
