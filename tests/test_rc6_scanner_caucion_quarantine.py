"""A144: invalid historical PAPER caucion fees never trigger scanner storms.

No network, credentials, real orders, or SQLite mutations are used here.
"""

import inspect

import pytest

from bf_production_paper_observer import (
    CAUCION_LEDGER_RECHECK_SECONDS,
    _settle_cauciones_fail_closed,
    run,
)


class DummyBroker:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = 0

    def settle_cauciones(self):
        self.calls += 1
        if self.failure:
            raise self.failure


def test_invalid_caucion_ledger_remains_blocked_for_one_hour_without_retry():
    broken = DummyBroker(ValueError(
        "CAUCION_LEDGER_INVALID: Autoridad de costos PAPER incompatible"))
    blocked, due = _settle_cauciones_fail_closed(broken, False, 0, 100)
    assert blocked is True
    assert due == 100 + CAUCION_LEDGER_RECHECK_SECONDS
    assert broken.calls == 1

    for now in (101, 301, due - 1):
        assert _settle_cauciones_fail_closed(broken, blocked, due, now) == (True, due)
    assert broken.calls == 1

    blocked, next_due = _settle_cauciones_fail_closed(broken, True, due, due)
    assert blocked is True and next_due == due + CAUCION_LEDGER_RECHECK_SECONDS
    assert broken.calls == 2


def test_valid_reconciliation_unblocks_only_after_successful_ledger_check():
    broker = DummyBroker()
    assert _settle_cauciones_fail_closed(broker, True, 200, 150) == (True, 200)
    assert broker.calls == 0
    assert _settle_cauciones_fail_closed(broker, True, 200, 200) == (False, 0.0)
    assert broker.calls == 1


def test_unknown_settlement_error_still_fails_closed():
    broker = DummyBroker(ValueError("UNRELATED_BROKER_FAILURE"))
    with pytest.raises(ValueError, match="UNRELATED_BROKER_FAILURE"):
        _settle_cauciones_fail_closed(broker, False, 0, 50)


def test_invalid_ledger_branch_precedes_provider_login_and_service_ticks():
    source = inspect.getsource(run)
    assert source.index("if caucion_ledger_blocked:") < source.index(
        "# Autenticación, catálogo e históricos son útiles")
    assert "time.sleep(max(30, COMMAND_POLL_SECONDS))" in source
    assert "process_state=\"DEGRADED\"" in source
