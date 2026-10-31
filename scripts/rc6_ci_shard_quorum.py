"""A222 offline prototype for lossless pytest sharding, NOT enabled in CI.

The current single-job 6,060-test governed suite remains authoritative.  This
module demonstrates deterministic partitioning and an exact, fail-closed quorum
contract before any future Actions job/matrix rewiring.  A shard report must
come from a trusted test runner; this code does not attest its origin or execute
tests.  In particular, do not substitute a green plan for CI evidence.
"""

from __future__ import annotations

import hashlib
import json
import re


class ShardQuorumRed(ValueError):
    """Incomplete, contradictory or unsafe test evidence."""


def _red() -> None:
    raise ShardQuorumRed("RC6_SHARD_QUORUM_RED")


def _sha(value: object, length: int = 40) -> str:
    if not isinstance(value, str) or re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is None:
        _red()
    return value


def _nodeids(items: object) -> list[str]:
    if not isinstance(items, list) or not items or len(items) > 100_000:
        _red()
    for name in items:
        if (not isinstance(name, str) or not 1 <= len(name) <= 2048 or
                any(ord(char) < 32 or ord(char) > 126 for char in name)):
            _red()
    if len(set(items)) != len(items):
        _red()
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
