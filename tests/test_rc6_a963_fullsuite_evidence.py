"""A963: FULL-suite collection and JUnit coverage must fail closed."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.etree import ElementTree as ET

import pytest
import yaml

from scripts import rc6_ci_shard_quorum as shard
from scripts import rc6_ci_fullsuite_evidence as proof
from scripts.rc6_ci_pytest_identity import nodeid_digest


SHA = "a" * 40
TREE = "b" * 40
NODES = ["tests/test_alpha.py::test_a[secret-case]", "tests/test_beta.py::test_b"]


def _prepare(tmp_path: Path, *, actual_nodes=None, cases=None, rc=0,
             collected_nodes=NODES):
    private = tmp_path / "private"
    private.mkdir()
    collected = shard.manifest(SHA, TREE, list(collected_nodes))
    executed = shard.manifest(SHA, TREE, list(actual_nodes or collected_nodes))
    (private / "collected.json").write_text(json.dumps(collected))
    (private / "executed.json").write_text(json.dumps(executed))
    (private / "pytest.rc").write_text(str(rc) + "\n")
    suite = ET.Element("testsuite", name="pytest")
    for node, outcome in cases or [(n, "passed") for n in actual_nodes or collected_nodes]:
        case = ET.SubElement(suite, "testcase", name=node, classname="secret.class")
        props = ET.SubElement(case, "properties")
        ET.SubElement(props, "property", name="rc6_nodeid_sha256", value=nodeid_digest(node))
        if outcome != "passed":
            ET.SubElement(case, outcome, message="SECRET SHOULD NEVER BE UPLOADED").text = "SENSITIVE TRACE"
        ET.SubElement(case, "system-out").text = "PERSONAL ACCOUNT DATA"
    (private / "junit.xml").write_bytes(ET.tostring(suite))
    return private


def _run(tmp_path, private):
    out = tmp_path / "sanitized"
    out.mkdir()
    result = proof.summarize(
        private / "collected.json", private / "executed.json",
        private / "junit.xml", private / "pytest.rc", out,
        source_sha=SHA, source_tree_sha=TREE,
    )
    return result, out


def test_all_pass_exact_same_sha_has_anonymized_complete_coverage(tmp_path):
    private = _prepare(tmp_path)
    result, out = _run(tmp_path, private)
    assert result["status"] == "GREEN_EVIDENCE_ONLY"
    assert (result["discovered"], result["executed"], result["missing"]) == (2, 2, 0)
    assert (result["failures"], result["errors"], result["skipped"]) == (0, 0, 0)
    assert result["collection_sha256"] == shard.manifest(SHA, TREE, NODES)["collection_sha256"]
    assert result["coverage_complete"] is True
    assert result["automatic_go"] is False
    assert result["release_gate_activated"] is False
    assert result["host_written"] is False
    assert result["runtime_authority"] is False
    sanitized = (out / "junit-hashed.xml").read_text()
    assert all(nodeid_digest(node) in sanitized for node in NODES)
    for forbidden in ("secret-case", "SECRET", "PERSONAL ACCOUNT DATA", "SENSITIVE TRACE", "secret.class"):
        assert forbidden not in sanitized
    assert json.loads((out / "receipt.json").read_text()) == result


def test_all_executed_but_one_failed_is_red_without_omitting_hashes(tmp_path):
    private = _prepare(tmp_path, cases=[(NODES[0], "failure"), (NODES[1], "passed")], rc=1)
    result, out = _run(tmp_path, private)
    assert result["status"] == "RED_FUNCTIONAL_FAILURES"
    assert result["coverage_complete"] is True
    assert result["executed"] == 2 and result["missing"] == 0
    assert result["failures"] == 1
    xml = (out / "junit-hashed.xml").read_text()
    assert 'type="SANITIZED_DIAGNOSTIC"' in xml
    assert "SENSITIVE TRACE" not in xml


def test_missing_testcase_writes_red_receipt_without_junit(tmp_path):
    private = _prepare(tmp_path, cases=[(NODES[0], "passed")], rc=1)
    result, out = _run(tmp_path, private)
    assert result["status"] == "RED_INCOMPLETE"
    assert result["coverage_complete"] is False
    assert result["missing"] == 1
    assert not (out / "junit-hashed.xml").exists()


def test_independent_collection_drift_is_red_even_with_complete_junit(tmp_path):
    private = _prepare(tmp_path, actual_nodes=[NODES[0], "tests/other.py::test_new"], rc=1)
    result, _ = _run(tmp_path, private)
    assert result["status"] == "RED_INCOMPLETE"
    assert result["reason"] == "COLLECTION_DRIFT"


def test_skip_cannot_be_green(tmp_path):
    private = _prepare(tmp_path, cases=[(NODES[0], "skipped"), (NODES[1], "passed")], rc=0)
    result, _ = _run(tmp_path, private)
    assert result["status"] == "RED_SKIPPED"
    assert result["skipped"] == 1
    assert result["coverage_complete"] is True


def test_cli_skip_cannot_be_a_successful_full_suite(tmp_path):
    private = _prepare(tmp_path, cases=[(NODES[0], "skipped"), (NODES[1], "passed")], rc=0)
    output = tmp_path / "sanitized"
    output.mkdir()
    exit_code = proof.main([
        "--collected", str(private / "collected.json"),
        "--executed", str(private / "executed.json"),
        "--junit", str(private / "junit.xml"),
        "--pytest-rc", str(private / "pytest.rc"),
        "--out", str(output), "--source-sha", SHA,
        "--source-tree-sha", TREE,
    ])
    assert exit_code == 2
    assert json.loads((output / "receipt.json").read_text())["status"] == "RED_SKIPPED"


def test_duplicate_junit_properties_is_red(tmp_path):
    private = _prepare(tmp_path)
    path = private / "junit.xml"
    root = ET.parse(path)
    properties = root.find("./testcase/properties")
    ET.SubElement(properties, "property", name="rc6_nodeid_sha256", value=nodeid_digest(NODES[0]))
    root.write(path)
    result, _ = _run(tmp_path, private)
    assert result["status"] == "RED_INCOMPLETE"


def test_symlink_junit_is_red_and_no_raw_data_published(tmp_path):
    private = _prepare(tmp_path)
    (private / "junit.xml").rename(private / "real-junit.xml")
    (private / "junit.xml").symlink_to(private / "real-junit.xml")
    result, out = _run(tmp_path, private)
    assert result["status"] == "RED_INCOMPLETE"
    assert not (out / "junit-hashed.xml").exists()
    assert "real-junit" not in (out / "receipt.json").read_text()


def test_unexpected_exit_code_with_green_tests_is_red(tmp_path):
    private = _prepare(tmp_path, rc=5)
    result, _ = _run(tmp_path, private)
    assert result["status"] == "RED_INCOMPLETE"
    assert result["reason"] == "PYTEST_EXIT_INCONSISTENT"


def test_missing_inputs_produce_red_receipt(tmp_path):
    private = _prepare(tmp_path)
    (private / "junit.xml").unlink()
    result, out = _run(tmp_path, private)
    assert result["status"] == "RED_INCOMPLETE"
    assert (out / "receipt.json").is_file()
    assert not (out / "junit-hashed.xml").exists()


def test_all_receipts_have_immutable_source_and_false_release_authority(tmp_path):
    private = _prepare(tmp_path)
    result, _ = _run(tmp_path, private)
    assert (result["source_sha"], result["source_tree_sha"]) == (SHA, TREE)
    assert result["sanitized_junit_sha256"] == hashlib.sha256(
        (tmp_path / "sanitized" / "junit-hashed.xml").read_bytes()).hexdigest()


def test_existing_full_job_keeps_focal_and_full_semantics_with_private_evidence():
    repo = Path(__file__).resolve().parents[1]
    path = repo / ".github/workflows/rc6-ws-tests-12-current-contract.yml"
    data = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert list(data["jobs"]) == ["focal", "full-suite"]
    assert data["on"] == {"push": {"branches": [
        "work/ws-tests-12-reconcile-current-contract-20261001",
        "integration/rc6-a757-expanded-20261107"]}, "workflow_dispatch": ""}
    focal = data["jobs"]["focal"]
    assert "Reconciled legacy assertions" in [s["name"] for s in focal["steps"] if "name" in s]
    job = data["jobs"]["full-suite"]
    assert job["timeout-minutes"] == "45"
    steps = job["steps"]
    names = [step["name"] for step in steps if "name" in step]
    assert names == [
        "Verify exact candidate checkout and tree (runner-only)",
        "Install dependencies", "Collect exact FULL nodeids privately",
        "Full suite", "Sanitize FULL evidence", "Upload anonymized FULL evidence"]
    full = next(s for s in steps if s.get("name") == "Full suite")
    script = full["run"]
    assert "PYTHONPATH=. pytest -q" in script
    assert "--collect-only" not in script and "--ignore" not in script
    assert "--junitxml=" in script and "-p scripts.rc6_ci_pytest_identity" in script
    assert "-o xfail_strict=true" in script
    sanitizer = next(s for s in steps if s.get("name") == "Sanitize FULL evidence")
    assert sanitizer["if"] == "always()"
    assert "scripts.rc6_ci_fullsuite_evidence" in sanitizer["run"]
    upload = next(s for s in steps if s.get("name") == "Upload anonymized FULL evidence")
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == "${{ runner.temp }}/rc6-a963-sanitized/"
    assert "private" not in upload["with"]["path"]
    assert upload["with"]["if-no-files-found"] == "error"


def test_real_pytest_collection_and_execution_emit_exact_hashed_properties(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    probe = tmp_path / "test_a963_probe.py"
    probe.write_text('def test_fixture_a():\n    assert True\n\n'
                     'def test_fixture_b():\n    assert 2 + 2 == 4\n', encoding="utf-8")
    private = tmp_path / "private"
    private.mkdir()
    base_env = {**os.environ, "PYTHONPATH": str(repo),
                "RC6_CI_IDENTITY_PROOF": "1", "RC6_CI_SOURCE_SHA": SHA,
                "RC6_CI_SOURCE_TREE_SHA": TREE}
    for label, extra in (
        ("collected", ["--collect-only"]),
        ("executed", [f"--junitxml={private / 'junit.xml'}"]),
    ):
        env = {**base_env, "RC6_CI_MANIFEST_PATH": str(private / f"{label}.json")}
        proc = subprocess.run([
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-p", "scripts.rc6_ci_pytest_identity", *extra, str(probe),
        ], cwd=repo, env=env, capture_output=True, text=True, timeout=25)
        assert proc.returncode == 0, proc.stdout[-800:] + proc.stderr[-800:]
    (private / "pytest.rc").write_text("0\n")
    receipt, out = _run(tmp_path, private)
    assert receipt["status"] == "GREEN_EVIDENCE_ONLY"
    assert (receipt["discovered"], receipt["executed"]) == (2, 2)
    safe_xml = (out / "junit-hashed.xml").read_text()
    assert "test_fixture_a" not in safe_xml and "test_fixture_b" not in safe_xml


def test_dynamically_injected_wasxfail_cannot_create_false_green(tmp_path):
    """A965: runtime fallback catches unmarked plugin-generated XPASS."""
    repo = Path(__file__).resolve().parents[1]
    data = yaml.load((repo / ".github/workflows/rc6-ws-tests-12-current-contract.yml")
                     .read_text(), Loader=yaml.BaseLoader)
    full = next(s for s in data["jobs"]["full-suite"]["steps"]
                if s.get("name") == "Full suite")
    strict = "-o xfail_strict=true" in full["run"]
    probe = tmp_path / "test_a963_xpass.py"
    probe.write_text('def test_unmarked_case():\n    assert True\n', encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        'import pytest\n'
        '@pytest.hookimpl(hookwrapper=True)\n'
        'def pytest_runtest_makereport(item, call):\n'
        '    outcome = yield\n'
        '    report = outcome.get_result()\n'
        '    if report.when == "call" and report.passed:\n'
        '        report.wasxfail = "adversarial third-party plugin"\n',
        encoding="utf-8")
    private = tmp_path / "private"
    private.mkdir()
    common = {**os.environ, "PYTHONPATH": str(repo),
              "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
              "RC6_CI_IDENTITY_PROOF": "1", "RC6_CI_SOURCE_SHA": SHA,
              "RC6_CI_SOURCE_TREE_SHA": TREE}
    for label, args in (
        ("collected", ["--collect-only"]),
        ("executed", (["-o", "xfail_strict=true"] if strict else []) +
         [f"--junitxml={private / 'junit.xml'}"]),
    ):
        env = {**common, "RC6_CI_MANIFEST_PATH": str(private / f"{label}.json")}
        proc = subprocess.run([
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-p", "scripts.rc6_ci_pytest_identity", *args, str(probe),
        ], cwd=repo, env=env, capture_output=True, text=True, timeout=25)
        if label == "executed":
            (private / "pytest.rc").write_text(str(proc.returncode) + "\n")
        else:
            assert proc.returncode == 0, proc.stderr[-500:]
    receipt, _ = _run(tmp_path, private)
    assert receipt["status"] == "RED_FUNCTIONAL_FAILURES"
    assert strict, "The actual full-suite command must force strict XPASS"
    assert receipt["coverage_complete"] is True
    assert receipt["executed"] == 1
    assert receipt["pytest_exit_code"] == 1 and receipt["failures"] == 1


def test_runtime_pytest_xfail_without_marker_is_red_skipped(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    probe = tmp_path / "test_runtime_xfail.py"
    probe.write_text('import pytest\ndef test_runtime_xfail():\n'
                     '    pytest.xfail("expected at runtime")\n', encoding="utf-8")
    private = tmp_path / "private"
    private.mkdir()
    common = {**os.environ, "PYTHONPATH": str(repo),
              "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
              "RC6_CI_IDENTITY_PROOF": "1", "RC6_CI_SOURCE_SHA": SHA,
              "RC6_CI_SOURCE_TREE_SHA": TREE}
    for label, options in (
        ("collected", ["--collect-only"]),
        ("executed", ["-o", "xfail_strict=true",
                      f"--junitxml={private / 'junit.xml'}"]),
    ):
        env = {**common, "RC6_CI_MANIFEST_PATH": str(private / f"{label}.json")}
        result = subprocess.run([
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-p", "scripts.rc6_ci_pytest_identity", *options, str(probe),
        ], cwd=repo, env=env, capture_output=True, text=True, timeout=25)
        assert result.returncode == 0, result.stdout[-400:] + result.stderr[-400:]
    (private / "pytest.rc").write_text("0\n")
    receipt, _ = _run(tmp_path, private)
    assert receipt["status"] == "RED_SKIPPED"
    assert receipt["coverage_complete"] is True
    assert receipt["skipped"] == 1


def test_xpass_guard_is_scoped_to_opt_in_ci_only(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    probe = tmp_path / "test_a963_optout.py"
    probe.write_text('import pytest\n'
                     '@pytest.mark.xfail(reason="legacy", strict=False)\n'
                     'def test_legacy_xpass():\n    assert True\n', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(repo),
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "RC6_CI_IDENTITY_PROOF": "0"}
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-p", "scripts.rc6_ci_pytest_identity", "-o", "xfail_strict=true",
        str(probe),
    ], cwd=repo, env=env, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout[-500:] + result.stderr[-500:]


@pytest.mark.parametrize("via_env", [False, True])
def test_ci_rejects_runxfail_before_collection(via_env, tmp_path):
    repo = Path(__file__).resolve().parents[1]
    probe = tmp_path / "test_a963_runxfail.py"
    probe.write_text('import pytest\n'
                     '@pytest.mark.xfail(reason="legacy", strict=False)\n'
                     'def test_unexpected_pass():\n    assert True\n', encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(repo),
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "RC6_CI_IDENTITY_PROOF": "1",
           "RC6_CI_SOURCE_SHA": SHA, "RC6_CI_SOURCE_TREE_SHA": TREE,
           "RC6_CI_MANIFEST_PATH": str(tmp_path / "manifest.json")}
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "-p", "scripts.rc6_ci_pytest_identity", "-o", "xfail_strict=true",
            "--collect-only", str(probe)]
    if via_env:
        env["PYTEST_ADDOPTS"] = "--runxfail"
    else:
        env.pop("PYTEST_ADDOPTS", None)
        args.append("--runxfail")
    result = subprocess.run(args, cwd=repo, env=env,
                            capture_output=True, text=True, timeout=25)
    assert result.returncode != 0, result.stdout[-400:]
    assert "RC6_CI_RUNXFAIL_FORBIDDEN" in (result.stdout + result.stderr)


@pytest.mark.parametrize("marker,passes", [
    ('@pytest.mark.xfail(reason="expected failure")', True),
    ('@pytest.mark.xfail(reason="expected failure", strict=False)', True),
    ('@pytest.mark.xfail(reason="expected failure")', False),
    ('@pytest.mark.xfail(reason="expected failure", strict=False)', False),
])
def test_fullsuite_rejects_xfail_markers_before_executing_tests(tmp_path, marker, passes):
    repo = Path(__file__).resolve().parents[1]
    probe = tmp_path / "test_a963_forbidden_marker.py"
    probe.write_text('import pytest\n' + marker + '\n'
                     'def test_marked_case():\n    assert ' + str(passes) + '\n',
                     encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(repo),
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "RC6_CI_IDENTITY_PROOF": "1",
           "RC6_CI_SOURCE_SHA": SHA, "RC6_CI_SOURCE_TREE_SHA": TREE,
           "RC6_CI_MANIFEST_PATH": str(tmp_path / "manifest.json")}
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-p", "scripts.rc6_ci_pytest_identity", "--collect-only", str(probe),
    ], cwd=repo, env=env, capture_output=True, text=True, timeout=25)
    assert result.returncode != 0, result.stdout[-400:]
    assert "RC6_CI_XFAIL_MARKER_FORBIDDEN" in (result.stdout + result.stderr)
