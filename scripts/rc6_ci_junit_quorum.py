"""Exact pytest collection ↔ JUnit nodeid proof and bounded shard aggregation.

Offline analysis only: no network, host, secret, source or deploy mutation.
The private manifest's nodeids are matched one-for-one to sha256 properties on
*actual* JUnit testcases.  A receipt cannot be GREEN if a test was uncollected,
missing, duplicated, skipped, xfailed, failed, errored or executed from a
different source SHA/tree.  This does not yet replace the existing Predeploy
governed tests evaluator and is not deployment authority.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile
from xml.etree import ElementTree as ET

from scripts import rc6_ci_shard_quorum as shard
from scripts.rc6_ci_pytest_identity import PROPERTY, nodeid_digest


MAX_XML_BYTES = 128 * 1024 * 1024


class JunitQuorumRed(ValueError):
    pass


def _red() -> None:
    raise JunitQuorumRed("RC6_JUNIT_NODEID_QUORUM_RED")


def _manifest(raw: object) -> dict:
    try:
        return shard._validate_manifest(raw)
    except (shard.ShardQuorumRed, ValueError, TypeError):
        _red()


def _path(raw: object) -> Path:
    if not isinstance(raw, (str, Path)):
        _red()
    try:
        value = Path(raw)
        if len(str(value)) > 4096:
            _red()
        return value
    except (TypeError, ValueError, OSError):
        _red()


def _xml(path: Path) -> tuple[ET.Element, bytes]:
    try:
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_XML_BYTES:
            _red()
        raw = path.read_bytes()
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError):
        _red()
    if root.tag not in {"testsuite", "testsuites"}:
        _red()
    return root, raw


def verify_junit(collected_manifest: object, executed_manifest: object,
                 junit_file: Path, pytest_exit_code: int,
                 *, required_nodeids: list[str] | None = None) -> dict:
    """Verify exact nodeid multiset using JUnit testcase user_properties."""
    expected_all, actual = _manifest(collected_manifest), _manifest(executed_manifest)
    if (expected_all["source_sha"] != actual["source_sha"] or
            expected_all["source_tree_sha"] != actual["source_tree_sha"] or
            type(pytest_exit_code) is not int or pytest_exit_code != 0):
        _red()
    if required_nodeids is None:
        required = expected_all["nodeids"]
    else:
        try:
            required = shard._nodeids(required_nodeids)
        except (shard.ShardQuorumRed, TypeError, ValueError):
            _red()
    if (not required or len(required) != len(set(required)) or
            any(value not in expected_all["nodeids"] for value in required) or
            actual["nodeids"] != required):
        _red()

    root, source_bytes = _xml(_path(junit_file))
    testcase_list = list(root.iter("testcase"))
    if len(testcase_list) != len(required):
        _red()
    observed = []
    for testcase in testcase_list:
        if (next(testcase.iter("failure"), None) is not None or
                next(testcase.iter("error"), None) is not None or
                next(testcase.iter("skipped"), None) is not None):
            _red()
        values = [p.get("value") for p in testcase.findall("./properties/property")
                  if p.get("name") == PROPERTY]
        if len(values) != 1 or not isinstance(values[0], str):
            _red()
        observed.append(values[0])
    expected_hashes = [nodeid_digest(nodeid) for nodeid in required]
    if len(set(observed)) != len(observed) or sorted(observed) != sorted(expected_hashes):
        _red()
    if any(next(root.iter(tag), None) is not None for tag in ("failure", "error", "skipped")):
        _red()
    return {"schema_version": 1, "status": "GREEN_JUNIT_NODEID_PROOF",
            "source_sha": actual["source_sha"],
            "source_tree_sha": actual["source_tree_sha"],
            "global_collection_sha256": expected_all["collection_sha256"],
            "executed_collection_sha256": actual["collection_sha256"],
            "junit_xml_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "discovered": len(required), "executed": len(observed),
            "failures": 0, "errors": 0, "skipped": 0, "xfail": 0,
            "automatic_go": False, "runtime_authority": False}


def verify_parallel(global_manifest: dict, shard_proofs: list[dict],
                    shard_count: int, output_junit: Path) -> dict:
    """Require every shard's JUnit before building a single full-suite XML."""
    m = _manifest(global_manifest)
    try:
        expected = shard.partition(m, shard_count)
    except (shard.ShardQuorumRed, TypeError, ValueError):
        _red()
    if not isinstance(shard_proofs, list) or len(shard_proofs) != shard_count:
        _red()
    seen = set()
    case_nodes = []
    junit_digests = []
    for receipt in shard_proofs:
        if not isinstance(receipt, dict) or set(receipt) != {
                "shard_index", "manifest", "junit_file", "pytest_exit_code"}:
            _red()
        i = receipt["shard_index"]
        if type(i) is not int or not 0 <= i < shard_count or i in seen:
            _red()
        seen.add(i)
        proof = verify_junit(m, receipt["manifest"], _path(receipt["junit_file"]),
                             receipt["pytest_exit_code"], required_nodeids=expected[i])
        junit_digests.append(proof["junit_xml_sha256"])
        root, _ = _xml(_path(receipt["junit_file"]))
        case_nodes.extend(deepcopy(node) for node in root.iter("testcase"))
    if len(case_nodes) != m["discovered"] or len(seen) != shard_count:
        _red()
    output_junit = _path(output_junit)
    if output_junit.is_symlink() or (output_junit.exists() and not output_junit.is_file()):
        _red()
    if any(output_junit.resolve() == _path(receipt["junit_file"]).resolve()
           for receipt in shard_proofs):
        _red()
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite", tests=str(m["discovered"]),
                          failures="0", errors="0", skipped="0")
    suite.extend(case_nodes)
    xml_bytes = ET.tostring(suites, encoding="utf-8", xml_declaration=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=output_junit.parent,
                                         prefix=".rc6-junit-", delete=False) as file:
            temp_path = Path(file.name)
            os.chmod(temp_path, 0o600)
            file.write(xml_bytes)
        os.replace(temp_path, output_junit)
    except OSError:
        _red()
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return {"schema_version": 1, "status": "GREEN_QUORUM_OFFLINE_ONLY",
            "source_sha": m["source_sha"], "source_tree_sha": m["source_tree_sha"],
            "collection_sha256": m["collection_sha256"],
            "shards": shard_count, "discovered": m["discovered"],
            "executed": len(case_nodes),
            "combined_junit_sha256": hashlib.sha256(xml_bytes).hexdigest(),
            "shard_junit_digests": sorted(junit_digests),
            "automatic_go": False, "runtime_authority": False}
