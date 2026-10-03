"""Causal HOT/WARM/DISCOVERY scheduler. Plans never authorize execution.

    Visibility is an independent source/event plane. Endpoint capacity bounds
    deep sampling, not the retained READY catalogue. Checkpoints are separate
    JSON evidence, never written into the trading DB by this package.
"""
from dataclasses import dataclass, asdict
from copy import deepcopy
from math import ceil, floor
import re
from .common import stamp, digest, identity, percentile, number
from .routing import strategy_route, PIPELINE, EQUITY_FAMILIES


@dataclass(frozen=True)
class EnginePolicy:
    engine: str = "SCALPING"
    hot_seconds: int = 30
    warm_seconds: int = 120
    discovery_seconds: int = 300
    warmup_samples: int = 15
    window_seconds: int = 2700
    max_age_seconds: int = 120
    hot_share: float = 0.5
    version: str = "ws-perf-03-shadow-v1"

    def __post_init__(self):
        if self.engine not in {"SCALPING", "EQUITY_SPOT"}:
            raise ValueError("SPECIALIZED_LIFECYCLE")
        for v in (self.hot_seconds, self.warm_seconds, self.discovery_seconds,
                  self.warmup_samples, self.window_seconds, self.max_age_seconds):
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise ValueError("INVALID_ENGINE_POLICY")
        if not self.hot_seconds < self.warm_seconds <= self.discovery_seconds or not 0 < self.hot_share < 1:
            raise ValueError("INVALID_ENGINE_CADENCE")

    @property
    def endpoints(self):
        return ("intraday", "book") if self.engine == "SCALPING" else ("current", "book")


