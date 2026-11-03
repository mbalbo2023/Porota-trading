"""A242 bounded, resumable, OFFHOST-ONLY review of >10k history versions.

Input batches are individually byte-hash verified by the caller.  This module
deliberately never produces a deletion set: a partition cannot prove that an
object is unreferenced by other partitions, stores, exports or PAPER ledgers.
Even an inventory marked complete is merely a *declaration*, not independent
evidence that all cross-store pins and foreign keys were inspected.

The checkpoint is a deterministic replay cursor, not a purge authorization or
proof of a committed database transaction.  Resumption replays and validates
the full preceding prefix before processing new partitions.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import re
from typing import Callable, Mapping

from rc6_data_retention import ART, daily_start
from rc6_source_retention_dryrun import (
    MAX_VERSIONS, _at, _hash, _sha, retention_dryrun,
)


SCHEMA = "RC6_RETENTION_BATCH_DRYRUN_A242_V1"
CHECKPOINT_SCHEMA = "RC6_RETENTION_BATCH_CHECKPOINT_A242_V1"
MAX_BATCHES = 256
MAX_TOTAL_VERSIONS = 1_000_000
MAX_BATCHES_PER_REVIEW = 32
NAME = re.compile(r"batch-[0-9]{6}\.json\Z")


def _positive_int(value, code: str, *, zero_allowed: bool = False) -> int:
    if type(value) is not int or value < (0 if zero_allowed else 1):
        raise ValueError(code)
    return value


def _index(index: Mapping, at: datetime) -> tuple[list, str, int]:
    if not isinstance(index, Mapping) or index.get("schema") != SCHEMA:
        raise ValueError("RETENTION_BATCH_INDEX_SCHEMA_INVALID")
    _sha(index.get("snapshot_sha256"), code="RETENTION_BATCH_CAPTURE_SHA_INVALID")
    if _at(index.get("snapshot_known_at"), code="RETENTION_BATCH_CAPTURE_CLOCK_INVALID") > at:
        raise ValueError("RETENTION_BATCH_CAPTURE_IN_FUTURE")
    batches = index.get("batches")
    total = _positive_int(index.get("total_versions"),
                          "RETENTION_BATCH_TOTAL_INVALID")
    if (total > MAX_TOTAL_VERSIONS or not isinstance(batches, list)
            or not 1 <= len(batches) <= MAX_BATCHES):
        raise ValueError("RETENTION_BATCH_INDEX_BUDGET_INVALID")
    previous_last = 0
    counts = 0
    for ordinal, desc in enumerate(batches):
        if not isinstance(desc, Mapping):
            raise ValueError("RETENTION_BATCH_DESCRIPTOR_INVALID")
        name = desc.get("name")
        if name != f"batch-{ordinal:06d}.json" or NAME.fullmatch(name) is None:
            raise ValueError("RETENTION_BATCH_FILE_NAME_OR_ORDER_INVALID")
        _sha(desc.get("sha256"), code="RETENTION_BATCH_DIGEST_INVALID")
        count = _positive_int(desc.get("versions_count"),
                              "RETENTION_BATCH_SIZE_INVALID")
        first = _positive_int(desc.get("first_version_id"),
                              "RETENTION_BATCH_RANGE_INVALID")
        last = _positive_int(desc.get("last_version_id"),
                             "RETENTION_BATCH_RANGE_INVALID")
        if count > MAX_VERSIONS or first <= previous_last or last < first:
            raise ValueError("RETENTION_BATCH_RANGE_OVERLAP_OR_BUDGET")
        if last - first + 1 < count:
            raise ValueError("RETENTION_BATCH_IMPOSSIBLE_CARDINALITY")
        previous_last = last
        counts += count
    if counts != total:
        raise ValueError("RETENTION_BATCH_TOTAL_COUNT_MISMATCH")
    return batches, _hash(index), total


def review_batches(index: Mapping, *, as_of, read_batch: Callable,
                   checkpoint: Mapping | None = None,
                   batch_limit: int = 8) -> dict:
    """Review at most ``batch_limit`` NEW partitions after revalidating prefix.

    ``read_batch(name, sha256)`` MUST return a dict after checking a bounded
    regular file's exact bytes, inode and SHA-256.  This pure function performs
    no filesystem, database, host or provider operation of its own.
    """
    at = _at(as_of, code="RETENTION_BATCH_CLOCK_INVALID")
    if not callable(read_batch):
        raise ValueError("RETENTION_BATCH_READER_REQUIRED")
    limit = _positive_int(batch_limit, "RETENTION_BATCH_LIMIT_INVALID")
    if limit > MAX_BATCHES_PER_REVIEW:
        raise ValueError("RETENTION_BATCH_LIMIT_EXCEEDED")
    batches, index_digest, total = _index(index, at)
    operation_id = _hash({"schema": SCHEMA, "index_digest": index_digest,
                          "as_of_utc": at.isoformat(),
                          "cutoff_art": daily_start(at).isoformat()})
    done = 0
    if checkpoint is not None:
        if (not isinstance(checkpoint, Mapping)
                or checkpoint.get("schema") != CHECKPOINT_SCHEMA
                or checkpoint.get("operation_id") != operation_id
                or checkpoint.get("index_digest") != index_digest
                or checkpoint.get("state") != "REVIEW_ONLY"):
            raise ValueError("RETENTION_BATCH_CHECKPOINT_CONFLICT")
        done = _positive_int(checkpoint.get("completed_batches"),
                             "RETENTION_BATCH_CHECKPOINT_CURSOR_INVALID",
                             zero_allowed=True)
        if done > len(batches):
            raise ValueError("RETENTION_BATCH_CHECKPOINT_CURSOR_INVALID")

    states = Counter()
    reasons = Counter()
    versions = 0
    pinned = 0
    unresolved_refs = 0
    prefix_digest = _hash({"operation_id": operation_id, "start": True})
    if checkpoint is not None and done == 0:
        if any(checkpoint.get(key) != value for key, value in {
                "prefix_digest": prefix_digest, "last_version_id": 0,
                "versions_classified": 0, "states": {}, "reasons": {},
                "pinned_versions": 0, "unresolved_reference_ids": 0}.items()):
            raise ValueError("RETENTION_BATCH_CHECKPOINT_REPLAY_CONFLICT")
    previous_last = 0
    end = min(done + limit, len(batches))
    for ordinal in range(end):
        desc = batches[ordinal]
        batch = read_batch(desc["name"], desc["sha256"])
        if (not isinstance(batch, Mapping)
                or batch.get("snapshot_sha256") != index["snapshot_sha256"]
                or batch.get("snapshot_known_at") != index["snapshot_known_at"]):
            raise ValueError("RETENTION_BATCH_CAPTURE_MISMATCH")
        rows = batch.get("versions")
        if not isinstance(rows, list) or len(rows) != desc["versions_count"]:
            raise ValueError("RETENTION_BATCH_CARDINALITY_MISMATCH")
        ids = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("RETENTION_BATCH_VERSION_INVALID")
            ids.append(_positive_int(row.get("id"),
                                     "RETENTION_BATCH_VERSION_ID_INVALID"))
        if (ids != sorted(set(ids)) or ids[0] <= previous_last
                or ids[0] != desc["first_version_id"]
                or ids[-1] != desc["last_version_id"]):
            raise ValueError("RETENTION_BATCH_VERSION_ORDER_OR_RANGE_INVALID")
        previous_last = ids[-1]
        # A local partition has no global pin / shared-object / FK authority.
        # Override only the *claim of completeness*, never row evidence.
        local = {**batch, "inventory_complete": False,
                 "pin_scopes_verified": []}
        receipt = retention_dryrun(local, as_of=at)
        if receipt["states"].get("UNPINNED_REVIEW_CANDIDATE_ONLY", 0):
            raise ValueError("RETENTION_BATCH_UNSAFE_LOCAL_CANDIDATE")
        states.update(receipt["states"])
        reasons.update(receipt["reasons"])
        versions += receipt["versions_classified"]
        pinned += receipt["pinned_versions"]
        unresolved_refs += receipt["missing_reference_ids"]
        prefix_digest = _hash({"previous": prefix_digest,
                               "ordinal": ordinal, "descriptor": desc,
                               "plan_id": receipt["plan_id"]})
        if ordinal + 1 == done and checkpoint is not None:
            expected = {"prefix_digest": prefix_digest,
                        "last_version_id": previous_last,
                        "versions_classified": versions,
                        "states": dict(sorted(states.items())),
                        "reasons": dict(sorted(reasons.items())),
                        "pinned_versions": pinned,
                        "unresolved_reference_ids": unresolved_refs}
            if any(checkpoint.get(key) != value for key, value in expected.items()):
                raise ValueError("RETENTION_BATCH_CHECKPOINT_REPLAY_CONFLICT")
    finished = end == len(batches)
    checkpoint_result = {
        "schema": CHECKPOINT_SCHEMA, "operation_id": operation_id,
        "index_digest": index_digest, "state": "REVIEW_ONLY",
        "completed_batches": end, "prefix_digest": prefix_digest,
        "last_version_id": previous_last,
        "versions_classified": versions,
        "states": dict(sorted(states.items())),
        "reasons": dict(sorted(reasons.items())),
        "pinned_versions": pinned,
        "unresolved_reference_ids": unresolved_refs,
    }
    return {
        "schema": SCHEMA, "operation_id": operation_id,
        "index_digest": index_digest,
        "cutoff_inclusive_art": daily_start(at).isoformat(),
        "today_art": at.astimezone(ART).date().isoformat(),
        "batches_total": len(batches), "batches_checked": end,
        "new_batches_checked": end - done,
        "versions_declared": total, "versions_checked": versions,
        "states": checkpoint_result["states"],
        "reasons": checkpoint_result["reasons"],
        "pinned_versions": pinned,
        "unresolved_reference_ids": unresolved_refs,
        "status": "OFFHOST_REVIEW_COMPLETE_HOLD" if finished else "OFFHOST_REVIEW_CONTINUE",
        "next_checkpoint_template": checkpoint_result,
        "snapshot_identity_independently_verified": False,
        "cross_store_pin_and_fk_verification": "NO_VERIFICADO",
        "cross_batch_raw_sharing_verification": "NO_VERIFICADO",
        "inventory_complete_proven": False,
        "physically_reclaimable_bytes": None,
        "host_purge_allowed": False, "host_migration_allowed": False,
        "source_mutated": False, "provider_calls": 0,
        "paper_entry_authority": False, "real_orders_sent": 0,
    }
