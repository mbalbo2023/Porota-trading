"""Auditable discovery rotation that does not consume an IOL REST slot."""
from bisect import bisect_left
from collections import Counter, defaultdict

from .common import stamp


SCHEMA = "rc6.iol-stream-coverage.v1"


def plan_stream_coverage(identities, priority, previous, *, slots, rest_budget):
    """Give discovery the remaining stream slots after protecting deep reads.

    The cursor is an identity rather than a catalog offset. Inserts or removals
    therefore do not reset the sweep, and an unacknowledged slice is retried.
    """
    eligible = tuple(sorted(set(map(tuple, identities))))
    eligible_set = set(eligible)
    protected = tuple(key for key in dict.fromkeys(map(tuple, priority)) if key in eligible_set)[:slots]
    protected_set = set(protected)
    remaining_slots = slots - len(protected)
    previous = previous if isinstance(previous, dict) and previous.get("schema") == SCHEMA else {}
    previous_targets = ()
    if previous.get("prepared") is True:
        prior_keys = {}
        for name in ("priority", "discovery"):
            items = previous.get(name)
            if isinstance(items, (tuple, list)):
                for item in items:
                    if (isinstance(item, (tuple, list)) and len(item) == 5
                            and all(isinstance(part, str) for part in item) and tuple(item) in eligible_set):
                        prior_keys[tuple(item)] = None
        previous_targets = tuple(prior_keys)[:slots]
    anchor = previous.get("next_identity" if previous.get("prepared") is True else "start_identity")
    valid_anchor = (isinstance(anchor, (tuple, list)) and len(anchor) == 5
                    and all(isinstance(part, str) for part in anchor))
    start = bisect_left(eligible, tuple(anchor)) if valid_anchor else 0
    start = start % len(eligible) if eligible else 0
    next_index = start
    discovery = []
    for _ in range(len(eligible)):
        if len(discovery) >= remaining_slots:
            break
        key = eligible[next_index]
        next_index = (next_index + 1) % len(eligible)
        if key not in protected_set:
            discovery.append(key)

    cold_total = len(eligible) - len(protected)
    scanner_slots = rest_budget["scanner_batch_limit"]
    per_key = rest_budget["scanner_calls_per_instrument"]
    batches = rest_budget["batches_per_minute"]
    scanner_rate = scanner_slots * per_key * batches
    return {
        "schema": SCHEMA,
        "eligible": len(eligible),
        "eligible_by_family": dict(Counter(key[1] for key in eligible)),
        "stream_slots": slots,
        "trade_connections_for_concurrent_coverage": ((len(eligible) + slots - 1) // slots
                                                      if slots else None),
        "priority": protected,
        "discovery": tuple(discovery),
        "previous_targets": previous_targets,
        "start_identity": eligible[start] if eligible else None,
        "next_identity": eligible[next_index] if eligible else None,
        "nominal_discovery_cycles": ((cold_total + remaining_slots - 1) // remaining_slots
                                     if remaining_slots else (None if cold_total else 0)),
        "rest_scanner_keys_per_minute": scanner_slots * batches,
        "nominal_rest_round_seconds": ((60 * len(eligible) * per_key + scanner_rate - 1) // scanner_rate
                                       if scanner_rate else None),
        "prepared": False,
    }


def stream_batches(plan, limit):
    keys = (*plan["priority"], *plan["discovery"])
    return tuple(keys[i:i + limit] for i in range(0, len(keys), limit))


def coverage_feasibility(coverage, *, cadence_seconds, freshness_seconds, window_minutes, required_samples):
    """Compute upper bounds for a strategy that needs a full REST read per sample."""
    cycles = coverage["nominal_discovery_cycles"]
    minimum_rotation = cycles * cadence_seconds if cycles is not None else None
    full_reads_per_minute = coverage["rest_scanner_keys_per_minute"]
    eligible = coverage["eligible"]
    per_key_samples = (full_reads_per_minute * window_minutes) // eligible if eligible else 0
    return {"dispatch_interval_seconds": cadence_seconds, "best_case_rotation_seconds": minimum_rotation,
            "native_data_freshness_seconds": freshness_seconds,
            "rotation_within_freshness": minimum_rotation is not None and minimum_rotation <= freshness_seconds,
            "rest_max_samples_per_key_in_signal_window": per_key_samples,
            "signal_required_samples": required_samples, "signal_window_minutes": window_minutes,
            "rest_full_signal_window_possible": per_key_samples >= required_samples}


def follow_fresh_discovery(active_plan, coverage, states):
    """Use one existing cold REST slot for a trade just seen in the stream."""
    if not active_plan or not active_plan["cold"]:
        return
    cold = active_plan["cold"][0]
    scopes = active_plan["read_scopes"]
    scope = next((item for item in scopes if tuple(item["request"]) == tuple(cold)), None)
    if not scope or scope["priority"] != "DISCOVERY":
        return
    original = tuple(scope["identity"])
    if (original not in coverage["priority"] or states.get(original) == "TRADE_FRESH"
            or original in map(tuple, active_plan["next_iol_subscriptions"])):
        return
    reserved = set(coverage["priority"])
    already_planned = {tuple(item) for name in ("warm", "pinned", "opening_identities")
                       for item in active_plan[name]}
    for identity, state in states.items():
        request = (identity[0], identity[1], identity[4])
        if state != "TRADE_FRESH" or identity in reserved or request in already_planned:
            continue
        scope.update(request=request, identity=identity)
        active_plan["cold"] = [request, *active_plan["cold"][1:]]
        if identity not in coverage["discovery"]:
            coverage["priority"] = tuple(identity if key == original else key for key in coverage["priority"])
        coverage["rest_followed_trade"] = identity
        return cold, request


def current_trade_receipts(rows, *, at, max_age_seconds):
    """Summarize observed data separately from planned or scheduled keys."""
    captured_at = stamp(at)
    statuses = {}
    by_family = defaultdict(Counter)
    seen = []
    for row in rows:
        key = tuple(row["identity"])
        state = row["state"]
        if state == "TRADE_OBSERVED":
            try:
                age = (captured_at - stamp(row["trade_at"])).total_seconds()
            except (KeyError, TypeError, ValueError, OverflowError):
                state = "TRADE_CLOCK_UNVERIFIED"
            else:
                state = "TRADE_FRESH" if 0 <= age <= max_age_seconds else "TRADE_OUTSIDE_WINDOW"
        statuses[key] = state
        by_family[key[1]][state] += 1
        seen.append({"identity": key, "state": state, "trade_at": row.get("trade_at")})
    return statuses, {"observed_at": captured_at.isoformat(), "by_state": dict(Counter(statuses.values())),
                      "by_family": {family: dict(counts) for family, counts in by_family.items()},
                      "per_identity": seen}


def book_followup(trade_received_at, book_at, captured_at):
    result = {"book_at": book_at, "captured_at": captured_at, "trade_to_capture_seconds": None}
    if trade_received_at and book_at and captured_at:
        try:
            latency = (stamp(captured_at) - stamp(trade_received_at)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return result
        if latency >= 0:
            result["trade_to_capture_seconds"] = round(latency, 3)
    return result
