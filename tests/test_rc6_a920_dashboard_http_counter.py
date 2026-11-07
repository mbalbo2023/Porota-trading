"""A920: a dashboard HTTP audit must not promote a missing PAPER counter."""

import json

import pytest

from scripts import rc6_dashboard_http_consistency_check as checker


_MISSING = object()


def _page() -> str:
    links = "".join(f"<a href='{route}'>Link</a>" for route in checker.CANONICAL_NAV)
    return (
        "<nav id='porota-canonical-nav'>" + links + "</nav>"
        "<div id='porota-runtime-truth'>PRODUCTION_PAPER</div>"
    )


def _audit(monkeypatch, capsys, counter, *, mode="PRODUCTION_PAPER",
           execution="SIMULATED", process="READY"):
    truth = {
        "mode": mode, "execution": execution,
        "session_state": "MARKET_CLOSED", "process_state": process,
    }
    if counter is not _MISSING:
        truth["real_orders_sent"] = counter

    paths = []

    def fake_fetch(path):
        paths.append(path)
        return 200, json.dumps(truth) if path == "/api/dashboard/truth" else _page()

    monkeypatch.setattr(checker, "fetch", fake_fetch)
    result = checker.main()
    return result, capsys.readouterr().out, paths


@pytest.mark.parametrize("invalid", [
    _MISSING, None, False, True, "0", "oops", "", 0.0, 1.0, [], {}, -1,
], ids=[
    "missing", "null", "false", "true", "str-zero", "text", "empty",
    "float-zero", "float-one", "list", "object", "negative",
])
def test_unknown_real_orders_counter_never_produces_green(monkeypatch, capsys, invalid):
    result, output, paths = _audit(monkeypatch, capsys, invalid)
    assert result == 1
    assert "FAIL=REAL_ORDERS_UNVERIFIED" in output
    assert "RESULT=RED" in output
    assert paths == ["/api/dashboard/truth", *checker.EXPECTED_ROUTES]


def test_exact_integer_zero_is_accepted_in_synthetic_http_audit(monkeypatch, capsys):
    result, output, paths = _audit(monkeypatch, capsys, 0)
    assert result == 0
    assert "REAL_ORDERS_SENT=0" in output
    assert "RESULT=GREEN" in output
    assert paths == ["/api/dashboard/truth", *checker.EXPECTED_ROUTES]


def test_positive_integer_reports_nonzero_not_unknown(monkeypatch, capsys):
    result, output, _ = _audit(monkeypatch, capsys, 2)
    assert result == 1
    assert "FAIL=REAL_ORDERS_NONZERO" in output
    assert "RESULT=RED" in output


def test_truth_json_list_is_rejected_as_invalid_schema(monkeypatch, capsys):
    monkeypatch.setattr(checker, "fetch", lambda _: (200, "[]"))
    assert checker.main() == 3
    assert "DASHBOARD_TRUTH_SCHEMA=INVALID" in capsys.readouterr().out


@pytest.mark.parametrize("mode,execution,expected", [
    ("REAL_MONEY", "LIVE", {"NON_PAPER_MODE", "NON_SIMULATED_EXECUTION"}),
    ("PRODUCTION_PAPER", "LIVE", {"NON_SIMULATED_EXECUTION"}),
    ("REAL", "SIMULATED", {"NON_PAPER_MODE"}),
    (None, "SIMULATED", {"NON_PAPER_MODE"}),
    ("PRODUCTION_PAPER", None, {"NON_SIMULATED_EXECUTION"}),
    (False, "SIMULATED", {"NON_PAPER_MODE"}),
])
def test_real_execution_or_invalid_mode_never_returns_green(
        monkeypatch, capsys, mode, execution, expected):
    result, output, _ = _audit(monkeypatch, capsys, 0, mode=mode,
                               execution=execution)
    assert result == 1
    assert "RESULT=RED" in output
    for blocker in expected:
        assert "FAIL=" + blocker in output


def test_untrusted_truth_values_are_not_echoed_into_public_logs(monkeypatch, capsys):
    marker = "SYNTHETIC_SECRET_MARKER"
    result, output, _ = _audit(
        monkeypatch, capsys, 0,
        mode="PRODUCTION_PAPER\nAuthorization: " + marker,
        execution="SIMULATED\n" + marker,
        process="RUNNING\n" + marker,
    )
    assert result == 1
    assert "RESULT=RED" in output
    assert marker not in output
    assert "MODE=NO_VERIFICADO" in output
    assert "EXECUTION=NO_VERIFICADO" in output
    assert "PROCESS=NO_VERIFICADO" in output
