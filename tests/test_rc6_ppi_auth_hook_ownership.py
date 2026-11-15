"""Process-global requests hook custody: AUTH HOLD must survive overlap.

All admissible sends terminate at a monkeypatched, in-memory adapter.
"""

import threading

import pytest
import requests

from bd_ppi_readonly_guard import (
    ProductionMarketReader, ReadOnlyPolicyViolation, ReadOnlyTransportGuard,
)
from ppi_synthetic_budget_fixtures import SyntheticWireLease


BASE = "https://clientapi.portfoliopersonal.com/api/1.0/"


@pytest.fixture
def fake_wire(monkeypatch):
    calls = []

    def send(_adapter, request, **_kwargs):
        calls.append((request.method, request.url))
        result = requests.Response()
        result.status_code = 200
        result._content = b'{}'
        result.request = request
        return result

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return calls


def test_second_guard_is_denied_without_replacing_first_hooks(fake_wire):
    first = ReadOnlyTransportGuard().install()
    owned_request, owned_send = requests.sessions.Session.request, requests.adapters.HTTPAdapter.send
    second = ReadOnlyTransportGuard()
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_HTTP_GUARD_ALREADY_OWNED"):
            second.install()
        assert requests.sessions.Session.request is owned_request
        assert requests.adapters.HTTPAdapter.send is owned_send
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_AUTHENTICATION_AUTHORITY_UNVERIFIED"):
            requests.post(BASE + "account/loginapi")
        assert fake_wire == []
    finally:
        second.restore()
        first.restore()


def test_two_readers_cannot_bypass_auth_hold_by_out_of_order_close(fake_wire):
    first = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET", budget=SyntheticWireLease())
    second = None
    try:
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_HTTP_GUARD_ALREADY_OWNED"):
            second = ProductionMarketReader("FAKE_KEY", "FAKE_SECRET",
                                            budget=SyntheticWireLease())
        first.close()
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_AUTHENTICATION_AUTHORITY_UNVERIFIED"):
            # Once the sole reader closes, a new guard must be installed to
            # take custody again. No stale reader can authorize a POST.
            guard = ReadOnlyTransportGuard().install()
            try:
                requests.post(BASE + "account/loginapi")
            finally:
                guard.restore()
        assert fake_wire == []
    finally:
        if second is not None:
            second.close()
        first.close()


@pytest.mark.parametrize("hook", ["request", "send"])
def test_saved_hook_callable_cannot_send_after_restore(fake_wire, hook):
    guard = ReadOnlyTransportGuard(budget=SyntheticWireLease()).install()
    owner = requests.sessions.Session if hook == "request" else requests.adapters.HTTPAdapter
    stale = getattr(owner, hook)
    guard.restore()
    request = requests.Request("GET", BASE + "MarketData/Current").prepare()
    with pytest.raises(ReadOnlyPolicyViolation, match="PPI_HTTP_GUARD_INSTALLATION_EXPIRED"):
        if hook == "request":
            stale(requests.Session(), "GET", request.url)
        else:
            stale(requests.adapters.HTTPAdapter(), request)
    assert fake_wire == []


def test_foreign_hook_mutation_is_not_overwritten_on_restore(fake_wire):
    guard = ReadOnlyTransportGuard().install()
    owned = requests.adapters.HTTPAdapter.send
    foreign = lambda *_args, **_kwargs: pytest.fail("foreign transport must not be invoked")
    try:
        requests.adapters.HTTPAdapter.send = foreign
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_HTTP_GUARD_CUSTODY_UNVERIFIED"):
            guard.restore()
        assert requests.adapters.HTTPAdapter.send is foreign
    finally:
        requests.adapters.HTTPAdapter.send = owned
        guard.restore()


def test_restore_denied_during_inflight_fake_wire(fake_wire, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_send(_adapter, request, **_kwargs):
        entered.set()
        assert release.wait(3)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.request = request
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", slow_send)
    guard = ReadOnlyTransportGuard(budget=SyntheticWireLease()).install()
    errors = []

    def worker():
        try:
            requests.get(BASE + "MarketData/Current")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert entered.wait(3)
        with pytest.raises(ReadOnlyPolicyViolation, match="PPI_HTTP_GUARD_INFLIGHT"):
            guard.restore()
    finally:
        release.set()
        thread.join(timeout=3)
        guard.restore()
    assert not thread.is_alive() and errors == []


def test_guard_reinstall_and_restore_are_idempotent(fake_wire):
    original_request = requests.sessions.Session.request
    original_send = requests.adapters.HTTPAdapter.send
    guard = ReadOnlyTransportGuard().install()
    hooked_request, hooked_send = requests.sessions.Session.request, requests.adapters.HTTPAdapter.send
    try:
        assert guard.install() is guard
        assert requests.sessions.Session.request is hooked_request
        assert requests.adapters.HTTPAdapter.send is hooked_send
    finally:
        guard.restore()
        guard.restore()
    assert requests.sessions.Session.request is original_request
    assert requests.adapters.HTTPAdapter.send is original_send
