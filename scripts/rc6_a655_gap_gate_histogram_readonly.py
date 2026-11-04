#!/usr/bin/env python3
"""A655: bounded, opt-in SQLite WAL READ_ONLY gap correlation receipt.

Never reconstruct the historical A626 575/172/74 cohort from a later DB.
No provider, PPI Watch, paper engine, broker, order, WAL copy, or DB mutation.
Host execution requires a private OS read-only mount in addition to mode=ro.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.parse import quote
import uuid


SCHEMA = "rc6.a655.gap-gate-histogram-readonly.v1"
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
MAX_RAW = 32768
MAX_ROWS = 60000
MAX_GROUPS = 128  # Keep even the maximal categorical receipt under MAX_RAW.
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 128 * 1024 * 1024
MAX_COUNT = 1000000
FAMILIES = frozenset({"BONOS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI"})
RAW_FAMILIES = FAMILIES | {"ON", "FCI_LOCAL"}
ACTIVE = frozenset({"AVAILABLE", "ACTIVE", "READY"})
CAP_CODES = frozenset({
    "NEEDS_NOMINAL_UNITS", "NEEDS_OPTION_CONTRACT",
    "NEEDS_FUTURES_MARGIN_AND_CONTRACT", "NEEDS_CAUCION_TERMS",
    "NEEDS_FUND_SETTLEMENT", "NEEDS_MARKET_EXECUTOR",
    "NEEDS_SPECIALIZED_EXECUTOR", "CONTRACT_EVIDENCE_REVIEW_REQUIRED",
    "OPTION_IOL_SERIES_CONTRACT_UNVERIFIED", "CONTRACT_SOURCE_CONFLICT",
    "CONTRADICTORY_CONTRACT", "MISSING_CURRENCY_OR_MARKET",
    "TYPE_NOT_ENUMERATED", "MARKET_NOT_ENUMERATED", "UNSUPPORTED_FAMILY",
    "DISCOVERED_COMPLEMENTARY_METADATA_INCOMPLETE", "UNKNOWN_CONTRACT",
    "OTHER_CAPABILITY_CODE",
})
BRIDGES = frozenset({"NORMALIZED", "BLOCKED", "ABSENT", "OTHER_BRIDGE_STATUS", "INVALID_METADATA"})
RETRIES = frozenset({"NO_RETRY", "AMBIGUOUS_IDENTITY", "MISSING_FIELDS", "NEEDS_CONTRACT",
                     "OTHER_RETRY", "MULTIPLE_RETRY_REASONS"})
CURRENTS = frozenset({"EXACT_PPI_SOURCE_LABEL", "EXACT_NONPPI_SOURCE_LABEL",
                      "NO_EXACT_CURRENT", "INCOMPLETE_IDENTITY"})
ATTACHMENTS = frozenset({"V17_ATTACHED", "V17_ABSENT", "V17_INVALID"})
FIELDS = ("family", "capability", "bridge", "retry", "current", "v17")
HEX = re.compile(r"[a-f0-9]{64}\Z")


class Hold(Exception):
    """Fail closed with a fixed error code; never expose DB/provider text."""


def _base():
    return {"schema": SCHEMA, "state": "NO_VERIFICADO",
            "reason": "DB_RO_UNAVAILABLE", "diagnostic_class": "CORRELATIVE_DIAGNOSTIC_ONLY",
            "a626_historical_row_attribution": "NOT_VERIFIED",
            "read_only": True, "private_ro_mount": False, "paper_workspace_verified": False,
            "provider_gets": 0, "orders_called": False, "ppi_watch_touched": False,
            "paper_admission_certified": False, "runtime_sha_verified": False,
            "real_orders_sent": None, "sampled_at_utc": None,
            "catalog_active_gap_total": None, "catalog_six_family_total": None,
            "status_coverage": {}, "by_family": {}, "joint": []}


def _mount_is_readonly(path):
    chosen = -1
    readonly = False
    try:
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            fields = line.split(" - ", 1)[0].split()
            if len(fields) < 6:
                continue
            point = fields[4].replace("\\040", " ")
            if point != "/" and path != Path(point) and not str(path).startswith(point.rstrip("/") + "/"):
                continue
            if len(point) > chosen:
                chosen = len(point)
                readonly = "ro" in fields[5].split(",")
    except (OSError, ValueError, UnicodeError):
        return False
    return chosen >= 0 and readonly


def _columns(conn, table):
    return {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")}


def _utc(value):
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        raise Hold("V2_SNAPSHOT_CLOCK_HOLD")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            raise ValueError("naive")
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise Hold("V2_SNAPSHOT_CLOCK_HOLD") from None


def _strict_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = item
        return result
    try:
        return json.loads(value, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (TypeError, ValueError):
        raise Hold("JSON_INTEGRITY_HOLD") from None


def _workspace(conn):
    if not {"id", "namespace", "origin", "imported_legacy", "dataset_id"} <= _columns(conn, "paper_workspace"):
        return False
    rows = conn.execute("SELECT id,namespace,origin,imported_legacy,dataset_id FROM paper_workspace LIMIT 2").fetchall()
    if len(rows) != 1:
        return False
    try:
        row = rows[0]
        return (tuple(row[:4]) == (1, "POROTA_PAPER_V17_FRESH", "FRESH_EMPTY", 0)
                and str(uuid.UUID(row[4])) == row[4])
    except (TypeError, ValueError, AttributeError):
        return False


def _key(row, *, prefix=""):
    return tuple(str(row[prefix + field] or "").strip().upper()
                 for field in ("family", "ticker", "market", "currency", "settlement"))


def _key_complete(key):
    return all(value not in {"", "UNKNOWN", "*", "NULL"} for value in key)


def _current_labels(conn):
    """Exact five-key only; verify immutable snapshot identity/hash/source/time."""
    if conn.execute("SELECT COUNT(*) FROM contract_evidence_v2_current").fetchone()[0] > MAX_ROWS:
        raise Hold("V2_ROWS_LIMIT_HOLD")
    sql = """SELECT c.family,c.ticker,c.market,c.currency,c.settlement,
      c.source_class,c.evidence_hash,c.observed_at,
      s.family AS snap_family,s.ticker AS snap_ticker,s.market AS snap_market,
      s.currency AS snap_currency,s.settlement AS snap_settlement,
      s.source_class AS snap_source_class,s.evidence_hash AS snap_hash,
      s.observed_at AS snap_observed_at,s.evidence_json
      FROM contract_evidence_v2_current c
      LEFT JOIN contract_evidence_v2_snapshots s ON s.snapshot_id=c.snapshot_id"""
    labels = defaultdict(set)
    used_bytes = 0
    rows = 0
    for row in conn.execute(sql):
        rows += 1
        if rows > MAX_ROWS:
            raise Hold("V2_ROWS_LIMIT_HOLD")
        current = _key(row)
        snapshot = (str(row["snap_" + f] or "").strip().upper()
                    for f in ("family", "ticker", "market", "currency", "settlement"))
        if (current != tuple(snapshot) or row["source_class"] != row["snap_source_class"]
                or row["evidence_hash"] != row["snap_hash"]
                or not isinstance(row["evidence_hash"], str)
                or HEX.fullmatch(row["evidence_hash"]) is None):
            raise Hold("V2_SNAPSHOT_IDENTITY_HOLD")
        if _utc(row["observed_at"]) < _utc(row["snap_observed_at"]):
            raise Hold("V2_SNAPSHOT_CLOCK_HOLD")
        value = row["evidence_json"]
        if not isinstance(value, str) or len(value) > 32768:
            raise Hold("V2_EVIDENCE_SIZE_HOLD")
        used_bytes += len(value)
        if used_bytes > MAX_EVIDENCE_BYTES:
            raise Hold("V2_EVIDENCE_SIZE_HOLD")
        parsed = _strict_json(value)
        if not isinstance(parsed, dict):
            raise Hold("V2_EVIDENCE_JSON_HOLD")
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != row["evidence_hash"]:
            raise Hold("V2_EVIDENCE_HASH_HOLD")
        if _key_complete(current):
            labels[current].add("PPI" if str(row["source_class"] or "").startswith("PPI_") else "OTHER")
    return labels


def _retry_reason(reason):
    if isinstance(reason, str):
        if reason.startswith("COMPLEMENTARY_IDENTITY_AMBIGUOUS:"):
            return "AMBIGUOUS_IDENTITY"
        if reason.startswith("MISSING_FIELDS:"):
            return "MISSING_FIELDS"
        if reason.startswith("NEEDS_"):
            return "NEEDS_CONTRACT"
    return "OTHER_RETRY"


def _retries(conn):
    rows = conn.execute("""SELECT ticker,instrument_type AS family,market,currency,
      settlement,reason FROM complementary_contract_retry LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise Hold("RETRY_ROWS_LIMIT_HOLD")
    result = defaultdict(set)
    for row in rows:
        result[_key(row)].add(_retry_reason(row["reason"]))
    return result


