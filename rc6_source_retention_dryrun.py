"""A228 bounded history retention and incremental-gap DRY-RUN contracts.

This pure planner consumes *already captured*, independently dated evidence.
It cannot read a broker, database, filesystem or provider, mutate data, mark a
legacy row as identified, claim complete cross-store pins or authorize purge.
An external offhost inventory/capture hash remains an assertion until the
existing SHA-verified offhost preflight independently checks the real copy.

History is research evidence, not PAPER admission.  Every proposed gap window
requires a separate exclusive provider lock and durable committed checkpoint;
this module intentionally implements neither side effect.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Mapping

from cp_history_ingest_policy_hf6 import OPERATIONAL_HISTORY_FAMILIES
from history_source_policy_rc6 import source_rank
from rc6_data_retention import ART, daily_start
from rc6_dynamic_universe.sources import FAMILIES, MARKETS, TERMS


SCHEMA = "RC6_SOURCE_RETENTION_DRYRUN_A228_V1"
GAP_SCHEMA = "RC6_SOURCE_INCREMENTAL_GAP_PLAN_A228_V1"
MAX_VERSIONS = 10000
MAX_REFERENCES = 30000
MAX_EXPORTS = 2000
MAX_GAP_DATES = 5
MAX_EXPORTED_LINKS = 30000
MAX_MANIFEST_JSON_BYTES = 16 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
IDENTITY = ("symbol", "instrument_type", "market", "currency", "settlement")
PRICE_BASES = ("price_basis", "adjustment_basis")
CASH_CURRENCIES = frozenset({"ARS", "USD", "USD_MEP", "USD_CCL"})

# A complete external inventory is not established merely by naming all these
# scopes.  Their proof belongs to an independently captured offhost manifest.
REQUIRED_PIN_SCOPES = frozenset({
    "history_canonical", "history_quarantine", "history_checks",
    "history_batches", "history_conflicts", "close_only_legacy",
    "market_snapshots_and_candles", "raw_objects_and_manifests",
    "exports_and_reports", "backups_and_restores", "cross_database_ledger",
    "active_paper_positions",
})


def _hash(value) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_JSON_BYTES:
        raise ValueError("RETENTION_MANIFEST_JSON_BUDGET_EXCEEDED")
    return hashlib.sha256(encoded).hexdigest()


def _sha(value, *, code: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise ValueError(code)
    return value


def _at(value, *, code: str) -> datetime:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(code) from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(code)
    return result.astimezone(timezone.utc)


def _day(value, *, code: str) -> date:
    if type(value) is not str:
        raise ValueError(code)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(code) from None
    if parsed.isoformat() != value:
        raise ValueError(code)
    return parsed


def _identity(record: Mapping) -> tuple[str, ...]:
    if not isinstance(record, Mapping):
        raise ValueError("SOURCE_IDENTITY_EVIDENCE_INVALID")
    fields = tuple(str(record.get(key) or "").strip().upper() for key in IDENTITY)
    if (any(not field or field in {"UNKNOWN", "NULL", "NONE", "NO_VERIFICADO", "*", "?"}
            for field in fields)
            or fields[3] not in CASH_CURRENCIES):
        raise ValueError("SOURCE_IDENTITY_EXACT_FIVE_KEY_REQUIRED")
    family = FAMILIES.get(fields[1], fields[1])
    if family not in OPERATIONAL_HISTORY_FAMILIES:
        raise ValueError("SOURCE_FAMILY_HISTORY_SCOPE_UNVERIFIED")
    if fields[2] not in set(MARKETS.values()) | {"FCI"}:
        raise ValueError("SOURCE_MARKET_CONTRACT_UNVERIFIED")
    if fields[4] not in set(TERMS.values()):
        raise ValueError("SOURCE_SETTLEMENT_CONTRACT_UNVERIFIED")
    # Market, currency and settlement are *not* inferred from the symbol.
    return (fields[0], family, fields[2], fields[3], fields[4])


def _version_evidence(row: Mapping, at: datetime) -> tuple[date | None, tuple[str, ...] | None, tuple[str, ...]]:
    reasons: list[str] = []
    try:
        identity = _identity(row)
    except ValueError as exc:
        identity = None
        reasons.append(str(exc))
    try:
        day = _day(row.get("date"), code="HISTORY_ECONOMIC_DATE_UNVERIFIED")
        if day > at.astimezone(ART).date():
            raise ValueError("HISTORY_ECONOMIC_DATE_IN_FUTURE")
    except ValueError as exc:
        day = None
        reasons.append(str(exc))
    if any(not isinstance(row.get(key), str) or not row[key].strip()
           for key in PRICE_BASES):
        reasons.append("HISTORY_PRICE_BASIS_UNVERIFIED")
    try:
        _sha(row.get("payload_hash"), code="HISTORY_PAYLOAD_SHA_UNVERIFIED")
    except ValueError as exc:
        reasons.append(str(exc))
    if (type(row.get("previous_version_id")) is not int
            or row["previous_version_id"] < 0):
        reasons.append("HISTORY_REVISION_LINEAGE_UNVERIFIED")
    source = str(row.get("source") or "").strip().upper()
    if not source or source_rank(source) == 100:
        reasons.append("HISTORY_SOURCE_PROVENANCE_UNVERIFIED")
    if source and source_rank(source) != 10:
        # IOL/A3/BYMA/Data912 are historical references or a separately
        # vetted fallback, never an automatic PPI live authority.
        reasons.append("HISTORY_NONPRIMARY_SOURCE_REQUIRES_REVIEW")
    try:
        known = _at(row.get("version_known_at"), code="HISTORY_VERSION_PIT_UNVERIFIED")
        event = _at(row.get("event_at"), code="HISTORY_EVENT_PIT_UNVERIFIED")
        observed = _at(row.get("observed_at"), code="HISTORY_OBSERVED_PIT_UNVERIFIED")
        provider = (_at(row["provider_at"], code="HISTORY_PROVIDER_PIT_UNVERIFIED")
                    if row.get("provider_at") not in (None, "") else None)
        if (not event <= known <= at or not event <= observed <= known
                or provider is not None and not event <= provider <= observed):
            raise ValueError("HISTORY_PIT_CLOCK_ORDER_INVALID")
        if provider is None:
            reasons.append("HISTORY_PROVIDER_NATIVE_CLOCK_UNVERIFIED")
    except ValueError as exc:
        reasons.append(str(exc))
    try:
        _sha(row.get("raw_sha256"), code="HISTORY_RAW_LINEAGE_UNVERIFIED")
    except ValueError as exc:
        reasons.append(str(exc))
    return day, identity, tuple(sorted(set(reasons)))


def retention_dryrun(manifest: Mapping, *, as_of, checkpoint: Mapping | None = None) -> dict:
    """Classify bounded version rows and explicit pins; authorize *zero* deletes.

    ``manifest`` comes from an independently verified offhost capture and is
    not reinterpreted as that proof.  No scope being declared COMPLETE turns
    this output into an apply-plan or a backup/restore certification.
    """
    at = _at(as_of, code="RETENTION_CLOCK_UNVERIFIED")
    if not isinstance(manifest, Mapping):
        raise ValueError("RETENTION_MANIFEST_REQUIRED")
    versions = manifest.get("versions")
    refs = manifest.get("references")
    exports = manifest.get("exports")
    if (not isinstance(versions, list) or not isinstance(refs, list)
            or not isinstance(exports, list)
            or len(versions) > MAX_VERSIONS or len(refs) > MAX_REFERENCES
            or len(exports) > MAX_EXPORTS):
        raise ValueError("RETENTION_INVENTORY_BUDGET_OR_SHAPE_INVALID")
    snapshot = _sha(manifest.get("snapshot_sha256"), code="RETENTION_CAPTURE_SHA_UNVERIFIED")
    captured = _at(manifest.get("snapshot_known_at"), code="RETENTION_CAPTURE_TIME_UNVERIFIED")
    if captured > at:
        raise ValueError("RETENTION_CAPTURE_IN_FUTURE")
    coverage = manifest.get("pin_scopes_verified")
    if not isinstance(coverage, list) or any(not isinstance(x, str) for x in coverage):
        raise ValueError("RETENTION_PIN_SCOPE_MANIFEST_INVALID")
    coverage_set = set(coverage)
    if coverage_set - REQUIRED_PIN_SCOPES:
        raise ValueError("RETENTION_PIN_SCOPE_UNKNOWN")
    complete = manifest.get("inventory_complete") is True
    missing_scopes = sorted(REQUIRED_PIN_SCOPES - coverage_set)
    version_by_id = {}
    for row in versions:
        if (not isinstance(row, Mapping) or type(row.get("id")) is not int
                or row["id"] <= 0 or row["id"] in version_by_id):
            raise ValueError("RETENTION_VERSION_ID_INVALID_OR_DUPLICATE")
        version_by_id[row["id"]] = row

    pinned = defaultdict(set)
    missing_reference_ids = 0
    for ref in refs:
        if (not isinstance(ref, Mapping) or ref.get("scope") not in REQUIRED_PIN_SCOPES
                or type(ref.get("version_id")) is not int
                or not isinstance(ref.get("reference"), str) or not ref["reference"].strip()):
            raise ValueError("RETENTION_PIN_REFERENCE_INVALID")
        version_id = ref["version_id"]
        if version_id not in version_by_id:
            missing_reference_ids += 1
        pinned[version_id].add(ref["scope"])

    exported_objects = set()
    exported_links = 0
    seen_exports = set()
    for export in exports:
        if (not isinstance(export, Mapping) or not isinstance(export.get("version_ids"), list)
                or not isinstance(export.get("object_sha256s"), list)
                or type(export.get("export_id")) is not str or not export["export_id"]):
            raise ValueError("RETENTION_EXPORT_LINEAGE_INVALID")
        if export["export_id"] in seen_exports:
            raise ValueError("RETENTION_EXPORT_DUPLICATE_ID")
        seen_exports.add(export["export_id"])
        if len(export["version_ids"]) != len(set(map(str, export["version_ids"]))):
            raise ValueError("RETENTION_EXPORT_DUPLICATE_VERSION")
        _sha(export.get("manifest_sha256"), code="RETENTION_EXPORT_DIGEST_UNVERIFIED")
        if _at(export.get("known_at"), code="RETENTION_EXPORT_CLOCK_UNVERIFIED") > at:
            raise ValueError("RETENTION_EXPORT_IN_FUTURE")
        exported_links += len(export["version_ids"]) + len(export["object_sha256s"])
        if exported_links > MAX_EXPORTED_LINKS:
            raise ValueError("RETENTION_EXPORT_LINK_BUDGET_EXCEEDED")
        for version_id in export["version_ids"]:
            if type(version_id) is not int or version_id <= 0:
                raise ValueError("RETENTION_EXPORT_VERSION_INVALID")
            if version_id not in version_by_id:
                missing_reference_ids += 1
            pinned[version_id].add("exports_and_reports")
        for obj in export["object_sha256s"]:
            exported_objects.add(_sha(obj, code="RETENTION_EXPORT_OBJECT_UNVERIFIED"))

    cutoff = daily_start(at)
    today = at.astimezone(ART).date()
    retained_object_users = defaultdict(set)
    evaluated = {}
    for version_id, row in sorted(version_by_id.items()):
        day, identity, reasons = _version_evidence(row, at)
        extended = set(reasons)
        try:
            if _at(row.get("version_known_at"),
                   code="HISTORY_VERSION_PIT_UNVERIFIED") > captured:
                extended.add("HISTORY_VERSION_KNOWN_AFTER_CAPTURE")
        except ValueError:
            pass  # Already reported by _version_evidence.
        previous = row.get("previous_version_id")
        if type(previous) is int and previous:
            if previous >= version_id:
                extended.add("HISTORY_REVISION_FORWARD_OR_CYCLIC_POINTER")
            prior = version_by_id.get(previous)
            if prior is None:
                extended.add("HISTORY_REVISION_PREDECESSOR_NOT_IN_INVENTORY")
            elif any(str(prior.get(key) or "").strip().upper() !=
                     str(row.get(key) or "").strip().upper()
                     for key in (*IDENTITY, *PRICE_BASES, "source")):
                extended.add("HISTORY_REVISION_IDENTITY_OR_BASIS_DRIFT")
        evaluated[version_id] = (day, identity, tuple(sorted(extended)))
        if day is None or day >= cutoff or pinned[version_id]:
            raw = row.get("raw_sha256")
            if isinstance(raw, str) and HEX64.fullmatch(raw):
                retained_object_users[raw].add(version_id)

    counts = Counter()
    reasons_count = Counter()
    samples = []
    for version_id, row in sorted(version_by_id.items()):
        day, identity, reasons = evaluated[version_id]
        linked = sorted(pinned[version_id])
        blocking = set(reasons)
        if day is None:
            blocking.add("RETENTION_DATE_UNCLASSIFIABLE")
        elif day >= cutoff:
            blocking.add("RETENTION_IN_WINDOW")
        if linked:
            blocking.add("RETENTION_PINNED_BY_CONSUMER")
        if (day is not None and day < cutoff
                and retained_object_users.get(row.get("raw_sha256"), set()) - {version_id}):
            blocking.add("RETENTION_RAW_OBJECT_SHARED_WITH_RETAINED")
        if row.get("raw_sha256") in exported_objects:
            blocking.add("RETENTION_RAW_OBJECT_PINNED_BY_EXPORT")
        if not complete or missing_scopes or missing_reference_ids:
            blocking.add("RETENTION_INVENTORY_OR_PIN_COVERAGE_UNVERIFIED")
        state = ("RETAIN_IN_WINDOW" if day is not None and day >= cutoff
                 else "HOLD_UNVERIFIED_OR_PINNED" if blocking
                 else "UNPINNED_REVIEW_CANDIDATE_ONLY")
        counts[state] += 1
        reasons_count.update(blocking)
        if len(samples) < 32:
            # This identifier is a digest, not ticker/account/raw evidence.
            samples.append({"version_ref": _hash({"id": version_id, "snapshot": snapshot})[:20],
                            "state": state, "reasons": sorted(blocking),
                            "pinned_scopes": linked})

    material_digest = _hash(manifest)
    plan_id = _hash({"schema": SCHEMA, "snapshot": snapshot,
                     "manifest_digest": material_digest, "cutoff": cutoff.isoformat()})
    if checkpoint is not None and (not isinstance(checkpoint, Mapping)
                                   or checkpoint.get("plan_id") != plan_id
                                   or checkpoint.get("manifest_digest") != material_digest):
        raise ValueError("RETENTION_CHECKPOINT_CONFLICT_REPLAN_REQUIRED")
    return {"schema": SCHEMA, "plan_id": plan_id, "manifest_digest": material_digest,
            "snapshot_sha256_declared": snapshot,
            "snapshot_identity_independently_verified": False,
            "cutoff_inclusive_art": cutoff.isoformat(), "today_art": today.isoformat(),
            "versions_classified": len(version_by_id),
            "states": dict(sorted(counts.items())),
            "reasons": dict(sorted(reasons_count.items())),
            "pinned_versions": sum(bool(pinned[version_id]) for version_id in version_by_id),
            "missing_reference_ids": missing_reference_ids,
            "exports_audited": len(exports), "exported_links": exported_links,
            "declared_pin_scopes": sorted(coverage_set),
            "missing_pin_scopes": missing_scopes,
            "inventory_claimed_complete": complete,
            "sampled_version_refs": samples,
            "same_plan_checkpoint_seen": checkpoint is not None,
            "source_mutated": False, "provider_calls": 0,
            "host_purge_allowed": False, "host_migration_allowed": False,
            "paper_entry_authority": False, "real_orders_sent": 0,
            "next_gate": "INDEPENDENT_OFFHOST_COPY_AND_CROSS_STORE_PIN_RECONCILIATION"}


def incremental_gap_dryrun(gap: Mapping, *, as_of,
                           checkpoint: Mapping | None = None) -> dict:
    """Propose at most five *dates* for a proven PPI gap, never issue a call.

    A consumer must separately maintain an exclusive lock, an append-only
    checkpoint and idempotent canonical sink. The returned receipt is not
    ingestion permission, and no calendar/source availability is inferred.
    """
    at = _at(as_of, code="GAP_CLOCK_UNVERIFIED")
    if not isinstance(gap, Mapping):
        raise ValueError("GAP_EVIDENCE_REQUIRED")
    key = _identity(gap)
    source = str(gap.get("source") or "").strip().upper()
    if source_rank(source) != 10:
        raise ValueError("GAP_PPI_PRIMARY_SOURCE_EVIDENCE_REQUIRED")
    _sha(gap.get("evidence_sha256"), code="GAP_EVIDENCE_DIGEST_REQUIRED")
    known = _at(gap.get("evidence_known_at"), code="GAP_EVIDENCE_PIT_UNVERIFIED")
    if known > at:
        raise ValueError("GAP_EVIDENCE_FUTURE_AT_AS_OF")
    start = _day(gap.get("gap_start"), code="GAP_START_UNVERIFIED")
    end = _day(gap.get("gap_end"), code="GAP_END_UNVERIFIED")
    if end < start:
        raise ValueError("GAP_DATE_ORDER_INVALID")
    today = at.astimezone(ART).date()
    cutoff = daily_start(at)
    if start < cutoff or end > today:
        raise ValueError("GAP_OUTSIDE_RETENTION_OR_FUTURE")
    operation_id = _hash({"schema": GAP_SCHEMA, "identity": key,
                          "source": source, "evidence": gap["evidence_sha256"],
                          "known_at": known.isoformat(), "start": start.isoformat(),
                          "end": end.isoformat()})
    if checkpoint is not None:
        if (not isinstance(checkpoint, Mapping)
                or checkpoint.get("operation_id") != operation_id):
            raise ValueError("GAP_CHECKPOINT_IDENTITY_OR_PROVENANCE_CONFLICT")
        # A plan/proposal is NOT a commit.  A durable sink receipt, whose
        # authenticity is checked by the external consumer, is indispensable.
        if checkpoint.get("state") != "COMMITTED":
            raise ValueError("GAP_CHECKPOINT_NOT_COMMITTED")
        _sha(checkpoint.get("sink_receipt_sha256"),
             code="GAP_CHECKPOINT_SINK_RECEIPT_REQUIRED")
        committed_at = _at(checkpoint.get("committed_at"),
                           code="GAP_CHECKPOINT_COMMIT_CLOCK_UNVERIFIED")
        if not known <= committed_at <= at:
            raise ValueError("GAP_CHECKPOINT_COMMIT_PIT_INVALID")
        completed = _day(checkpoint.get("completed_through"),
                         code="GAP_CHECKPOINT_DATE_UNVERIFIED")
        if completed < start or completed > end:
            raise ValueError("GAP_CHECKPOINT_OUT_OF_RANGE")
        ordinal = (completed-start).days // MAX_GAP_DATES
        previous_start = start + timedelta(days=ordinal * MAX_GAP_DATES)
        expected_end = min(previous_start + timedelta(days=MAX_GAP_DATES - 1), end)
        expected_window_id = _hash({"operation_id": operation_id,
                                    "start": previous_start.isoformat(),
                                    "end": expected_end.isoformat()})
        if completed != expected_end or checkpoint.get("window_id") != expected_window_id:
            raise ValueError("GAP_CHECKPOINT_WINDOW_CONFLICT")
        next_start = max(start, completed + timedelta(days=1))
    else:
        next_start = start
    remaining = next_start <= end
    window_end = min(next_start + timedelta(days=MAX_GAP_DATES - 1), end) if remaining else None
    window_id = (_hash({"operation_id": operation_id,
                        "start": next_start.isoformat(), "end": window_end.isoformat()})
                 if window_end else None)
    return {"schema": GAP_SCHEMA, "operation_id": operation_id,
            "identity_sha256": _hash(key), "source": source,
            "source_authority": "PPI_PRIMARY_HISTORY_ONLY",
            "evidence_sha256": gap["evidence_sha256"],
            "window_start_art": next_start.isoformat() if remaining else None,
            "window_end_art": window_end.isoformat() if window_end else None,
            "window_id": window_id,
            "window_date_count": ((window_end-next_start).days+1) if window_end else 0,
            "status": "PROPOSED_OFFLINE_ONLY" if remaining else "ALREADY_COVERED_CHECKPOINT",
            "next_checkpoint_template": {"operation_id": operation_id,
                                         "window_id": window_id,
                                         "completed_through": window_end.isoformat(),
                                         "state": "UNCOMMITTED_PROPOSAL"}
                                         if window_end else None,
            "source_mutated": False, "provider_calls": 0,
            "requires_exclusive_lock": True, "requires_durable_checkpoint": True,
            "sink_idempotence_not_verified": True,
            "sink_receipt_independently_verified": False,
            "execution_authority": False, "paper_entry_authority": False,
            "real_orders_sent": 0}
