"""Bounded READ_ONLY census. No application/broker imports or DB writes.

Every current identity is exported. Large metadata is projected to explicit
contract/identity fields, not copied recursively. Historical snapshots, market
history and account data are deliberately excluded. Actions artifacts only.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

TABLES = ("candidate_identity_v2", "financial_instrument_catalog", "contract_evidence_v2_current")
REQUIRED = frozenset(TABLES[:2])
SENSITIVE = ("cookie", "authorization", "api_key", "apikey", "api_secret", "apisecret",
             "password", "passwd", "access_token", "refresh_token", "session_token",
             "bearer", "private_key", "account_number", "numero_cuenta", "account_id", "email", "cuit", "dni")
MAX_ROWS = 100000
MAX_BYTES = 48000000
METADATA_FIELDS = (
    "_discovery_source", "_availability_source", "_provider_instrument_type",
    "_contract_bridge.status", "_contract_bridge.gaps", "_contract_conflicts",
    "isin", "cajaValoresCode", "nominalInPrice", "type", "currency",
    "underlying", "strike", "putCall", "expirationDate", "lotSize",
    "financial_contract_v17.cash_multiplier", "financial_contract_v17.quantity_step",
    "financial_contract_v17.minimum_quantity", "financial_contract_v17.expires_at",
    "financial_contract_v17.underlying", "financial_contract_v17.strike",
    "financial_contract_v17.option_right", "financial_contract_v17.paper_margin_policy",
    "financial_contract_v17.paper_margin_rate", "_iol_units_per_lot",
    "operable", "isActive", "isTradable", "status",
)
EVIDENCE_FIELDS = (
    "currency", "market", "settlement", "underlying", "put_call", "strike", "expiry_at",
    "cash_multiplier", "quantity_min", "quantity_step", "paper_cash_multiplier",
    "paper_quantity_min", "paper_quantity_step", "paper_margin_policy", "paper_margin_rate",
    "margin_requirement", "initial_margin", "revoked", "adjusted_series_unverified",
    "maturity_date", "nominal_in_price", "units_per_lot", "paper_subscription_policy",
    "paper_subscription_min", "evidence_scope", "derivation_rule", "lot_size",
)


def clean(value):
    if isinstance(value, dict):
        return {str(k): ("REDACTED" if any(p in str(k).lower().replace("-", "_") for p in SENSITIVE)
                        else clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, bytes):
        return {"binary_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if isinstance(value, str):
        if len(value) > 250000:
            raise ValueError("CENSUS_FIELD_SIZE_LIMIT")
        if value[:1] in ("{", "["):
            try:
                return clean(json.loads(value))
            except json.JSONDecodeError:
                pass
        return re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "REDACTED", value)
    return value


def summary(rows):
    groups = Counter((str(r.get("instrument_type") or r.get("family") or "UNKNOWN"),
                      str(r.get("status") or "UNKNOWN"), int(r.get("can_simulate") or 0)) for r in rows)
    reasons = Counter()
    for row in rows:
        if row.get("status") == "AVAILABLE" and row.get("can_simulate") == 1:
            continue
        family = str(row.get("instrument_type") or row.get("family") or "UNKNOWN")
        for key, val in row.items():
            if (key == "detail" or any(p in key.lower() for p in ("reason", "capability", "block", "paused"))) and val not in (None, "", [], {}):
                reasons[(family, key, json.dumps(val, sort_keys=True, ensure_ascii=False))] += 1
    return {"total": len(rows), "ready": sum(r.get("status") == "AVAILABLE" and r.get("can_simulate") == 1 for r in rows),
            "groups": [dict(family=f, status=s, can_simulate=c, n=n) for (f, s, c), n in sorted(groups.items())],
            "reasons": [dict(family=f, field=k, reason=v, n=n) for (f, k, v), n in sorted(reasons.items())]}


def projection_sql(column, fields):
    paths = ",".join("'$." + name + "'" for name in fields)
    return "json_extract(" + column + "," + paths + ")"


def collect(db_path, *, deadline_seconds=30.0, max_rows=MAX_ROWS):
    path = Path(db_path).resolve(strict=True)
    started = time.monotonic()
    deadline = started + deadline_seconds
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA busy_timeout=250")
        con.execute("PRAGMA cache_size=-4096")
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            raise RuntimeError("CENSUS_REQUIRES_EXISTING_WAL_NO_WRITER_LOCK")
        con.execute("BEGIN")
        state = con.execute("SELECT mode,real_orders_sent,heartbeat_at,ppi_auth,process_state,session_state FROM observer_state WHERE id=1").fetchone()
        if state is None or state["mode"] != "PRODUCTION_PAPER" or state["real_orders_sent"] != 0:
            raise RuntimeError("CENSUS_PAPER_INVARIANT")
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not REQUIRED <= names:
            raise RuntimeError("CENSUS_REQUIRED_TABLE_MISSING")
        result = {"schema_version": 2, "mode": "READ_ONLY", "captured_at": datetime.now(timezone.utc).isoformat(),
                  "observer": clean(dict(state)), "tables": {}, "schemas": {}, "missing_optional_tables": [],
                  "related_table_names": sorted(n for n in names if any(x in n for x in ("candidate", "contract", "iol"))),
                  "metadata_projection_fields": METADATA_FIELDS, "evidence_projection_fields": EVIDENCE_FIELDS,
                  "application_imports": False, "broker_calls": 0, "database_writes": 0,
                  "raw_metadata_exported": False, "historical_snapshots_exported": False}
        total_rows = 0
        queries = []
        for table in TABLES:
            if table not in names:
                result["missing_optional_tables"].append(table)
                continue
            info = [dict(r) for r in con.execute('PRAGMA table_info("' + table + '")')]
            result["schemas"][table] = info
            columns = [r["name"] for r in info]
            query = 'SELECT * FROM "' + table + '"'
            if table == "financial_instrument_catalog" and "metadata_json" in columns:
                scalar = ','.join('"' + col.replace('"','""') + '"' for col in columns if col != "metadata_json")
                # Small fixture/provider objects remain sanitized in full. Large
                # graphs are projected with one JSON parse in SQLite, not Python.
                meta = "CASE WHEN length(metadata_json)<=4096 THEN metadata_json ELSE " + projection_sql("metadata_json", METADATA_FIELDS) + " END AS metadata_json"
                query = 'SELECT ' + scalar + ',' + meta + ' FROM financial_instrument_catalog'
            queries.append((table, query, 'SELECT count(*) FROM "' + table + '"'))
        if {"contract_evidence_v2_current", "contract_evidence_v2_snapshots"} <= names:
            base = " FROM contract_evidence_v2_current c JOIN contract_evidence_v2_snapshots s ON s.snapshot_id=c.snapshot_id"
            queries.append(("current_evidence_projection",
                "SELECT c.family,c.ticker,c.market,c.currency,c.settlement,c.source_class,c.evidence_hash,c.observed_at,s.source_ref,s.effective_at," + projection_sql("s.evidence_json",EVIDENCE_FIELDS) + " AS evidence_projection" + base,
                "SELECT count(*)" + base))
        for table, query, count_query in queries:
            expected = con.execute(count_query).fetchone()[0]
            print("CENSUS_READ_STAGE=" + table + "|rows=" + str(expected), file=sys.stderr)
            if expected + total_rows > max_rows:
                raise RuntimeError("CENSUS_ROW_LIMIT_NO_PARTIAL_SUCCESS")
            cursor = con.execute(query)
            rows = []
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError("CENSUS_DEADLINE_NO_PARTIAL_SUCCESS:" + table)
                batch = cursor.fetchmany(250)
                if not batch:
                    break
                rows.extend(clean(dict(r)) for r in batch)
            if len(rows) != expected:
                raise RuntimeError("CENSUS_COUNT_MISMATCH")
            result["tables"][table] = rows
            total_rows += len(rows)
        result["summary"] = summary(result["tables"]["candidate_identity_v2"])
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        result["complete"] = True
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_BYTES:
            raise RuntimeError("CENSUS_BYTE_LIMIT_NO_PARTIAL_SUCCESS")
        return result
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sha):
        raise ValueError("CENSUS_INVALID_EXPECTED_SHA")
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    if state.get("deploy_sha") != args.expected_sha or state.get("mode") != "PRODUCTION_PAPER" or state.get("real_orders_sent") != 0:
        raise RuntimeError("CENSUS_SOURCE_OR_SAFETY_DRIFT")
    os.nice(15)
    result = collect(args.db)
    result["product_sha"] = args.expected_sha
    result["runtime_provenance"] = {k: state.get(k) for k in
        ("candidate_sha", "deploy_sha", "image_id", "validation_status", "recorded_at", "real_order_routes")}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
