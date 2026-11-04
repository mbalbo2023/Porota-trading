#!/usr/bin/env python3
"""A655/A658/A661/A666: bounded, opt-in SQLite WAL READ_ONLY gate correlation.

Never reconstruct the historical A626 575/172/74 cohort from a later DB.
No provider, PPI Watch, paper engine, broker, order, WAL copy, or DB mutation.
Host execution requires a private OS read-only mount in addition to mode=ro.
A661 adds current V2 review-root categories; it cannot authorize remediation or
certify whether a persisted bridge gate would be the same after live replay.
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


SCHEMA = "rc6.a666.option-ppi-material-fields-readonly.v4"
DB = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
MAX_RAW = 32768
MAX_ROWS = 60000
MAX_GROUPS = 128  # Keep even the maximal categorical receipt under MAX_RAW.
MAX_ROOT_GROUPS = 48
MAX_PPI_FIELD_GROUPS = 32
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
RETRY_SOURCES = frozenset({"NO_RETRY", "IOL_ONLY", "V2_ONLY", "BYMA_ONLY",
                           "PPI_ONLY", "OTHER_ONLY", "MIXED_WITH_IOL", "MIXED_NO_IOL"})
BRIDGE_REASON_LABELS = frozenset({
    "CHANGE_REVIEW_REQUIRED", "CONFLICT_TERM", "IDENTITY_HOLD",
    "PROVENANCE_HOLD", "EVIDENCE_TIME_HOLD", "CURRENCY_CONFLICT",
    "INVALID_CONTRACT", "OPTION_IOL_SERIES_UNVERIFIED", "MISSING_FIELDS",
    "EXECUTOR_HOLD", "OTHER_BLOCKED_GAP",
})
BRIDGE_REASON_STANDALONE = frozenset({"NOT_BLOCKED", "INVALID_METADATA"})
CHANGE_LOGS = frozenset({"REVIEW_LOG_ENTRY_PRESENT", "NO_REVIEW_LOG_ENTRY"})
CURRENTS = frozenset({"EXACT_PPI_SOURCE_LABEL", "EXACT_NONPPI_SOURCE_LABEL",
                      "NO_EXACT_CURRENT", "INCOMPLETE_IDENTITY"})
ATTACHMENTS = frozenset({"V17_ATTACHED", "V17_ABSENT", "V17_INVALID"})
FIELDS = ("family", "capability", "bridge", "retry", "current", "v17",
          "bridge_reasons", "retry_source", "change_log")
ROOT_FLAGS = frozenset({"PENDING_MATERIAL", "REVOKED", "ADJUSTED_SERIES_UNVERIFIED",
                        "EVIDENCE_INTEGRITY_HOLD"})
ROOT_STANDALONE = "UNEXPLAINED_PERSISTED_BRIDGE"
ROOT_SOURCES = frozenset({"PPI_ONLY", "IOL_ONLY", "DERIVED_OFFICIAL_ONLY",
                          "OTHER_ONLY", "MIXED_SOURCES", "NONE"})
# Only fixed, audited keys may leave this private RO read as field names.
# An arbitrary JSON member, including secret/ticker/account-like input, is
# always bucketed without exporting its name or associated value.
PPI_MATERIAL_FIELD_LABELS = {
    "market": "MARKET", "currency": "CURRENCY", "settlement": "SETTLEMENT",
    "ppi_identity_verified": "PPI_IDENTITY_VERIFIED",
    "provider_instrument_type": "PROVIDER_INSTRUMENT_TYPE",
    "provider_catalog_status": "PROVIDER_CATALOG_STATUS",
    "price_quote_unit": "PRICE_QUOTE_UNIT",
    "ppi_nominal_in_price": "PPI_NOMINAL_IN_PRICE",
}
PPI_MATERIAL_FIELDS = frozenset(PPI_MATERIAL_FIELD_LABELS.values()) | {"UNKNOWN_FIELD"}
PPI_MATERIAL_OPERATIONS = frozenset({"ADDED", "REMOVED", "CHANGED"})
PPI_CURRENT_CATEGORIES = frozenset({"LATEST_CURRENT", "HISTORICAL_NOT_LATEST",
                                    "CURRENT_SOURCE_ABSENT"})
PPI_REF_CATEGORIES = frozenset({"SEARCHINSTRUMENT_STABLE", "STABLE_OTHER_PPI_REF",
                                "CHANGED_PPI_REF", "MISSING_PPI_REF"})
PPI_SEARCH_INSTRUMENT_REF = "PPI_API:/api/1.0/marketdata/searchinstrument"
# Exact copy of cp_contract_evidence_v2_hf6.PROVENANCE_ONLY_FIELDS at product
# 7e1e744d6ca9ab30c51b5747dd4467af5ece0145. Do not silently alter this
# list: the material-vs-provenance distinction is business logic, not heuristic.
PROVENANCE_ONLY_FIELDS = frozenset({
    "provider_timestamp", "capture_timestamp", "freshness_basis",
    "derivation_rule", "confidence", "evidence_class",
    "source_job", "source_route", "evidence_scope", "readiness_guard",
    "semantic_guard", "metadata_source", "quantity_terms_source",
    "provider_currency", "fund_description", "iol_operable_observed",
})
# Same source gate as rc6_contract_bridge.normalize_group(), minus the ignored
# IOL_STRUCTURED_API option-series inference and legacy evidence.
USABLE_V2_SOURCES = frozenset({
    "PPI_STRUCTURED_API", "PPI_AUTHENTICATED_XHR", "PPI_AUTHENTICATED_DOM",
    "PPI_AUTHENTICATED_WEB", "IOL_AUTHENTICATED_XHR", "MARKET_OFFICIAL",
    "BYMA_STRUCTURED_API", "BYMA_OFFICIAL_DOCUMENTATION", "A3_PRIMARY_API",
    "A3_OFFICIAL_DOCUMENTATION", "CLEARING_OFFICIAL", "A3_RISK_POSTTRADE",
    "FUND_MANAGER_OFFICIAL", "IOL_AUTHENTICATED_DOM", "IOL_AUTHENTICATED_WEB",
    "DERIVED_OFFICIAL_RULE", "PPI_OFFICIAL_DOCUMENTATION", "PPI_SUPPORT",
})
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
            "status_coverage": {}, "by_family": {}, "joint": [],
            "option_review_root_total": None, "option_review_root_hist": [],
            "option_ppi_material_identity_total": None,
            "option_ppi_material_change_total": None,
            "option_ppi_material_field_hist": [],
            "option_ppi_material_current_hist": [],
            "option_ppi_material_source_ref_hist": []}


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
      settlement,reason,source FROM complementary_contract_retry LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise Hold("RETRY_ROWS_LIMIT_HOLD")
    result = defaultdict(set)
    sources = defaultdict(set)
    for row in rows:
        result[_key(row)].add(_retry_reason(row["reason"]))
        sources[_key(row)].add(_retry_source(row["source"]))
    return result, sources


def _retry_source(source):
    # Never export untrusted source strings, even when they resemble a ticker.
    known = {"IOL_COMPLEMENTARY": "IOL", "CONTRACT_EVIDENCE_V2": "V2",
             "BYMA_PUBLIC_COMPLEMENTARY": "BYMA", "PPI_PRIMARY": "PPI"}
    return known.get(source, "OTHER")


def _retry_source_label(values):
    if not values:
        return "NO_RETRY"
    if len(values) == 1:
        return {"IOL": "IOL_ONLY", "V2": "V2_ONLY", "BYMA": "BYMA_ONLY",
                "PPI": "PPI_ONLY", "OTHER": "OTHER_ONLY"}[next(iter(values))]
    return "MIXED_WITH_IOL" if "IOL" in values else "MIXED_NO_IOL"


def _change_log_entries(conn):
    """A historical review log entry is NOT an unresolved material change.

    The canonical pending_material_changes() additionally compares prior and
    current material payloads and permitted review receipts. Do not re-create
    that authority with a status-only SQL shortcut.
    """
    rows = conn.execute("""SELECT family,ticker,market,currency,settlement,status
      FROM contract_evidence_v2_changes LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise Hold("CHANGE_LOG_ROWS_LIMIT_HOLD")
    return {_key(row) for row in rows if row["status"] == "CHANGED_REVIEW_REQUIRED"}