class UniverseOrchestrator:
    def __init__(self, catalog, *, policy=None):
        self.policy = policy or EnginePolicy()
        self.catalog = deepcopy(list(catalog))
        self.keys = [identity(r) for r in self.catalog]
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("DUPLICATE_EXACT_IDENTITY")

    def plan(self, *, at, session_open, frozen, capacity, observations=(), events=(),
             opened=(), previous=None, phase="OPEN"):
        at, opening = stamp(at), stamp(session_open)
        if phase not in {"OPEN", "PREOPEN", "CLOSED"}:
            raise ValueError("INVALID_MARKET_PHASE")
        payload = deepcopy(frozen.get("payload", {}))
        if frozen.get("digest") != digest(payload) or stamp(payload["frozen_at"]) >= opening:
            raise ValueError("INVALID_PREOPEN_FREEZE")
        if stamp(payload["session_open"]) != opening:
            raise ValueError("PREOPEN_SESSION_MISMATCH")
        if stamp(payload["frozen_at"]) > at or stamp(payload["cutoff"]) >= opening:
            raise ValueError("FUTURE_PREOPEN_FREEZE")
        ranked = payload.get("rows", [])
        ranks = {tuple(r["identity"]): r for r in ranked}
        ready = {k for k, r in zip(self.keys, self.catalog)
                 if r.get("status") == "AVAILABLE" and str(r.get("capability", "")).startswith("READY_PAPER")}
        eligible = {k for k in ready if k[1] in EQUITY_FAMILIES}
        # Full request aliases cannot be resolved with an SDK lacking market/currency.
        request_aliases = {}
        for k in eligible:
            request_aliases.setdefault((k[0], k[1], k[4]), []).append(k)
        ambiguous = {k for siblings in request_aliases.values() if len(siblings) > 1 for k in siblings}
        eligible -= ambiguous
        opened = list(dict.fromkeys(map(tuple, opened)))
        if any(len(k) != 5 or any(not x for x in k) for k in opened):
            raise ValueError("OPEN_POSITION_EXACT_IDENTITY_REQUIRED")
        specialized_opened = [k for k in opened if k[1] not in EQUITY_FAMILIES]
        opened = [k for k in opened if k[1] in EQUITY_FAMILIES]
        capacity_contract = {k: capacity.get(k) for k in ("configuration_fingerprint", "evidence_digest",
                            "safe_limit", "slots_by_endpoint", "status")}
        config = digest({"policy": asdict(self.policy), "capacity": capacity_contract,
                         "catalog": sorted(ready), "preopen": frozen["digest"],
                         "opening": opening.isoformat()})
        old = deepcopy(previous or {})
        reused = old.get("configuration_fingerprint") == config
        if reused and stamp(old["planned_at"]) > at:
            raise ValueError("CHECKPOINT_FROM_FUTURE")
        states = old.get("instruments", {}) if reused else {}
        last_plan = stamp(old["planned_at"]) if reused else opening
        current = {}
        for k in sorted(eligible | set(opened)):
            name = digest(k)
            s = states.get(name, {})
            current[name] = {"identity": k, "state": s.get("state", "DISCOVERY"),
                             "selected_at": s.get("selected_at"),
                             "promoted_at": s.get("promoted_at"),
                             "demoted_at": s.get("demoted_at"),
                             "last_touched_at": s.get("last_touched_at"),
                             "samples": s.get("samples", []), "attempts": s.get("attempts", []),
                             "event_at": s.get("event_at"), "event_reasons": s.get("event_reasons", []),
                             "intraday_confirmed": s.get("intraday_confirmed", False),
                             "book_at": s.get("book_at"),
                             "warmup_complete_at": s.get("warmup_complete_at"),
                             "latency_ms": s.get("latency_ms"), "source": s.get("source"),
                             "source_failures": s.get("source_failures", []),
                             "source_at": s.get("source_at"),
                             "last_useful_observation_at": s.get("last_useful_observation_at")}
        # Both provider AND receipt clocks must exist; no retrospective receipt rewrite.
        for obs in sorted(observations, key=lambda r: (stamp(r["received_at"]), stamp(r["source_at"]), tuple(r["identity"]))):
            k = tuple(obs["identity"])
            s = current.get(digest(k))
            source, received = stamp(obs["source_at"]), stamp(obs["received_at"])
            if source > received or received > at:
                raise ValueError("FUTURE_OBSERVATION")
            if s is None or source < opening or received < last_plan:
                continue
            s["last_touched_at"] = received.isoformat()
            if not s["source_at"] or source >= stamp(s["source_at"]):
                s["source"], s["source_at"] = obs.get("source"), source.isoformat()
            s["latency_ms"] = obs.get("latency_ms")
            # Keep native instrument/source gaps in their own identity. Never
            # turn a missing instrument or provider error into provider=0.
            if not obs.get("useful"):
                codes = [obs.get(k) for k in ("native_reason", "error_code", "reason")]
                for code in codes:
                    if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", code):
                        failure = [received.isoformat(), obs.get("source"), code]
                        if failure not in s["source_failures"]:
                            s["source_failures"].append(failure)
            else:
                s["source_failures"] = [f for f in s["source_failures"] if f[1] != obs.get("source")]
            if obs.get("book_at") and obs.get("book_useful") is True:
                book_at = stamp(obs["book_at"])
                if book_at > received:
                    raise ValueError("FUTURE_BOOK_OBSERVATION")
                if not s.get("book_at") or book_at > stamp(s["book_at"]):
                    s["book_at"] = book_at.isoformat()
            attempt = [received.isoformat(), source.isoformat(), bool(obs.get("useful")) and
                       0 <= (received-source).total_seconds() <= self.policy.max_age_seconds]
            if attempt not in s["attempts"]:
                s["attempts"].append(attempt)
            if (obs.get("useful") and obs.get("source") and
                    0 <= (at-source).total_seconds() <= self.policy.max_age_seconds):
                value = source.isoformat()
                strategy_sample = (obs.get("endpoint") == "intraday" and
                                   obs.get("source") == "PPI_MARKETDATA_INTRADAY" and
                                   obs.get("intraday_confirmed") is True) if self.policy.engine == "SCALPING" else (
                                   obs.get("endpoint") == "current" and obs.get("source") == "PPI_MARKETDATA_CURRENT")
                if strategy_sample and value not in s["samples"]:
                    s["samples"].append(value)
                if obs.get("endpoint") == "intraday":
                    s["intraday_confirmed"] = obs.get("intraday_confirmed") is True
                s["last_useful_observation_at"] = value
        for event in events:
            k = tuple(event["identity"])
            s = current.get(digest(k))
            if s is None:
                continue
            event_at = stamp(event["at"])
            if event_at > at:
                raise ValueError("FUTURE_DISCOVERY_EVENT")
            # A promotion is prospective and requires a contemporaneous useful source.
            useful_at = s.get("last_useful_observation_at")
            if (event.get("promotion_candidate") and opening <= event_at and
                    useful_at and abs((event_at-stamp(useful_at)).total_seconds()) <= self.policy.max_age_seconds and
                    0 <= (at-event_at).total_seconds() <= self.policy.max_age_seconds):
                s["event_at"], s["event_reasons"] = event_at.isoformat(), list(event.get("reason_codes", []))
        for s in current.values():
            s["samples"] = sorted({t for t in s["samples"]
                                   if 0 <= (at-stamp(t)).total_seconds() <= self.policy.window_seconds})
            s["attempts"] = [v for v in s["attempts"] if 0 <= (at-stamp(v[0])).total_seconds() <= self.policy.window_seconds]
            s["source_failures"] = [v for v in s["source_failures"] if 0 <= (at-stamp(v[0])).total_seconds() <= self.policy.window_seconds]
            if s["event_at"] and not 0 <= (at-stamp(s["event_at"])).total_seconds() <= self.policy.discovery_seconds:
                s["event_at"], s["event_reasons"] = None, []
        # New measurements invalidate warmup/checkpoints, not yesterday's
        # immutable ranking. Intraday measurement cannot rewrite that ranking.
        valid_capacity = capacity.get("status") == "SHADOW_RECOMMENDATION"
        if valid_capacity:
            try:
                valid_capacity = stamp(capacity["generated_at"]) <= at <= stamp(capacity["expires_at"])
            except (ValueError, KeyError, TypeError):
                valid_capacity = False
        safe_limit = capacity.get("safe_limit", 0) if valid_capacity else 0
        if isinstance(safe_limit, bool) or not isinstance(safe_limit, int) or safe_limit < 0:
            raise ValueError("INVALID_CAPACITY_LIMIT")
        fresh = lambda s: (s.get("last_useful_observation_at") and
                           0 <= (at-stamp(s["last_useful_observation_at"])).total_seconds() <= self.policy.max_age_seconds)
        def warmed(s):
            # Interval-volume capability is additionally required by the existing cf evaluator.
            return (len(s["samples"]) >= self.policy.warmup_samples and fresh(s) and
                    s.get("book_at") and 0 <= (at-stamp(s["book_at"])).total_seconds() <= self.policy.max_age_seconds and
                    0 <= (at-stamp(s["samples"][-1])).total_seconds() <= self.policy.max_age_seconds and
                    (self.policy.engine != "SCALPING" or s.get("intraday_confirmed")))
        def priority(s):
            row = ranks.get(tuple(s["identity"]), {})
            return (not bool(s["event_at"]), -float(row.get("tradeability_score") or 0), s["identity"])
        tradeable = {k for k in eligible if ranks.get(k, {}).get("tradeable")}
        prospects = sorted([s for s in current.values() if tuple(s["identity"]) in tradeable or s["event_at"]], key=priority)
        free = max(0, safe_limit-len(opened))
        discovery_slots = 1 if free else 0
        hot_slots = min(max(0, free-discovery_slots), floor(free*self.policy.hot_share))
        warm_slots = max(0, free-discovery_slots-hot_slots)
        warm_capacity = warm_slots * max(1, floor(self.policy.warm_seconds/self.policy.hot_seconds))
        hot = {tuple(s["identity"]) for s in prospects if warmed(s) and s["state"] in {"WARM", "HOT"}}
        hot = set(sorted(hot, key=lambda k: priority(current[digest(k)]))[:hot_slots])
        warm = {tuple(s["identity"]) for s in prospects if tuple(s["identity"]) not in hot}
        warm = set(sorted(warm, key=lambda k: priority(current[digest(k)]))[:warm_capacity])
        for s in current.values():
            k = tuple(s["identity"])
            before = s["state"]
            state = "HOT" if k in set(opened) | hot else "WARM" if k in warm else "DISCOVERY"
            if before != state:
                s["state"] = state
                if {"DISCOVERY": 0, "WARM": 1, "HOT": 2}[state] > {"DISCOVERY": 0, "WARM": 1, "HOT": 2}[before]:
                    s["promoted_at"] = at.isoformat()
                else:
                    s["demoted_at"] = at.isoformat()
            if state == "HOT" and warmed(s) and not s["warmup_complete_at"]:
                s["warmup_complete_at"] = at.isoformat()
        def due(s, seconds):
            return not s["selected_at"] or (at-stamp(s["selected_at"])).total_seconds() >= seconds
        warm_due = sorted([s for s in current.values() if s["state"] == "WARM" and due(s, self.policy.warm_seconds)],
                          key=lambda s: (s["selected_at"] or "", priority(s)))[:warm_slots]
        discovery_due = sorted([s for s in current.values() if s["state"] == "DISCOVERY" and due(s, self.policy.discovery_seconds)],
                               key=lambda s: (s["selected_at"] or "", s["last_touched_at"] or "", priority(s)))[:discovery_slots]
        selected = opened + [k for k in sorted(hot) if due(current[digest(k)], self.policy.hot_seconds)]
        selected += [tuple(s["identity"]) for s in warm_due + discovery_due if tuple(s["identity"]) not in selected]
        if phase != "OPEN" or at < opening:
            selected = []
        # Insufficient measurement stops new deep reads, never erases open-position priority.
        elif not valid_capacity:
            selected = opened
        for k in selected:
            current[digest(k)]["selected_at"] = at.isoformat()
        telemetry = []
        ages, touched, useful_count = [], 0, 0
        for k, record in zip(self.keys, self.catalog):
            route = strategy_route(k[1], scalping=self.policy.engine == "SCALPING")
            s = current.get(digest(k), {})
            row = ranks.get(k, {})
            state = s.get("state", "EXCLUDED")
            age = (at-stamp(s["last_touched_at"] or opening)).total_seconds() if k in eligible else None
            if k in eligible:
                ages.append(max(0, age)); touched += int(bool(s.get("last_touched_at"))); useful_count += int(bool(fresh(s)))
            attempts = s.get("attempts", [])
            useful_fraction = sum(bool(v[2]) for v in attempts)/len(attempts) if attempts else None
            receipts = sorted({stamp(v[0]) for v in attempts})
            reasons = list(route["reason_codes"])
            reasons.extend(v[2] for v in s.get("source_failures", []))
            if k not in ready:
                reasons.append("CATALOG_NOT_READY")
            elif k in ambiguous:
                reasons.append("PPI_REQUEST_IDENTITY_AMBIGUOUS")
            elif not route["generic_equity"]:
                pass
            elif not valid_capacity:
                reasons.append("SCANNER_CAPACITY")
            elif state == "DISCOVERY":
                reasons.append("DISCOVERY_ONLY")
            elif not warmed(s):
                reasons.append("WARMUP_INCOMPLETE")
            if k in eligible and not row.get("tradeable") and not s.get("event_at"):
                reasons.extend(row.get("reason_codes", ["NO_RECENT_TRADES"]))
            signal_ready = bool(state == "HOT" and warmed(s) and valid_capacity)
            telemetry.append({"identity": k, "family": k[1], "engine": route["engine"], "strategy": route["strategy"],
                "state": state, "rank": row.get("rank"), "rank_components": row.get("components", {}),
                "tradeability_score": row.get("tradeability_score"), "opportunity_score": "NO_VERIFICADO",
                "score_is_probability": False, "source": s.get("source"), "provenance": row.get("provenance", []),
                "selected_at": s.get("selected_at"), "promoted_at": s.get("promoted_at"), "demoted_at": s.get("demoted_at"),
                "last_useful_observation_at": s.get("last_useful_observation_at"), "usable_observation_fraction": useful_fraction,
                "achieved_revisit_seconds": (receipts[-1]-receipts[-2]).total_seconds() if len(receipts) >= 2 else None,
                "revisit_basis": "REQUESTED_CADENCE; achieved only from distinct receipt clocks",
                "revisit_seconds": {"HOT": self.policy.hot_seconds, "WARM": self.policy.warm_seconds,
                    "DISCOVERY": self.policy.discovery_seconds}.get(state),
                "warmup_progress": {"distinct_samples": len(s.get("samples", [])), "required": self.policy.warmup_samples},
                "discovery_age": age, "discovery_age_lower_bound": not bool(s.get("last_touched_at")),
                "time_to_promotion_seconds": (stamp(s["promoted_at"])-opening).total_seconds() if s.get("promoted_at") else None,
                "event_to_promotion_seconds": (stamp(s["promoted_at"])-stamp(s["event_at"])).total_seconds()
                    if s.get("promoted_at") and s.get("event_at") and stamp(s["promoted_at"]) >= stamp(s["event_at"]) else None,
                "time_to_warmup_seconds": (stamp(s["warmup_complete_at"])-opening).total_seconds() if s.get("warmup_complete_at") else None,
                "rejection_reason": list(dict.fromkeys(reasons)), "promotion_reasons": s.get("event_reasons", []),
                "candidate_result": "OBSERVE_ONLY", "signal_result": "SAMPLES_READY" if signal_ready else "NOT_READY",
                "economics_result": "NO_VERIFICADO", "risk_result": "NOT_CALLED", "paper_result": "NOT_CALLED",
                "latency_ms": s.get("latency_ms"), "freshness_seconds": (at-stamp(s["source_at"])).total_seconds() if s.get("source_at") else None,
                "strategy_source_at": s["samples"][-1] if s.get("samples") else None,
                "book_at": s.get("book_at"),
                "pipeline": {"CATALOG_READY": k in ready, "STRATEGY_ELIGIBLE": k in eligible,
                    "TRADEABLE": k in tradeable, "OBSERVABLE": bool(fresh(s)), "SIGNAL_READY": signal_ready,
                    "ECONOMICS": "NO_VERIFICADO", "RISK": "NOT_CALLED", "PAPER": "NOT_CALLED"},
                "entry_authority": False})
        discovery_count = sum(s["state"] == "DISCOVERY" for s in current.values())
        groups = {}
        for row in telemetry:
            if not row["pipeline"]["STRATEGY_ELIGIBLE"]:
                continue
            group = groups.setdefault(row["family"], {"eligible": 0, "touched_in_window": 0, "fresh_useful": 0, "sources": {}})
            group["eligible"] += 1
            s = current.get(digest(row["identity"]), {})
            group["touched_in_window"] += int(bool(s.get("attempts")))
            group["fresh_useful"] += int(bool(fresh(s)))
            if s.get("source"):
                source = group["sources"].setdefault(s["source"], {"touched": 0, "fresh_useful": 0})
                source["touched"] += int(bool(s.get("attempts")))
                source["fresh_useful"] += int(bool(fresh(s)))
        worst_bound = max(self.policy.discovery_seconds,
                          ceil(discovery_count/discovery_slots)*self.policy.hot_seconds) if discovery_slots else None
        return {"schema": "ws-perf-03-orchestrator-v1", "mode": "SHADOW", "planned_at": at.isoformat(),
            "phase": phase, "configuration_fingerprint": config, "checkpoint_reused": reused,
            "checkpoint_invalidated": bool(previous) and not reused, "instruments": current,
            "selected": selected, "opened_priority": opened, "capacity": capacity,
            "specialized_opened_priority": [{"identity": k, "route": strategy_route(k[1]),
                "reason": "SPECIALIZED_LIFECYCLE"} for k in specialized_opened],
            "catalog_ready_count": len(ready), "catalog_effect": "NONE", "strategy_eligible_count": len(eligible),
            "hot_count": len(hot), "warm_count": len(warm), "discovery_count": discovery_count,
            "discovery": {"p50_age_seconds": percentile(ages, .5), "p95_age_seconds": percentile(ages, .95),
                "max_discovery_age": max(ages) if ages else None,
                "touched_fraction_since_open": touched/len(eligible) if eligible else None,
                "window_seconds": self.policy.window_seconds,
                "touched_fraction_in_window": sum(bool(s["attempts"]) for s in current.values()
                    if tuple(s["identity"]) in eligible)/len(eligible) if eligible else None,
                "fresh_useful_fraction": useful_count/len(eligible) if eligible else None,
                "by_family_source": groups,
                "rotation_lower_bound_seconds": worst_bound, "bound_assumption": "one completed serial task per base tick; no errors",
                "missed_late_discovery": "NO_VERIFICADO", "all_movements_detected": False},
            "telemetry": telemetry, "preopen_digest": frozen["digest"], "pipeline_stages": PIPELINE,
            "capacity_overflow_open_positions": max(0, len(opened)-safe_limit),
            "real_orders_sent": 0, "real_routes": "NOT_CALLED"}


