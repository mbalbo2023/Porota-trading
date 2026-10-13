#!/usr/bin/env python3
"""Independent offline probes for Issue #469, F-01/F-02, candidate #466.

This script never opens a network socket or a production database.  All SQLite
files and simulated PAPER activity live below a fresh TemporaryDirectory.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time


CANDIDATE = Path("/workspace/scratch/10843fa5dbba/audit_work/ra_a_f01_f02_candidate")
EXPECTED_SHA = "c27dfd963c4fe83465c0f2105347e974fbbe6356"
sys.path.insert(0, str(CANDIDATE))

from rc6_ppi_global_budget import (  # noqa: E402
    BudgetBackpressure,
    GlobalPPIBudget,
    SCHEMA,
)


class Clock:
    def __init__(self, value="2026-10-05T13:00:00+00:00"):
        self.value = datetime.fromisoformat(value) if isinstance(value, str) else value

    def now(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def policy(clock, *, limits=None, global_limit=None, reserves=None, maximum_bytes=8 * 1024**2):
    limits = limits or {"current": 20, "book": 20, "intraday": 20}
    return {
        "schema": SCHEMA,
        "version": "ra-a-independent-v1",
        "recommendation_digest": "a" * 64,
        "configuration_fingerprint": "b" * 64,
        "window_seconds": 30,
        "endpoint_limits": limits,
        "global_limit": global_limit if global_limit is not None else sum(limits.values()),
        "max_parallel_requests": 1,
        "safety_reserve": "RA_SYNTHETIC_NOT_SPENDABLE",
        "priority_reserves": reserves or {},
        "expires_at": (clock.now() + timedelta(hours=4)).isoformat(),
        "critical_book_seconds": 5,
        "lease_seconds": 60,
        "breaker_seconds": 60,
        "session_breaker_seconds": 900,
        "server_error_threshold": 2,
        "maximum_bytes": maximum_bytes,
        "open_positions_count": 0,
        "exit_demand": {},
    }


def use(budget, endpoint, **scope):
    row = budget.acquire(endpoint, **scope)
    if row["allowed"]:
        budget.start(row["lease"])
        budget.finish(row["lease"])
    return row


def db_stats(path):
    with sqlite3.connect(path) as c:
        return {
            "file_bytes": Path(path).stat().st_size,
            "page_size": c.execute("PRAGMA page_size").fetchone()[0],
            "page_count": c.execute("PRAGMA page_count").fetchone()[0],
            "freelist_count": c.execute("PRAGMA freelist_count").fetchone()[0],
            "journal_mode": c.execute("PRAGMA journal_mode").fetchone()[0],
            "window_rows": c.execute(
                "SELECT COUNT(*) FROM budget_state WHERE key LIKE 'window:%'"
            ).fetchone()[0],
            "request_rows": c.execute("SELECT COUNT(*) FROM budget_requests").fetchone()[0],
        }


def reserve_order_probe(root):
    clock = Clock()
    value = policy(
        clock,
        limits={"current": 5, "book": 5, "intraday": 5},
        global_limit=15,
        reserves={"EXIT_CRITICAL": {"book": 5}},
    )
    value.update(open_positions_count=5, exit_demand={"book": 5})
    budget = GlobalPPIBudget(root / "reserve.sqlite", value, clock=clock.now)
    opened = [use(budget, "book", consumer="SCANNER", priority="OPENED_CRITICAL") for _ in range(5)]
    exits = [use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL") for _ in range(5)]
    return {
        "opened": [{"allowed": x["allowed"], "reason": x.get("reason")} for x in opened],
        "exits_allowed": sum(x["allowed"] for x in exits),
        "book": budget.metrics()["by_endpoint"]["book"],
        "window": budget.metrics()["window"]["by_endpoint"]["book"],
    }


def insufficient_capacity_fairness_probe(root):
    clock = Clock()
    value = policy(
        clock,
        limits={"current": 20, "book": 5, "intraday": 20},
        global_limit=5,
        reserves={"EXIT_CRITICAL": {"book": 5}},
    )
    value.update(open_positions_count=10, exit_demand={"book": 10})
    budget = GlobalPPIBudget(root / "fairness.sqlite", value, clock=clock.now)
    identities = [f"P{i:02d}" for i in range(10)]
    windows = []
    for cycle in range(3):
        allowed = []
        denied = []
        for identity in identities:
            row = use(budget, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
            (allowed if row["allowed"] else denied).append(identity)
        windows.append({"cycle": cycle, "allowed": allowed, "denied": denied})
        clock.advance(31)
    metrics = budget.metrics()["window"]
    return {
        "windows": windows,
        "exit_unreserved_demand": metrics["exit_unreserved_demand"],
        "reserved_total": metrics["by_endpoint"]["book"]["reserved_total"],
    }


def single_flight_priority_probe(root):
    clock = Clock()
    value = policy(
        clock,
        limits={"current": 20, "book": 6, "intraday": 20},
        reserves={"EXIT_CRITICAL": {"book": 5}},
    )
    value.update(open_positions_count=5, exit_demand={"book": 5})
    budget = GlobalPPIBudget(root / "single-flight.sqlite", value, clock=clock.now)
    identity = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    provider_calls = []
    cycles = []

    for cycle in range(3):
        entered = threading.Event()
        owner_result = {}

        def opened_fetch():
            provider_calls.append(f"cycle-{cycle}:SCANNER_PROVIDER_BOOK")
            entered.set()
            time.sleep(0.120)
            return {
                "date": clock.now().isoformat(),
                "bids": [{"price": "99", "quantity": "1000"}],
                "offers": [{"price": "101", "quantity": "1000"}],
            }

        def opened_owner():
            try:
                owner_result["value"] = budget.coalesced_book(
                    identity,
                    opened_fetch,
                    consumer="SCANNER",
                    priority="OPENED_CRITICAL",
                )["date"]
            except BaseException as error:  # evidence must retain unexpected owner failures
                owner_result["error"] = f"{type(error).__name__}:{error}"

        owner = threading.Thread(target=opened_owner, name=f"opened-owner-{cycle}")
        owner.start()
        if not entered.wait(2):
            raise AssertionError("lower-priority owner never entered provider fetch")
        started = time.monotonic()
        try:
            budget.coalesced_book(
                identity,
                lambda: provider_calls.append(f"cycle-{cycle}:EXIT_PROVIDER_BOOK"),
                consumer="EXIT_READER",
                priority="EXIT_CRITICAL",
            )
            exit_result = "UNEXPECTED_SUCCESS"
        except BaseException as error:
            exit_result = f"{type(error).__name__}:{error}"
        elapsed = time.monotonic() - started
        owner.join(2)
        if owner.is_alive():
            raise AssertionError("lower-priority owner did not terminate")
        cycles.append(
            {
                "cycle": cycle,
                "exit": exit_result,
                "exit_elapsed_seconds": round(elapsed, 6),
                "opened_owner": owner_result,
            }
        )
        clock.advance(6)  # greater than the five-second successful-book cache TTL
    return {
        "identity": list(identity),
        "cycles": cycles,
        "provider_calls": provider_calls,
        "coalesced": budget.metrics()["window"]["by_endpoint"]["book"]["coalesced"],
    }


def quota_probe(root, *, maximum_bytes, scopes, name):
    clock = Clock()
    limits = {"current": 10000, "book": 10000, "intraday": 10000}
    value = policy(
        clock,
        limits=limits,
        global_limit=30000,
        reserves={"EXIT_CRITICAL": deepcopy(limits)},
        maximum_bytes=maximum_bytes,
    )
    value.update(open_positions_count=10000, exit_demand=deepcopy(limits))
    path = root / f"quota-{name}.sqlite"
    budget = GlobalPPIBudget(path, value, clock=clock.now)
    completed = 0
    failure = None
    failed_second = None
    for second in range(5000):
        for endpoint, consumer, priority in scopes:
            try:
                row = budget.acquire(endpoint, consumer=consumer, priority=priority)
                if row["allowed"]:
                    raise AssertionError("quota fixture unexpectedly spent EXIT floor")
                completed += 1
            except BaseException as error:
                failure = f"{type(error).__name__}:{error}"
                failure_cause = repr(error.__cause__)
                failed_second = second
                break
        if failure:
            break
        clock.advance(1)
    before = db_stats(path)
    clock.advance(4000)
    try:
        later = budget.acquire("book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
        after_horizon = {"returned": {k: v for k, v in later.items() if k != "lease"}}
    except BaseException as error:
        after_horizon = {
            "error": f"{type(error).__name__}:{error}",
            "cause": repr(error.__cause__),
        }

    # Causal control on an isolated copy: prune before the next telemetry write.
    control_path = root / f"quota-{name}-manual-prune.sqlite"
    shutil.copy2(path, control_path)
    with sqlite3.connect(control_path) as c:
        deleted = c.execute(
            "DELETE FROM budget_state WHERE key LIKE 'window:%' "
            "AND CAST(substr(key,8) AS INTEGER)<?",
            (int(clock.now().timestamp()) - 3600,),
        ).rowcount
    control = GlobalPPIBudget(control_path, value, clock=clock.now)
    try:
        recovered = use(control, "book", consumer="EXIT_READER", priority="EXIT_CRITICAL")
        manual_prune = {"deleted": deleted, "allowed": recovered["allowed"], "reason": recovered.get("reason")}
    except BaseException as error:
        manual_prune = {"deleted": deleted, "error": f"{type(error).__name__}:{error}"}
    return {
        "configured_maximum_bytes": maximum_bytes,
        "scopes_per_second": len(scopes),
        "completed_denials": completed,
        "failed_second": failed_second,
        "failure": failure,
        "failure_cause": failure_cause,
        "db": before,
        "after_plus_4000_seconds": after_horizon,
        "manual_prune_control": manual_prune,
    }


def intraread_clock_rollback_probe(root):
    # Reuse only the production-path harness plumbing; the rollback sequence and
    # assertions below are independent and absent from the native test suite.
    import pytest
    import cf_intraday_scalping as scalping
    from tests.test_issue465_capability_cache import DAY, Harness, payload

    actual_probe_start = DAY + timedelta(minutes=15)
    rolled_back_received = DAY + timedelta(minutes=1, seconds=40)
    times = [
        DAY,
        actual_probe_start,
        actual_probe_start + timedelta(seconds=1),
        actual_probe_start + timedelta(minutes=2, seconds=1),
    ]

    def behavior(harness, _request):
        if harness.cycle == 0:
            return Exception("Instrument not found")
        if harness.cycle == 1:
            # decision()/begin_probe() saw 11:01.  The provider callback moves
            # clock_fn backwards before received/outcome/persist_payload.
            harness.at = rolled_back_received
        return None

    monkeypatch = pytest.MonkeyPatch()
    (root / "f02").mkdir()
    try:
        harness = Harness(root / "f02", monkeypatch, times=times, behavior=behavior).run()
        with harness.store.connect() as c:
            state = dict(c.execute("SELECT * FROM ppi_intraday_contract_state").fetchone())
            capability = json.loads(state["detail"])["capability"]
            recorded_epoch = capability["warmup_after"]
            points_after_recorded = c.execute(
                "SELECT COUNT(*) FROM ppi_intraday_points WHERE julianday(event_at)>julianday(?)",
                (recorded_epoch,),
            ).fetchone()[0]
            points_after_actual_start = c.execute(
                "SELECT COUNT(*) FROM ppi_intraday_points WHERE julianday(event_at)>julianday(?)",
                (scalping._stamp(actual_probe_start),),
            ).fetchone()[0]
            recovered_cycle_source = scalping._stamp(
                datetime.fromisoformat(payload(rolled_back_received)[-1]["date"])
            )
            candidate_actions = [r[0] for r in c.execute("SELECT action FROM scalping_candidates ORDER BY id")]
            fills = c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
            real_orders = c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0]
        return {
            "calls_before_callback": [
                {"cycle": cycle, "clock": at.isoformat(), "request": list(request)}
                for cycle, at, request in harness.calls
            ],
            "probe_started_at": scalping._stamp(actual_probe_start),
            "received_and_outcome_at": scalping._stamp(rolled_back_received),
            "recorded_recovery_epoch": recorded_epoch,
            "recovery_cycle_max_source_event": recovered_cycle_source,
            "source_event_not_after_received": recovered_cycle_source <= scalping._stamp(rolled_back_received),
            "points_after_recorded_epoch": points_after_recorded,
            "points_after_actual_probe_start": points_after_actual_start,
            "final_state": state["state"],
            "candidate_actions": candidate_actions,
            "paper_fills": fills,
            "real_orders_sent": real_orders,
        }
    finally:
        monkeypatch.undo()


def main():
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{commit}"], cwd=CANDIDATE, text=True
    ).strip()
    if actual != EXPECTED_SHA:
        raise SystemExit(f"wrong candidate: {actual}")
    with tempfile.TemporaryDirectory(prefix="ra-a-f01-f02-") as raw:
        root = Path(raw)
        output = {
            "candidate_sha": actual,
            "mode": "OFFLINE_PAPER_SHADOW_ONLY",
            "real_orders_sent_by_probe": 0,
            "reserve_order": reserve_order_probe(root),
            "insufficient_capacity_fairness": insufficient_capacity_fairness_probe(root),
            "single_flight_priority": single_flight_priority_probe(root),
            "quota_minimum": quota_probe(
                root,
                maximum_bytes=65536,
                scopes=[("book", "SCANNER", "OPENED_CRITICAL")],
                name="minimum",
            ),
            "quota_default_six_scopes": quota_probe(
                root,
                maximum_bytes=8 * 1024**2,
                scopes=[
                    ("book", "SCANNER", "OPENED_CRITICAL"),
                    ("intraday", "SCALPING", "SCALPING_HOT"),
                    ("intraday", "SCALPING", "WARM"),
                    ("intraday", "SCALPING", "DISCOVERY"),
                    ("current", "SCANNER", "STRATEGY_HOT"),
                    ("book", "SCANNER", "WARM"),
                ],
                name="default-six-scopes",
            ),
            "f02_intraread_clock_rollback": intraread_clock_rollback_probe(root),
        }
        print(json.dumps(output, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