def _material_projection(value):
    """Faithful to cp_contract_evidence_v2_hf6.material_evidence()."""
    if isinstance(value, dict):
        return {str(key): _material_projection(nested) for key, nested in value.items()
                if str(key) not in PROVENANCE_ONLY_FIELDS}
    if isinstance(value, (tuple, list)):
        return [_material_projection(item) for item in value]
    return value


def _canonical_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_bucket(sources):
    if not sources:
        return "NONE"
    buckets = set()
    for source in sources:
        if isinstance(source, str) and source.startswith("PPI_"):
            buckets.add("PPI")
        elif isinstance(source, str) and source.startswith("IOL_"):
            buckets.add("IOL")
        elif source == "DERIVED_OFFICIAL_RULE":
            buckets.add("DERIVED_OFFICIAL")
        else:
            buckets.add("OTHER")
    if len(buckets) != 1:
        return "MIXED_SOURCES"
    return {"PPI": "PPI_ONLY", "IOL": "IOL_ONLY",
            "DERIVED_OFFICIAL": "DERIVED_OFFICIAL_ONLY", "OTHER": "OTHER_ONLY"}[
        next(iter(buckets))]


def _ppi_source_ref_category(previous, current):
    """Categorize provenance with fixed labels; never export its raw text."""
    if not isinstance(previous, str) or not previous or not isinstance(current, str) or not current:
        return "MISSING_PPI_REF"
    if previous != current:
        return "CHANGED_PPI_REF"
    if previous == PPI_SEARCH_INSTRUMENT_REF:
        return "SEARCHINSTRUMENT_STABLE"
    return "STABLE_OTHER_PPI_REF"


