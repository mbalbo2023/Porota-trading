"""Capacity is distinct from contract readiness and from economic edge."""
import math
from dataclasses import dataclass


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
