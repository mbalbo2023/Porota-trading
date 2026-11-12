"""The OFFHOST JUnit counter contract must cover every candle subscenario."""

import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CANDLE_TEST = "tests/test_rc6_candle_inmarket_evidence.py"


def test_candle_unittest_subscenarios_are_individual_junit_testcases(tmp_path):
    """Exercise real pytest/JUnit emission, not a handcrafted XML approximation."""
    report = tmp_path / "candle.xml"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider",
         f"--basetemp={tmp_path / 'pytest_scratch'}", f"--junitxml={report}", CANDLE_TEST],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False, timeout=180,
    )
    assert result.returncode == 0, f"candle pytest failed: exit={result.returncode}"
    xml = ET.parse(report).getroot()
    suites = [xml] if xml.tag == "testsuite" else list(xml.findall(".//testsuite"))
    assert len(suites) == 1
    suite = suites[0]
    direct = suite.findall("testcase")
    descendant = suite.findall(".//testcase")
    counts = {key: int(suite.attrib[key]) for key in
              ("tests", "failures", "errors", "skipped")}
    assert counts["tests"] == len(direct), (
        f"R163_JUNIT_COUNTER_MISMATCH: tests={counts['tests']} direct={len(direct)}"
    )
    assert counts["tests"] == len(descendant)
    assert counts["failures"] == counts["errors"] == counts["skipped"] == 0

    names = {case.get("name") for case in direct}
    assert {
        "test_real_corruption_of_candle_dirty_cannot_produce_green",
        "test_real_corruption_of_candle_worker_state_cannot_produce_green",
        "test_missing_partial_results_cannot_produce_green",
        "test_incomplete_partial_results_cannot_produce_green",
        "test_corrupt_partial_results_cannot_produce_green",
    } <= names
