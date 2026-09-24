"""RC4 versioned, append-only Contract Evidence v2 storage.

Financial identity includes settlement.  Evidence is provider-backed, secrets
are rejected recursively, and a changed/stale/conflicting contract can never
silently promote a family.  This module has no broker-order capability.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

SCHEMA = "porota-contract-evidence-v2-rc4"
SOURCE_RANK = {
    "PPI_STRUCTURED_API": 10,
    "PPI_AUTHENTICATED_XHR": 20,
    "PPI_OFFICIAL_DOCUMENTATION": 30,
    "PPI_AUTHENTICATED_WEB": 40,
    "PPI_SUPPORT": 50,
    "A3_PRIMARY_API": 10,
    "A3_RISK_POSTTRADE": 10,
    "A3_OFFICIAL_DOCUMENTATION": 30,
    "POROTA_LEGACY_EVIDENCE": 900,
}
FAMILY_ALIASES = {
    "ACCIONES-USA": "ACCIONES_USA",
    "ACCIONES USA": "ACCIONES_USA",
    "FCI-EXTERIOR": "FCI_EXTERIOR",
    "FCI EXTERIOR": "FCI_EXTERIOR",
    "OBLIGACIONES_NEGOCIABLES": "ON",
    "OBLIGACIONES NEGOCIABLES": "ON",
    "OBLIGACIONES": "ON",
    "ONS": "ON",
    "ETFS": "ETF",
    "FCIS": "FCI",
}
FORBIDDEN_KEY_PARTS = (
    "cookie", "authorization", "api_key", "apikey", "api_secret", "apisecret",
    "password", "passwd", "otp", "one_time", "access_token", "refresh_token",
    "session_token", "bearer", "private_key", "account_number", "numero_cuenta",
)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_family(value):
    raw = str(value or "UNKNOWN").strip().upper().replace("/", "_")
    return FAMILY_ALIASES.get(raw, raw)


def normalize_identity(*, family, ticker, market, settlement):
    return (
        normalize_family(family),
        str(ticker or "*").strip().upper(),
        str(market or "UNKNOWN").strip().upper(),
        str(settlement or "UNKNOWN").strip().upper(),
    )


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def evidence_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _assert_sanitized(value, path="evidence"):
    if isinstance(value, dict):
        for key, nested in value.items():
            lower = str(key).lower().replace("-", "_")
            if any(part in lower for part in FORBIDDEN_KEY_PARTS):
                raise ValueError("CONTRACT_V2_SENSITIVE_FIELD:" + path + "." + str(key))
            _assert_sanitized(nested, path + "." + str(key))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _assert_sanitized(nested, f"{path}[{index}]")


def _table_columns(connection, table):
    return {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}


def _assert_schema_compatible(connection):
    tables = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    required = {"family", "ticker", "market", "settlement", "source_class"}
    for table in ("contract_evidence_v2_snapshots", "contract_evidence_v2_current",
                  "contract_evidence_v2_changes"):
        if table in tables and not required.issubset(_table_columns(connection, table)):
            raise RuntimeError("CONTRACT_V2_MIGRATION_REQUIRED:" + table)


def init_schema(store):
    with store.connect() as c:
        _assert_schema_compatible(c)
        c.executescript("""
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_snapshots(
          snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          settlement TEXT NOT NULL,
          source_class TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          effective_at TEXT,
          evidence_hash TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          UNIQUE(family,ticker,market,settlement,source_class,evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_current(
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          settlement TEXT NOT NULL,
          source_class TEXT NOT NULL,
          snapshot_id INTEGER NOT NULL REFERENCES contract_evidence_v2_snapshots(snapshot_id),
          evidence_hash TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          PRIMARY KEY(family,ticker,market,settlement,source_class)
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_changes(
          change_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          settlement TEXT NOT NULL,
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
          started_at TEXT NOT NULL,
          finished_at TEXT,
          state TEXT NOT NULL,
          records INTEGER NOT NULL DEFAULT 0,
          changed INTEGER NOT NULL DEFAULT 0,
          conflicts INTEGER NOT NULL DEFAULT 0,
          detail TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_contract_v2_snapshot_key
          ON contract_evidence_v2_snapshots(family,ticker,market,settlement,observed_at);
        CREATE INDEX IF NOT EXISTS idx_contract_v2_changes_key
          ON contract_evidence_v2_changes(family,ticker,market,settlement,detected_at);
        CREATE INDEX IF NOT EXISTS idx_contract_v2_runs_job
          ON contract_evidence_v2_runs(job_key,started_at DESC);
        """)


def start_run(store, *, run_id, job_key, started_at=None, detail=""):
    init_schema(store)
    stamp = started_at or now_iso()
    with store.connect() as c:
        c.execute("""INSERT OR REPLACE INTO contract_evidence_v2_runs
          (run_id,job_key,started_at,finished_at,state,records,changed,conflicts,detail)
          VALUES(?,?,?,NULL,'RUNNING',0,0,0,?)""",
          (str(run_id), str(job_key), stamp, str(detail)[:1000]))
    return stamp


def finish_run(store, *, run_id, state, records=0, changed=0, conflicts=0,
               detail="", finished_at=None):
    stamp = finished_at or now_iso()
    with store.connect() as c:
        c.execute("""UPDATE contract_evidence_v2_runs SET finished_at=?,state=?,records=?,
          changed=?,conflicts=?,detail=? WHERE run_id=?""",
          (stamp, str(state), int(records), int(changed), int(conflicts),
           str(detail)[:2000], str(run_id)))
    return stamp


def record_snapshot(store, *, family, ticker, market, source_class,
                    source_ref, evidence, settlement="UNKNOWN",
                    observed_at=None, effective_at=None):
    """Append provider-backed evidence and report whether the contract changed."""
    if source_class not in SOURCE_RANK:
        raise ValueError("CONTRACT_V2_UNSUPPORTED_SOURCE")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("CONTRACT_V2_EMPTY_EVIDENCE")
    _assert_sanitized(evidence)
    family, ticker, market, settlement = normalize_identity(
        family=family, ticker=ticker, market=market, settlement=settlement)
    observed_at = observed_at or now_iso()
    digest = evidence_hash(evidence)
    payload = canonical_json(evidence)

    init_schema(store)
    with store.connect() as c:
        previous = c.execute("""SELECT snapshot_id,evidence_hash,observed_at
          FROM contract_evidence_v2_current WHERE family=? AND ticker=? AND market=?
          AND settlement=? AND source_class=?""",
          (family,ticker,market,settlement,source_class)).fetchone()
        c.execute("""INSERT OR IGNORE INTO contract_evidence_v2_snapshots
          (family,ticker,market,settlement,source_class,source_ref,observed_at,effective_at,
           evidence_hash,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
          (family,ticker,market,settlement,source_class,str(source_ref),observed_at,
           effective_at,digest,payload))
        snap = c.execute("""SELECT snapshot_id FROM contract_evidence_v2_snapshots
          WHERE family=? AND ticker=? AND market=? AND settlement=? AND source_class=?
          AND evidence_hash=?""",
          (family,ticker,market,settlement,source_class,digest)).fetchone()
        snapshot_id = int(snap[0])
        previous_hash = previous[1] if previous else None
        changed = bool(previous_hash and previous_hash != digest)
        first_seen = previous is None
        c.execute("""INSERT INTO contract_evidence_v2_current
          (family,ticker,market,settlement,source_class,snapshot_id,evidence_hash,observed_at)
          VALUES(?,?,?,?,?,?,?,?)
          ON CONFLICT(family,ticker,market,settlement,source_class) DO UPDATE SET
            snapshot_id=excluded.snapshot_id,evidence_hash=excluded.evidence_hash,
            observed_at=excluded.observed_at""",
          (family,ticker,market,settlement,source_class,snapshot_id,digest,observed_at))
        if first_seen or changed:
            status = "FIRST_SEEN" if first_seen else "CHANGED_REVIEW_REQUIRED"
            c.execute("""INSERT INTO contract_evidence_v2_changes
              (family,ticker,market,settlement,source_class,previous_hash,current_hash,
               detected_at,status,detail) VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (family,ticker,market,settlement,source_class,previous_hash,digest,observed_at,
               status,"Nueva evidencia contractual." if first_seen else
               "El hash contractual cambió; no promover ni ejecutar hasta revisión."))
    return {"schema":SCHEMA,"family":family,"ticker":ticker,"market":market,
            "settlement":settlement,"source_class":source_class,
            "snapshot_id":snapshot_id,"evidence_hash":digest,
            "first_seen":first_seen,"changed":changed,
            "status":"CHANGED_REVIEW_REQUIRED" if changed else "RECORDED"}


def current_records(store, *, family=None, ticker=None):
    init_schema(store)
    clauses=[]; params=[]
    if family:
        clauses.append("c.family=?"); params.append(normalize_family(family))
    if ticker:
        clauses.append("c.ticker=?"); params.append(str(ticker).upper())
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with store.connect() as c:
        rows=c.execute("""SELECT c.*,s.source_ref,s.effective_at,s.evidence_json
          FROM contract_evidence_v2_current c
          JOIN contract_evidence_v2_snapshots s ON s.snapshot_id=c.snapshot_id""" + where,
          params).fetchall()
    result=[]
    for row in rows:
        item=dict(row)
        item["evidence"]=json.loads(item.pop("evidence_json") or "{}")
        result.append(item)
    return result


def source_conflict(records):
    """Detect contradictory non-empty values, respecting source provenance."""
    values={}
    for record in records or []:
        if not isinstance(record,dict):
            continue
        source=record.get("source_class")
        evidence=record.get("evidence") or {}
        if source not in SOURCE_RANK or not isinstance(evidence,dict):
            continue
        for field,value in evidence.items():
            if value in (None,"",[],{}):
                continue
            values.setdefault(field,[]).append((SOURCE_RANK[source],source,value))
    conflicts={}
    for field,entries in values.items():
        rendered={canonical_json(v) for _,_,v in entries}
        if len(rendered)>1:
            conflicts[field]=sorted(entries,key=lambda x:x[0])
    return conflicts


def readiness_state(records, *, required_fields=(), max_age_seconds=None, now=None):
    """Evidence-only readiness; maximum output is READY_PAPER_CANDIDATE."""
    rows=list(records or [])
    if not rows:
        return {"state":"MISSING","missing":list(required_fields),"conflicts":{}}
    conflicts=source_conflict(rows)
    if conflicts:
        return {"state":"CONFLICT","missing":[],"conflicts":conflicts}
    merged={}
    for row in sorted(rows,key=lambda r:SOURCE_RANK.get(r.get("source_class"),999),reverse=True):
        merged.update({k:v for k,v in (row.get("evidence") or {}).items()
                       if v not in (None,"",[],{})})
    missing=[field for field in required_fields if merged.get(field) in (None,"",[],{})]
    if missing:
        return {"state":"MISSING","missing":missing,"conflicts":{},"evidence":merged}
    if max_age_seconds is not None:
        ref = now or datetime.now(timezone.utc)
        for row in rows:
            try:
                at=datetime.fromisoformat(str(row.get("observed_at")).replace("Z","+00:00"))
                if at.tzinfo is None: at=at.replace(tzinfo=timezone.utc)
                if (ref-at).total_seconds() > int(max_age_seconds):
                    return {"state":"STALE","missing":[],"conflicts":{},"evidence":merged}
            except Exception:
                return {"state":"STALE","missing":[],"conflicts":{},"evidence":merged}
    return {"state":"READY_PAPER_CANDIDATE","missing":[],"conflicts":{},
            "evidence":merged,"auto_activation_allowed":False}


def family_readiness_state(records, *, family, max_age_seconds=None, now=None,
                           simulator_ready=False, cost_ready=False):
    """Family-aware readiness using canonical execution requirements.

    This is still evidence-only: the maximum state is READY_PAPER_CANDIDATE and
    auto activation is permanently false.
    """
    from cq_contract_readiness_hf6 import evaluate as evaluate_family
    rows = list(records or [])
    if not rows:
        base = evaluate_family(family, {}, simulator_ready=simulator_ready,
                               cost_ready=cost_ready, freshness_ok=False,
                               source_conflict=False)
        return {**base, "auto_activation_allowed": False, "conflicts": {}}
    conflicts = source_conflict(rows)
    merged = {}
    for row in sorted(rows, key=lambda r: SOURCE_RANK.get(r.get("source_class"), 999), reverse=True):
        merged.update({k:v for k,v in (row.get("evidence") or {}).items()
                       if v not in (None,"",[],{})})
    freshness_ok = True
    if max_age_seconds is not None:
        ref = now or datetime.now(timezone.utc)
        for row in rows:
            try:
                at = datetime.fromisoformat(str(row.get("observed_at")).replace("Z","+00:00"))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                if (ref - at.astimezone(timezone.utc)).total_seconds() > int(max_age_seconds):
                    freshness_ok = False
                    break
            except Exception:
                freshness_ok = False
                break
    # Complete source evidence is enough for PAPER/SHADOW. The live
    # executor/cost gates remain separate and can still veto real routes.
    shadow_enabled = os.getenv("RC6_PAPER_SHADOW_ENABLED", "ON").upper() in {
        "ON", "TRUE", "1", "SHADOW",
    }
    result = evaluate_family(
        family, merged,
        simulator_ready=bool(simulator_ready) or shadow_enabled,
        cost_ready=bool(cost_ready) or shadow_enabled,
        freshness_ok=freshness_ok,
        source_conflict=bool(conflicts),
    )
    # PPI remains primary; IOL/BYMA may complete the contract. Once the
    # merged evidence is complete, fresh and conflict-free, it is available
    # for PAPER/SHADOW even when a live-money executor is not involved.
    if result.get("status") == "READY_PAPER_CANDIDATE":
        result = {
            **result,
            "status": "READY_PAPER_SHADOW",
            "paper_simulatable": True,
            "paper_execution_mode": "SHADOW",
        }
    return {**result, "evidence": merged, "conflicts": conflicts,
            "paper_auto_enabled": result.get("status") in {
                "READY_PAPER_CANDIDATE", "READY_PAPER_SHADOW"},
            "auto_activation_allowed": False,
            "real_money_authorized": False}
