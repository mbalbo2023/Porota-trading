"""OFFHOST contract for the existing PAPER performance child wiring.

Tests do not start a broker, worker process, provider, or trading database.
The subprocess lifecycle and capture/report internals have their own suites.
"""

import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def test_performance_flag_dispatches_once_without_paper_or_provider_side_effects(
    tmp_path, monkeypatch
):
    """Removing or misrouting --performance-worker must break this contract."""
    import bv_paper_runtime as runtime
    from rc6_performance import capture

    source = tmp_path / "source.sqlite"
    calls = []

    def forbidden(*_args, **_kwargs):
        pytest.fail("PERFORMANCE_DISPATCH_TOUCHED_BROKER_DB_PROCESS_OR_NETWORK")

    def captured_worker(path, stop):
        calls.append((path, stop))

    monkeypatch.setattr(runtime, "database_path", lambda: source)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(runtime, "PaperStore", forbidden)
    monkeypatch.setattr(runtime, "runtime_store", forbidden)
    monkeypatch.setattr(runtime, "broker_from_environment", forbidden)
    monkeypatch.setattr(runtime.sqlite3, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(capture, "run_worker", captured_worker)

    assert runtime.main(["--performance-worker"]) == 0
    assert len(calls) == 1
    assert calls[0][0] == source
    assert isinstance(calls[0][1], threading.Event)
    assert not calls[0][1].is_set()
    assert not source.exists()


def test_parent_registers_one_performance_child_after_startup_grace(tmp_path, monkeypatch):
    """The PAPER parent must register one worker, with delayed startup."""
    import bv_paper_runtime as runtime

    source = tmp_path / "paper.sqlite"
    store = SimpleNamespace(path=str(source))
    registered = []
    clock_calls = []
    original_children = runtime.ChildProcesses

    def forbidden(*_args, **_kwargs):
        pytest.fail("PARENT_WIRING_TOUCHED_BROKER_DB_PROCESS_OR_NETWORK")

    def children_factory(commands):
        child = original_children(commands, clock=lambda: 100.0)
        registered.append(child)
        return child

    def observe_clock(current_store, children, stop):
        clock_calls.append((current_store, children, stop))

    observer = ModuleType("bf_production_paper_observer")
    observer._support_schema = lambda _store: None
    monkeypatch.setitem(sys.modules, "bf_production_paper_observer", observer)
    monkeypatch.setattr(runtime, "runtime_store", lambda: store)
    monkeypatch.setattr(runtime, "PaperStore", forbidden)
    monkeypatch.setattr(runtime, "broker_from_environment", forbidden)
    monkeypatch.setattr(runtime, "run_clock", observe_clock)
    monkeypatch.setattr(runtime, "ChildProcesses", children_factory)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(runtime.sqlite3, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "0")
    monkeypatch.setenv(runtime.DB_ENV, str(source))

    assert runtime.main([]) == 0
    assert len(registered) == 1
    assert len(clock_calls) == 1
    child = registered[0]
    assert clock_calls[0][0] is store
    assert clock_calls[0][1] is child
    assert isinstance(clock_calls[0][2], threading.Event)
    expected = [sys.executable, str(Path(runtime.__file__).resolve()), "--performance-worker"]
    performance = [(name, command) for name, command in child.commands.items()
                   if "--performance-worker" in command]
    assert performance == [("performance", expected)]
    assert child.next_start["scanner"] == 100.0
    assert child.next_start["performance"] == 125.0
    assert not source.exists()