def _material_fields(previous, current):
    """Project exact semantic JSON; return only allowlisted key labels."""
    before, after = _material_projection(previous), _material_projection(current)
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise Hold("A666_PPI_MATERIAL_SHAPE_HOLD")
    changes = []
    for name in sorted(set(before) | set(after)):
        if name in before and name in after and before[name] == after[name]:
            continue
        operation = "ADDED" if name not in before else "REMOVED" if name not in after else "CHANGED"
        changes.append((PPI_MATERIAL_FIELD_LABELS.get(name, "UNKNOWN_FIELD"), operation))
    if not changes:
        raise Hold("A666_PPI_MATERIAL_EMPTY_HOLD")
    return changes


def _material_field_receipt(identities, events, field_events, field_ids, current_hist, source_hist):
    """Keep unknown names, values, hashes and full keys entirely in memory."""
    if len(field_events) > MAX_PPI_FIELD_GROUPS:
        raise Hold("A666_PPI_FIELD_GROUPS_HOLD")
    return {
        "option_ppi_material_identity_total": len(identities),
        "option_ppi_material_change_total": events,
        "option_ppi_material_field_hist": [
            {"field": name, "operation": operation,
             "identities": len(field_ids[(name, operation)]), "events": count}
            for (name, operation), count in sorted(field_events.items())
        ],
        "option_ppi_material_current_hist": [
            {"category": cat, "count": count} for cat, count in sorted(current_hist.items())],
        "option_ppi_material_source_ref_hist": [
            {"category": cat, "count": count} for cat, count in sorted(source_hist.items())],
    }


