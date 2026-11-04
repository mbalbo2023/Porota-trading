"""PPI GET quota-scope regressions; fake HTTPAdapter only, no PPI credentials.

The budget V1 cannot authorize Configuration, SearchInstrument, historical
Search or Bonds/Estimate under a Current/Book/Intraday lease. Their provider
and external-Watch quota contract is not verified, so live GET must fail
closed. Existing persisted SHADOW/historical data need no provider GET.
"""

from contextlib import nullcontext

import pytest
import requests

from bd_ppi_readonly_guard import (
    PRODUCTION_HOST, ReadOnlyTransportGuard, ReadOnlyPolicyViolation,
    _BUDGETED_MARKET_GET_PATHS, _GET_PATHS,
)
from rc6_ppi_global_budget import BudgetBackpressure
from tests.test_rc6_ppi_capacity_benchmark import wire


ROOT = f"https://{PRODUCTION_HOST}"


@pytest.mark.parametrize("path", sorted(_GET_PATHS - _BUDGETED_MARKET_GET_PATHS))
def test_each_unmetered_provider_get_denied_before_wire_even_if_readonly_allowlisted(
    wire, path,
):
    _, provider_calls, _ = wire
    audits = []
    guard = ReadOnlyTransportGuard(
        audit=lambda *entry: audits.append(entry), budget=None,
        consumer="SCANNER",
    ).install()
    expected = ("PPI_MARKETDATA_ENDPOINT_BUDGET_UNVERIFIED"
                if path.startswith("/api/1.0/marketdata/")
                else "PPI_CONFIGURATION_ENDPOINT_BUDGET_UNVERIFIED")
    try:
        # Being legally allowlisted as read-only is NOT a quota grant.
        assert guard.check("GET", ROOT + path) == ("GET", path)
        with pytest.raises(BudgetBackpressure, match=f"^{expected}$"):
            requests.get(ROOT + path)
        assert guard.calls_allowed == 0
        assert guard.calls_blocked == 1
        assert audits == [("GET", path, "BACKPRESSURE")]
        assert guard._scope.read_error_code == expected
        assert not provider_calls
    finally:
        guard.restore()


@pytest.mark.parametrize("path", [
    "/api/1.0/marketdata/searchinstrument",
    "/api/1.0/marketdata/search",
    "/api/1.0/marketdata/bonds/estimate",
    "/api/1.0/configuration/markets",
])
def test_direct_adapter_send_cannot_bypass_unbudgeted_get_guard(wire, path):
    _, provider_calls, _ = wire
    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        prepared = requests.Request("GET", ROOT + path).prepare()
        with pytest.raises(BudgetBackpressure, match="BUDGET_UNVERIFIED"):
            requests.adapters.HTTPAdapter().send(prepared)
        assert guard.calls_allowed == 0
        assert guard.calls_blocked == 1
        assert not provider_calls
    finally:
        guard.restore()


def test_approved_three_endpoint_budget_cannot_be_borrowed_for_catalog_or_history(wire):
    _, provider_calls, _ = wire

    class LeaseAuthority:
        def acquire(self, *args, **kwargs):
            raise AssertionError("Unmetered endpoint must not borrow a Current/Book lease")

    guard = ReadOnlyTransportGuard(budget=LeaseAuthority(), consumer="SCANNER").install()
    try:
        for path in sorted(_GET_PATHS - _BUDGETED_MARKET_GET_PATHS):
            with pytest.raises(BudgetBackpressure, match="BUDGET_UNVERIFIED"):
                requests.get(ROOT + path)
        assert not provider_calls
        assert guard.calls_allowed == 0
    finally:
        guard.restore()


def test_three_metered_market_reads_retain_their_real_lease_contract(wire):
    _, provider_calls, _ = wire
    started, finished, admitted = [], [], []

    class SyntheticLeaseAuthority:
        def acquire(self, endpoint, *, consumer, priority):
            admitted.append((endpoint, consumer, priority))
            return {"allowed": True, "reason": "TEST_ONLY", "lease": f"synthetic-{endpoint}"}

        def wire_scope(self, lease):
            return nullcontext()

        def start(self, lease):
            started.append(lease)

        def finish(self, lease, **kwargs):
            finished.append(lease)

    guard = ReadOnlyTransportGuard(
        budget=SyntheticLeaseAuthority(), consumer="SCANNER",
    ).install()
    try:
        for path in sorted(_BUDGETED_MARKET_GET_PATHS):
            result = requests.get(ROOT + path)
            assert result.status_code == 200
        assert len(provider_calls) == 3
        assert {x[0] for x in admitted} == {"current", "book", "intraday"}
        assert started == finished
        assert len(started) == 3
        assert guard.calls_allowed == 3 and guard.calls_blocked == 0
    finally:
        guard.restore()


def test_login_post_remains_legal_but_is_not_misrepresented_as_metered(wire):
    _, provider_calls, _ = wire
    guard = ReadOnlyTransportGuard(budget=None, consumer="SCANNER").install()
    try:
        result = requests.post(ROOT + "/api/1.0/account/loginapi")
        assert result.status_code == 200
        assert len(provider_calls) == 1
        assert guard.login_calls == 1
        # Login/refresh cost in any PPI global quota is still UNVERIFIED.
        assert guard.calls_allowed == 1
    finally:
        guard.restore()


def test_unknown_provider_route_still_rejected_as_policy_violation(wire):
    _, provider_calls, _ = wire
    guard = ReadOnlyTransportGuard(budget=None).install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation):
            requests.get(ROOT + "/api/1.0/MarketData/Unrecognized")
        assert not provider_calls
    finally:
        guard.restore()
