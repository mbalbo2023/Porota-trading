"""Explicit dependency adapters for #456's shared causal SHADOW laboratory.

No reimplementation, parameter tuning or changes to factual scores/exit rules.
Absence of the frozen dependency is reported, never an alternative cost model.
"""
from importlib import import_module
from decimal import Decimal
from math import sqrt
from .common import stamp, number, digest


def preregister_exit_variants(baseline, *, as_of, eod_at, volatility,
                             volatility_available_at, horizon_seconds,
                             target_sigma=2, stop_sigma=1, max_hold_seconds=1800,
                             trailing_fraction="0.008", minimum_net_lock="0.002"):
    """Versioned hypotheses from available volatility/time, with factual baseline.

    Range/volatility describe available movement, not a directional forecast.
    These coefficients are hypotheses for prospectively frozen OOS evaluation.
    """
    at, deadline = stamp(as_of), stamp(eod_at)
    if stamp(volatility_available_at) > at or deadline <= at:
        raise ValueError("EXIT_LAB_FUTURE_INFORMATION")
    vol = number(volatility, minimum=0.0000001)
    horizon = number(horizon_seconds, minimum=1)
    target, stop = vol*number(target_sigma, minimum=0.0000001), vol*number(stop_sigma, minimum=0.0000001)
    time_scale = sqrt(min(1, (deadline-at).total_seconds()/horizon))
    config = {"version": "ws-perf-03-exit-hypotheses-v1", "as_of": at.isoformat(),
              "eod_at": deadline.isoformat(), "volatility": vol,
              "available_at": str(volatility_available_at), "horizon_seconds": horizon,
              "target_sigma": target_sigma, "stop_sigma": stop_sigma,
              "max_hold_seconds": max_hold_seconds, "trailing_fraction": trailing_fraction,
              "minimum_net_lock": minimum_net_lock, "baseline": str(baseline)}
    replay = import_module("rc6_performance.replay")
    policy = replay.ExitPolicy
    variants = [baseline,
                policy("VOLATILITY_SHADOW", Decimal(str(stop)), Decimal(str(target)), baseline.max_hold_seconds),
                policy("TIME_TO_EOD_SHADOW", Decimal(str(stop*time_scale)), Decimal(str(target*time_scale)), baseline.max_hold_seconds),
                policy("MAX_HOLD_SHADOW", baseline.stop_fraction, baseline.target_fraction, max_hold_seconds),
                policy("TRAILING_SHADOW", baseline.stop_fraction, baseline.target_fraction,
                       baseline.max_hold_seconds, trailing_fraction=Decimal(trailing_fraction)),
                policy("BREAK_EVEN_SHADOW", baseline.stop_fraction, baseline.target_fraction,
                       baseline.max_hold_seconds, minimum_net_lock=Decimal(minimum_net_lock))]
    return {"policies": variants, "configuration_fingerprint": digest(config),
            "hypotheses": config, "mode": "SHADOW", "parameter_promotion": False}


def shadow_economics(book, **kwargs):
    try:
        module = import_module("rc6_performance.shadow")
    except ModuleNotFoundError as exc:
        if exc.name not in {"rc6_performance", "rc6_performance.shadow"}:
            raise
        return {"status": "NO_VERIFICADO", "reason": "DEPENDENCY_PR_456_NOT_INTEGRATED",
                "mode": "SHADOW", "decision_effect": "NONE", "economic_edge_validated": False,
                "real_order_routes": []}
    return module.economics_gate(book, **kwargs)


