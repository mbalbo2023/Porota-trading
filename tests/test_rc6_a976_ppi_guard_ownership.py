"""A977 reconstruction: synthetic HTTP custody tests; no provider access."""
import threading

import pytest
import requests

from bd_ppi_readonly_guard import ReadOnlyTransportGuard, ReadOnlyPolicyViolation


@pytest.mark.parametrize("child_first", [False, True])
def test_second_guard_cannot_replace_global_hooks(child_first):
    class Child(ReadOnlyTransportGuard):
        pass
    first = (Child if child_first else ReadOnlyTransportGuard)().install()
    second = (ReadOnlyTransportGuard if child_first else Child)()
    hooks = requests.sessions.Session.request, requests.adapters.HTTPAdapter.send
    try:
        with pytest.raises(ReadOnlyPolicyViolation):
            second.install()
        assert hooks == (requests.sessions.Session.request, requests.adapters.HTTPAdapter.send)
    finally:
        second.restore()
        first.restore()


def test_restore_does_not_overwrite_foreign_hook(monkeypatch):
    guard = ReadOnlyTransportGuard().install()
    owned = requests.adapters.HTTPAdapter.send
    foreign = lambda *a, **kw: None
    try:
        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", foreign)
        with pytest.raises(ReadOnlyPolicyViolation):
            guard.restore()
        assert requests.adapters.HTTPAdapter.send is foreign
    finally:
        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", owned)
        guard.restore()


def test_restore_retains_guard_during_http_invocation(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    errors = []
    def wire(*a, **kw):
        entered.set()
        assert release.wait(3)
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", wire)
    guard = ReadOnlyTransportGuard().install()
    request = requests.Request("POST", "https://clientapi.portfoliopersonal.com/api/1.0/account/loginapi").prepare()
    def run():
        try:
            requests.adapters.HTTPAdapter().send(request)
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(ReadOnlyPolicyViolation):
            guard.restore()
        with pytest.raises(ReadOnlyPolicyViolation):
            ReadOnlyTransportGuard().install()
    finally:
        release.set()
        worker.join(3)
        guard.restore()
    assert not worker.is_alive()
    assert errors == []


@pytest.mark.parametrize("hook", ["send", "request"])
@pytest.mark.parametrize("reinstall", [False, True])
def test_saved_callable_expires_after_restore_and_same_instance_reinstall(monkeypatch, hook, reinstall):
    owner = requests.adapters.HTTPAdapter if hook == "send" else requests.sessions.Session
    calls = []
    def transport(label):
        def call(*a, **kw):
            calls.append(label)
            response = requests.Response()
            response.status_code = 200
            response._content = b"{}"
            return response
        return call
    monkeypatch.setattr(owner, hook, transport("OLD_TRANSPORT"))
    guard = ReadOnlyTransportGuard().install()
    stale = getattr(owner, hook)
    request = requests.Request("POST", "https://clientapi.portfoliopersonal.com/api/1.0/account/loginapi").prepare()
    def invoke(call):
        if hook == "send":
            return call(requests.adapters.HTTPAdapter(), request)
        return call(requests.Session(), "POST", request.url)
    guard.restore()
    try:
        if not reinstall:
            with pytest.raises(ReadOnlyPolicyViolation):
                invoke(stale)
            assert calls == []
            return
        monkeypatch.setattr(owner, hook, transport("NEW_TRANSPORT"))
        guard.install()
        with pytest.raises(ReadOnlyPolicyViolation):
            invoke(stale)
        assert calls == []
        invoke(getattr(owner, hook))
        assert calls == ["NEW_TRANSPORT"]
    finally:
        guard.restore()


def test_contenders_have_only_one_process_global_guard():
    start, finish = threading.Barrier(8), threading.Barrier(8)
    admitted, blocked, errors = [], [], []
    def run():
        guard = ReadOnlyTransportGuard()
        start.wait(3)
        try:
            guard.install()
            admitted.append(guard)
        except ReadOnlyPolicyViolation:
            blocked.append(guard)
        except BaseException as error:
            errors.append(error)
        finally:
            finish.wait(3)
            guard.restore()
    workers = [threading.Thread(target=run) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
    assert not any(worker.is_alive() for worker in workers)
    assert errors == []
    assert len(admitted) == 1 and len(blocked) == 7
