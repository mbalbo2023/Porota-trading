#!/usr/bin/env python3
"""Replay/live-report the separate observation engines without trading writes.

Accept a private evidence bundle and optional existing SQLite runtime source.
Output/checkpoint is a separate artifact; capacity/config changes invalidate it.
No secrets, login, remote fetch, order client or factual parameter mutation.
"""
import argparse
import fcntl
import json
import os
from dataclasses import asdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rc6_dynamic_universe.common import stamp
from rc6_dynamic_universe.capacity import safe_capacity
from rc6_dynamic_universe.orchestrator import (EnginePolicy, UniverseOrchestrator,
    reconcile_endpoint_slots, apply_shared_allocation)
from rc6_dynamic_universe.runtime import read_runtime
from rc6_dynamic_universe.sources import audit_sources, source_observations
from rc6_dynamic_universe.tradeability import rank_tradeability, freeze_preopen, anomaly_events
from cf_intraday_scalping import shadow_sampling_plan


def run_shadow(bundle, *, previous=None):
    safety = bundle.get("safety", {})
    if (safety.get("mode") not in {"PRODUCTION_PAPER", "SIMULATION"} or
            safety.get("real_orders_sent") != 0 or safety.get("real_routes") != "NOT_CALLED"):
        raise ValueError("PAPER_SAFETY_REQUIRED")
    at, opening = stamp(bundle["as_of"]), stamp(bundle["session_open"])
    # Calendar, not supplied phase alone, determines whether this is live rueda.
    from co_market_sessions_hf6 import byma_paper_spot_phase
    phase = byma_paper_spot_phase(at)
    policies = [EnginePolicy(), EnginePolicy(engine="EQUITY_SPOT", hot_seconds=120,
                warm_seconds=300, discovery_seconds=600, warmup_samples=6, window_seconds=5400)]
    policies = [EnginePolicy(**(asdict(p) | bundle.get("policies", {}).get(p.engine, {}))) for p in policies]
    observations = list(bundle.get("observations", []))
    source_reports = []
    for source, snapshot in bundle.get("sources", {}).items():
        report = source_observations(snapshot, source=source, as_of=at)
        source_reports.append(report)
        observations.extend(r for r in report["observations"]
            if r.get("identity") and len(r["identity"]) == 5 and r.get("source_at") and r.get("received_at")
            and stamp(r["source_at"]) <= stamp(r["received_at"]) <= at)
    # Tradeability uses yesterday's availability cutoff and stays frozen.
    rankings = rank_tradeability(bundle["catalog"], bundle.get("history", []),
        bundle.get("preopen_observations", []), cutoff=bundle["preopen_cutoff"],
        sessions=bundle["sessions"], config=bundle.get("tradeability_config"))
    anomaly_rows = []
    for row in observations:
        key = tuple(row["identity"])
        normalized = dict(zip(("ticker", "instrument_type", "market", "currency", "settlement"), key))
        normalized.update(row.get("fields", {}))
        normalized.update({"observed_at": row["source_at"], "published_at": row["received_at"],
            "session": stamp(row["source_at"]).date().isoformat(), "source": row.get("source"),
            "usable": row.get("useful") is True,
            "minute_of_session": int((stamp(row["source_at"])-opening).total_seconds()//60),
            "volume_unit": row.get("volume_unit") or row.get("units", {}).get("cumulative_volume") or row.get("units", {}).get("volume"),
            "turnover_unit": row.get("turnover_unit") or row.get("units", {}).get("turnover"),
            "volume_semantics": row.get("volume_semantics") or row.get("units", {}).get("volume_semantics")})
        if "depth" in normalized:
            normalized["depth_units"] = normalized["depth"]
            normalized["depth_unit"] = row.get("depth_unit") or row.get("units", {}).get("depth")
        anomaly_rows.append(normalized)
    events = anomaly_events(bundle.get("intraday_history", []), anomaly_rows,
                            as_of=at, config=bundle.get("tradeability_config"))
    plans, frozen_reports = [], {}
    for policy in policies:
        capacity = safe_capacity(bundle.get("capacity_report", {}), endpoints=policy.endpoints,
            cadence_seconds=policy.hot_seconds, window_seconds=policy.window_seconds,
            required_samples=policy.warmup_samples, latency_budget_seconds=policy.hot_seconds,
            as_of=at, **bundle.get("capacity_config", {}))
        # An existing preopen is immutable; never silently regenerate during wheel.
        frozen = bundle.get("frozen", {}).get(policy.engine) or (previous or {}).get("frozen", {}).get(policy.engine)
        if frozen is None:
            frozen = freeze_preopen(rankings, frozen_at=bundle["frozen_at"], session_open=opening,
                                    capacity_fingerprint=capacity["configuration_fingerprint"]).to_dict()
            if at >= opening:
                raise ValueError("PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION")
        frozen_reports[policy.engine] = frozen
        kwargs = dict(at=at, session_open=opening, frozen=frozen, capacity=capacity,
            observations=observations, events=events["events"], opened=bundle.get("opened", []),
            previous=(previous or {}).get("engines", previous or {}).get(policy.engine), phase=phase)
        if policy.engine == "SCALPING":
            plan = shadow_sampling_plan(bundle["catalog"], policy=policy, **kwargs)
        else:
            plan = UniverseOrchestrator(bundle["catalog"], policy=policy).plan(**kwargs)
        plans.append((plan, policy))
    # Reserve per-endpoint against the minimum jointly verified capacity.
    available = {}
    for plan, policy in plans:
        for endpoint, count in plan["capacity"].get("slots_by_endpoint", {}).items():
            available[endpoint] = min(available.get(endpoint, count), count)
    # Allocate in the smallest observed scheduling quantum, not each engine's
    # entire cadence budget at once. Open reservations expose overflow.
    quantum = min(p.hot_seconds for _, p in plans)
    global_limits = []
    for plan, policy in plans:
        if plan["capacity"].get("status") == "SHADOW_RECOMMENDATION":
            global_limits.append(int(plan["capacity"]["shared_global_slots"]*quantum/policy.hot_seconds))
        for endpoint, count in plan["capacity"].get("slots_by_endpoint", {}).items():
            equivalent = int(count*quantum/policy.hot_seconds)
            available[endpoint] = min(available.get(endpoint, equivalent), equivalent)
    allocation = reconcile_endpoint_slots(plans, available, global_slots=min(global_limits, default=0))
    apply_shared_allocation(plans, allocation, previous)
    return {"mode": "SHADOW", "as_of": at.isoformat(), "engines": {p.engine: r for r, p in plans},
            "aggregate_schedule": allocation,
            "source_audit": audit_sources(), "source_reports": source_reports,
            "events": events, "frozen": frozen_reports, "tradeability": rankings, "real_orders_sent": 0,
            "input_quality": {"source_database_effect": bundle.get("source_database_effect", "NOT_USED"),
                "catalog_view": bundle.get("catalog_view"),
                "observation_read_truncated": bundle.get("observation_read_truncated", "NO_VERIFICADO")},
            "real_routes": "NOT_CALLED", "profitability": "NO_VERIFICADO"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--db")
    args = parser.parse_args()
    source, target, checkpoint = map(lambda p: Path(p).resolve(), (args.input, args.out, args.checkpoint))
    inputs = {source} | ({Path(args.db).resolve()} if args.db else set())
    # Temporary/lock aliases are writes too: e.g. input=report.json.tmp must
    # never be replaced while atomically publishing report.json.
    writes = {target, checkpoint, target.with_suffix(target.suffix+".tmp"),
              checkpoint.with_suffix(checkpoint.suffix+".tmp"),
              checkpoint.with_suffix(checkpoint.suffix+".lock")}
    if len(writes) != 5 or writes & inputs:
        raise ValueError("OUTPUT_MUST_BE_SEPARATE_FROM_INPUT")
    bundle = json.loads(source.read_text())
    if args.db:
        bundle.update(read_runtime(args.db, as_of=bundle["as_of"]))
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint.with_suffix(checkpoint.suffix+".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
        report = run_shadow(bundle, previous=previous)
        for path, payload in ((target, report), (checkpoint, {"engines": report["engines"], "frozen": report["frozen"]})):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix+".tmp")
            tmp.write_text(json.dumps(payload, sort_keys=True, default=str, allow_nan=False))
            os.chmod(tmp, 0o600)
            tmp.replace(path)
    print(json.dumps({"status": "SHADOW_REPORT", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                      "engines": list(report["engines"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
