#!/usr/bin/env python3
"""Reproduce three original RC6 findings on a complete, unmodified c27 archive.

Only HTTPAdapter.send, a caller wall clock and explicit offline inputs are
substituted. This runner never installs product overlays or contacts a provider.
The result is historical PAPER evidence, not an attestation of current runtime.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import urlsplit


COMMIT = "c27dfd963c4fe83465c0f2105347e974fbbe6356"
TREE = "bf3cf193434641aa89e4c746b26c77aec5d1d2b2"
ARCHIVE_SHA256 = "11681d9682ff41ff595e10550d0e3fb4d861161e2fda9237930e6fd32dcc61d9"
NETWORK_EVENTS = []


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AssertionError(f"Archive contains a symlink: {path}")
        if path.is_file():
            stat = path.stat()
            result[str(path.relative_to(root))] = {
                "sha256": sha(path.read_bytes()), "bytes": stat.st_size,
                "mode": oct(stat.st_mode & 0o777), "mtime_ns": stat.st_mtime_ns,
            }
    return result


def deny_network(event, arguments):
    if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
        NETWORK_EVENTS.append({"event": event, "stack": traceback.format_stack(limit=12)})
        raise PermissionError("OFFLINE_PROBE_FORBIDS_NETWORK")


def original_u01(driver, root):
    from rc6_ppi_global_budget import BudgetBackpressure, GlobalPPIBudget, SCHEMA
    raw = driver.read_bytes()
    tree = ast.parse(raw, filename=str(driver))
    names = {"Clock", "policy", "single_flight_priority_probe"}
    definitions = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    assert {node.name for node in definitions} == names
    # Execute the original definitions intact, avoiding the obsolete hardcoded
    # candidate sys.path assignment in the original script's module prologue.
    context = dict(BudgetBackpressure=BudgetBackpressure, GlobalPPIBudget=GlobalPPIBudget,
                   SCHEMA=SCHEMA, datetime=datetime, timedelta=timedelta,
                   deepcopy=deepcopy, threading=threading, time=time)
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(driver), "exec"), context)
    result = context["single_flight_priority_probe"](root)
    return {
        "evidence_kind": "OLD_NATIVE_COALESCER_RED_ORIGINAL_DRIVER_DEFINITIONS",
        "driver_path": str(driver), "driver_sha256": sha(raw),
        "intact_definition_sha256": {node.name: sha(ast.get_source_segment(raw.decode(), node).encode()) for node in definitions},
        "result": result,
        "boundary": "Three six-second-separated cycles; native SQLite coalescer with modeled fetch. Neither three 30-second windows nor SDK wire.",
    }


def sdk_u01(root, owner_delay):
    import pytest
    import requests
    import bf_production_paper_observer as observer
    from bd_ppi_readonly_guard import ProductionMarketReader
    from bq_exit_policy import PaperSessionPolicy
    from bv_paper_runtime import collect_exit_books
    from rc6_ppi_global_budget import GlobalPPIBudget, SCHEMA
    from tests.test_issue465_budget_adversarial import native_opened_store
    from tests.test_rc6_ppi_capacity_benchmark import wire

    patch = pytest.MonkeyPatch()
    clock, calls, behavior = wire.__wrapped__(patch)
    behavior["latency"] = 0
    native_send = requests.adapters.HTTPAdapter.send
    entered = threading.Event()
    wire_trace = []

    def timed_send(adapter, request, **kwargs):
        endpoint = urlsplit(request.url).path.rsplit("/", 1)[-1].lower()
        if endpoint == "book":
            wire_trace.append({"wire": len(wire_trace) + 1, "sent_monotonic": time.monotonic(), "url": request.url})
            entered.set()
            time.sleep(owner_delay)
        response = native_send(adapter, request, **kwargs)
        if endpoint == "book":
            payload = response.json()
            payload["bids"][0]["quantity"] = 1000
            payload["offers"][0]["quantity"] = 1000
            response._content = json.dumps(payload).encode()
            wire_trace[-1]["body_drained_monotonic"] = time.monotonic()
        return response

    patch.setattr(requests.adapters.HTTPAdapter, "send", timed_send)
    patch.setattr(observer, "now_iso", lambda: clock.now().isoformat())
    reader = None
    try:
        store, _broker = native_opened_store(root, clock, patch, count=1)
        policy = {"schema": SCHEMA, "version": "offline-u01-feasible-one-position",
            "recommendation_digest": "a" * 64, "configuration_fingerprint": "b" * 64,
            "window_seconds": 30, "endpoint_limits": {"current": 20, "book": 7, "intraday": 20},
            "global_limit": 47, "max_parallel_requests": 1, "safety_reserve": "OFFLINE_SYNTHETIC_NOT_PROVIDER_AUTHORITY",
            "priority_reserves": {"EXIT_CRITICAL": {"book": 6}}, "open_positions_count": 1,
            "exit_demand": {"book": 6}, "expires_at": (clock.now() + timedelta(hours=2)).isoformat(),
            "critical_book_seconds": 5, "lease_seconds": 60, "breaker_seconds": 60,
            "session_breaker_seconds": 900, "server_error_threshold": 2, "maximum_bytes": 8 * 1024**2}
        budget = GlobalPPIBudget(root / "budget.sqlite", policy, clock=clock.now)
        reader = ProductionMarketReader("OFFLINE_KEY", "OFFLINE_SECRET", budget=budget, consumer="SCANNER")
        reader.login_once()
        identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
        cycles = []
        for index in range(3):
            entered.clear()
            owner_result = {}

            def owner():
                try:
                    with reader.read_scope(priority="OPENED_CRITICAL", identity=identity):
                        owner_result["book"] = reader.book("GGAL", "ACCIONES", "A-24HS")
                except BaseException as error:
                    owner_result["error"] = f"{type(error).__name__}:{error}"

            thread = threading.Thread(target=owner, name=f"offline-u01-owner-{index}")
            thread.start()
            assert entered.wait(2), "Owner did not reach native guarded SDK wire"
            begun = time.monotonic()
            failures = collect_exit_books(reader, store, PaperSessionPolicy(), clock.now().isoformat())
            completed = time.monotonic()
            thread.join(2)
            assert not thread.is_alive() and "error" not in owner_result, owner_result
            assert len(wire_trace) == index + 1, "Unexpected duplicate wire or cache reuse across windows"
            cycles.append({"window": index + 1, "as_of": clock.now().isoformat(),
                "exit_failures": failures, "follower_started_monotonic": begun,
                "follower_completed_monotonic": completed, "exit_elapsed_seconds": completed - begun,
                "owner_result": owner_result, "wire_count_cumulative": len(wire_trace),
                "budget_metrics": budget.metrics()})
            clock.advance(31)
        with store.connect() as connection:
            events = [dict(row) for row in connection.execute("SELECT * FROM paper_events WHERE event_type='EXIT_BOOK_ERROR' ORDER BY id")]
        return {"evidence_kind": "OLD_NATIVE_SDK_CALLER_RED" if owner_delay > .05 else "POSITIVE_CONTROL_PRESERVED",
            "caller": "ProductionMarketReader.book/read_scope -> native ReadOnlyTransportGuard -> GlobalPPIBudget; bv_paper_runtime.collect_exit_books",
            "identity": list(identity), "consumer_label": "SCANNER; per-call priority is thread scoped",
            "owner_body_delay_seconds": owner_delay, "policy": policy, "cycles": cycles,
            "wire_trace": wire_trace, "native_exit_error_events": events,
            "boundary": "Native SDK, collector, lease/debt and body-drain path; only HTTPAdapter.send is fake. Logical windows are 31 seconds apart; the wire delay uses the real monotonic clock. No real PPI latency or provider contract is certified."}
    finally:
        if reader is not None:
            reader.close()
        patch.undo()


def u07(root):
    import pytest
    import bf_production_paper_observer as observer
    from be_paper_engine import D, PaperBroker, PaperStore, Quote
    from bs_instrument_contracts import InstrumentContract
    from rc6_paper_family_lifecycle import future_positions
    from rc6_ppi_future_contract_policy import standard_dlr_terms
    from rc6_ppi_global_budget import GlobalPPIBudget, RuntimePPIBudget, budget_policy
    from rc6_dynamic_universe.common import digest
    from rc6_dynamic_universe.promotion import DEFAULT_POLICY, APPROVAL_SCHEMA, RuntimeCapacityController, build_recommendation
    from tests.test_production_paper_v1634 import quote
    from tests.test_rc6_ppi_capacity_benchmark import wire, measure

    patch = pytest.MonkeyPatch()
    patch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    patch.setenv("PAPER_EMERGENCY_MAX_OPEN_POSITIONS", "AUTO")
    try:
        opening = "2026-08-24T14:00:00+00:00"
        store = PaperStore(str(root / "mixed-paper.sqlite"))
        observer._support_schema(store)
        broker = PaperBroker(store, initial_cash="100000000", risk_pct=".002", max_positions=10,
            participation="1", max_position_pct="1", max_total_exposure_pct="1",
            clock_fn=lambda: opening, require_supervisor=False, ai_mode="OFF", economics_mode="SHADOW",
            session_policy=None, daily_loss_pct="5", daily_soft_stop_pct="4")
        openings = []
        for index in range(5):
            q = quote(symbol=f"SPOT{index}", at=opening)
            store.add_quote(q)
            outcome = broker._open(q, D(".8"), {})
            assert outcome[0], outcome
            openings.append({"family": "ACCIONES", "symbol": q.symbol, "outcome": outcome})
        for month in ("AGO", "SEP", "OCT", "NOV", "DIC"):
            ticker = f"DLR/{month}26"
            terms = standard_dlr_terms(ticker)
            contract = InstrumentContract(ticker, "FUTUROS", "ARS", "A3", "INMEDIATA", D("1000"), D("1"),
                "OFFLINE_SYNTHETIC_CONTRACT_INPUT_NOT_DATED_PROVIDER_EVIDENCE",
                expires_at=terms["expires_at"], minimum_quantity=D("1"),
                paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE", paper_margin_rate=D("1"), underlying=terms["underlying"])
            q = Quote(ticker, "FUTUROS", "INMEDIATA", D("1499"), D("1499"), D("1500"), D("1"), D("1"), opening,
                contract=contract, currency="ARS", market="A3",
                metadata_source="PPI_CATALOG:OFFLINE_SYNTHETIC_FIXTURE_NOT_DATED_PROVIDER_EVIDENCE",
                book_at=opening, trade_at=opening, last_kind="TRADE")
            store.add_quote(q)
            outcome = broker._open_future(q, D(".8"), {})
            assert outcome[0], outcome
            openings.append({"family": "FUTUROS", "symbol": ticker, "outcome": outcome})
        with store.connect() as connection:
            spot = [dict(row) for row in connection.execute("SELECT symbol,status,market,currency,settlement FROM paper_positions WHERE status='OPEN' ORDER BY symbol")]
            factual_mode = list(connection.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone())
        futures = future_positions(store, "ARS", active_only=True)
        assert len(spot) == 5 and len(futures) == 5 and factual_mode == ["PRODUCTION_PAPER", 0]
        network = wire.__wrapped__(patch)
        report = measure(network, batches=(100,), cadence_seconds=30)
        cut = network[0].now()
        config = deepcopy(DEFAULT_POLICY)
        recommendation = build_recommendation(report, policy=config, as_of=cut)
        assert recommendation["status"] == "SHADOW_RECOMMENDATION", recommendation["status"]
        approval = {"schema": APPROVAL_SCHEMA, "approved": True, "reviewer": "offline-explicit-synthetic-review",
            "reviewed_at": cut.isoformat(), "recommendation_digest": recommendation["recommendation_digest"],
            "runtime_policy_fingerprint": recommendation["runtime_policy_fingerprint"]}
        approval["approval_digest"] = digest(approval)
        config.update(mode="APPROVED", approved_recommendation_digest=recommendation["recommendation_digest"])
        controller = RuntimeCapacityController(store.path, environ={}, policy=config, recommendation=recommendation,
            report=report, approval=approval, shadow={})
        state = controller.state(cut)
        assert state["status"] == "APPROVED_DYNAMIC", state
        assert state["global_budget"]["endpoint_limits"] == {"current": 75, "book": 75, "intraday": 75}, state["global_budget"]
        runtime = RuntimePPIBudget(store.path, controller, clock=network[0].now)
        counted = runtime._opened(77)
        actual_budget = runtime._current()
        expected = budget_policy(state, opened_count=len(spot) + len(futures))

        def use(budget, priority):
            row = budget.acquire("book", consumer="EXIT_READER" if priority == "EXIT_CRITICAL" else "SCANNER", priority=priority)
            if row["allowed"]:
                budget.start(row["lease"])
                budget.finish(row["lease"])
            return {key: row.get(key) for key in ("allowed", "reason")}

        lower = [use(actual_budget, "DISCOVERY") for _ in range(46)]
        exits = [use(actual_budget, "EXIT_CRITICAL") for _ in range(60)]
        control = GlobalPPIBudget(root / "correct-floor.sqlite", expected, clock=network[0].now)
        correct_lower = [use(control, "DISCOVERY") for _ in range(46)]
        correct_exits = [use(control, "EXIT_CRITICAL") for _ in range(60)]
        return {"evidence_kind": "OLD_NATIVE_MIXED_LEDGER_AND_RUNTIME_POLICY_RED",
            "native_callers": ["PaperBroker._open", "PaperBroker._open_future", "RuntimeCapacityController.state", "RuntimePPIBudget._opened/_current", "GlobalPPIBudget.acquire/start/finish"],
            "opening_as_of": opening, "budget_as_of": cut.isoformat(), "native_open_outcomes": openings,
            "broker_configuration": {"initial_cash": "100000000", "risk_pct": ".002", "daily_soft_stop_pct": "4",
                "emergency_position_cap": "AUTO, derived natively as 20; no override or gate replacement",
                "max_positions": 10, "participation": "1", "economics_mode": "SHADOW", "ai_mode": "OFF",
                "metadata_source": "PPI_CATALOG:OFFLINE_SYNTHETIC_FIXTURE_NOT_DATED_PROVIDER_EVIDENCE"},
            "observer_mode": factual_mode, "spot_open": spot,
            "future_active": [{key: row[key] for key in ("symbol", "status", "opened_at", "expires_at", "currency", "market", "settlement")} for row in futures],
            "conservative_count": 10, "expired_but_active_count": sum(datetime.fromisoformat(row["expires_at"]) < cut for row in futures),
            "runtime_count": counted, "actual_runtime_policy": actual_budget.policy, "correct_count_policy": expected,
            "feasible_native_controller_state": state,
            "synthetic_benchmark_sha256": sha(json.dumps(report, sort_keys=True).encode()),
            "benchmark_sdk_fake_wire_calls": len(network[1]),
            "wrong_count_discovery": lower, "wrong_count_exit": exits,
            "correct_floor_discovery": correct_lower, "correct_floor_exit": correct_exits,
            "boundary": "Ten native PAPER positions; August opening contracts are explicit synthetic inputs, not historical provider evidence. Two futures remain ACTIVE past expiry and are conservatively counted. Policy/controller and SQLite debt are native; acquire/start/finish traffic is modeled, not SDK sends or a cadence claim. Correct-count control changes only the input count using the same archived budget implementation."}
    finally:
        patch.undo()


def u27():
    from rc6_source_consolidation import consolidate
    from rc6_ppi_iol_reconciliation_rc6 import reconcile
    at = datetime.now(timezone.utc)
    base = {"ticker": "YPFD", "symbol": "YPFD", "family": "ACCIONES", "instrument_type": "ACCIONES",
        "market": "BYMA", "currency": "ARS", "settlement": "A-24HS", "term": "A-24HS",
        "last": 100, "bid": 99, "ask": 101, "bid_size": 20, "ask_size": 20,
        "provider_observed_at": at.isoformat(), "received_at": at.isoformat(), "book_at": at.isoformat(),
        "price_unit": "ARS_PER_UNIT", "volume_unit": "UNITS", "state": "READY"}
    results = []
    for source in ("IOL", "BYMA"):
        row = {**base, "source": source}
        aggregate = consolidate([], [row] if source == "IOL" else [], [row] if source == "BYMA" else [], as_of=at)
        comparison = reconcile({}, row if source == "IOL" else {}, row if source == "BYMA" else {}, now=at)
        results.append({"case": source + "_ONLY", "input": row, "consolidation": aggregate, "reconciliation": comparison})
    primary = {**base, "source": "PPI"}
    results.append({"case": "PPI_PRIMARY_POSITIVE", "input": primary,
        "consolidation": consolidate([primary], [], [], as_of=at), "reconciliation": reconcile(primary, {}, now=at)})
    for dimension, value in (("ticker", "OTHER"), ("family", "CEDEARS"), ("market", "A3"), ("currency", "USD"), ("settlement", "INMEDIATA")):
        other = {**base, "source": "IOL", dimension: value}
        if dimension == "ticker":
            other["symbol"] = value
        elif dimension == "family":
            other["instrument_type"] = value
        elif dimension == "settlement":
            other["term"] = value
        results.append({"case": "IDENTITY_SCOPE_" + dimension.upper(), "input_primary": primary, "input_complement": other,
            "consolidation": consolidate([primary], [other], [], as_of=at), "reconciliation": reconcile(primary, other, now=at)})
    return {"evidence_kind": "OLD_NATIVE_ADVISORY_FLAG_INCONSISTENCY", "as_of": at.isoformat(),
        "native_callers": ["rc6_source_consolidation.consolidate", "rc6_ppi_iol_reconciliation_rc6.reconcile"],
        "cases": results,
        "boundary": "Complement-only shadow_promotion=True is an ambiguous advisory flag. Entry/live/real-money authorities stay False. Identity controls cover the five original scope dimensions; they are variants, not five new requirements."}


def validate_observations(requirements):
    one = requirements["U01"]
    original = one["original_driver"]["result"]
    assert len(original["cycles"]) == 3 and original["coalesced"] == 0
    assert all("PPI_BOOK_SINGLE_FLIGHT_BACKPRESSURE" in cycle["exit"] for cycle in original["cycles"])
    assert len(original["provider_calls"]) == 3 and not any("EXIT_PROVIDER" in item for item in original["provider_calls"])
    for variant, failures in (("native_slow_three_windows", [1, 1, 1]), ("native_fast_three_windows", [0, 0, 0])):
        observed = one[variant]
        assert [cycle["exit_failures"] for cycle in observed["cycles"]] == failures
        assert len(observed["wire_trace"]) == 3
        assert [cycle["wire_count_cumulative"] for cycle in observed["cycles"]] == [1, 2, 3]
    assert len(one["native_slow_three_windows"]["native_exit_error_events"]) == 3
    assert one["native_fast_three_windows"]["native_exit_error_events"] == []
    for cycle, wire in zip(one["native_slow_three_windows"]["cycles"], one["native_slow_three_windows"]["wire_trace"]):
        assert cycle["follower_completed_monotonic"] < wire["body_drained_monotonic"]
        assert wire["body_drained_monotonic"] - wire["sent_monotonic"] >= .12
    seven = requirements["U07"]
    assert seven["runtime_count"] == 5 and seven["conservative_count"] == 10
    assert seven["actual_runtime_policy"]["exit_demand"]["book"] == 30
    assert seven["correct_count_policy"]["exit_demand"]["book"] == 60
    assert sum(row["allowed"] for row in seven["wrong_count_discovery"]) == 40
    assert sum(row["allowed"] for row in seven["wrong_count_exit"]) == 35
    assert sum(row["allowed"] for row in seven["correct_floor_discovery"]) == 5
    assert all(row["allowed"] for row in seven["correct_floor_exit"])
    for case in requirements["U27"]["cases"]:
        comparison = case["reconciliation"]
        assert not any(comparison[name] for name in ("entry_authority", "live_decision_authority", "real_money_authorized"))
        rows = case["consolidation"]["rows"]
        assert all(not any(row[name] for name in ("entry_authority", "live_decision_authority", "real_money_authorized")) for row in rows)
        if case["case"] in {"IOL_ONLY", "BYMA_ONLY"}:
            assert comparison["shadow_promotion"] and not comparison["selection_eligible"]
            assert len(rows) == 1 and rows[0]["shadow_promotion"] and not rows[0]["selection_eligible"]
        elif case["case"].startswith("IDENTITY_SCOPE_"):
            assert comparison["contract_state"] == "BLOCKED_CONFLICT" and not comparison["selection_eligible"]
            assert len(rows) == 2 and sum(row["selection_eligible"] for row in rows) == 1
        elif case["case"] == "PPI_PRIMARY_POSITIVE":
            assert comparison["selection_eligible"] and len(rows) == 1 and rows[0]["selection_eligible"]
    return "PASS: three original clauses, with shared controls and five identity variants; no inflation of requirement/R counts"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--original-u01-driver", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_root.resolve()
    args.archive = args.archive.resolve()
    args.original_u01_driver = args.original_u01_driver.resolve()
    args.receipt = args.receipt.resolve()
    before = inventory(source)
    assert len(before) == 1109, len(before)
    assert sha(args.archive.read_bytes()) == ARCHIVE_SHA256
    # Exclude all mutable repo/worktree paths before any product import.
    sys.path = [str(source)] + [p for p in sys.path if p and not str(Path(p).resolve()).startswith("/workspace/porota_rc6")]
    os.chdir(source)
    sys.addaudithook(deny_network)
    try:
        socket.socket()
    except PermissionError:
        pass
    assert len(NETWORK_EVENTS) == 1 and NETWORK_EVENTS[0]["event"] == "socket.__new__"
    NETWORK_EVENTS.clear()
    started = datetime.now(timezone.utc)
    receipt = {"schema": "rc6-original-budget-c27-replay-v1", "phase": "INTERMEDIATE_HISTORICAL_REPLAY",
        "source_commit": COMMIT, "source_tree": TREE, "source_root": str(source),
        "archive_sha256": ARCHIVE_SHA256, "probe_sha256": sha(Path(__file__).read_bytes()),
        "python": sys.version, "started_at": started.isoformat(),
        "configuration_scope": "OFFLINE_SYNTHETIC_PAPER; no provider authority, credentials or real orders",
        "seed": None, "seed_boundary": "Native PAPER IDs retain their native UUID generation; no deterministic seed or byte-identical output replay is claimed.",
        "network_control": "Python audit hook forbids socket creation, connect and DNS; negative control attempted once before product calls",
        "source_inventory_before": before, "source_inventory_before_sha256": sha(json.dumps(before, sort_keys=True).encode()),
        "requirements": {}, "final_current_green": "PENDING_FINAL_FROZEN_SOURCE_AND_EXTERNAL_ATTESTATION"}
    exit_code = 0
    try:
        with tempfile.TemporaryDirectory(prefix="rc6-budget-c27-native-") as temp:
            root = Path(temp)
            for name in ("original-u01", "slow-u01", "fast-u01", "u07"):
                (root / name).mkdir()
            receipt["requirements"]["U01"] = {"original_driver": original_u01(args.original_u01_driver, root / "original-u01"),
                "native_slow_three_windows": sdk_u01(root / "slow-u01", .12),
                "native_fast_three_windows": sdk_u01(root / "fast-u01", .015)}
            receipt["requirements"]["U07"] = u07(root / "u07")
            receipt["requirements"]["U27"] = u27()
            receipt["observation_validation"] = validate_observations(receipt["requirements"])
    except BaseException as error:
        exit_code = 2
        receipt["harness_failure"] = {"evidence_kind": "INVALIDATED_HARNESS_FAILURE_NOT_PRODUCT_RED",
            "type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    imported = []
    aliens = []
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path is None:
            continue
        path = Path(path).resolve()
        if name in {"__main__", "__mp_main__"} and path == Path(__file__).resolve():
            continue
        if path.is_relative_to(source):
            imported.append({"module": name, "path": str(path.relative_to(source)), "sha256": sha(path.read_bytes())})
        elif str(path).startswith("/workspace/porota_rc6"):
            aliens.append({"module": name, "path": str(path)})
    after = inventory(source)
    receipt.update(finished_at=datetime.now(timezone.utc).isoformat(),
        source_files=len(before), source_unchanged=after == before,
        source_inventory_after_sha256=sha(json.dumps(after, sort_keys=True).encode()),
        project_imports=imported, project_import_count=len(imported), alien_project_imports=aliens,
        product_network_events=NETWORK_EVENTS, actual_network_calls=0, real_orders_sent=0, real_routes_used=[])
    dependency_events = [event for event in NETWORK_EVENTS if event["event"] == "socket.__new__"
        and any('HAS_IPV6 = _has_ipv6("::1")' in frame for frame in event["stack"])]
    receipt["blocked_dependency_initialization_events"] = dependency_events
    receipt["dependency_initialization_boundary"] = "urllib3's local IPv6 capability socket is also blocked before creation; no bind, connect or DNS occurs. This leaves HAS_IPV6=False and does not alter the fake HTTP transport."
    receipt["product_network_events"] = [event for event in NETWORK_EVENTS if event not in dependency_events]
    if aliens or after != before or receipt["product_network_events"]:
        exit_code = 3
        receipt["provenance_validation"] = "FAILED_NOT_ACCEPTED_AS_PRODUCT_EVIDENCE"
    else:
        receipt["provenance_validation"] = "PASS"
    for package in ("ppi-client", "requests", "pytest"):
        try:
            receipt.setdefault("dependencies", {})[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            receipt.setdefault("dependencies", {})[package] = "NOT_INSTALLED_UNDER_THIS_DISTRIBUTION_NAME"
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"receipt": str(args.receipt), "sha256": sha(args.receipt.read_bytes()),
        "exit_code": exit_code, "source_files": len(before), "source_unchanged": after == before,
        "imports": len(imported), "aliens": len(aliens), "product_network_events": len(receipt["product_network_events"]),
        "blocked_dependency_initialization_events": len(dependency_events),
        "harness_failure": receipt.get("harness_failure", {}).get("message")}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