def _capability(raw):
    return raw if raw in CAP_CODES else "OTHER_CAPABILITY_CODE"


def _metadata_flags(raw):
    if not isinstance(raw, str) or len(raw) > 32768:
        raise Hold("CATALOG_METADATA_SIZE_HOLD")
    try:
        value = _strict_json(raw or "{}")
    except Hold:
        return "INVALID_METADATA", "V17_INVALID"
    if not isinstance(value, dict):
        return "INVALID_METADATA", "V17_INVALID"
    bridge = value.get("_contract_bridge")
    status = bridge.get("status") if isinstance(bridge, dict) else None
    state = status if status in {"NORMALIZED", "BLOCKED"} else (
        "ABSENT" if not bridge else "OTHER_BRIDGE_STATUS")
    attached = value.get("financial_contract_v17")
    v17 = "V17_ABSENT" if attached is None else (
        "V17_ATTACHED" if isinstance(attached, dict) and attached else "V17_INVALID")
    return state, v17


def _aggregate(conn):
    required = {
        "paper_workspace": {"id", "namespace", "origin", "imported_legacy", "dataset_id"},
        "observer_state": {"id", "real_orders_sent"},
        "financial_instrument_catalog": {"ticker", "instrument_type", "market", "currency", "settlement", "status", "capability", "metadata_json"},
        "contract_evidence_v2_current": {"family", "ticker", "market", "currency", "settlement", "snapshot_id", "source_class", "evidence_hash", "observed_at"},
        "contract_evidence_v2_snapshots": {"family", "ticker", "market", "currency", "settlement", "snapshot_id", "source_class", "evidence_hash", "observed_at", "evidence_json"},
        "complementary_contract_retry": {"ticker", "instrument_type", "market", "currency", "settlement", "reason"},
    }
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required.keys() <= tables or any(not fields <= _columns(conn, name) for name, fields in required.items()):
        raise Hold("REQUIRED_SCHEMA_HOLD")
    if not _workspace(conn):
        raise Hold("PAPER_WORKSPACE_HOLD")
    state = conn.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()
    if not state or type(state[0]) is not int or state[0] != 0:
        raise Hold("PAPER_ORDER_COUNTER_HOLD")
    labels = _current_labels(conn)
    retries = _retries(conn)
    rows = conn.execute("""SELECT instrument_type AS family,ticker,market,currency,
      settlement,status,capability,metadata_json
      FROM financial_instrument_catalog LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise Hold("CATALOG_ROWS_LIMIT_HOLD")
    groups = Counter()
    # Prevent a false zero if the catalog's actual status vocabulary drifts.
    # Every family row is covered, including paused/nonactive identities that
    # deliberately do not belong to the A626-like active-gap denominator.
    coverage = {family: Counter({"all": 0, "active": 0, "ready": 0,
                                 "active_gap": 0, "nonactive": 0}) for family in FAMILIES}
    keys_seen = set()
    metadata_bytes = 0
    for row in rows:
        key = _key(row)
        if key in keys_seen:
            raise Hold("CATALOG_IDENTITY_DUPLICATE_HOLD")
        keys_seen.add(key)
        if key[0] not in RAW_FAMILIES:
            continue
        family = {"ON": "OBLIGACIONES", "FCI_LOCAL": "FCI"}.get(key[0], key[0])
        coverage[family]["all"] += 1
        if str(row["status"] or "").upper() not in ACTIVE:
            coverage[family]["nonactive"] += 1
            continue
        coverage[family]["active"] += 1
        if str(row["capability"] or "").startswith("READY_PAPER_"):
            coverage[family]["ready"] += 1
            continue
        coverage[family]["active_gap"] += 1
        metadata_bytes += len(str(row["metadata_json"] or ""))
        if metadata_bytes > MAX_METADATA_BYTES:
            raise Hold("CATALOG_METADATA_SIZE_HOLD")
        bridge, v17 = _metadata_flags(row["metadata_json"])
        retry_set = retries.get(key, set())
        retry = ("NO_RETRY" if not retry_set else next(iter(retry_set))
                 if len(retry_set) == 1 else "MULTIPLE_RETRY_REASONS")
        current = ("INCOMPLETE_IDENTITY" if not _key_complete(key)
                   else "EXACT_PPI_SOURCE_LABEL" if "PPI" in labels.get(key, ())
                   else "EXACT_NONPPI_SOURCE_LABEL" if labels.get(key)
                   else "NO_EXACT_CURRENT")
        groups[(family, _capability(row["capability"]), bridge, retry, current, v17)] += 1
    if len(groups) > MAX_GROUPS:
        raise Hold("GROUPS_LIMIT_HOLD")
    if (any(coverage[family]["all"] == 0 for family in FAMILIES)
            or sum(c["active_gap"] for c in coverage.values()) == 0):
        raise Hold("ACTIVE_GAP_DENOMINATOR_HOLD")
    joint = [dict(zip(FIELDS, key), count=value) for key, value in sorted(groups.items())]
    return joint, {family: dict(sorted(row.items())) for family, row in sorted(coverage.items())}


def diagnose(db=DB, *, enforce_ro_mount=True):
    report = _base()
    conn = None
    try:
        if not db.is_absolute() or db.is_symlink():
            raise Hold("DB_PATH_HOLD")
        if enforce_ro_mount and not _mount_is_readonly(db):
            raise Hold("PRIVATE_RO_MOUNT_REQUIRED")
        report["private_ro_mount"] = bool(enforce_ro_mount)
        conn = sqlite3.connect("file:" + quote(str(db), safe="/") + "?mode=ro",
                               uri=True, timeout=0.35, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        # Explicit single WAL read generation. No write transaction, COMMIT or WAL copy.
        conn.execute("BEGIN")
        start = time.monotonic()
        conn.set_progress_handler(lambda: int(time.monotonic() - start > 19), 1000)
        joint, coverage = _aggregate(conn)
        report["paper_workspace_verified"] = True
        report["real_orders_sent"] = 0
        report["sampled_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["joint"] = joint
        report["catalog_active_gap_total"] = sum(item["count"] for item in joint)
        report["status_coverage"] = coverage
        report["catalog_six_family_total"] = sum(row["all"] for row in coverage.values())
        report["state"] = "READ_ONLY_OBSERVED"
        report["reason"] = "CORRELATIVE_DIAGNOSTIC_ONLY"
    except Hold as exc:
        report["reason"] = str(exc)
    except (sqlite3.Error, OSError, TypeError, ValueError, OverflowError):
        report["reason"] = "DB_RO_QUERY_HOLD"
    finally:
        if conn is not None:
            conn.close()
    return report


def sanitize(value):
    """Runner-side allowlist; never reproduce raw keys, symbols or provider text."""
    safe = _base()
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("state") != "READ_ONLY_OBSERVED"
            or value.get("diagnostic_class") != "CORRELATIVE_DIAGNOSTIC_ONLY"
            or value.get("a626_historical_row_attribution") != "NOT_VERIFIED"
            or value.get("private_ro_mount") is not True
            or value.get("paper_workspace_verified") is not True
            or value.get("real_orders_sent") != 0 or type(value.get("real_orders_sent")) is not int
            or value.get("read_only") is not True or value.get("provider_gets") != 0
            or value.get("orders_called") is not False or value.get("ppi_watch_touched") is not False
            or value.get("paper_admission_certified") is not False
            or value.get("runtime_sha_verified") is not False):
        safe["reason"] = "UNTRUSTED_HOST_RESPONSE"
        return safe
    try:
        dt = _utc(value.get("sampled_at_utc"))
        if not isinstance(value.get("joint"), list) or len(value["joint"]) > MAX_GROUPS:
            raise Hold("UNTRUSTED_GROUPS")
        groups = set()
        totals = Counter()
        joint = []
        for item in value["joint"]:
            if not isinstance(item, dict) or set(item) != set(FIELDS) | {"count"}:
                raise Hold("UNTRUSTED_GROUPS")
            key = tuple(item[field] for field in FIELDS)
            count = item["count"]
            if (key in groups or key[0] not in FAMILIES or key[1] not in CAP_CODES
                    or key[2] not in BRIDGES or key[3] not in RETRIES
                    or key[4] not in CURRENTS or key[5] not in ATTACHMENTS
                    or type(count) is not int or not 0 < count <= MAX_COUNT):
                raise Hold("UNTRUSTED_GROUPS")
            groups.add(key)
            totals[key[0]] += count
            joint.append(dict(zip(FIELDS, key), count=count))
        total = sum(totals.values())
        coverage = value.get("status_coverage")
        if (not isinstance(coverage, dict) or set(coverage) != FAMILIES
                or any(not isinstance(row, dict)
                       or set(row) != {"all", "active", "ready", "active_gap", "nonactive"}
                       or any(type(n) is not int or not 0 <= n <= MAX_COUNT for n in row.values())
                       or row["all"] != row["active"] + row["nonactive"]
                       or row["active"] != row["ready"] + row["active_gap"]
                       or row["active_gap"] != totals.get(family, 0)
                       or row["all"] == 0
                       for family, row in coverage.items())):
            raise Hold("UNTRUSTED_STATUS_COVERAGE")
        covered_total = sum(row["all"] for row in coverage.values())
        if (total != value.get("catalog_active_gap_total") or total > MAX_COUNT or total == 0
                or covered_total != value.get("catalog_six_family_total")
                or covered_total > MAX_COUNT):
            raise Hold("UNTRUSTED_DENOMINATOR")
        safe.update(state="READ_ONLY_OBSERVED", reason="CORRELATIVE_DIAGNOSTIC_ONLY",
                    private_ro_mount=True, paper_workspace_verified=True,
                    real_orders_sent=0, sampled_at_utc=dt.isoformat(),
                    catalog_active_gap_total=total,
                    catalog_six_family_total=covered_total,
                    status_coverage={family: dict(sorted(row.items())) for family, row in sorted(coverage.items())},
                    by_family=dict(sorted(totals.items())),
                    joint=sorted(joint, key=lambda x: tuple(x[f] for f in FIELDS)))
    except (Hold, TypeError, ValueError, KeyError):
        safe["reason"] = "UNTRUSTED_AGGREGATE"
    return safe


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["host-probe"]:
        print(json.dumps(diagnose(), sort_keys=True, separators=(",", ":")))
        return 0
    if len(args) == 3 and args[0] == "sanitize":
        raw, dest = Path(args[1]), Path(args[2])
        safe = _base()
        try:
            if 0 < raw.stat().st_size <= MAX_RAW and not raw.is_symlink():
                safe = sanitize(_strict_json(raw.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, TypeError, ValueError, Hold):
            pass
        dest.write_text(json.dumps(safe, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8")
        print("A655_GAP_HIST=" + safe["state"])
        return 0 if safe["state"] == "READ_ONLY_OBSERVED" else 2
    raise SystemExit("A655_HOST_PROBE_OR_RUNNER_SANITIZE_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