def shadow_exit_lab(entries, policies, snapshots, *, fees, replay_kwargs=None):
    """Run preregistered policies on identical paths through the existing lab.

    Policies and cost model are explicit inputs frozen by the caller; do not
    search targets/stops or select an in-sample winner. Labels remain outcomes.
    """
    try:
        replay = import_module("rc6_performance.replay")
        common = import_module("rc6_performance.common")
        shadow = import_module("rc6_performance.shadow")
    except ModuleNotFoundError as exc:
        if not str(exc.name).startswith("rc6_performance"):
            raise
        return {"status": "NO_VERIFICADO", "reason": "DEPENDENCY_PR_456_NOT_INTEGRATED",
                "mode": "SHADOW", "real_order_routes": []}
    entries, policies, snapshots = list(entries), list(policies), list(snapshots)
    clocks = [stamp(s["as_of"]) for s in snapshots]
    if clocks != sorted(clocks):
        raise ValueError("REPLAY_TIME_REVERSED")
    if len({p.name for p in policies}) != len(policies):
        raise ValueError("DUPLICATE_EXIT_POLICY")
    results, cohorts = [], {}
    deadline = stamp((replay_kwargs or {})["eod_at"])
    for entry in entries:
        opened, ident = stamp(entry["opened_at"]), common.identity(entry)
        path = [s for s in snapshots if stamp(s["as_of"]) >= opened]
        books = [s["book"] for s in path if s.get("book") is not None and
                 common.identity(s["book"]) == ident and stamp(s["book"]["observed_at"]) <= stamp(s["as_of"])]
        available = max((stamp(s["as_of"]) for s in path), default=opened)
        horizon = int((deadline-opened).total_seconds())
        labels = shadow.forward_labels(dict(entry, decision_at=entry["opened_at"]),
            books, [horizon], as_of=available)
        label = labels[0]
        # Sampled level touches are outcome diagnostics, never a probability
        # forecast or a feature for ranking. Missing EOD coverage is censored.
        before_eod = [b for b in books if opened < stamp(b["book_at"]) <= stamp(b["observed_at"]) < deadline
                      and not shadow.usable_book(b, stamp(b["observed_at"]))]
        for policy in policies:
            laboratory = replay.ExitReplay(entry, policy, fees=fees, **(replay_kwargs or {}))
            for snapshot in path:
                book = snapshot.get("book")
                laboratory.advance(book if book is not None and common.identity(book) == ident else None,
                                   as_of=snapshot["as_of"])
            anchor = common.number(entry["entry_price"], positive=True)
            target_touch = next((b["observed_at"] for b in before_eod if
                                 common.number(b["bid"]) >= anchor*(1+policy.target_fraction)), None)
            stop_touch = next((b["observed_at"] for b in before_eod if
                               common.number(b["bid"]) <= anchor*(1-policy.stop_fraction)), None)
            outcome = laboratory.result()
            cohort_key = (ident[1], ident[3], opened.hour, policy.name)
            cohort = cohorts.setdefault(cohort_key, {"family": ident[1], "currency": ident[3],
                "entry_hour_utc": opened.hour, "policy": policy.name, "entries": 0,
                "complete_eod_labels": 0, "target_touches_complete": 0, "stop_touches_complete": 0,
                "net": Decimal(0), "mfe_observed": [], "mae_observed": []})
            cohort["entries"] += 1
            cohort["net"] += outcome["net"]
            if label["status"] == "MEDIDO":
                cohort["complete_eod_labels"] += 1
                cohort["target_touches_complete"] += int(target_touch is not None)
                cohort["stop_touches_complete"] += int(stop_touch is not None)
                cohort["mfe_observed"].append(label["mfe_observed"])
                cohort["mae_observed"].append(label["mae_observed"])
            results.append({"entry_id": entry.get("id"), "policy": policy.name,
                            "input_sha256": common.digest(path), "forward_label": label,
                            "sampled_entry_level_touches": {"target_at": target_touch, "stop_at": stop_touch,
                                "basis": "entry fixed levels; trailing/break-even conditions remain in causal replay",
                                "coverage": label["status"], "continuous_hit_probability": "NO_VERIFICADO"},
                            "entry_hour_utc": opened.hour, "result": outcome})
    for cohort in cohorts.values():
        n = cohort["complete_eod_labels"]
        cohort["sampled_target_touch_fraction"] = cohort["target_touches_complete"]/n if n else None
        cohort["sampled_stop_touch_fraction"] = cohort["stop_touches_complete"]/n if n else None
        cohort["continuous_hit_probability"] = "NO_VERIFICADO"
    return {"status": "SHADOW_REPLAY", "results": results, "entry_hour_cohorts": list(cohorts.values()), "mode": "SHADOW",
            "edge_oos": "NO_VERIFICADO", "real_order_routes": []}
