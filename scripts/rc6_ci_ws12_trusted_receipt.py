"""Protected-main WS12 receipt review; never an OCI or release certificate.

The caller must be a separately trusted runner which has physically observed
the OCI test execution and acquired private JUnit/collection data.  This
module validates their *content* and emits only fixed names and digests.
Candidate-created JSON alone is not independent physical custody; accordingly
even a complete review retains qa_certified=false and automatic_go=false.

No dependency on, import from, or execution of candidate-controlled scripts.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata


class EvidenceRejected(ValueError):
    """An exact set, source binding or execution result was unproven."""


AF_UNIX_NODEID = (
    "tests/test_rc6_a678_ppi_shared_feed.py::PPISharedFeedTests::"
    "test_unix_socket_exposes_data_only_pull_not_provider_or_order_command"
)
_SHA40 = re.compile(r"[0-9a-f]{40}\Z")
_SHA64 = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NUMBER = re.compile(r"[1-9][0-9]{0,19}\Z")
_MANIFEST_KEYS = frozenset((
    "schema_version", "source_sha", "source_tree_sha", "nodeids",
    "discovered", "collection_sha256",
))
_SOURCE_KEYS = frozenset((
    "full_manifest", "subset_manifest", "cases", "pytest_exit_code",
    "junit_sha256",
))


def _reject() -> None:
    raise EvidenceRejected("RC6_QA_EVIDENCE_REJECTED")


def _hex(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _reject()
    return value


def _manifest(raw: object, candidate_sha: str, tree: str,
              expected_count: int) -> tuple[list[str], str]:
    if not isinstance(raw, dict) or set(raw) != _MANIFEST_KEYS:
        _reject()
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        _reject()
    if raw["source_sha"] != candidate_sha or raw["source_tree_sha"] != tree:
        _reject()
    nodes = raw["nodeids"]
    # Check length before scanning/serializing untrusted data. A bad candidate
    # must not force the trusted publisher into large CPU/RAM allocations.
    if not isinstance(nodes, list) or len(nodes) != expected_count:
        _reject()
    if any(not isinstance(node, str) or not node or len(node) > 2048 or
           len(node.encode("utf-8", errors="surrogatepass")) > 4096 or
           not node.isprintable() or
           any(unicodedata.category(ch) in {"Cc", "Cf", "Cs"} for ch in node)
           for node in nodes):
        _reject()
    if nodes != sorted(set(nodes)) or type(raw["discovered"]) is not int or raw["discovered"] != len(nodes):
        _reject()
    canonical = json.dumps({"v": 1, "source_sha": candidate_sha,
                            "source_tree_sha": tree, "nodeids": nodes},
                           sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if raw["collection_sha256"] != digest:
        _reject()
    return nodes, digest


def review_evidence(raw: object, *, trusted_main_sha: str, candidate_sha: str,
                    candidate_tree_sha: str, qa_image_digest: str,
                    run_id: str, attempt: str, expected_full: int = 8125,
                    expected_subset: int = 8102) -> dict:
    """Bind full execution to exact subset membership; prohibit false quorums.

    Independent OCI provenance, sealing and artifact signature are external
    prerequisites.  They are NEVER inferred from this function succeeding.
    """
    for value in (trusted_main_sha, candidate_sha, candidate_tree_sha):
        _hex(value, _SHA40)
    _hex(qa_image_digest, _IMAGE)
    for value in (run_id, attempt):
        _hex(value, _NUMBER)
    if (trusted_main_sha == candidate_sha or type(expected_full) is not int or
            type(expected_subset) is not int or
            not 1 <= expected_subset < expected_full <= 100000):
        _reject()
    if not isinstance(raw, dict) or set(raw) != _SOURCE_KEYS:
        _reject()
    full, full_digest = _manifest(raw["full_manifest"], candidate_sha,
                                  candidate_tree_sha, expected_full)
    subset, subset_digest = _manifest(raw["subset_manifest"], candidate_sha,
                                      candidate_tree_sha, expected_subset)
    if (len(full) != expected_full or len(subset) != expected_subset or
            not set(subset).issubset(full) or AF_UNIX_NODEID not in subset):
        _reject()
    observations = raw["cases"]
    if not isinstance(observations, list) or len(observations) != expected_full:
        _reject()
    observed_hashes = []
    for case in observations:
        if not isinstance(case, dict) or set(case) != {"nodeid_sha256", "outcome"}:
            _reject()
        observed_hashes.append(_hex(case["nodeid_sha256"], _SHA64))
        if case["outcome"] != "passed":
            _reject()
    expected_hashes = {hashlib.sha256(node.encode("utf-8")).hexdigest() for node in full}
    if (len(set(observed_hashes)) != expected_full or
            set(observed_hashes) != expected_hashes or
            type(raw["pytest_exit_code"]) is not int or raw["pytest_exit_code"] != 0):
        _reject()
    junit_digest = _hex(raw["junit_sha256"], _SHA64)
    # Never echo arbitrary candidate-supplied strings or raw identity data.
    return {
        "schema_version": 1,
        "status": "READY_FOR_PHYSICAL_ATTESTATION",
        "trusted_main_sha": trusted_main_sha,
        "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "qa_image_digest": qa_image_digest,
        # The QA execution image D_qa is NOT the immutable Deploy V2 runtime
        # artifact D_runtime. Runtime-smoke and build-once attestations are
        # separate gates and may never be inferred from QA success.
        "runtime_artifact_digest": None,
        "runtime_artifact_tested": False,
        "run_id": run_id, "attempt": attempt,
        "scope_full": "repository-root-exact-policy",
        "scope_subset": "tests/",
        "full_collected": len(full), "full_executed": len(observed_hashes),
        "subset_collected": len(subset), "subset_executed": len(subset),
        # All subset node IDs passed inside the FULL run.  This does not
        # claim a second, independent 8,102-case pytest invocation.
        "subset_execution_mode": "covered_by_full_execution",
        "subset_independent_run": False,
        "full_collection_sha256": full_digest,
        "subset_collection_sha256": subset_digest,
        "junit_sha256": junit_digest,
        "skipped": 0, "failures": 0, "errors": 0, "xfail": 0,
        "af_unix_passed": True,
        "physical_custody_certified": False,
        "qa_certified": False,
        "release_authority": False,
        "automatic_go": False,
    }
