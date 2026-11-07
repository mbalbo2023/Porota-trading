"""A789 selective #960 regression: held ETF must not spend PPI scanner leases.

Execute exact AST-extracted production functions under synthetic stores and
planner inputs. No SDK import, sockets, HTTP, real provider, host or order route.
The oracle is an independent identity/lease contract, not code-source matching.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import ceil, floor
from pathlib import Path
from types import ModuleType
from unittest import TestCase, main
import json
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
OPEN = datetime(2026, 10, 5, 14, tzinfo=timezone.utc)
EQ = ("A789_EQ", "ACCIONES", "BYMA", "ARS", "A-24HS")
CDR = ("A789_CDR", "CEDEARS", "BYMA", "ARS", "A-24HS")
ETF = ("A789_ETF", "ETFS", "BYMA", "ARS", "A-24HS")
ETF_SIBLING = ("A789_ETF", "ETFS", "BYMA", "USD", "A-24HS")


def hold(family):
    return "ETF_OPERATOR_HOLD" if family in {"ETF", "ETFS"} else ""


def instant(value):
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def hash_content(value):
    encoded = json.dumps(value, sort_keys=True, default=str).encode()
    return sha256(encoded).hexdigest()


def source_namespace(path, functions, env):
    """Compile production AST for named top-level functions with fake edges."""
    source = ROOT / path
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and node.name in functions]
    assert {node.name for node in selected} == set(functions)
    namespace = dict(env)
    exec(compile(ast.fix_missing_locations(ast.Module(
        body=selected, type_ignores=[])), str(source), "exec"), namespace)
    return namespace


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, *_args):
        if "sqlite_master" in sql:
            return FakeRows([(1,)])
        if "cursor_after,detail" in sql:
            return FakeRows([])
        if ("SELECT symbol,asset_class,market,currency,settlement" in sql
                and "FROM paper_positions" in sql):
            return FakeRows([tuple(key) for key in self.store.opened])
        if "SELECT DISTINCT symbol FROM paper_positions" in sql:
            return FakeRows([(key[0],) for key in self.store.opened])
        if "FROM financial_instrument_catalog c" in sql:
            if "c.capability,c.status" not in sql:
                return FakeRows([tuple(key) for key in self.store.catalog])
            return FakeRows([dict(zip(("ticker", "instrument_type", "market", "currency",
                                       "settlement"), key), capability="READY_PAPER_SPOT",
                                  status="AVAILABLE", settlement_source="PPI_FIELD",
                                  metadata_json="{}") for key in self.store.catalog])
        raise AssertionError("Unexpected SQL in synthetic facade: " + sql[:180])


class FakeRows(list):
    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return list(self)


class FakeStore:
    path = "OFFHOST_SYNTHETIC"

    def __init__(self, catalog=(EQ, CDR, ETF), opened=()):
        self.catalog, self.opened = list(catalog), list(opened)

    def connect(self):
        return FakeConnection(self)

    def open_positions(self):
        return [{"symbol": key[0], "asset_class": key[1], "market": key[2],
                 "currency": key[3], "settlement": key[4]}
                for key in self.opened]


class FakeCapacityController:
    def state(self, _time):
        return {"status": "OFF_BASELINE"}


class SyntheticApproval:
    def state(self, _at):
        return {"status": "APPROVED_DYNAMIC"}

    def selection(self, _engine, _baseline, **kwargs):
        return {"dynamic": True, "selected": [ETF, EQ, CDR],
                "opened_priority": list(kwargs["opened"]),
                "entry_identities": [ETF, EQ, CDR],
                "rows": {key: {"state": "HOT"} for key in (ETF, EQ, CDR)},
                "policy_state": {"fingerprint": "A789_SYNTHETIC"},
                "cadence_seconds": 30, "limit": 3}


class ModulePatch:
    def __init__(self):
        self.changes = []

    def set(self, name, value):
        self.changes.append((name, sys.modules.get(name)))
        sys.modules[name] = value

    def restore(self):
        for name, old in reversed(self.changes):
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def select_batch_namespace():
    return source_namespace("cf_intraday_scalping.py",
        ("select_batch", "select_runtime_batch", "_identity"), {
            "register_exact_time": lambda connection: None,
            "SCALPING_STRATEGY_FAMILIES": {"ACCIONES", "CEDEARS", "ETFS"},
            "FOCUS": set(),
            "family_name": lambda name: name,
            "paper_entry_hold_reason": hold,
        })


def observer_namespace(patcher, seen):
    mod = ModuleType("rc6_performance.scanner")

    def sampling_plan(universe, opened, focus, **kwargs):
        seen.append({"universe": list(universe), "opened": list(opened),
                     "focus": list(focus)})
        selected = list(dict.fromkeys([*opened, *universe]))[:kwargs["limit"]]
        return tuple(selected), 0, 1, {"opening_identities": selected,
                                       "feasible": True, "focus_feasible": True,
                                       "active_budget": {"status": "FEASIBLE"}}

    mod.sampling_plan = sampling_plan
    patcher.set("rc6_performance.scanner", mod)
    promotion = ModuleType("rc6_dynamic_universe.promotion")
    promotion.capacity_controller_from_environment = lambda path: FakeCapacityController()
    patcher.set("rc6_dynamic_universe.promotion", promotion)

    def eligible(store):
        return tuple((key[0], key[1], key[4]) for key in store.catalog)

    return source_namespace("bf_production_paper_observer.py",
        ("_cycle_plan", "_approved_equity_selection"), {
            "_eligible_symbols": eligible, "now_iso": lambda: OPEN.isoformat(),
            "INTERVAL": 30, "ACTIVE_SYMBOL_LIMIT": 20,
            "PPI_CALL_BUDGET_SECONDS": 1, "FOCUS_SYMBOLS": (),
            "SIGNAL_MIN_SAMPLES": 15, "SIGNAL_WINDOW_MINUTES": 45,
            "paper_entry_hold_reason": hold,
        })


def orchestrator_namespace(patcher):
    persistence = ModuleType("rc6_shadow_runtime.persistence")
    persistence.streamed_digest = hash_content
    patcher.set("rc6_shadow_runtime.persistence", persistence)
    env = {
        "dataclass": dataclass, "asdict": asdict, "deepcopy": deepcopy,
        "ceil": ceil, "floor": floor, "re": re, "stamp": instant,
        "digest": hash_content, "identity": lambda row: tuple(row[k] for k in (
            "ticker", "instrument_type", "market", "currency", "settlement")),
        "percentile": lambda values, _pct: sorted(values)[len(values)//2] if values else None,
        "number": lambda value: float(value),
        "strategy_route": lambda family, **kwargs: {
            "engine": "SCALPING" if kwargs.get("scalping") else "EQUITY_SPOT",
            "strategy": "SYNTHETIC", "reason_codes": [hold(family)] if hold(family) else [],
            "generic_equity": family in {"ACCIONES", "CEDEARS", "ETFS"}},
        "PIPELINE": ("CATALOG_READY", "STRATEGY_ELIGIBLE", "SIGNAL_READY"),
        "EQUITY_FAMILIES": frozenset({"ACCIONES", "CEDEARS", "ETFS"}),
        "paper_entry_hold_reason": hold,
    }
    return source_namespace("rc6_dynamic_universe/orchestrator.py",
                            ("EnginePolicy", "UniverseOrchestrator"), env)


def test_assets(keys):
    return [dict(zip(("ticker", "instrument_type", "market", "currency",
                      "settlement"), key), capability="READY_PAPER_SPOT",
                 status="AVAILABLE") for key in keys]


def test_frozen():
    payload = {"frozen_at": (OPEN-timedelta(minutes=10)).isoformat(),
               "cutoff": (OPEN-timedelta(minutes=5)).isoformat(),
               "session_open": OPEN.isoformat(), "rows": []}
    return {"payload": payload, "digest": hash_content(payload)}


def test_capacity(limit=3):
    return {"status": "SHADOW_RECOMMENDATION", "safe_limit": limit,
            "generated_at": (OPEN-timedelta(minutes=1)).isoformat(),
            "expires_at": (OPEN+timedelta(minutes=30)).isoformat()}


class A789EtfPpiBudgetSelection(TestCase):
    def setUp(self):
        self.modules = ModulePatch()
        self.addCleanup(self.modules.restore)

    def test_observer_cold_sampler_removes_held_etf_but_keeps_catalog(self):
        seen = []
        ns = observer_namespace(self.modules, seen)
        store = FakeStore()
        selected, total, *_ = ns["_cycle_plan"](store)
        self.assertEqual(total, 3)
        self.assertIn((EQ[0], EQ[1], EQ[4]), selected)
        self.assertNotIn((ETF[0], ETF[1], ETF[4]), seen[0]["universe"])
        self.assertNotIn((ETF[0], ETF[1], ETF[4]), selected)
        self.assertEqual(len(store.catalog), 3)

    def test_observer_open_etf_still_prioritized(self):
        seen = []
        ns = observer_namespace(self.modules, seen)
        selected, *_ = ns["_cycle_plan"](FakeStore(opened=[ETF]))
        self.assertEqual(seen[0]["opened"], [(ETF[0], ETF[1], ETF[4])])
        self.assertIn((ETF[0], ETF[1], ETF[4]), selected)

    def test_observer_stale_dynamic_selection_cannot_reinsert_held_etf(self):
        seen = []
        ns = observer_namespace(self.modules, seen)
        chosen = {"selected": [ETF, EQ, CDR], "opened_priority": [],
                  "entry_identities": [ETF, EQ, CDR],
                  "rows": {key: {"state": "HOT"} for key in (ETF, EQ, CDR)},
                  "policy_state": {"fingerprint": "SYNTHETIC"}, "cadence_seconds": 30,
                  "limit": 3}
        selected, plan = ns["_approved_equity_selection"](FakeStore(), chosen, {})
        self.assertNotIn((ETF[0], ETF[1], ETF[4]), selected)
        self.assertTrue(all(s["identity"] != ETF for s in plan["dynamic_scopes"]))
        chosen["opened_priority"] = [ETF]
        selected, plan = ns["_approved_equity_selection"](FakeStore(opened=[ETF]), chosen, {})
        self.assertIn((ETF[0], ETF[1], ETF[4]), selected)
        self.assertTrue(any(s["identity"] == ETF and s["priority"] == "OPENED_CRITICAL"
                            for s in plan["dynamic_scopes"]))
        self.assertNotIn((ETF[0], ETF[1], ETF[4]), plan["opening_identities"])

    def test_intraday_selector_held_etf_requires_exact_open_identity(self):
        ns = select_batch_namespace()
        rows, _, count = ns["select_batch"](FakeStore(), limit=8)
        self.assertEqual(count, 2)
        self.assertTrue({row["instrument_type"] for row in rows} == {"ACCIONES", "CEDEARS"})
        rows, *_ = ns["select_batch"](FakeStore(opened=[ETF_SIBLING]), limit=8)
        self.assertNotIn(ETF, [ns["_identity"](row) for row in rows])
        rows, *_ = ns["select_batch"](FakeStore(opened=[ETF]), limit=8)
        self.assertIn(ETF, [ns["_identity"](row) for row in rows])

    def test_approved_intraday_selection_cannot_reinsert_held_etf(self):
        promotion = ModuleType("rc6_dynamic_universe.promotion")
        promotion.capacity_controller_from_environment = lambda path: FakeCapacityController()
        self.modules.set("rc6_dynamic_universe.promotion", promotion)
        ns = select_batch_namespace()
        selected, _, _, state = ns["select_runtime_batch"](
            FakeStore(), limit=8, at=OPEN, controller=SyntheticApproval())
        self.assertTrue(state["dynamic"])
        self.assertNotIn(ETF, [ns["_identity"](row) for row in selected])
        selected, *_ = ns["select_runtime_batch"](
            FakeStore(opened=[ETF]), limit=8, at=OPEN, controller=SyntheticApproval())
        self.assertIn(ETF, [ns["_identity"](row) for row in selected])

    def test_orchestrator_skips_held_etf_but_open_exit_survives(self):
        ns = orchestrator_namespace(self.modules)
        orch = ns["UniverseOrchestrator"](test_assets([ETF]))
        without = orch.plan(at=OPEN, session_open=OPEN,
                            frozen=test_frozen(), capacity=test_capacity())
        self.assertEqual(without["catalog_ready_count"], 1)
        self.assertEqual(without["strategy_eligible_count"], 0)
        self.assertNotIn(ETF, [tuple(key) for key in without["selected"]])
        self.assertIn("ETF_OPERATOR_HOLD", without["telemetry"][0]["rejection_reason"])
        self.assertIsNone(without["discovery"]["touched_fraction_since_open"])
        with_open = orch.plan(at=OPEN, session_open=OPEN, opened=[ETF],
                              frozen=test_frozen(), capacity={"status": "NO_VERIFICADO", "safe_limit": 0})
        self.assertEqual(with_open["opened_priority"], [ETF])
        self.assertIn(ETF, [tuple(key) for key in with_open["selected"]])
        self.assertEqual(with_open["real_orders_sent"], 0)


if __name__ == "__main__":
    main()
