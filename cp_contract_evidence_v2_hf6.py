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

SCHEMA = "porota-contract-evidence-v2-currency-key-v3"
SOURCE_RANK = {
    "PPI_STRUCTURED_API": 10,
    "PPI_AUTHENTICATED_XHR": 20,
    "PPI_AUTHENTICATED_DOM": 30,
    "PPI_AUTHENTICATED_WEB": 30,
    "IOL_STRUCTURED_API": 40,
    "IOL_AUTHENTICATED_XHR": 50,
    "MARKET_OFFICIAL": 60,
    "BYMA_STRUCTURED_API": 60,
    "BYMA_OFFICIAL_DOCUMENTATION": 60,
    "A3_PRIMARY_API": 60,
    "A3_OFFICIAL_DOCUMENTATION": 60,
    "CLEARING_OFFICIAL": 70,
    "A3_RISK_POSTTRADE": 70,
    "FUND_MANAGER_OFFICIAL": 80,
    "IOL_AUTHENTICATED_DOM": 90,
    "IOL_AUTHENTICATED_WEB": 90,
    "DERIVED_OFFICIAL_RULE": 100,
    "PPI_OFFICIAL_DOCUMENTATION": 110,
    "PPI_SUPPORT": 120,
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
PROVENANCE_ONLY_FIELDS = frozenset({
    "provider_timestamp", "capture_timestamp", "freshness_basis",
    "derivation_rule", "confidence", "evidence_class",
    "source_job", "source_route", "evidence_scope", "readiness_guard",
    "semantic_guard", "metadata_source", "quantity_terms_source",
    "provider_currency", "fund_description", "iol_operable_observed",
})


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_family(value):
    raw = str(value or "UNKNOWN").strip().upper().replace("/", "_")
    return FAMILY_ALIASES.get(raw, raw)


def normalize_identity(*, family, ticker, market, currency, settlement):
    return (
        normalize_family(family),
        str(ticker or "*").strip().upper(),
        str(market or "UNKNOWN").strip().upper(),
        str(currency or "UNKNOWN").strip().upper(),
        str(settlement or "UNKNOWN").strip().upper(),
    )


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def evidence_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def material_evidence(value):
    """Remove provenance-only fields while preserving contractual semantics.

    Full evidence snapshots remain byte-for-byte auditable and keep their full
    evidence_hash. This projection is used only to decide whether a provider
    observation materially changed the contract.
    """
    if isinstance(value, dict):
        return {
            str(key): material_evidence(nested)
            for key, nested in value.items()
            if str(key) not in PROVENANCE_ONLY_FIELDS
        }
    if isinstance(value, list):
        return [material_evidence(item) for item in value]
    if isinstance(value, tuple):
        return [material_evidence(item) for item in value]
    return value


def material_evidence_hash(value):
    return evidence_hash(material_evidence(value))


def pending_material_changes(connection):
    """Return identities with at least one unresolved *material* change.

    Historical false positives are not mutated or deleted: the append-only
    change log remains intact. We reclassify them at read time by comparing
    the exact previous/current snapshot payloads with provenance-only fields
    removed. Missing/corrupt evidence fails closed as material.
    """
    tables = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if not {"contract_evidence_v2_changes", "contract_evidence_v2_snapshots"} <= tables:
        return set()
    rows = connection.execute("""
      SELECT ch.family,ch.ticker,ch.market,ch.currency,ch.settlement,
             prev.evidence_json,curr.evidence_json
      FROM contract_evidence_v2_changes ch
      LEFT JOIN contract_evidence_v2_snapshots prev
        ON prev.family=ch.family AND prev.ticker=ch.ticker
       AND prev.market=ch.market AND prev.currency=ch.currency
       AND prev.settlement=ch.settlement AND prev.source_class=ch.source_class
       AND prev.evidence_hash=ch.previous_hash
      LEFT JOIN contract_evidence_v2_snapshots curr
        ON curr.family=ch.family AND curr.ticker=ch.ticker
       AND curr.market=ch.market AND curr.currency=ch.currency
       AND curr.settlement=ch.settlement AND curr.source_class=ch.source_class
       AND curr.evidence_hash=ch.current_hash
      WHERE ch.status='CHANGED_REVIEW_REQUIRED'
    """).fetchall()
    pending = set()
    for row in rows:
        identity = tuple(row[:5])
        if row[5] is None or row[6] is None:
            pending.add(identity)
            continue
        try:
            previous = json.loads(row[5])
            current = json.loads(row[6])
            changed = material_evidence_hash(previous) != material_evidence_hash(current)
        except (TypeError, ValueError, json.JSONDecodeError):
            changed = True
        if changed:
            pending.add(identity)
    return pending


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
    required = {"family", "ticker", "market", "currency", "settlement", "source_class"}
    for table in ("contract_evidence_v2_snapshots", "contract_evidence_v2_current",
                  "contract_evidence_v2_changes"):
        if table in tables and not required.issubset(_table_columns(connection, table)):
            raise RuntimeError("CONTRACT_V2_MIGRATION_REQUIRED:" + table)


def _migrate_currencyless_schema(connection):
    """Forward-only migration from the original key that omitted currency.

    The old ``current`` table may have overwritten an ARS/USD variant.  History
    remains append-only, so current is rebuilt from every snapshot, partitioned
    by the recovered payload currency.  Backups are retained for audit and the
    operation is idempotent.  This helper only acts on the supplied store; the
    production DB is never selected implicitly.
    """
    tables = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    managed = ("contract_evidence_v2_snapshots", "contract_evidence_v2_current",
               "contract_evidence_v2_changes")
    present = [table for table in managed if table in tables]
    if not present or all("currency" in _table_columns(connection, table)
                          for table in present):
        return False
    if any(table + "_currencyless_backup" in tables for table in present):
        raise RuntimeError("CONTRACT_V2_CURRENCY_MIGRATION_BACKUP_EXISTS")

    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        for table in present:
            connection.execute(
                f"ALTER TABLE {table} RENAME TO {table}_currencyless_backup")
        connection.executescript("""
        CREATE TABLE contract_evidence_v2_snapshots(
          snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL, ticker TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL,
          source_class TEXT NOT NULL, source_ref TEXT NOT NULL,
          observed_at TEXT NOT NULL, effective_at TEXT,
          evidence_hash TEXT NOT NULL, evidence_json TEXT NOT NULL,
          UNIQUE(family,ticker,market,currency,settlement,source_class,evidence_hash)
        );
        CREATE TABLE contract_evidence_v2_current(
          family TEXT NOT NULL, ticker TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL,
          source_class TEXT NOT NULL,
          snapshot_id INTEGER NOT NULL REFERENCES contract_evidence_v2_snapshots(snapshot_id),
          evidence_hash TEXT NOT NULL, observed_at TEXT NOT NULL,
          PRIMARY KEY(family,ticker,market,currency,settlement,source_class)
        );
        CREATE TABLE contract_evidence_v2_changes(
          change_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL, ticker TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL,
          source_class TEXT NOT NULL, previous_hash TEXT, current_hash TEXT NOT NULL,
          detected_at TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL
        );
        """)
        if "contract_evidence_v2_snapshots" in present:
            rows = connection.execute("""SELECT snapshot_id,family,ticker,market,
              settlement,source_class,source_ref,observed_at,effective_at,
              evidence_hash,evidence_json
              FROM contract_evidence_v2_snapshots_currencyless_backup
              ORDER BY snapshot_id""").fetchall()
            for row in rows:
                payload = json.loads(row[10] or "{}")
                currency = str(payload.get("currency") or "UNKNOWN").strip().upper()
                connection.execute("""INSERT INTO contract_evidence_v2_snapshots
                  (snapshot_id,family,ticker,market,currency,settlement,source_class,
                   source_ref,observed_at,effective_at,evidence_hash,evidence_json)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  tuple(row[:4]) + (currency,) + tuple(row[4:]))
            connection.execute("""INSERT INTO contract_evidence_v2_current
              (family,ticker,market,currency,settlement,source_class,snapshot_id,
               evidence_hash,observed_at)
              SELECT s.family,s.ticker,s.market,s.currency,s.settlement,s.source_class,
                     s.snapshot_id,s.evidence_hash,s.observed_at
              FROM contract_evidence_v2_snapshots s
              WHERE s.snapshot_id=(SELECT s2.snapshot_id
                FROM contract_evidence_v2_snapshots s2
                WHERE s2.family=s.family AND s2.ticker=s.ticker
                  AND s2.market=s.market AND s2.currency=s.currency
                  AND s2.settlement=s.settlement AND s2.source_class=s.source_class
                ORDER BY s2.observed_at DESC,s2.snapshot_id DESC LIMIT 1)""")
        if "contract_evidence_v2_changes" in present:
            rows = connection.execute("""SELECT change_id,family,ticker,market,
              settlement,source_class,previous_hash,current_hash,detected_at,status,detail
              FROM contract_evidence_v2_changes_currencyless_backup ORDER BY change_id""").fetchall()
            for row in rows:
                match = connection.execute("""SELECT currency
                  FROM contract_evidence_v2_snapshots
                  WHERE family=? AND ticker=? AND market=? AND settlement=?
                    AND source_class=? AND evidence_hash=?
                  ORDER BY snapshot_id DESC LIMIT 1""",
                  (row[1],row[2],row[3],row[4],row[5],row[7])).fetchone()
                currency = str(match[0] if match else "UNKNOWN").upper()
                connection.execute("""INSERT INTO contract_evidence_v2_changes
                  (change_id,family,ticker,market,currency,settlement,source_class,
                   previous_hash,current_hash,detected_at,status,detail)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                  tuple(row[:4]) + (currency,) + tuple(row[4:]))
        return True
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


def init_schema(store):
    with store.connect() as c:
        _migrate_currencyless_schema(c)
        _assert_schema_compatible(c)
        c.executescript("""
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_snapshots(
          snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          currency TEXT NOT NULL,
          settlement TEXT NOT NULL,
          source_class TEXT NOT NULL,
          source_ref TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          effective_at TEXT,
          evidence_hash TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          UNIQUE(family,ticker,market,currency,settlement,source_class,evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_current(
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          currency TEXT NOT NULL,
          settlement TEXT NOT NULL,
          source_class TEXT NOT NULL,
          snapshot_id INTEGER NOT NULL REFERENCES contract_evidence_v2_snapshots(snapshot_id),
          evidence_hash TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          PRIMARY KEY(family,ticker,market,currency,settlement,source_class)
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_v2_changes(
          change_id INTEGER PRIMARY KEY AUTOINCREMENT,
          family TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          currency TEXT NOT NULL,
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
          ON contract_evidence_v2_snapshots(family,ticker,market,currency,settlement,observed_at);
        CREATE INDEX IF NOT EXISTS idx_contract_v2_changes_key
          ON contract_evidence_v2_changes(family,ticker,market,currency,settlement,detected_at);
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
                    source_ref, evidence, currency=None, settlement="UNKNOWN",
                    observed_at=None, effective_at=None):
    """Append provider-backed evidence and report whether the contract changed."""
    init_schema(store)
    with store.connect() as c:
        return _record_snapshot_connection(
            c, family=family, ticker=ticker, market=market,
            source_class=source_class, source_ref=source_ref,
            evidence=evidence, currency=currency, settlement=settlement,
            observed_at=observed_at, effective_at=effective_at)


def _record_snapshot_connection(c, *, family, ticker, market, source_class,
                                source_ref, evidence, currency=None,
                                settlement="UNKNOWN", observed_at=None,
                                effective_at=None):
    """Connection-scoped primitive used by the atomic bulk ingester."""
    if source_class not in SOURCE_RANK:
        raise ValueError("CONTRACT_V2_UNSUPPORTED_SOURCE")
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("CONTRACT_V2_EMPTY_EVIDENCE")
    _assert_sanitized(evidence)
    payload_currency = str(evidence.get("currency") or "UNKNOWN").strip().upper()
    storage_currency = str(currency or payload_currency or "UNKNOWN").strip().upper()
    if payload_currency not in {"", "UNKNOWN"} and storage_currency != payload_currency:
        raise ValueError("CONTRACT_V2_CURRENCY_MISMATCH")
    family, ticker, market, storage_currency, settlement = normalize_identity(
        family=family, ticker=ticker, market=market, currency=storage_currency,
        settlement=settlement)
    observed_at = observed_at or now_iso()
    digest = evidence_hash(evidence)
    payload = canonical_json(evidence)

    previous = c.execute("""SELECT cur.snapshot_id,cur.evidence_hash,cur.observed_at,
                                    snap.evidence_json
      FROM contract_evidence_v2_current cur
      JOIN contract_evidence_v2_snapshots snap ON snap.snapshot_id=cur.snapshot_id
      WHERE cur.family=? AND cur.ticker=? AND cur.market=?
        AND cur.currency=? AND cur.settlement=? AND cur.source_class=?""",
      (family,ticker,market,storage_currency,settlement,source_class)).fetchone()
    c.execute("""INSERT OR IGNORE INTO contract_evidence_v2_snapshots
      (family,ticker,market,currency,settlement,source_class,source_ref,observed_at,effective_at,
       evidence_hash,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
      (family,ticker,market,storage_currency,settlement,source_class,str(source_ref),observed_at,
       effective_at,digest,payload))
    snap = c.execute("""SELECT snapshot_id FROM contract_evidence_v2_snapshots
      WHERE family=? AND ticker=? AND market=? AND currency=? AND settlement=? AND source_class=?
      AND evidence_hash=?""",
      (family,ticker,market,storage_currency,settlement,source_class,digest)).fetchone()
    snapshot_id = int(snap[0])
    previous_hash = previous[1] if previous else None
    full_hash_changed = bool(previous_hash and previous_hash != digest)
    changed = False
    if full_hash_changed:
        try:
            previous_evidence = json.loads(previous[3])
            changed = material_evidence_hash(previous_evidence) != material_evidence_hash(evidence)
        except (TypeError, ValueError, json.JSONDecodeError):
            # Corrupt or unavailable prior evidence must remain fail-closed.
            changed = True
    first_seen = previous is None
    c.execute("""INSERT INTO contract_evidence_v2_current
      (family,ticker,market,currency,settlement,source_class,snapshot_id,evidence_hash,observed_at)
      VALUES(?,?,?,?,?,?,?,?,?)
      ON CONFLICT(family,ticker,market,currency,settlement,source_class) DO UPDATE SET
        snapshot_id=excluded.snapshot_id,evidence_hash=excluded.evidence_hash,
        observed_at=excluded.observed_at""",
      (family,ticker,market,storage_currency,settlement,source_class,snapshot_id,digest,observed_at))
    if first_seen or changed:
        status = "FIRST_SEEN" if first_seen else "CHANGED_REVIEW_REQUIRED"
        c.execute("""INSERT INTO contract_evidence_v2_changes
          (family,ticker,market,currency,settlement,source_class,previous_hash,current_hash,
           detected_at,status,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          (family,ticker,market,storage_currency,settlement,source_class,previous_hash,digest,observed_at,
           status,"Nueva evidencia contractual." if first_seen else
           "El hash contractual cambió; no promover ni ejecutar hasta revisión."))
    return {"schema":SCHEMA,"family":family,"ticker":ticker,"market":market,
            "currency":storage_currency,
            "settlement":settlement,"source_class":source_class,
            "snapshot_id":snapshot_id,"evidence_hash":digest,
            "first_seen":first_seen,"changed":changed,
            "provenance_only_change":bool(full_hash_changed and not changed),
            "status":"CHANGED_REVIEW_REQUIRED" if changed else "RECORDED"}


def record_snapshots(store, records):
    """Atomically ingest a bounded catalogue batch without per-row connections.

    Every item accepts the same keyword fields as :func:`record_snapshot`.
    A malformed row aborts the whole batch, leaving the append-only store
    unchanged.  This is the mass-universe path; it has no network capability.
    """
    rows = list(records or [])
    init_schema(store)
    results = []
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("CONTRACT_V2_BATCH_ROW_INVALID")
            results.append(_record_snapshot_connection(c, **row))
    return results


def current_records(store, *, family=None, ticker=None, currency=None, settlement=None):
    init_schema(store)
    clauses=[]; params=[]
    if family:
        clauses.append("c.family=?"); params.append(normalize_family(family))
    if ticker:
        clauses.append("c.ticker=?"); params.append(str(ticker).upper())
    if currency:
        clauses.append("c.currency=?"); params.append(str(currency).upper())
    if settlement:
        clauses.append("c.settlement=?"); params.append(str(settlement).upper())
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
            if field in PROVENANCE_ONLY_FIELDS:
                continue
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
                           simulator_ready=False, cost_ready=False, profile="FULL"):
    """Family-aware readiness using canonical execution requirements.

    This is still evidence-only: the maximum state is READY_PAPER_CANDIDATE and
    auto activation is permanently false.
    """
    from cq_contract_readiness_hf6 import evaluate as evaluate_family
    rows = list(records or [])
    conflicts = source_conflict(rows)
    merged = {}
    for row in sorted(rows, key=lambda r: SOURCE_RANK.get(r.get("source_class"), 999), reverse=True):
        merged.update({k:v for k,v in (row.get("evidence") or {}).items()
                       if v not in (None,"",[],{})})
    # ``max_age_seconds`` is retained for API compatibility only.  A global
    # TTL must not expire static contract terms; the canonical evaluator owns
    # per-field TTLs for dynamic evidence.
    # Evidence completeness is distinct from execution readiness.
    # SHADOW observation can remain enabled, but it cannot fabricate a PAPER
    # simulator or a certified cost model for a family.
    result = evaluate_family(
        family, rows,
        simulator_ready=bool(simulator_ready),
        cost_ready=bool(cost_ready),
        freshness_ok=False,
        source_conflict=bool(conflicts),
        profile=profile,
        now=now,
    )
    paper_ready = result.get("status") == "READY_PAPER_CANDIDATE"
    return {**result, "evidence": merged, "conflicts": conflicts,
            "paper_simulatable": paper_ready,
            "paper_execution_mode": "PAPER" if paper_ready else "SHADOW_OBSERVE_ONLY",
            "paper_auto_enabled": paper_ready,
            "auto_activation_allowed": False,
            "real_money_authorized": False,
            "legacy_global_ttl_ignored": True}