def reconcile_endpoint_slots(plans, slots_by_endpoint, *, global_slots=None):
    """One aggregate observed budget across engines; open/HOT tasks come first.

    Expose reservation overflow instead of dropping existing positions. Engines
    with overlapping endpoint demand cannot both claim the whole measurement.
    """
    remaining = {k: int(number(v)) for k, v in slots_by_endpoint.items()}
    global_remaining = int(number(global_slots)) if global_slots is not None else min(remaining.values(), default=0)
    global_paid = set()
    tasks = []
    for plan, policy in plans:
        states = {tuple(r["identity"]): r["state"] for r in plan["telemetry"]}
        for k in plan["selected"]:
            opened = tuple(k) in set(map(tuple, plan["opened_priority"]))
            priority = 0 if opened else {"HOT": 1, "WARM": 2, "DISCOVERY": 3}.get(states.get(tuple(k)), 4)
            tasks.append((priority, policy.engine, tuple(k), policy.endpoints))
    accepted, rejected, shared = [], [], set()
    for priority, engine, key, endpoints in sorted(tasks):
        needed = [e for e in endpoints if (e, key) not in shared]
        enough = all(remaining.get(e, 0) > 0 for e in needed) and (key in global_paid or global_remaining > 0)
        if enough or priority == 0:
            if key not in global_paid:
                global_remaining -= 1
                global_paid.add(key)
            for e in needed:
                remaining[e] = remaining.get(e, 0)-1
                shared.add((e, key))
            accepted.append({"engine": engine, "identity": key, "endpoints": endpoints,
                             "reused_endpoints": [e for e in endpoints if e not in needed],
                             "reservation_overflow": not enough})
        else:
            rejected.append({"engine": engine, "identity": key, "reason": "SCANNER_CAPACITY"})
    return {"tasks": accepted, "rejected": rejected, "remaining_slots": remaining,
            "remaining_global_slots": global_remaining,
            "mode": "SHADOW", "real_orders_sent": 0}


def apply_shared_allocation(plans, allocation, previous=None):
    """Only accepted tentative tasks advance cadence; rejected tasks stay due."""
    previous = (previous or {}).get("engines", previous or {})
    accepted = {(r["engine"], tuple(r["identity"])) for r in allocation["tasks"]}
    for plan, policy in plans:
        rejected = [tuple(k) for k in plan["selected"] if (policy.engine, tuple(k)) not in accepted]
        old = previous.get(policy.engine, {})
        for k in rejected:
            name = digest(k)
            old_state = old.get("instruments", {}).get(name, {}) if plan["checkpoint_reused"] else {}
            plan["instruments"][name]["selected_at"] = old_state.get("selected_at")
            for row in plan["telemetry"]:
                if tuple(row["identity"]) == k:
                    row["selected_at"] = old_state.get("selected_at")
                    row["rejection_reason"] = list(dict.fromkeys(row["rejection_reason"]+["SCANNER_CAPACITY"]))
        plan["selected"] = [k for k in plan["selected"] if tuple(k) not in rejected]
        plan["aggregate_rejected"] = rejected
    return plans
