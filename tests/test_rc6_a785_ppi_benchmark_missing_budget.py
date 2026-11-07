"""A785: production benchmark must never login before shared PPI budget proof.

AST-extracts the exact production functions into an OFFHOST environment, uses
fake readers/budgets and no SDK sockets, host files, PPI Watch, or real orders.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from json import JSONDecodeError
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest

import requests


SOURCE = Path(__file__).resolve().parents[1] / "rc6_dynamic_universe" / "benchmark.py"
IDENTITY_FIELDS = ("ticker", "instrument_type", "market", "currency", "settlement")


class SyntheticBudgetBackpressure(RuntimeError):
    pass


class SyntheticClock:
    def __init__(self):
        self.start = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
        self.seconds = 0.0

    def now(self):
        from datetime import timedelta
        return self.start + timedelta(seconds=self.seconds)

    def monotonic(self):
        return self.seconds

    def pause(self, delay):
        self.seconds += delay


def rows(count=1):
    return [
        {"ticker": f"TEST{index:03d}", "instrument_type": "ACCIONES",
         "market": "BYMA", "currency": "ARS", "settlement": "A-24HS"}
        for index in range(count)
    ]


@contextmanager
def fake_wire_gate(**_kwargs):
    yield {"requests": 0, "status_codes": Counter(), "retries": 0}


def production_functions(*, phase="OPEN"):
    """Compile actual production code; replace only external/side-effect edges."""
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(SOURCE))
    names = {"BenchmarkReadError", "error_code", "_config", "_candidates", "run_benchmark"}
    selected = [node for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    assert len(selected) == len(names)
    def stamp(value):
        return value if isinstance(value, datetime) else datetime.fromisoformat(value)
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
    def identity(row):
        return tuple(row[k] for k in IDENTITY_FIELDS)
    def number(value, *, minimum):
        result = float(value)
        if result < minimum:
            raise ValueError("BOUNDS")
        return result
    ns = {
        "__name__": "rc6_a785_offhost_benchmark",
        "Counter": Counter,
        "defaultdict": defaultdict,
        "contextmanager": contextmanager,
        "datetime": datetime,
        "timezone": timezone,
        "JSONDecodeError": JSONDecodeError,
        "requests": requests,
        "SimpleNamespace": SimpleNamespace,
        "time": time,
        "BATCHES": (20, 40, 60, 80, 100),
        "ENDPOINTS": ("current", "book", "intraday"),
        "IDENTITY_FIELDS": IDENTITY_FIELDS,
        "ProductionMarketReader": object,  # every call supplies a fake factory
        "BudgetBackpressure": SyntheticBudgetBackpressure,
        "ReadOnlyPolicyViolation": type("ReadOnlyPolicyViolation", (RuntimeError,), {}),
        "session_invalid": lambda *_: False,
        "instrument_not_found": lambda *_: False,
        "byma_paper_spot_phase": lambda _at: phase,
        "byma_schedule_status": lambda: {"clock": "SYNTHETIC"},
        "stamp": stamp,
        "digest": digest,
        "identity": identity,
        "number": number,
        "percentile": lambda *_: 0,
        "_wire_gate": fake_wire_gate,
        "_summary": lambda observations, elapsed: {"requests": len(observations)},
        "_observation": lambda *_: {},
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


def budget_module(monkeypatch, budget):
    mod = ModuleType("rc6_ppi_global_budget")
    mod.BudgetBackpressure = SyntheticBudgetBackpressure
    mod.budget_from_environment = lambda: budget
    monkeypatch.setitem(sys.modules, mod.__name__, mod)


def benchmark(ns, reader_factory, clock, *, candidates=None, **kwargs):
    return ns["run_benchmark"](
        candidates or rows(), api_key="FAKE_KEY", api_secret="FAKE_SECRET",
        reader_factory=reader_factory, clock=clock.now,
        monotonic=clock.monotonic, pause=clock.pause,
        batches=(20,), endpoints=("current",), **kwargs,
    )


def _check_missing_budget_never_constructs_reader_or_logs_in(monkeypatch):
    """Catches reader_factory invocation even when no shared ledger exists."""
    budget_module(monkeypatch, None)
    ns, clock = production_functions(), SyntheticClock()
    events = []
    class Reader:
        metrics = {}
        def login_once(self): events.append("login")
        def close(self): events.append("close")
    def reader_factory(*_args, **_kwargs):
        events.append("construct")
        return Reader()
    result = benchmark(ns, reader_factory, clock)
    assert result["stop_reason"] == "PPI_SHARED_BUDGET_UNAVAILABLE"
    assert result["real_orders_sent"] == 0
    assert events == []


def _check_explicit_budget_is_forwarded_and_env_not_consulted(monkeypatch):
    """Catches ignored explicit budget and duplicate or speculative login."""
    mod = ModuleType("rc6_ppi_global_budget")
    mod.BudgetBackpressure = SyntheticBudgetBackpressure
    def forbidden_loader():
        raise AssertionError("EXPLICIT_BUDGET_MUST_BYPASS_ENV")
    mod.budget_from_environment = forbidden_loader
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    ns, clock = production_functions(), SyntheticClock()
    budget = object()
    events = []
    class Reader:
        metrics = {"login_calls": 1, "http_allowed": 0}
        def login_once(self): events.append("login")
        def close(self): events.append("close")
    def reader_factory(_key, _secret, **kwargs):
        assert kwargs["budget"] is budget
        events.append("construct")
        return Reader()
    result = benchmark(ns, reader_factory, clock, budget=budget)
    assert result["stop_reason"] is None
    assert result["real_orders_sent"] == 0
    assert events == ["construct", "login", "close"]


def _check_budget_backpressure_is_terminal_and_sanitized(monkeypatch):
    """Catches retrying a denied lease or leaking provider exception payload."""
    budget_module(monkeypatch, object())
    ns, clock = production_functions(), SyntheticClock()
    calls = []
    class Reader:
        metrics = {"login_calls": 1, "http_allowed": 0}
        def login_once(self): calls.append("login")
        def current(self, *_args): 
            calls.append("current")
            raise SyntheticBudgetBackpressure("PPI_BUDGET_LEASE_UNVERIFIED")
        def close(self): calls.append("close")
    def reader_factory(_key, _secret, **kwargs):
        assert kwargs["budget"] is not None
        return Reader()
    result = benchmark(ns, reader_factory, clock, candidates=rows(20))
    assert result["stop_reason"] == "PPI_SHARED_BUDGET_UNVERIFIED"
    assert result["real_orders_sent"] == 0
    assert calls == ["login", "current", "close"]
    assert "PPI_BUDGET_LEASE_UNVERIFIED" not in json.dumps(result)


def _check_closed_session_never_reads_ledger_or_reader(monkeypatch):
    """Existing market calendar veto takes precedence over credentials/budget."""
    mod = ModuleType("rc6_ppi_global_budget")
    mod.BudgetBackpressure = SyntheticBudgetBackpressure
    def forbidden():
        raise AssertionError("CLOSED_BUDGET_LOOKUP")
    mod.budget_from_environment = forbidden
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    ns, clock = production_functions(phase="CLOSED"), SyntheticClock()
    result = benchmark(ns, forbidden, clock)
    assert result["stop_reason"] == "MARKET_NOT_OPEN"
    assert result["real_orders_sent"] == 0


class ModulePatch:
    """Scope module overrides and restore interpreter imports after each case."""
    def __init__(self):
        self._old = []

    def setitem(self, mapping, key, value):
        self._old.append((mapping, key, mapping.get(key)))
        mapping[key] = value

    def restore(self):
        for mapping, key, old in reversed(self._old):
            if old is None:
                mapping.pop(key, None)
            else:
                mapping[key] = old


class BenchmarkBudgetTests(unittest.TestCase):
    def setUp(self):
        self.patcher = ModulePatch()
        self.addCleanup(self.patcher.restore)

    def test_missing_budget_never_constructs_reader_or_logs_in(self):
        _check_missing_budget_never_constructs_reader_or_logs_in(self.patcher)

    def test_explicit_budget_is_forwarded_and_env_not_consulted(self):
        _check_explicit_budget_is_forwarded_and_env_not_consulted(self.patcher)

    def test_budget_backpressure_is_terminal_and_sanitized(self):
        _check_budget_backpressure_is_terminal_and_sanitized(self.patcher)

    def test_closed_session_never_reads_ledger_or_reader(self):
        _check_closed_session_never_reads_ledger_or_reader(self.patcher)
