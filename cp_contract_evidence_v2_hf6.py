"""RC4 versioned, append-only Contract Evidence v2 storage.

Financial identity includes settlement.  Evidence is provider-backed, secrets
are rejected recursively, and a changed/stale/conflicting contract can never
silently promote a family.  This module has no broker-order capability.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
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


CAUCION_FEE_REVIEW_POLICY = "RC6_A198_EXACT_PAPER_FEE_AUTHORITY_DOWNGRADE_V1"
_FEE_AUTHORITY_OLD = "PPI_TARIFF_EFFECTIVE_2026-07-01+BYMA_RIGHTS_VERIFIED_2026-08-21"
_FEE_AUTHORITY_NEW = "PAPER_PPI_PUBLIC_TARIFF_2026-07-01+BYMA_RIGHTS_UNVERIFIED"
_FEE_POLICY_SOURCE = "PPI_CAUCION_TICKER+POROTA_PAPER_LIVE_BOOK_POLICY:v1"
_FEE_POLICY_SOURCE_COMMIT = "9f5a4de307a5493f1cb2c414cfbc2b1a8468efaa"
_FEE_REVIEW_TICKERS = frozenset({"PESOS1", "PESOS2", "PESOS7", "PESOS30", "PESOS120"})
_CHANGE_COLUMNS = ("change_id", "family", "ticker", "market", "currency",
                   "settlement", "source_class", "previous_hash", "current_hash",
                   "detected_at")
# Exact production incident reviewed in #732 from read-only Actions
# 40992555376 / 40993210661. These are hashes of public policy snapshots, not
# account data. Do not extend this manifest automatically to another change.
_A198_REVIEWED_CHANGES = (
    (36957, "PESOS1", "4f7d6440446e8cc92485541441dbb5a88313363fa8a53689f21f2d27ed15d6a6",
     "64b8d68ed1448bc7a5549f70fe21c9ced4bca3cf5f219f53bb39e0bce5a60a89"),
    (36958, "PESOS120", "feae0a5b4f2354e070f320c05592b85d4d91bd42286e3213da371a0b457e00a0",
     "453924844737a6861fceb9d56f6c38d7c64a7f49c20cb463633adf9a977d9f76"),
    (36959, "PESOS2", "c1a78a604b014523e86e3909435b53e0de8b5a741a93b9ccf00284db089b8e28",
     "8259e8939568c38f6408372dcefe1f5fcd023ef07a5782186a0408231a85c533"),
    (36960, "PESOS30", "008aeb24836281252c83f9323553c096a24bdfd99533e48fc0455887a4115295",
     "ebc54e753ba00fdf17d30ae5da02b6db3ad52774db94eda1785899650eccbdee"),
    (36961, "PESOS7", "b37910f21f7e1ff73cc754ce40aab51d1fb9b991fd3a90075a97f58130968166",
     "184c6225d8d6812b91eb48bd171d6919f495b5a16521132e50212579e3057a4f"),
)


def _fee_authority_review_matches(receipt, change, previous, current,
                                  previous_source_ref, current_source_ref):
    """Validate one explicit review, never classify fee changes as provenance.

    A116 commit 9f5a4de corrected an overstated authority label while retaining
    the ARS public PAPER simulation policy. Only that exact transition can be
    acknowledged here. Future tariff/contract changes still require review.
    The receipt is an audit record of the caller's review, not a signature or
    broker fee verification, and never supplies dynamic execution evidence.
    """
    try:
        from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY
        if CAUCION_PAPER_FEE_AUTHORITY != _FEE_AUTHORITY_NEW:
            return False
        if (not isinstance(receipt, dict) or set(receipt) != {
                "policy", "decision", "scope", "change", "reviewer", "reviewed_at",
                "policy_source_commit", "source_run_id", "rationale_ref"}):
            return False
        if (receipt["policy"] != CAUCION_FEE_REVIEW_POLICY
                or receipt["decision"] != "ACCEPT_EXACT_PAPER_AUTHORITY_CORRECTION"
                or receipt["scope"] != "PRODUCTION_PAPER_SIMULATION_ONLY"
                or receipt["change"] != change):
            return False
        if (not isinstance(receipt["reviewer"], str)
                or re.fullmatch(r"[A-Za-z0-9_./:-]{1,80}", receipt["reviewer"]) is None
                or not isinstance(receipt["policy_source_commit"], str)
                or receipt["policy_source_commit"] != _FEE_POLICY_SOURCE_COMMIT
                or type(receipt["source_run_id"]) is not int or receipt["source_run_id"] <= 0
                or not isinstance(receipt["rationale_ref"], str)
                or re.fullmatch(r"https://github\.com/mbalbo2023/Porota-trading/issues/[0-9]+#issuecomment-[0-9]+",
                                receipt["rationale_ref"]) is None):
            return False
        reviewed = datetime.fromisoformat(receipt["reviewed_at"].replace("Z", "+00:00"))
        detected = datetime.fromisoformat(change["detected_at"].replace("Z", "+00:00"))
        if (reviewed.tzinfo is None or detected.tzinfo is None
                or not detected <= reviewed <= datetime.now(timezone.utc)):
            return False
        if (set(change) != set(_CHANGE_COLUMNS)
                or type(change["change_id"]) is not int or change["change_id"] <= 0
                or change["family"] != "CAUCIONES" or change["market"] != "BYMA"
                or change["currency"] != "ARS" or change["settlement"] != "INMEDIATA"
                or change["ticker"] not in _FEE_REVIEW_TICKERS
                or change["source_class"] != "DERIVED_OFFICIAL_RULE"
                or previous_source_ref != _FEE_POLICY_SOURCE
                or current_source_ref != _FEE_POLICY_SOURCE
                or evidence_hash(previous) != change["previous_hash"]
                or evidence_hash(current) != change["current_hash"]):
            return False
        # Full hashes bind the actual snapshots. The exact allowed policy below
        # prevents a reviewer from accepting a new fee rate, term or new field.
        expected = {
            "side": "COLOCADORA", "term_days": change["ticker"][5:],
            "minimum_principal": "100000", "paper_principal_step": "1",
            "day_count_basis": "365", "fee_payment": "UPFRONT",
            "paper_fill_policy": "LIVE_PPI_BID_PARTICIPATION_CAP",
            "policy_scope": "PRODUCTION_PAPER_SIMULATION_ONLY",
            "dynamic_execution_gate": "FRESH_PPI_PLACING_BID_REQUIRED",
        }
        return (material_evidence(previous) == {**expected, "fee_authority": _FEE_AUTHORITY_OLD}
                and material_evidence(current) == {**expected, "fee_authority": _FEE_AUTHORITY_NEW})
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def _fee_review_receipts(connection, tables):
    if "contract_evidence_v2_reviews" not in tables:
        return {}
    try:
        rows = connection.execute("SELECT change_id,receipt_json,receipt_hash "
                                  "FROM contract_evidence_v2_reviews LIMIT 6").fetchall()
        if len(rows) > 5:
            return {}  # this version only supports the five audited identities
        receipts = {}
        for change_id, raw, digest in rows:
            try:
                receipt = json.loads(raw)
                if evidence_hash(receipt) == digest:
                    receipts[change_id] = receipt
            except (TypeError, ValueError):
                continue
        return receipts
    except sqlite3.Error:
        return {}  # malformed/incomplete review storage never clears a HOLD


def review_caucion_fee_authority_changes(store, *, changes, reviewed_at, reviewer,
                                        policy_source_commit, source_run_id, rationale_ref):
    """Explicit bounded, atomic, idempotent review of supplied evidence.

    Caller must supply exact change IDs, five-part identities, old/new hashes,
    source and detected time from a read-only receipt. The SQLite workspace
    must be PRODUCTION_PAPER/0. Original changes/snapshots/current/catalog and
    all financial ledgers remain untouched; only review receipts are appended.
    Dynamic PPI freshness, calendar, fees and all admission gates still apply.
    """
    requested = list(changes)
    if (not 1 <= len(requested) <= 5 or any(not isinstance(v, dict) for v in requested)
            or any(type(v.get("change_id")) is not int for v in requested)
            or len({v.get("change_id") for v in requested}) != len(requested)):
        raise ValueError("CONTRACT_FEE_REVIEW_SCOPE_INVALID")
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        safety = connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if safety is None or tuple(safety) != ("PRODUCTION_PAPER", 0):
            raise ValueError("CONTRACT_FEE_REVIEW_PAPER_SAFETY_REQUIRED")
        receipts = []
        for expected in requested:
            row = connection.execute("""SELECT ch.*,p.evidence_json AS previous_json,
              n.evidence_json AS current_json,p.source_ref AS previous_source_ref,
              n.source_ref AS current_source_ref,cur.evidence_hash AS latest_hash
              FROM contract_evidence_v2_changes ch
              JOIN contract_evidence_v2_snapshots p
                ON p.family=ch.family AND p.ticker=ch.ticker AND p.market=ch.market
               AND p.currency=ch.currency AND p.settlement=ch.settlement
               AND p.source_class=ch.source_class AND p.evidence_hash=ch.previous_hash
              JOIN contract_evidence_v2_snapshots n
                ON n.family=ch.family AND n.ticker=ch.ticker AND n.market=ch.market
               AND n.currency=ch.currency AND n.settlement=ch.settlement
               AND n.source_class=ch.source_class AND n.evidence_hash=ch.current_hash
              JOIN contract_evidence_v2_current cur
                ON cur.family=ch.family AND cur.ticker=ch.ticker AND cur.market=ch.market
               AND cur.currency=ch.currency AND cur.settlement=ch.settlement
               AND cur.source_class=ch.source_class
               AND cur.snapshot_id=n.snapshot_id
              WHERE ch.change_id=?""", (expected.get("change_id"),)).fetchone()
            if row is None:
                raise ValueError("CONTRACT_FEE_REVIEW_EVIDENCE_MISSING")
            actual = {key: row[key] for key in _CHANGE_COLUMNS}
            receipt = {"policy": CAUCION_FEE_REVIEW_POLICY,
                       "decision": "ACCEPT_EXACT_PAPER_AUTHORITY_CORRECTION",
                       "scope": "PRODUCTION_PAPER_SIMULATION_ONLY", "change": actual,
                       "reviewer": reviewer, "reviewed_at": reviewed_at,
                       "policy_source_commit": policy_source_commit, "source_run_id": source_run_id,
                       "rationale_ref": rationale_ref}
            if (actual != expected or row["status"] != "CHANGED_REVIEW_REQUIRED"
                    or row["latest_hash"] != actual["current_hash"]
                    or not _fee_authority_review_matches(
                        receipt, actual, json.loads(row["previous_json"]),
                        json.loads(row["current_json"]), row["previous_source_ref"],
                        row["current_source_ref"])):
                raise ValueError("CONTRACT_FEE_REVIEW_EXACT_TRANSITION_REQUIRED")
            receipts.append(receipt)
        connection.execute("""CREATE TABLE IF NOT EXISTS contract_evidence_v2_reviews(
          change_id INTEGER PRIMARY KEY, receipt_json TEXT NOT NULL, receipt_hash TEXT NOT NULL)""")
        prior_ids = {r[0] for r in connection.execute("SELECT change_id FROM contract_evidence_v2_reviews")}
        if len(prior_ids | {r["change"]["change_id"] for r in receipts}) > 5:
            raise ValueError("CONTRACT_FEE_REVIEW_SCOPE_EXCEEDED")
        added = 0
        for receipt in receipts:
            change_id = receipt["change"]["change_id"]
            payload, digest = canonical_json(receipt), evidence_hash(receipt)
            existing = connection.execute("SELECT receipt_json,receipt_hash FROM contract_evidence_v2_reviews "
                                          "WHERE change_id=?", (change_id,)).fetchone()
            if existing is not None:
                if tuple(existing) != (payload, digest):
                    raise ValueError("CONTRACT_FEE_REVIEW_RECEIPT_CONFLICT")
                continue
            connection.execute("INSERT INTO contract_evidence_v2_reviews VALUES(?,?,?)",
                               (change_id, payload, digest))
            added += 1
    return {"policy": CAUCION_FEE_REVIEW_POLICY, "reviewed": len(receipts), "appended": added,
            "decision_effect": "EXACT_CHANGE_REVIEW_ONLY", "real_routes": []}


def apply_reviewed_caucion_fee_authority_correction(store):
    """Apply only the five reviewed A116 incident transitions on normal collect.

    This is a versioned audit migration, not automatic tariff approval. Missing
    or different incident evidence leaves every existing HOLD intact and does
    not prevent collection for unrelated identities. Repeated calls append
    nothing; subsequent material changes remain pending in their own right.
    """
    result = {"policy": CAUCION_FEE_REVIEW_POLICY, "status": "NOT_APPLICABLE",
              "reviewed": 0, "appended": 0, "real_routes": []}
    changes = [dict(zip(_CHANGE_COLUMNS, (
        change_id, "CAUCIONES", ticker, "BYMA", "ARS", "INMEDIATA",
        "DERIVED_OFFICIAL_RULE", previous_hash, current_hash,
        "2026-10-27T03:00:34.204768+00:00",
    ))) for change_id, ticker, previous_hash, current_hash in _A198_REVIEWED_CHANGES]
    try:
        with store.connect() as connection:
            found = connection.execute("""SELECT COUNT(*) FROM contract_evidence_v2_changes
              WHERE change_id IN (36957,36958,36959,36960,36961)""").fetchone()[0]
        if not found:
            return result
        if found != len(changes):
            return {**result, "status": "BLOCKED", "reason": "REVIEWED_INCIDENT_INCOMPLETE"}
        review = review_caucion_fee_authority_changes(
            store, changes=changes, reviewed_at="2026-10-30T02:44:24+00:00",
            reviewer="CODEX:RC6-A198",
            policy_source_commit=_FEE_POLICY_SOURCE_COMMIT,
            source_run_id=40993210661,
            rationale_ref="https://github.com/mbalbo2023/Porota-trading/issues/732#issuecomment-6513673394")
        return {**review, "status": "APPLIED" if review["appended"] else "ALREADY_APPLIED"}
    except (sqlite3.Error, ValueError, TypeError) as exc:
        # No partial writes: the explicit API validates the entire manifest in
        # one transaction before appending. Never publish raw DB error details.
        reason = str(exc) if str(exc).startswith("CONTRACT_FEE_REVIEW_") else type(exc).__name__
        return {**result, "status": "BLOCKED", "reason": reason}


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
    reviews = _fee_review_receipts(connection, tables)
    rows = connection.execute("""
      SELECT ch.family,ch.ticker,ch.market,ch.currency,ch.settlement,
             prev.evidence_json,curr.evidence_json,ch.change_id,ch.source_class,
             ch.previous_hash,ch.current_hash,ch.detected_at,prev.source_ref,curr.source_ref
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
        previous = current = None
        try:
            previous = json.loads(row[5])
            current = json.loads(row[6])
            changed = material_evidence_hash(previous) != material_evidence_hash(current)
        except (TypeError, ValueError, json.JSONDecodeError):
            changed = True
        change = dict(zip(_CHANGE_COLUMNS, (row[7], *identity, row[8], row[9], row[10], row[11])))
        reviewed = (changed and row[7] in reviews and _fee_authority_review_matches(
            reviews[row[7]], change, previous, current, row[12], row[13]))
        if changed and not reviewed:
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
            "paper_contract_ready": paper_ready,
            "paper_execution_mode": "SHADOW_OBSERVE_ONLY",
            "paper_auto_enabled": False,
            "entry_authority": False,
            "auto_activation_allowed": False,
            "real_money_authorized": False,
            "legacy_global_ttl_ignored": True}
