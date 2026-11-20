"""A222 lossless pytest sharding, used by a non-authoritative Predeploy pilot.

The current single-job 6,060-test governed suite remains authoritative.  This
module demonstrates deterministic partitioning and an exact, fail-closed quorum
contract without an Actions job/matrix rewiring. A shard report must come from
a trusted test runner; this code does not attest its origin or execute tests.
In particular, do not substitute pilot GREEN for merge or runtime authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata


# Pytest parameter IDs are source identities, not a provider-size limit.
# Each ID must still fit one Linux argv entry (<128 KiB), and the aggregate
# private collection is bounded to avoid cgroup OOM from adversarial IDs.
MAX_NODEID_CHARS = 65535
MAX_NODEID_UTF8_BYTES = 65535
MAX_COLLECTION_NODEID_BYTES = 64 * 1024 * 1024
_BIDI_CONTROLS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF",
                            "LRI", "RLI", "FSI", "PDI"})


class ShardQuorumRed(ValueError):
    """Incomplete, contradictory or unsafe test evidence."""


_NODEID_REASON_CODES = frozenset({
    "NODEID_COLLECTION_INVALID", "NODEID_EMPTY_OR_UNBOUNDED",
    "NODEID_NOT_STRING", "NODEID_EMPTY", "NODEID_CHAR_LENGTH",
    "NODEID_UTF8_ENCODING", "NODEID_UTF8_BYTES",
    "NODEID_NONPRINTABLE", "NODEID_CONTROL_OR_BIDI",
    "NODEID_DUPLICATE", "NODEID_TOTAL_BYTES_UNBOUNDED",
})


def _red(reason=None) -> None:
    # Reasons are controlled literals only; never disclose parameter values.
    code = ":" + reason if reason in _NODEID_REASON_CODES else ""
    raise ShardQuorumRed("RC6_SHARD_QUORUM_RED" + code)


def _sha(value: object, length: int = 40) -> str:
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        _red()
    return value


def _nodeids(items: object) -> list[str]:
    if not isinstance(items, list):
        _red("NODEID_COLLECTION_INVALID")
    if not items or len(items) > 100_000:
        _red("NODEID_EMPTY_OR_UNBOUNDED")
    total_bytes = 0
    for name in items:
        if not isinstance(name, str):
            _red("NODEID_NOT_STRING")
        if not name:
            _red("NODEID_EMPTY")
        if len(name) > MAX_NODEID_CHARS:
            _red("NODEID_CHAR_LENGTH")
        # Real pytest param IDs may contain printable UTF-8 (e.g. "á").
        # Keep exact Unicode codepoints for digest and quorum identity: no
        # normalization, lossy ASCII folding, or hidden control characters.
        try:
            encoded = name.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _red("NODEID_UTF8_ENCODING")  # Unpaired surrogate.
        if len(encoded) > MAX_NODEID_UTF8_BYTES:
            _red("NODEID_UTF8_BYTES")
        total_bytes += len(encoded)
        if total_bytes > MAX_COLLECTION_NODEID_BYTES:
            _red("NODEID_TOTAL_BYTES_UNBOUNDED")
        if not name.isprintable():
            _red("NODEID_NONPRINTABLE")
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} or
               unicodedata.bidirectional(char) in _BIDI_CONTROLS
               for char in name):
            _red("NODEID_CONTROL_OR_BIDI")
    if len(set(items)) != len(items):
        _red("NODEID_DUPLICATE")
    return sorted(items)


def _digest(source_sha: str, source_tree_sha: str, items: list[str]) -> str:
    # Node IDs are never printed by this module.  A frozen collection digest
    # binds the productive test set to the exact candidate source.
    raw = json.dumps({"v": 1, "source_sha": source_sha,
                      "source_tree_sha": source_tree_sha, "nodeids": items},
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def manifest(source_sha: str, source_tree_sha: str,
             collected_nodeids: list[str]) -> dict:
    sha = _sha(source_sha)
    tree = _sha(source_tree_sha)
    items = _nodeids(collected_nodeids)
    return {"schema_version": 1, "source_sha": sha, "source_tree_sha": tree,
            "nodeids": items,
            "collection_sha256": _digest(sha, tree, items), "discovered": len(items)}


def _validate_manifest(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {
            "schema_version", "source_sha", "source_tree_sha", "nodeids",
            "collection_sha256", "discovered"}:
        _red()
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        _red()
    sha = _sha(data["source_sha"])
    tree = _sha(data["source_tree_sha"])
    items = _nodeids(data["nodeids"])
    if (data["nodeids"] != items or type(data["discovered"]) is not int or
            data["discovered"] != len(items) or
            data["collection_sha256"] != _digest(sha, tree, items)):
        _red()
    return data


def partition(data: dict, count: int) -> list[list[str]]:
    m = _validate_manifest(data)
    if type(count) is not int or not 1 <= count <= min(16, m["discovered"]):
        _red()
    # Stable hashing spreads expensive files while round-robin guarantees no
    # empty shard.  Nodeid test costs are unknown until measured in Actions.
    ordered = sorted(m["nodeids"], key=lambda item:
                     (hashlib.sha256(item.encode("utf-8")).digest(), item))
    groups = [[] for _ in range(count)]
    for index, item in enumerate(ordered):
        groups[index % count].append(item)
    return [sorted(part) for part in groups]


def quorum(data: dict, count: int, reports: object) -> dict:
    """Exact set parity plus JUnit/execution metrics; never an automatic GO."""
    m = _validate_manifest(data)
    expected = partition(m, count)
    if not isinstance(reports, list) or len(reports) != count:
        _red()
    required = {"shard_index", "source_sha", "collection_sha256",
                "source_tree_sha",
                "executed_nodeids", "pytest_exit_code", "junit_testcases",
                "failures", "errors", "skipped", "xfail", "junit_sha256"}
    by_index = {}
    for receipt in reports:
        if not isinstance(receipt, dict) or set(receipt) != required:
            _red()
        index = receipt["shard_index"]
        if type(index) is not int or not 0 <= index < count or index in by_index:
            _red()
        if (receipt["source_sha"] != m["source_sha"] or
                receipt["source_tree_sha"] != m["source_tree_sha"] or
                receipt["collection_sha256"] != m["collection_sha256"]):
            _red()
        executed = _nodeids(receipt["executed_nodeids"])
        if executed != expected[index]:
            _red()  # Missing, duplicated, added or misassigned nodeid.
        _sha(receipt["junit_sha256"], 64)
        if (any(type(receipt[field]) is not int or receipt[field] != 0 for field in
                ("pytest_exit_code", "failures", "errors", "skipped", "xfail")) or
                type(receipt["junit_testcases"]) is not int or
                receipt["junit_testcases"] != len(executed)):
            _red()
        by_index[index] = receipt
    if sum(len(part) for part in expected) != m["discovered"]:
        _red()
    return {"schema_version": 1, "status": "GREEN_QUORUM_OFFLINE_ONLY",
            "source_sha": m["source_sha"], "source_tree_sha": m["source_tree_sha"],
            "collection_sha256": m["collection_sha256"],
            "shards": count, "discovered": m["discovered"],
            "executed": sum(r["junit_testcases"] for r in by_index.values()),
            "failures": 0, "errors": 0, "skipped": 0, "xfail": 0,
            "automatic_go": False, "runtime_authority": False}
