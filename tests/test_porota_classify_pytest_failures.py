from scripts.porota_classify_pytest_failures import classify

def registry():
    return {
        "status": "PROVISIONAL",
        "failures": [
            {"nodeid": "tests/test_a.py::test_current", "classification": "CURRENT_CONTRACT_RECONCILIATION_REQUIRED"},
            {"nodeid": "tests/test_b.py::test_legacy[x]", "classification": "LEGACY_REGRESSION_STALE_EXPECTATION"},
        ],
    }

def test_classifies_exact_nodeids_and_unclassified():
    log = """FAILED tests/test_a.py::test_current
FAILED tests/test_b.py::test_legacy[x]
FAILED tests/test_c.py::test_unknown
"""
    result = classify(log, registry(), 1)
    assert result["failed_cases"] == 3
    assert result["counts"]["CURRENT_CONTRACT_RECONCILIATION_REQUIRED"] == 1
    assert result["counts"]["LEGACY_REGRESSION_STALE_EXPECTATION"] == 1
    assert result["counts"]["UNCLASSIFIED"] == 1
    assert result["unclassified_failures"] == ["tests/test_c.py::test_unknown"]

def test_preserves_pytest_error_evidence():
    result = classify("ERROR tests/test_collect.py::test_x\n", registry(), 2)
    assert result["pytest_exit_code"] == 2
    assert result["error_nodes"] == ["tests/test_collect.py::test_x"]
