"""Shared SHADOW entry point used by both the canonical runtime and offline CLI."""
from dataclasses import asdict
from .common import stamp
from .capacity import safe_capacity
from .orchestrator import (EnginePolicy, UniverseOrchestrator,
    reconcile_endpoint_slots, apply_shared_allocation)
from .sources import audit_sources, native_source_reports, source_observations
from .tradeability import rank_tradeability, freeze_preopen, anomaly_events
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
    native_observations = list(bundle.get("observations", []))
    observations = list(native_observations)
    capacity_policy = bundle.get("capacity_policy")
    if capacity_policy and capacity_policy.get("status") == "APPROVED_DYNAMIC":
        # The canonical child may consume the approved measured profiles; OFF
        # still uses the identical SHADOW policies above and zero new calls.
        policies = [EnginePolicy(**(asdict(p) | {k: v for k, v in
            capacity_policy["engine_profiles"][p.engine]["profile"].items() if k != "endpoints"})) for p in policies]
    source_reports = bundle.get("source_observation_reports")
    if source_reports is None:
        source_reports = [source_observations(snapshot, source=source, as_of=at)
            for source, snapshot in bundle.get("sources", {}).items()]
    else:
        source_reports = [dict(report) for report in source_reports]
    for report in source_reports:
        causal = [r for r in report["observations"]
            if r.get("identity") and len(r["identity"]) == 5 and r.get("source_at") and r.get("received_at")
            and stamp(r["source_at"]) <= stamp(r["received_at"]) <= at]
        accepted = [r for r in causal
            if (not bundle.get("observation_not_before") or
                stamp(r["source_at"]) >= stamp(bundle["observation_not_before"]))
            and (not bundle.get("observation_received_after") or
                stamp(r["received_at"]) > stamp(bundle["observation_received_after"]))]
        report["runtime_ingestion"] = {"accepted": len(accepted),
            "excluded_before_watermark": len(causal) - len(accepted),
            "reason": "PROSPECTIVE_RECEIPTS_ONLY; excluded rows retained as source evidence"}
        observations.extend(accepted)
    # Tradeability uses yesterday's availability cutoff and stays frozen.
    rankings = bundle.get("rankings") or rank_tradeability(bundle["catalog"], bundle.get("history", []),
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
    # Native SQLite observations are source evidence too. Add their references
    # only after planner ingestion; telemetry must not duplicate native warmup.
    source_reports = source_reports + native_source_reports(native_observations, as_of=at)
    return {"mode": "SHADOW", "as_of": at.isoformat(), "engines": {p.engine: r for r, p in plans},
            "aggregate_schedule": allocation,
            "source_audit": audit_sources(reports=source_reports, as_of=at), "source_reports": source_reports,
            "events": events, "frozen": frozen_reports, "tradeability": rankings, "real_orders_sent": 0,
            "deep_priority_authority": "STRATEGY_TRADEABILITY_CAPACITY", "family_quota": False,
            "coverage_fairness_authority": "DISCOVERY_ONLY",
            "input_quality": {"source_database_effect": bundle.get("source_database_effect", "NOT_USED"),
                "catalog_view": bundle.get("catalog_view"),
                "observation_read_truncated": bundle.get("observation_read_truncated", "NO_VERIFICADO")},
            "real_routes": "NOT_CALLED", "profitability": "NO_VERIFICADO"}
