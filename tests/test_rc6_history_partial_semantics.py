import pytest


def _semantics():
    import bf_production_paper_observer as m
    return m._history_batch_semantics


def test_all_full_is_green():
    value = _semantics()(["VALID_PAYLOAD", "VALID_PAYLOAD"])
    assert value == {
        "full_valid": 2,
        "partial_with_valid_evidence": 0,
        "empty_invalid": 0,
        "errors": 0,
        "usable": 2,
        "hard_failures": 0,
        "state": "VERDE",
    }


def test_full_plus_partial_is_yellow_without_hard_failure():
    value = _semantics()(["VALID_PAYLOAD", "PARTIAL", "PARTIAL"])
    assert value["state"] == "AMARILLO"
    assert value["usable"] == 3
    assert value["partial_with_valid_evidence"] == 2
    assert value["hard_failures"] == 0


def test_partial_only_is_usable_but_yellow():
    value = _semantics()(["PARTIAL", "PARTIAL"])
    assert value["state"] == "AMARILLO"
    assert value["usable"] == 2
    assert value["hard_failures"] == 0


def test_empty_and_error_only_are_red_hard_failures():
    value = _semantics()(["EMPTY_OR_INVALID", "ERROR"])
    assert value["state"] == "ROJO"
    assert value["usable"] == 0
    assert value["hard_failures"] == 2


def test_partial_plus_hard_failure_stays_yellow_and_preserves_both_truths():
    value = _semantics()(["PARTIAL", "EMPTY_OR_INVALID", "ERROR"])
    assert value["state"] == "AMARILLO"
    assert value["usable"] == 1
    assert value["hard_failures"] == 2
    assert value["partial_with_valid_evidence"] == 1


def test_unknown_status_fails_closed():
    with pytest.raises(ValueError, match="PPI_HISTORY_BATCH_UNKNOWN_STATUS"):
        _semantics()(["VALID_PAYLOAD", "MYSTERY"])
