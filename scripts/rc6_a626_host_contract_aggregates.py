#!/usr/bin/env python3
"""A626: aggregate six financial families on a private read-only host mount.

Only execute via the dedicated Actions SSH step after a private mount namespace
has remounted /opt/porota-trading/data read-only. Never copy the live database,
create files, fetch providers, inspect secrets or promote a PAPER capability.
The live WAL snapshot is not a PIT causal receipt or provider authorization.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import uuid

DATA_ROOT = Path("/opt/porota-trading/data")
DB = DATA_ROOT / "paper_v17" / "observer_v17.db"
NAMESPACE = "POROTA_PAPER_V17_FRESH"
SCHEMA = "rc6.a626.host-contract-aggregates-ro.v1"
FAMILIES = ("BONOS", "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI")
ACTIVE = frozenset({"AVAILABLE", "ACTIVE", "READY"})
PPI_CATALOG = frozenset({"PPI_FIELD", "REQUEST_CANDIDATE"})
CURRENT_INTEGRITY_REASONS = frozenset({
    "HOST_V2_SNAPSHOT_REFERENCE_MISSING",
    "HOST_V2_SNAPSHOT_FIVE_KEY_MISMATCH",
    "HOST_V2_SNAPSHOT_SOURCE_CLASS_MISMATCH",
    "HOST_V2_SNAPSHOT_EVIDENCE_HASH_MISMATCH",
    "HOST_V2_SNAPSHOT_OBSERVED_AT_INVALID",
    "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED",
})
MAX_CATALOG_ROWS = 60_000
MAX_CURRENT_ROWS = 60_000
MAX_METADATA_BYTES = 128 * 1024 * 1024
MAX_HISTORY_ROWS = 200_000
MAX_HISTORY_IDENTITY_BYTES = 24 * 1024 * 1024
MAX_SECONDS = 14.0
MAX_GAP_SNAPSHOTS = 3000
MAX_GAP_EVIDENCE_BYTES = 8 * 1024 * 1024
MAX_GAP_FIELD_BYTES = 32 * 1024
MAX_GAP_TARGETS = 3000
GAP_IN_BATCH = 64
# These are only static PAPER contract questions. Passing this diagnostic
# never certifies provider provenance, market/PIT/Book or runtime admission.
GAP_TERM_REQUIREMENTS = {
    "BONOS": {
        "cash_multiplier": ("cash_multiplier", "paper_cash_multiplier"),
        "quantity_step": ("quantity_step", "paper_quantity_step"),
        "minimum_quantity": ("quantity_min", "minimum_quantity", "paper_quantity_min"),
    },
    "OBLIGACIONES": {
        "cash_multiplier": ("cash_multiplier", "paper_cash_multiplier"),
        "quantity_step": ("quantity_step", "paper_quantity_step"),
        "minimum_quantity": ("quantity_min", "minimum_quantity", "paper_quantity_min"),
    },
    "OPCIONES": {
        "cash_multiplier": ("cash_multiplier",),
        "quantity_step": ("quantity_step", "paper_quantity_step"),
        "minimum_quantity": ("quantity_min", "minimum_quantity", "paper_quantity_min"),
        "expires_at": ("expires_at", "expiry_at"),
        "underlying": ("underlying",),
        "strike": ("strike",),
        "option_right": ("option_right", "put_call"),
    },
    "FUTUROS": {
        "cash_multiplier": ("cash_multiplier",),
        "quantity_step": ("quantity_step", "paper_quantity_step"),
        "minimum_quantity": ("quantity_min", "minimum_quantity", "paper_quantity_min"),
        "expires_at": ("expires_at", "expiry_at"),
        "underlying": ("underlying",),
        "margin_rule": ("initial_margin", "paper_margin_rate"),
    },
    "CAUCIONES": {
        "side": ("side",), "term_days": ("term_days",),
        "minimum_principal": ("minimum_principal",),
        "paper_principal_step": ("paper_principal_step", "principal_step"),
        "day_count_basis": ("day_count_basis",),
        "fee_payment": ("fee_payment",),
        "paper_fill_policy": ("paper_fill_policy",),
        "fee_authority": ("fee_authority",),
    },
    "FCI": {
        # A numeric minimum does not authenticate a fund subscription policy.
        "subscription_policy": ("paper_subscription_policy",),
        "subscription_min": ("paper_subscription_min", "subscription_min"),
        "amount_step": ("paper_amount_unit", "subscription_step"),
    },
}
GAP_POSITIVE_NUMERIC = frozenset({
    "cash_multiplier", "paper_cash_multiplier", "quantity_step",
    "paper_quantity_step", "quantity_min", "minimum_quantity",
    "paper_quantity_min", "strike", "initial_margin", "paper_margin_rate",
    "term_days", "minimum_principal", "paper_principal_step",
    "principal_step", "day_count_basis", "paper_subscription_min",
    "subscription_min", "paper_amount_unit", "subscription_step",
})


class Blocked(Exception):
    pass


def _family(value):
    """Reporting bucket only; never use a family alias for evidence identity."""
    value = str(value or "").strip().upper()
    return {"ON": "OBLIGACIONES", "FCI_LOCAL": "FCI"}.get(value, value)


def _key(row):
    # Physical 5-key equality must be literal across catalog/current/snapshot/
    # historical provenance. Display aliases cannot accredit PPI evidence.
    return (str(row["family"] or "").strip().upper(),
            str(row["ticker"] or "").strip().upper(),
            str(row["market"] or "").strip().upper(),
            str(row["currency"] or "").strip().upper(),
            str(row["settlement"] or "").strip().upper())


def _complete(key):
    return len(key) == 5 and all(x not in ("", "UNKNOWN", "*", "NULL") for x in key)


def _mount_field(value):
    # Linux /proc/self/mountinfo escapes literal whitespace/backslash.
    return re.sub(r"\\([0-7]{3})",
                  lambda m: chr(int(m.group(1), 8)), value)


def private_ro_mount_verified(directory=DATA_ROOT):
    """No DB open when the longest covering mount is not the exact RO bind."""
    try:
        # A RO mount in the host's initial namespace would not be isolated.
        if os.stat("/proc/self/ns/mnt").st_ino == os.stat("/proc/1/ns/mnt").st_ino:
            return False
        directory = Path(directory).absolute()
        if directory.is_symlink() or directory.resolve(strict=True) != directory:
            return False
        mounts = []
        for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
            left = line.split(" - ", 1)[0].split()
            if len(left) < 6:
                continue
            point = Path(_mount_field(left[4]))
            opts = set(left[5].split(","))
            shared = any(flag.startswith("shared:") for flag in left[6:])
            mounts.append((point, opts, shared))
        # Actions' unshare+bind+remount must create an exact RO mount here.
        if not any(point == directory and "ro" in opts and not shared
                   for point, opts, shared in mounts):
            return False
        db = directory / "paper_v17" / "observer_v17.db"
        covering = [(len(str(point)), opts, shared) for point, opts, shared in mounts
                    if point == db or point in db.parents]
        if not covering:
            return False
        _, opts, shared = max(covering, key=lambda v: v[0])
        return "ro" in opts and not shared
    except (OSError, ValueError):
        return False


def _safe_files(directory, db):
    directory = Path(directory).absolute()
    db = Path(db).absolute()
    if (db != directory / "paper_v17" / "observer_v17.db"
            or ".." in db.parts or directory.is_symlink()
            or directory.resolve(strict=True) != directory):
        raise Blocked("HOST_DB_PATH_NOT_CANONICAL")
    if db.is_symlink() or db.resolve(strict=True) != db:
        raise Blocked("HOST_DB_ALIAS_UNSAFE")
    st = db.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size <= 0:
        raise Blocked("HOST_DB_FILE_IDENTITY_UNVERIFIED")
    found = {}
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(db) + suffix)
        if sidecar.is_symlink():
            raise Blocked("HOST_DB_SIDECAR_ALIAS_UNSAFE")
        if sidecar.exists():
            st = sidecar.stat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise Blocked("HOST_DB_SIDECAR_UNVERIFIED")
            found[suffix] = True
        else:
            found[suffix] = False
    if found["-wal"] and not found["-shm"]:
        raise Blocked("HOST_WAL_SHM_MISSING_FAILCLOSED")
    if found["-journal"]:
        raise Blocked("HOST_ROLLBACK_JOURNAL_UNVERIFIED")
    return {"wal_present": found["-wal"], "shm_present": found["-shm"],
            "journal_present": found["-journal"],
            "db_bytes": db.stat().st_size}


def _columns(conn, table, required):
    names = {str(row[1]) for row in conn.execute("PRAGMA table_info(" + table + ")")}
    if not required <= names:
        raise Blocked("HOST_SCHEMA_MISSING_" + table.upper())


def _metadata(value):
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _primary_claim(row, meta):
    return (str(row["settlement_source"] or "").strip().upper() in PPI_CATALOG
            or str(meta.get("_discovery_source") or "").upper() == "PPI_PRIMARY"
            or str(meta.get("_availability_source") or "").upper().startswith("PPI_PRIMARY"))


def _historical_ppi_memberships(conn, wanted):
    """One bounded streaming pass, no per-gap SQL, DISTINCT temp-B-tree or disk."""
    if not wanted:
        return set(), 0, 0
    matches = set()
    rows_scanned = identity_bytes = 0
    cursor = conn.execute("""SELECT family,ticker,market,currency,settlement
       FROM contract_evidence_v2_snapshots
       WHERE substr(source_class,1,4)='PPI_' LIMIT ?""",
                          (MAX_HISTORY_ROWS + 1,))
    for row in cursor:
        rows_scanned += 1
        if rows_scanned > MAX_HISTORY_ROWS:
            raise Blocked("HOST_HISTORY_PPI_ROWS_BUDGET")
        key = _key(row)
        identity_bytes += sum(len(str(part).encode("utf-8")) for part in key)
        if identity_bytes > MAX_HISTORY_IDENTITY_BYTES:
            raise Blocked("HOST_HISTORY_PPI_IDENTITY_BYTES_BUDGET")
        if key in wanted and _complete(key):
            matches.add(key)
    return matches, rows_scanned, identity_bytes


def _bucket():
    return {"catalog_active": 0, "catalog_prelim_ready": 0,
            "capability_gaps": 0, "gap_ppi_catalog_claimed": 0,
            "gap_complement_catalog_only": 0, "gap_ticker_ambiguous": 0,
            "gap_incomplete_five_key": 0, "exact_current_v2_ppi": 0,
            "exact_current_v2_complement_only": 0,
            "ticker_only_v2_mismatch": 0, "v2_current_absent": 0,
            "historic_ppi_exact_without_current": 0,
            "catalog_financial_contract_v17_declared": 0,
            "catalog_fund_terms_v1_declared": 0,
            "catalog_caucion_terms_v1_declared": 0,
            "catalog_contract_conflicts_declared": 0,
            "capability_codes": Counter()}


def _observed_utc(value):
    """Decode only bounded timezone-aware observations; never infer a clock."""
    if type(value) is not str or not value or len(value) > 64 or value.strip() != value:
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            return None
        return instant.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def _current_snapshot_integrity_reason(item):
    """Classify a bad current pointer without emitting market or identity data.

    The producer preserves the first observed_at for a reused snapshot hash,
    while current records the latest observation. Equality of strings is not
    a valid integrity guard; a timezone-aware chronological reversal is.
    """
    if item["joined_snapshot_id"] is None:
        return "HOST_V2_SNAPSHOT_REFERENCE_MISSING"
    snapshot_key = _key({
        "family": item["sf"], "ticker": item["st"],
        "market": item["sm"], "currency": item["sc"],
        "settlement": item["ss"],
    })
    if _key(item) != snapshot_key:
        return "HOST_V2_SNAPSHOT_FIVE_KEY_MISMATCH"
    if item["source_class"] != item["so"]:
        return "HOST_V2_SNAPSHOT_SOURCE_CLASS_MISMATCH"
    if item["evidence_hash"] != item["sh"]:
        return "HOST_V2_SNAPSHOT_EVIDENCE_HASH_MISMATCH"
    current_at = _observed_utc(item["observed_at"])
    snapshot_at = _observed_utc(item["sa"])
    if current_at is None or snapshot_at is None:
        return "HOST_V2_SNAPSHOT_OBSERVED_AT_INVALID"
    if current_at < snapshot_at:
        return "HOST_V2_SNAPSHOT_OBSERVED_AT_REVERSED"
    return None


def _gap_term_present(field, value):
    """No guessed numbers/clock/structured terms; diagnostic only."""
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        return False
    if field in GAP_POSITIVE_NUMERIC:
        try:
            parsed = Decimal(str(value))
            return parsed.is_finite() and parsed > 0
        except (InvalidOperation, ValueError, TypeError):
            return False
    if not isinstance(value, str) or not 0 < len(value) <= 512:
        return False
    return value.strip() not in {"", "UNKNOWN", "NO_VERIFICADO", "NONE", "NULL"}


def _inspect_gap_terms(conn, targets, current_by_key, *,
                       uninspectable, expected_gaps, deadline):
    """Bounded, aggregate-only look at Current V2 payloads of capability gaps.

    This is an opt-in extension of the existing host's single RO transaction;
    it never inspects names/positions/orders or writes temporary tables/files.
    SHA validity is not provider authenticity or even a trading contract.
    """
    if time.monotonic() >= deadline:
        raise Blocked("HOST_GAP_TERMS_TIME_BUDGET")
    try:
        _columns(conn, "contract_evidence_v2_snapshots", {
            "snapshot_id", "source_ref", "effective_at", "evidence_json"})
    except Blocked:
        raise Blocked("HOST_GAP_TERMS_SCHEMA_MISSING") from None
    if len(targets) > MAX_GAP_TARGETS:
        raise Blocked("HOST_GAP_TERMS_TARGET_BUDGET")
    selected = {}
    for family, key in targets:
        for item in current_by_key.get(key, ()):
            sid = item["snapshot_id"]
            if type(sid) is not int or sid <= 0:
                raise Blocked("HOST_GAP_TERMS_SNAPSHOT_ID_INVALID")
            selected[sid] = item
        if time.monotonic() >= deadline:
            raise Blocked("HOST_GAP_TERMS_TIME_BUDGET")
    if len(selected) > MAX_GAP_SNAPSHOTS:
        raise Blocked("HOST_GAP_TERMS_SNAPSHOT_BUDGET")

    samples = {}
    total_bytes = 0
    snapshot_ids = sorted(selected)
    for start in range(0, len(snapshot_ids), GAP_IN_BATCH):
        if time.monotonic() >= deadline:
            raise Blocked("HOST_GAP_TERMS_TIME_BUDGET")
        batch = snapshot_ids[start:start + GAP_IN_BATCH]
        params = ",".join("?" for _ in batch)
        for row in conn.execute(
                "SELECT snapshot_id,source_ref,effective_at,evidence_json "
                "FROM contract_evidence_v2_snapshots WHERE snapshot_id IN ("+params+")", batch):
            sid = row["snapshot_id"]
            raw = row["evidence_json"]
            if not isinstance(raw, str):
                raise Blocked("HOST_GAP_TERMS_PAYLOAD_MISSING")
            encoded = raw.encode("utf-8")
            if not 0 < len(encoded) <= MAX_GAP_FIELD_BYTES:
                raise Blocked("HOST_GAP_TERMS_FIELD_BUDGET")
            total_bytes += len(encoded)
            if total_bytes > MAX_GAP_EVIDENCE_BYTES:
                raise Blocked("HOST_GAP_TERMS_BYTES_BUDGET")
            ref = row["source_ref"]
            if not isinstance(ref, str) or not 0 < len(ref.strip()) <= 1024:
                raise Blocked("HOST_GAP_TERMS_SOURCE_REF_UNVERIFIED")
            effective = row["effective_at"]
            if effective:
                at = _observed_utc(effective)
                if at is None or at > datetime.now(timezone.utc):
                    raise Blocked("HOST_GAP_TERMS_EFFECTIVITY_UNVERIFIED")
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                raise Blocked("HOST_GAP_TERMS_PAYLOAD_JSON_INVALID") from None
            if not isinstance(parsed, dict):
                raise Blocked("HOST_GAP_TERMS_PAYLOAD_JSON_INVALID")
            # Mirrors cp_contract_evidence_v2_hf6.canonical_json exactly.
            canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str)
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != selected[sid]["evidence_hash"]:
                raise Blocked("HOST_GAP_TERMS_PAYLOAD_HASH_MISMATCH")
            samples[sid] = parsed
            if time.monotonic() >= deadline:
                raise Blocked("HOST_GAP_TERMS_TIME_BUDGET")
    if len(samples) != len(selected):
        raise Blocked("HOST_GAP_TERMS_SNAPSHOT_MISSING")

    stats = {family: {"gap_rows": uninspectable[family],
                      "uninspectable_incomplete_five_key": uninspectable[family],
                      "no_exact_current_payload": 0,
                      "exact_current_source_label_only": 0,
                      "offline_terms_present_not_admitted": 0,
                      "offline_terms_missing": 0,
                      "missing_term_counts": Counter()}
             for family in FAMILIES}
    for family, key in targets:
        group = stats[family]
        group["gap_rows"] += 1
        records = current_by_key.get(key, ())
        if not records:
            group["no_exact_current_payload"] += 1
            continue
        group["exact_current_source_label_only"] += 1
        visible = []
        for item in records:
            if family == "OPCIONES" and item["source_class"] == "IOL_STRUCTURED_API":
                continue  # IOL option series/lot sidecar intentionally not authority.
            visible.append(samples[item["snapshot_id"]])
        missing = [name for name, aliases in GAP_TERM_REQUIREMENTS[family].items()
                   if not any(_gap_term_present(alias, evidence.get(alias))
                              for alias in aliases for evidence in visible)]
        if missing:
            group["offline_terms_missing"] += 1
            group["missing_term_counts"].update(missing)
        else:
            group["offline_terms_present_not_admitted"] += 1
        if time.monotonic() >= deadline:
            raise Blocked("HOST_GAP_TERMS_TIME_BUDGET")
    gap_total = sum(group["gap_rows"] for group in stats.values())
    if (gap_total != sum(expected_gaps.values())
            or any(group["gap_rows"] != expected_gaps[family]
                   for family, group in stats.items())):
        raise Blocked("HOST_GAP_TERMS_DENOMINATOR_MISMATCH")
    return {"state": "EVIDENCE_ONLY_NO_GO", "read_only": True,
            "payload_hash_integrity_checked": True,
            "provider_authenticity_checked": False,
            "causal_pit_or_book_checked": False,
            "paper_auto_enabled": False, "snapshots_checked": len(samples),
            "payload_bytes_checked": total_bytes, "gap_total": gap_total,
            "by_family": {family: {name: (dict(sorted(value.items()))
                                    if isinstance(value, Counter) else value)
                                  for name, value in group.items()}
                          for family, group in stats.items()}}


def _aggregate(conn, *, inspect_gap_terms=False, deadline=None):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"paper_workspace", "financial_instrument_catalog",
                "contract_evidence_v2_current", "contract_evidence_v2_snapshots"}
    if not required <= names:
        raise Blocked("HOST_REQUIRED_TABLES_MISSING")
    _columns(conn, "paper_workspace", {
        "id", "namespace", "dataset_id", "origin", "imported_legacy", "created_at"})
    _columns(conn, "financial_instrument_catalog", {
        "ticker", "instrument_type", "market", "currency", "settlement",
        "settlement_source", "status", "capability", "metadata_json"})
    _columns(conn, "contract_evidence_v2_current", {
        "family", "ticker", "market", "currency", "settlement",
        "source_class", "snapshot_id", "evidence_hash", "observed_at"})
    _columns(conn, "contract_evidence_v2_snapshots", {
        "snapshot_id", "family", "ticker", "market", "currency", "settlement",
        "source_class", "evidence_hash", "observed_at"})
    identity = conn.execute("""SELECT id,namespace,dataset_id,origin,imported_legacy,
           created_at FROM paper_workspace LIMIT 2""").fetchall()
    if len(identity) != 1:
        raise Blocked("HOST_PAPER_WORKSPACE_UNVERIFIED")
    wid, ns, dataset, origin, imported, created = identity[0]
    try:
        valid_uuid = str(uuid.UUID(str(dataset))) == str(dataset)
        valid_created = datetime.fromisoformat(str(created)).tzinfo is not None
    except (TypeError, ValueError):
        valid_uuid = valid_created = False
    if not (wid == 1 and ns == NAMESPACE and origin == "FRESH_EMPTY"
            and imported == 0 and valid_uuid and valid_created):
        raise Blocked("HOST_PAPER_WORKSPACE_UNVERIFIED")
    rows = [dict(row) for row in conn.execute("""SELECT ticker,
        instrument_type AS family, market,currency,settlement,
        settlement_source,status,capability,metadata_json
        FROM financial_instrument_catalog LIMIT ?""", (MAX_CATALOG_ROWS + 1,))]
    if not 0 < len(rows) <= MAX_CATALOG_ROWS:
        raise Blocked("HOST_CATALOG_DENOMINATOR_INVALID")
    if sum(len(str(r["metadata_json"] or "")) for r in rows) > MAX_METADATA_BYTES:
        raise Blocked("HOST_CATALOG_METADATA_BUDGET")
    declared_current = conn.execute(
        "SELECT COUNT(*) FROM contract_evidence_v2_current").fetchone()[0]
    if declared_current > MAX_CURRENT_ROWS:
        raise Blocked("HOST_V2_CURRENT_BUDGET")
    current = [dict(row) for row in conn.execute("""SELECT
          c.family,c.ticker,c.market,c.currency,c.settlement,c.source_class,
          c.evidence_hash,c.observed_at,c.snapshot_id,
          s.family AS sf,s.ticker AS st,s.market AS sm,s.currency AS sc,
          s.settlement AS ss,s.source_class AS so,
          s.evidence_hash AS sh,s.observed_at AS sa,
          s.snapshot_id AS joined_snapshot_id
        FROM contract_evidence_v2_current c
        LEFT JOIN contract_evidence_v2_snapshots s
          ON s.snapshot_id=c.snapshot_id LIMIT ?""", (MAX_CURRENT_ROWS + 1,))]
    if len(current) != declared_current:
        raise Blocked("HOST_V2_ORPHAN_OR_BOUND")
    by_key = defaultdict(set)
    current_by_key = defaultdict(list) if inspect_gap_terms else None
    by_ticker = Counter()
    invalid_current = Counter()
    valid_current = 0
    for item in current:
        integrity_reason = _current_snapshot_integrity_reason(item)
        if integrity_reason:
            # The catalog denominator remains physically measurable even if
            # some v2 pointers are corrupt. Quarantine instead of accrediting
            # their identity, source or ticker, and surface an explicit NO_GO.
            if integrity_reason not in CURRENT_INTEGRITY_REASONS:
                raise Blocked("HOST_V2_INTEGRITY_REASON_UNVERIFIED")
            invalid_current[integrity_reason] += 1
            continue
        valid_current += 1
        key = _key(item)
        by_ticker[key[1]] += 1
        if _complete(key):
            by_key[key].add(str(item["source_class"]))
            if inspect_gap_terms:
                current_by_key[key].append(item)
    ppi_symbol = Counter()
    for row in rows:
        meta = _metadata(row["metadata_json"])
        row["_meta"] = meta
        row["_ppi_claim"] = _primary_claim(row, meta)
        if row["_ppi_claim"]:
            ppi_symbol[str(row["ticker"] or "").strip().upper()] += 1
    out = {family: _bucket() for family in FAMILIES}
    pending_historical = []
    gap_targets = []
    uninspectable = Counter()
    for row in rows:
        key = _key(row)
        bucket = _family(row["family"])
        if bucket not in out or str(row["status"] or "").upper() not in ACTIVE:
            continue
        item = out[bucket]
        item["catalog_active"] += 1
        if str(row["capability"] or "").startswith("READY_PAPER_"):
            item["catalog_prelim_ready"] += 1
            continue
        item["capability_gaps"] += 1
        item["gap_ppi_catalog_claimed" if row["_ppi_claim"]
             else "gap_complement_catalog_only"] += 1
        if ppi_symbol[key[1]] != 1:
            item["gap_ticker_ambiguous"] += 1
        meta = row["_meta"]
        if isinstance(meta.get("financial_contract_v17"), dict) and meta["financial_contract_v17"]:
            item["catalog_financial_contract_v17_declared"] += 1
        if isinstance(meta.get("paper_family_contract_v1"), dict) and meta["paper_family_contract_v1"]:
            item["catalog_fund_terms_v1_declared"] += 1
        if isinstance(meta.get("paper_caucion_contract_v1"), dict) and meta["paper_caucion_contract_v1"]:
            item["catalog_caucion_terms_v1_declared"] += 1
        if meta.get("_contract_conflicts"):
            item["catalog_contract_conflicts_declared"] += 1
        code = str(row["capability"] or "")
        code = code if re.fullmatch(r"[A-Z_]{2,65}", code) else "OTHER_OR_UNSAFE_CODE"
        item["capability_codes"][code] += 1
        if not _complete(key):
            item["gap_incomplete_five_key"] += 1
            if inspect_gap_terms:
                uninspectable[bucket] += 1
            continue
        if inspect_gap_terms:
            gap_targets.append((bucket, key))
        classes = by_key.get(key, set())
        primary = any(x.startswith("PPI_") for x in classes)
        if primary:
            item["exact_current_v2_ppi"] += 1
        elif classes:
            item["exact_current_v2_complement_only"] += 1
        elif by_ticker[key[1]]:
            item["ticker_only_v2_mismatch"] += 1
        else:
            item["v2_current_absent"] += 1
        if not primary:
            pending_historical.append((key, item))
    historic, historical_rows, historical_bytes = _historical_ppi_memberships(
        conn, {key for key, _ in pending_historical})
    for key, item in pending_historical:
        if key in historic:
            item["historic_ppi_exact_without_current"] += 1
    families = []
    for family in FAMILIES:
        families.append({name: (dict(sorted(value.items())) if isinstance(value, Counter)
                                else value)
                         for name, value in {"family": family, **out[family]}.items()})
    if inspect_gap_terms and invalid_current:
        raise Blocked("HOST_GAP_TERMS_CURRENT_INTEGRITY_INVALID")
    detail = (_inspect_gap_terms(
                conn, gap_targets, current_by_key, uninspectable=uninspectable,
                expected_gaps={family: out[family]["capability_gaps"]
                               for family in FAMILIES}, deadline=deadline)
              if inspect_gap_terms else None)
    return (len(rows), len(current), historical_rows, historical_bytes, families,
            valid_current, dict(sorted(invalid_current.items())), detail)


def probe(*, data_root=DATA_ROOT, db=DB, guard=private_ro_mount_verified,
          inspect_gap_terms=False):
    if not guard(data_root):
        raise Blocked("HOST_PRIVATE_RO_MOUNT_UNVERIFIED")
    sidecars = _safe_files(data_root, db)
    conn = None
    try:
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        start = time.monotonic()
        if inspect_gap_terms and MAX_SECONDS <= 0:
            raise Blocked("HOST_GAP_TERMS_TIME_BUDGET")
        conn.set_progress_handler(lambda: int(time.monotonic() - start > MAX_SECONDS), 1000)
        conn.execute("BEGIN")
        (catalog_rows, current_rows, historical_rows, historical_bytes, families,
         valid_current, invalid_current, detail) = _aggregate(
             conn, inspect_gap_terms=inspect_gap_terms,
             deadline=start + MAX_SECONDS)
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        raise Blocked("HOST_SQLITE_READ_FAILED_OR_WAL_PERMISSION") from None
    finally:
        if conn is not None:
            conn.close()
    result = {"schema": SCHEMA,
            "status": ("DIAGNOSTIC_PARTIAL_NO_GO" if invalid_current
                       else "DIAGNOSTIC_ONLY"),
            "runtime_path": "PAPER_V17_CANONICAL_RO", "private_ro_mount_verified": True,
            "paper_workspace_identity_verified": True,
            "ppi_identity_authority_verified": False,
            "source_contract_terms_fresh_verified": False,
            "causal_pit_verified": False, "v2_payload_hash_checked": False,
            "host_db_writes": 0, "provider_gets": 0, "real_orders_sent_by_probe": 0,
            "paper_auto_enabled": False, "host_runtime_order_count": "NO_VERIFICADO",
            "catalog_rows": catalog_rows, "v2_current_rows": current_rows,
            "v2_current_valid_rows": valid_current,
            "v2_current_integrity_verified": not bool(invalid_current),
            "v2_current_invalid_reason_counts": invalid_current,
            "v2_history_ppi_rows_scanned": historical_rows,
            "v2_history_identity_bytes_scanned": historical_bytes,
            "historical_819_gap_match": sum(x["capability_gaps"] for x in families) == 819,
            "capability_gaps_measured": sum(x["capability_gaps"] for x in families),
            "families": families, **sidecars,
            "measured_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if inspect_gap_terms:
        result["gap_terms_diagnostic"] = detail
    return result


def main():
    try:
        if sys.argv[1:] not in ([], ["--inspect-gap-terms"]):
            raise Blocked("HOST_GAP_TERMS_ARG_INVALID")
        result = probe(inspect_gap_terms=sys.argv[1:] == ["--inspect-gap-terms"])
        print(json.dumps(result, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")))
        return 0
    except (Blocked, OSError) as exc:
        reason = str(exc) if isinstance(exc, Blocked) else "HOST_FS_READ_UNAVAILABLE"
        print(json.dumps({"schema": SCHEMA, "status": "NO_VERIFICADO",
                          "reason": reason, "host_db_writes": 0,
                          "provider_gets": 0, "paper_auto_enabled": False},
                         sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