def _option_root_hist(conn, keys):
    """Reconstruct only the OPCIONES-relevant V2 part of normalize_group().

    The canonical pending_material_changes() marks a five-key identity pending
    when *any* CHANGED_REVIEW_REQUIRED row has missing/corrupt snapshots or a
    material semantic difference. Its only review exception is A198 CAUCIONES,
    never OPCIONES. The append-only change-log flag alone cannot certify this.
    A V2 current 'revoked' or 'adjusted_series_unverified' truthy flag on a
    usable non-IOL_STRUCTURED_API option record independently triggers the
    same persisted bridge reason. This tool reads but never updates evidence.
    """
    if not keys:
        # A later snapshot may genuinely have no A658-like option review gate.
        # This is a zero *diagnostic target*, never PAPER-admission evidence.
        return [], _material_field_receipt(set(), 0, Counter(), {}, Counter(), Counter())
    if len(keys) > MAX_ROWS:
        raise Hold("A661_TARGET_ROWS_HOLD")
    flags = {key: set() for key in keys}
    sources = {key: set() for key in keys}
    ppi_changed_identities = set()
    ppi_changed_events = 0
    field_events = Counter()
    field_ids = defaultdict(set)
    ppi_current_hist = Counter()
    ppi_source_hist = Counter()
    now = datetime.now(timezone.utc)
    used_bytes = 0
    changes = conn.execute("""SELECT ch.family,ch.ticker,ch.market,ch.currency,
      ch.settlement,ch.change_id,ch.source_class,ch.previous_hash,ch.current_hash,
      ch.detected_at,prev.evidence_json AS previous_json,
      curr.evidence_json AS current_json,prev.observed_at AS previous_observed_at,
      curr.observed_at AS current_observed_at,
      prev.source_ref AS previous_source_ref,curr.source_ref AS current_source_ref,
      latest.evidence_hash AS latest_hash
      FROM contract_evidence_v2_changes ch
      LEFT JOIN contract_evidence_v2_snapshots prev
        ON prev.family=ch.family AND prev.ticker=ch.ticker AND prev.market=ch.market
       AND prev.currency=ch.currency AND prev.settlement=ch.settlement
       AND prev.source_class=ch.source_class AND prev.evidence_hash=ch.previous_hash
      LEFT JOIN contract_evidence_v2_snapshots curr
        ON curr.family=ch.family AND curr.ticker=ch.ticker AND curr.market=ch.market
       AND curr.currency=ch.currency AND curr.settlement=ch.settlement
       AND curr.source_class=ch.source_class AND curr.evidence_hash=ch.current_hash
      LEFT JOIN contract_evidence_v2_current latest
        ON latest.family=ch.family AND latest.ticker=ch.ticker
       AND latest.market=ch.market AND latest.currency=ch.currency
       AND latest.settlement=ch.settlement AND latest.source_class=ch.source_class
      WHERE ch.family='OPCIONES' AND ch.status='CHANGED_REVIEW_REQUIRED'
      LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(changes) > MAX_ROWS:
        raise Hold("A661_CHANGES_ROWS_HOLD")
    for row in changes:
        key = _key(row)
        if key not in keys:
            continue
        source = row["source_class"]
        problems = False
        try:
            detected = _utc(row["detected_at"])
            if detected > now:
                problems = True
        except Hold:
            problems = True
        previous_raw, current_raw = row["previous_json"], row["current_json"]
        if previous_raw is None or current_raw is None:
            # Canonical missing evidence is pending, not automatically safe.
            if isinstance(source, str) and source.startswith("PPI_"):
                raise Hold("A666_PPI_MATERIAL_INTEGRITY_HOLD")
            flags[key].update(("PENDING_MATERIAL", "EVIDENCE_INTEGRITY_HOLD"))
            sources[key].add(source)
            continue
        if not isinstance(previous_raw, str) or not isinstance(current_raw, str):
            if isinstance(source, str) and source.startswith("PPI_"):
                raise Hold("A666_PPI_MATERIAL_INTEGRITY_HOLD")
            flags[key].update(("PENDING_MATERIAL", "EVIDENCE_INTEGRITY_HOLD"))
            sources[key].add(source)
            continue
        used_bytes += len(previous_raw) + len(current_raw)
        if used_bytes > MAX_EVIDENCE_BYTES:
            raise Hold("A661_EVIDENCE_BYTES_HOLD")
        try:
            previous, current = _strict_json(previous_raw), _strict_json(current_raw)
            # Same material-evidence hash as the actual product module. Full
            # snapshot hash/clock checks add a conservative integrity alarm.
            is_material = (_canonical_hash(_material_projection(previous)) !=
                           _canonical_hash(_material_projection(current)))
            if (not isinstance(row["previous_hash"], str)
                    or not isinstance(row["current_hash"], str)
                    or _canonical_hash(previous) != row["previous_hash"]
                    or _canonical_hash(current) != row["current_hash"]
                    or _utc(row["previous_observed_at"]) > now
                    or _utc(row["current_observed_at"]) > now):
                problems = True
        except (Hold, TypeError, ValueError, OverflowError):
            # Mirrors canonical json.loads failure -> material pending.
            is_material = True
            problems = True
        if is_material:
            flags[key].add("PENDING_MATERIAL")
            sources[key].add(source)
            if isinstance(source, str) and source.startswith("PPI_"):
                # Corrupt PPI snapshots may not become a misleading clean
                # field histogram; fail the diagnostic rather than waive HOLD.
                if problems:
                    raise Hold("A666_PPI_MATERIAL_INTEGRITY_HOLD")
                changed_fields = _material_fields(previous, current)
                ppi_changed_identities.add(key)
                ppi_changed_events += 1
                for field, operation in changed_fields:
                    field_events[(field, operation)] += 1
                    field_ids[(field, operation)].add(key)
                latest = row["latest_hash"]
                current_category = ("CURRENT_SOURCE_ABSENT" if latest is None
                                    else "LATEST_CURRENT" if latest == row["current_hash"]
                                    else "HISTORICAL_NOT_LATEST")
                ppi_current_hist[current_category] += 1
                ppi_source_hist[_ppi_source_ref_category(
                    row["previous_source_ref"], row["current_source_ref"])] += 1
        if problems:
            flags[key].add("EVIDENCE_INTEGRITY_HOLD")
            sources[key].add(source)

    current = conn.execute("""SELECT c.family,c.ticker,c.market,c.currency,
      c.settlement,c.source_class,c.observed_at,s.source_ref,s.effective_at,
      s.evidence_json,s.evidence_hash
      FROM contract_evidence_v2_current c JOIN contract_evidence_v2_snapshots s
        ON s.snapshot_id=c.snapshot_id
      WHERE c.family='OPCIONES' LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(current) > MAX_ROWS:
        raise Hold("A661_CURRENT_ROWS_HOLD")
    for row in current:
        key = _key(row)
        if key not in keys:
            continue
        source = row["source_class"]
        if source not in USABLE_V2_SOURCES:
            # normalize_group() ignores inferred IOL_STRUCTURED_API options.
            # Unknown / legacy sources cannot cause revoked or adjusted flags.
            continue
        value = row["evidence_json"]
        if not isinstance(value, str) or len(value) > 32768:
            flags[key].add("EVIDENCE_INTEGRITY_HOLD")
            sources[key].add(source)
            continue
        used_bytes += len(value)
        if used_bytes > MAX_EVIDENCE_BYTES:
            raise Hold("A661_EVIDENCE_BYTES_HOLD")
        try:
            evidence = _strict_json(value)
            if (not isinstance(evidence, dict) or not row["source_ref"]
                    or _canonical_hash(evidence) != row["evidence_hash"]):
                raise Hold("A661_CURRENT_INTEGRITY_HOLD")
            seen = _utc(row["observed_at"])
            if seen > now or (row["effective_at"] and _utc(row["effective_at"]) > now):
                flags[key].add("EVIDENCE_INTEGRITY_HOLD")
                sources[key].add(source)
            for literal, label in (("revoked", "REVOKED"),
                                   ("adjusted_series_unverified", "ADJUSTED_SERIES_UNVERIFIED")):
                raw = evidence.get(literal)
                if raw is not None and type(raw) is not bool:
                    flags[key].add("EVIDENCE_INTEGRITY_HOLD")
                    sources[key].add(source)
                if raw:
                    flags[key].add(label)
                    sources[key].add(source)
        except (Hold, TypeError, ValueError, OverflowError):
            flags[key].add("EVIDENCE_INTEGRITY_HOLD")
            sources[key].add(source)
    hist = Counter()
    for key in keys:
        root = ("+".join(sorted(flags[key])) if flags[key] else ROOT_STANDALONE)
        hist[(root, _source_bucket(sources[key]))] += 1
    if len(hist) > MAX_ROOT_GROUPS:
        raise Hold("A661_ROOT_GROUPS_HOLD")
    return ([dict(flags=reason, source_class_bucket=bucket, count=count)
             for (reason, bucket), count in sorted(hist.items())],
            _material_field_receipt(ppi_changed_identities, ppi_changed_events,
                                    field_events, field_ids, ppi_current_hist, ppi_source_hist))


def _capability(raw):
    return raw if raw in CAP_CODES else "OTHER_CAPABILITY_CODE"


def _metadata_flags(raw):
    if not isinstance(raw, str) or len(raw) > 32768:
        raise Hold("CATALOG_METADATA_SIZE_HOLD")
    try:
        value = _strict_json(raw or "{}")
    except Hold:
        return "INVALID_METADATA", "V17_INVALID", "INVALID_METADATA"
    if not isinstance(value, dict):
        return "INVALID_METADATA", "V17_INVALID", "INVALID_METADATA"
    bridge = value.get("_contract_bridge")
    status = bridge.get("status") if isinstance(bridge, dict) else None
    state = status if status in {"NORMALIZED", "BLOCKED"} else (
        "ABSENT" if not bridge else "OTHER_BRIDGE_STATUS")
    attached = value.get("financial_contract_v17")
    v17 = "V17_ABSENT" if attached is None else (
        "V17_ATTACHED" if isinstance(attached, dict) and attached else "V17_INVALID")
    return state, v17, _bridge_reason_set(bridge if isinstance(bridge, dict) else None)


def _bridge_reason_set(bridge):
    if not isinstance(bridge, dict) or bridge.get("status") != "BLOCKED":
        return "NOT_BLOCKED"
    gaps = bridge.get("gaps")
    if not isinstance(gaps, list) or not 0 < len(gaps) <= 64:
        raise Hold("BRIDGE_GAPS_CONTRACT_HOLD")
    labels = set()
    for reason in gaps:
        if not isinstance(reason, str) or len(reason) > 512:
            raise Hold("BRIDGE_GAPS_CONTRACT_HOLD")
        if reason == "CHANGE_REVIEW_REQUIRED":
            label = "CHANGE_REVIEW_REQUIRED"
        elif reason.startswith("CONFLICT:"):
            label = "CONFLICT_TERM"
        elif reason.startswith("IDENTITY_"):
            label = "IDENTITY_HOLD"
        elif reason in {"INVALID_PROVENANCE", "UNVERIFIED_SOURCE"}:
            label = "PROVENANCE_HOLD"
        elif reason == "EVIDENCE_NOT_CURRENT":
            label = "EVIDENCE_TIME_HOLD"
        elif reason == "CONTRACT_V2_CURRENCY_MISMATCH":
            label = "CURRENCY_CONFLICT"
        elif reason.startswith(("INVALID_", "CONTRACT_INVALID:")):
            label = "INVALID_CONTRACT"
        elif reason == "OPTION_IOL_SERIES_CONTRACT_UNVERIFIED":
            label = "OPTION_IOL_SERIES_UNVERIFIED"
        elif reason.startswith("MISSING:"):
            label = "MISSING_FIELDS"
        elif reason.startswith("EXECUTOR_GAP:"):
            label = "EXECUTOR_HOLD"
        else:
            label = "OTHER_BLOCKED_GAP"
        labels.add(label)
    return "+".join(sorted(labels))


def _safe_bridge_reasons(value):
    if value in BRIDGE_REASON_STANDALONE:
        return True
    if not isinstance(value, str) or len(value) > 256:
        return False
    values = value.split("+")
    return bool(values) and values == sorted(set(values)) and all(
        item in BRIDGE_REASON_LABELS for item in values)


def _aggregate(conn):
    required = {
        "paper_workspace": {"id", "namespace", "origin", "imported_legacy", "dataset_id"},
        "observer_state": {"id", "real_orders_sent"},
        "financial_instrument_catalog": {"ticker", "instrument_type", "market", "currency", "settlement", "status", "capability", "metadata_json"},
        "contract_evidence_v2_current": {"family", "ticker", "market", "currency", "settlement", "snapshot_id", "source_class", "evidence_hash", "observed_at"},
        "contract_evidence_v2_snapshots": {"family", "ticker", "market", "currency", "settlement", "snapshot_id", "source_class", "source_ref", "effective_at", "evidence_hash", "observed_at", "evidence_json"},
        "complementary_contract_retry": {"ticker", "instrument_type", "market", "currency", "settlement", "reason", "source"},
        "contract_evidence_v2_changes": {"family", "ticker", "market", "currency", "settlement", "status", "change_id", "source_class", "previous_hash", "current_hash", "detected_at"},
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
    retries, retry_sources = _retries(conn)
    change_logs = _change_log_entries(conn)
    rows = conn.execute("""SELECT instrument_type AS family,ticker,market,currency,
      settlement,status,capability,metadata_json
      FROM financial_instrument_catalog LIMIT ?""", (MAX_ROWS + 1,)).fetchall()
    if len(rows) > MAX_ROWS:
        raise Hold("CATALOG_ROWS_LIMIT_HOLD")
    groups = Counter()
    option_review_keys = set()
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
        bridge, v17, bridge_reasons = _metadata_flags(row["metadata_json"])
        retry_set = retries.get(key, set())
        retry = ("NO_RETRY" if not retry_set else next(iter(retry_set))
                 if len(retry_set) == 1 else "MULTIPLE_RETRY_REASONS")
        current = ("INCOMPLETE_IDENTITY" if not _key_complete(key)
                   else "EXACT_PPI_SOURCE_LABEL" if "PPI" in labels.get(key, ())
                   else "EXACT_NONPPI_SOURCE_LABEL" if labels.get(key)
                   else "NO_EXACT_CURRENT")
        if (family == "OPCIONES" and bridge == "BLOCKED"
                and bridge_reasons == "CHANGE_REVIEW_REQUIRED"
                and row["capability"] == "CONTRACT_EVIDENCE_REVIEW_REQUIRED"
                and current == "EXACT_PPI_SOURCE_LABEL"):
            option_review_keys.add(key)
        groups[(family, _capability(row["capability"]), bridge, retry, current, v17,
                bridge_reasons, _retry_source_label(retry_sources.get(key, set())),
                "REVIEW_LOG_ENTRY_PRESENT" if key in change_logs else "NO_REVIEW_LOG_ENTRY")] += 1
    if len(groups) > MAX_GROUPS:
        raise Hold("GROUPS_LIMIT_HOLD")
    if (any(coverage[family]["all"] == 0 for family in FAMILIES)
            or sum(c["active_gap"] for c in coverage.values()) == 0):
        raise Hold("ACTIVE_GAP_DENOMINATOR_HOLD")
    joint = [dict(zip(FIELDS, key), count=value) for key, value in sorted(groups.items())]
    option_roots, ppi_material = _option_root_hist(conn, option_review_keys)
    return (joint, {family: dict(sorted(row.items())) for family, row in sorted(coverage.items())},
            option_roots, len(option_review_keys), ppi_material)


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
        joint, coverage, option_roots, option_root_total, ppi_material = _aggregate(conn)
        report["paper_workspace_verified"] = True
        report["real_orders_sent"] = 0
        report["sampled_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["joint"] = joint
        report["option_review_root_total"] = option_root_total
        report["option_review_root_hist"] = option_roots
        report.update(ppi_material)
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
                    or not _safe_bridge_reasons(key[6]) or key[7] not in RETRY_SOURCES
                    or key[8] not in CHANGE_LOGS
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
        roots = value.get("option_review_root_hist")
        root_total = value.get("option_review_root_total")
        if (not isinstance(roots, list) or len(roots) > MAX_ROOT_GROUPS
                or type(root_total) is not int or not 0 <= root_total <= MAX_COUNT):
            raise Hold("UNTRUSTED_ROOT_GROUPS")
        root_seen = set()
        source_roots = []
        for item in roots:
            if not isinstance(item, dict) or set(item) != {"flags", "source_class_bucket", "count"}:
                raise Hold("UNTRUSTED_ROOT_GROUPS")
            flags, bucket, count = item["flags"], item["source_class_bucket"], item["count"]
            parts = flags.split("+") if isinstance(flags, str) else []
            if (not ((flags == ROOT_STANDALONE and bucket == "NONE") or
                     (parts and parts == sorted(set(parts)) and set(parts) <= ROOT_FLAGS))
                    or bucket not in ROOT_SOURCES or type(count) is not int
                    or not 0 < count <= MAX_COUNT or (flags, bucket) in root_seen):
                raise Hold("UNTRUSTED_ROOT_GROUPS")
            root_seen.add((flags, bucket))
            source_roots.append({"flags": flags, "source_class_bucket": bucket, "count": count})
        expected_options = sum(item["count"] for item in joint
                               if item["family"] == "OPCIONES" and item["bridge"] == "BLOCKED"
                               and item["bridge_reasons"] == "CHANGE_REVIEW_REQUIRED"
                               and item["capability"] == "CONTRACT_EVIDENCE_REVIEW_REQUIRED"
                               and item["current"] == "EXACT_PPI_SOURCE_LABEL")
        if sum(item["count"] for item in roots) != root_total or root_total != expected_options:
            raise Hold("UNTRUSTED_ROOT_DENOMINATOR")
        ppi_identities = value.get("option_ppi_material_identity_total")
        ppi_changes = value.get("option_ppi_material_change_total")
        field_hist = value.get("option_ppi_material_field_hist")
        current_hist = value.get("option_ppi_material_current_hist")
        ref_hist = value.get("option_ppi_material_source_ref_hist")
        if (type(ppi_identities) is not int or type(ppi_changes) is not int
                or not 0 <= ppi_identities <= root_total
                or not ppi_identities <= ppi_changes <= MAX_ROWS
                or not isinstance(field_hist, list) or len(field_hist) > MAX_PPI_FIELD_GROUPS
                or not isinstance(current_hist, list) or len(current_hist) > len(PPI_CURRENT_CATEGORIES)
                or not isinstance(ref_hist, list) or len(ref_hist) > len(PPI_REF_CATEGORIES)):
            raise Hold("UNTRUSTED_PPI_MATERIAL_HISTOGRAM")
        seen_fields = set()
        field_events_total = 0
        for item in field_hist:
            if not isinstance(item, dict) or set(item) != {"field", "operation", "identities", "events"}:
                raise Hold("UNTRUSTED_PPI_MATERIAL_HISTOGRAM")
            key = (item["field"], item["operation"])
            if (key in seen_fields or key[0] not in PPI_MATERIAL_FIELDS
                    or key[1] not in PPI_MATERIAL_OPERATIONS
                    or type(item["identities"]) is not int or type(item["events"]) is not int
                    or not 0 < item["identities"] <= ppi_identities
                    or not item["identities"] <= item["events"] <= ppi_changes):
                raise Hold("UNTRUSTED_PPI_MATERIAL_HISTOGRAM")
            seen_fields.add(key)
            field_events_total += item["events"]
        if ((ppi_changes > 0 and field_events_total < ppi_changes)
                or (ppi_changes == 0 and (field_hist or ppi_identities))):
            raise Hold("UNTRUSTED_PPI_MATERIAL_HISTOGRAM")

        def safe_categories(items, allowed):
            seen = set()
            total = 0
            for item in items:
                if (not isinstance(item, dict) or set(item) != {"category", "count"}
                        or item["category"] not in allowed or item["category"] in seen
                        or type(item["count"]) is not int or not 0 < item["count"] <= MAX_ROWS):
                    raise Hold("UNTRUSTED_PPI_MATERIAL_HISTOGRAM")
                seen.add(item["category"])
                total += item["count"]
            if total != ppi_changes:
                raise Hold("UNTRUSTED_PPI_MATERIAL_HISTOGRAM")

        safe_categories(current_hist, PPI_CURRENT_CATEGORIES)
        safe_categories(ref_hist, PPI_REF_CATEGORIES)
        expected_ppi_material = sum(item["count"] for item in source_roots
                                    if item["source_class_bucket"] == "PPI_ONLY"
                                    and "PENDING_MATERIAL" in item["flags"].split("+"))
        if ppi_identities < expected_ppi_material:
            raise Hold("UNTRUSTED_PPI_MATERIAL_DENOMINATOR")
        if (total != value.get("catalog_active_gap_total") or total > MAX_COUNT or total == 0
                or covered_total != value.get("catalog_six_family_total")
                or covered_total > MAX_COUNT):
            raise Hold("UNTRUSTED_DENOMINATOR")
        safe.update(state="READ_ONLY_OBSERVED", reason="CORRELATIVE_DIAGNOSTIC_ONLY",
                    private_ro_mount=True, paper_workspace_verified=True,
                    real_orders_sent=0, sampled_at_utc=dt.isoformat(),
                    catalog_active_gap_total=total,
                    catalog_six_family_total=covered_total,
                    option_review_root_total=root_total,
                    option_review_root_hist=sorted(
                        source_roots, key=lambda x: (x["flags"], x["source_class_bucket"])),
                    option_ppi_material_identity_total=ppi_identities,
                    option_ppi_material_change_total=ppi_changes,
                    option_ppi_material_field_hist=sorted(
                        field_hist, key=lambda x: (x["field"], x["operation"])),
                    option_ppi_material_current_hist=sorted(
                        current_hist, key=lambda x: x["category"]),
                    option_ppi_material_source_ref_hist=sorted(
                        ref_hist, key=lambda x: x["category"]),
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
