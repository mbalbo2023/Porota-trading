"""Capacity is distinct from contract readiness and from economic edge."""
import math
from dataclasses import dataclass
from datetime import timedelta

from .common import digest, stamp


@dataclass(frozen=True)
class ScanBudget:
    catalog_ready: int
    active: int
    slots: int
    cycle_seconds: float
    required_samples: int
    window_seconds: float
    success_fraction: float = 1.0

    def report(self):
        for value in (self.catalog_ready, self.active, self.slots, self.required_samples):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("INVALID_SCAN_BUDGET")
        if (self.active > self.catalog_ready or self.required_samples < 1 or
                not math.isfinite(self.cycle_seconds) or self.cycle_seconds <= 0 or
                not math.isfinite(self.window_seconds) or self.window_seconds <= 0 or
                not 0 <= self.success_fraction <= 1):
            raise ValueError("INVALID_SCAN_BUDGET")
        turns = math.ceil(self.active / self.slots) if self.slots and self.active else None
        revisit = turns * self.cycle_seconds if turns else None
        samples = math.floor(self.window_seconds / revisit * self.success_fraction) if revisit else 0
        feasible = self.active == 0 or samples >= self.required_samples
        maximum = math.floor(self.slots * self.window_seconds * self.success_fraction /
                             (self.cycle_seconds * self.required_samples))
        conservative = self.slots * math.floor(self.window_seconds * self.success_fraction /
                                               (self.cycle_seconds * self.required_samples))
        return {"status": "FEASIBLE" if feasible else "INFEASIBLE",
                "catalog_ready": self.catalog_ready, "active_observable_requested": self.active,
                "slots": self.slots, "cycle_seconds": self.cycle_seconds,
                "revisit_seconds": revisit, "required_samples": self.required_samples,
                "estimated_samples_per_window": samples, "window_seconds": self.window_seconds,
                "ideal_active_upper_bound": maximum,
                "conservative_active_limit": conservative,
                "usable_observations_per_second_upper_bound": self.slots * self.success_fraction / self.cycle_seconds,
                "full_cycle_turns": turns,
                "capacity_coverage_upper_bound": min(self.active, maximum) / self.catalog_ready if self.catalog_ready else 0,
                "observed_freshness": "NO_VERIFICADO",
                "assumption": "requests return distinct usable trades; observed coverage must be measured",
                "signal_ready": "NO_VERIFICADO", "economically_actionable": "NO_VERIFICADO"}

    def require_feasible(self):
        result = self.report()
        if result["status"] != "FEASIBLE":
            raise ValueError("SCANNER_INFEASIBLE")
        return result


def plan_active_universe(ranked_identities, budget):
    """Explicit ranked input; retain catalog, cap active size, do not rank on future returns."""
    report = budget.report()
    limit = min(budget.catalog_ready, report["conservative_active_limit"])
    active = list(dict.fromkeys(ranked_identities))[:limit]
    return {"active": active, "catalog_effect": "NONE", "ranking_authority": "CALLER_POINT_IN_TIME",
            "status": ScanBudget(budget.catalog_ready, len(active), budget.slots, budget.cycle_seconds,
                                 budget.required_samples, budget.window_seconds,
                                 budget.success_fraction).report()["status"]}


def sampling_plan(catalog, opened, focus, *, limit, cycle_seconds, required_samples,
                  window_seconds, now, cursor=0, previous=None):
    """Keep a warm basket for a whole window and reserve bounded cold discovery.

    Catalog order is the existing point-in-time family-balanced order. No return
    data or future quotes rank this basket. Open positions are always selected.
    All cursor/basket state fits in the existing cycle-metrics checkpoint.
    """
    catalog = list(dict.fromkeys(map(tuple, catalog)))
    opened = list(dict.fromkeys(map(tuple, opened)))
    available = set(catalog)
    configured_focus = [x for x in dict.fromkeys(map(tuple, focus)) if x in available]
    focus = [x for x in configured_focus if x not in opened]
    focus = focus[:min(len(focus), max(1, limit // 2), max(0, limit - len(opened)))]
    pinned = opened + focus
    pool = [x for x in catalog if x not in set(pinned)]
    free = max(0, limit - len(pinned))
    cold_slots = 1 if len(pool) > free and free >= 2 else 0
    warm_slots = free - cold_slots
    budget = ScanBudget(len(pool), len(pool), warm_slots, cycle_seconds,
                        required_samples, window_seconds).report()
    cap = min(len(pool), budget["conservative_active_limit"])
    config = digest({"limit": limit, "slots": warm_slots, "cycle": cycle_seconds,
                     "samples": required_samples, "window": window_seconds,
                     "pinned": pinned})
    old = previous or {}
    at = stamp(now)
    warm = [tuple(x) for x in old.get("warm", [])]
    try:
        started = stamp(old["started_at"])
        keep = (old.get("configuration_fingerprint") == config and
                started <= at < started + timedelta(seconds=window_seconds) and
                len(warm) == cap and len(set(warm)) == len(warm) and
                set(warm) <= set(pool))
    except (KeyError, ValueError, TypeError):
        keep = False
    start = int(old.get("basket_cursor", 0)) % max(1, len(pool))
    if not keep:
        warm = [pool[(start + i) % len(pool)] for i in range(cap)]
        started = at
        start = (start + cap) % max(1, len(pool))
    warm_cursor = int(old.get("warm_cursor", 0) if keep else cursor) % max(1, len(warm))
    hot = [warm[(warm_cursor + i) % len(warm)] for i in range(min(warm_slots, len(warm)))]
    warm_cursor = (warm_cursor + len(hot)) % max(1, len(warm))
    # Cold observations never gain opening authority until promoted to warm.
    cold, visited = [], 0
    cursor = int(cursor) % max(1, len(catalog))
    while len(cold) < cold_slots and visited < len(catalog):
        item = catalog[(cursor + visited) % len(catalog)]
        visited += 1
        if item not in set(pinned) | set(warm):
            cold.append(item)
    active = ScanBudget(len(pool), len(warm), warm_slots, cycle_seconds,
                        required_samples, window_seconds).report()
    focus_feasible = bool(set(configured_focus) & set(pinned)) and math.floor(window_seconds / cycle_seconds) >= required_samples
    plan = {"schema": "rc6.active-sampling.v1", "warm": warm, "cold": cold,
            "pinned": pinned, "started_at": started.isoformat(), "basket_cursor": start,
            "warm_cursor": warm_cursor, "configuration_fingerprint": config,
            "active_budget": active, "full_catalog_budget": budget,
            "warm_slots": warm_slots, "cold_slots": cold_slots,
            "focus_feasible": focus_feasible, "feasible": focus_feasible and active["status"] == "FEASIBLE",
            "catalog_effect": "NONE", "basket_reused": keep,
            "opening_identities": pinned + warm if focus_feasible and active["status"] == "FEASIBLE" else []}
    advance = visited if cold else len(hot)
    return tuple(pinned + hot + cold), cursor, (cursor + advance) % max(1, len(catalog)), plan
