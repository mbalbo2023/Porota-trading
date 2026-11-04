"""A652 OFFHOST: CAUCIONES fee hold must not starve closed catalog repair.

No PPI GET, runtime DB, broker, Watch, orders, or host privileges are used.
"""

import ast
import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch


# Compile only these two local, side-effect-free functions; importing the full
# production observer pulls the provider SDK in environments without requests.
# This fixture intentionally has NO PPI reader, network client or broker.
SOURCE = Path(__file__).resolve().parents[1] / "bf_production_paper_observer.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
DECLARATIONS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}
ENV = {"time": time, "json": json, "sqlite3": sqlite3, "sys": sys,
       "OFFHOURS_RECONCILE_SECONDS": 3600,
       "COMPLEMENTARY_RECONCILE_SECONDS": 300}
for name in ("_periodic_complementary_reconcile", "_caucion_blocked_closed_catalog_tick"):
    exec(compile(ast.Module(body=[DECLARATIONS[name]], type_ignores=[]),
                 str(SOURCE), "exec"), ENV)


def tick(store, phase, last_attempt):
    return ENV["_caucion_blocked_closed_catalog_tick"](store, phase, last_attempt)


def _blocked_branch():
    tree = DECLARATIONS["run"]
    return next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "caucion_ledger_blocked"
        and any(isinstance(item, ast.Continue) for item in node.body)
    )


def test_global_caucion_hold_still_has_local_closed_maintenance_before_continue():
    blocked = _blocked_branch()
    assert isinstance(blocked.body[-1], ast.Continue)
    calls = [
        node.func.id for statement in blocked.body[:-1]
        for node in ast.walk(statement)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "_caucion_blocked_closed_catalog_tick" in calls
    assert not ({"ProductionMarketReader", "login_once", "broker_from_environment"}
                & set(calls))


def test_closed_blocked_ledger_runs_only_local_reconcile_and_preserves_gate():
    store = Mock()
    writer = Mock(return_value=1730754900.0)
    with patch.dict(ENV, {"_periodic_complementary_reconcile": writer}):
        completed = tick(store, "CLOSED", 0.0)
    assert completed == 1730754900.0
    writer.assert_called_once_with(store, "CLOSED", 0.0)
    store.event.assert_not_called()


def test_open_and_equity_t1_hold_skip_writer_even_when_ledger_blocked():
    store = Mock()
    writer = Mock()
    with patch.dict(ENV, {"_periodic_complementary_reconcile": writer}):
        for phase in ("OPEN", "EQUITY_T1_READONLY", "PREOPEN"):
            assert tick(store, phase, 42.0) == 42.0
    writer.assert_not_called()
    store.event.assert_not_called()


def test_sqlite_interrupt_does_not_reopen_admission_or_enter_hot_retry():
    store = Mock()
    writer = Mock(side_effect=sqlite3.OperationalError("SQLITE_INTERRUPT"))
    with (patch.dict(ENV, {"_periodic_complementary_reconcile": writer}),
          patch.object(time, "time", return_value=1740000000.0)):
        throttled = tick(store, "CLOSED", 0.0)
    assert throttled == 1740000000.0  # retry held for offhours TTL
    writer.assert_called_once_with(store, "CLOSED", 0.0)
    store.event.assert_called_once_with(
        "CAUCION_BLOCKED_CATALOG_RECONCILE_ERROR", "SQLITE_OPERATIONAL_ERROR")


def test_even_failed_event_write_cannot_bypass_caucion_admission_hold(capsys):
    store = Mock()
    store.event.side_effect = sqlite3.OperationalError("database is locked")
    writer = Mock(side_effect=sqlite3.OperationalError("database is locked"))
    with (patch.dict(ENV, {"_periodic_complementary_reconcile": writer}),
          patch.object(time, "time", return_value=1740000001.0)):
        assert tick(store, "CLOSED", 0.0) == 1740000001.0
    assert capsys.readouterr().err == (
        "CAUCION_BLOCKED_CATALOG_RECONCILE_ERROR:SQLITE_OPERATIONAL_ERROR\n")


def test_real_periodic_called_without_reader_or_provider_catches_only_local_tools():
    store = Mock()
    reconcile = Mock(return_value=0)
    release = Mock(return_value={})
    with (patch.dict(ENV, {"_reconcile_complementary_catalog": reconcile,
                           "_release_idle_catalog_heap": release}),
          patch.object(time, "time", return_value=1740000100.0)):
        assert tick(store, "CLOSED", 0.0) == 1740000100.0
    reconcile.assert_called_once_with(store)
    release.assert_called_once_with()
    store.event.assert_called_once()
    assert store.event.call_args.args[0] == "CLOSED_CATALOG_HEAP_RELEASE"
