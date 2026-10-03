"""Fail-closed SHADOW capacity from completed, contemporaneous OPEN lanes.

A tested batch is an upper bound, never a vendor rate limit. Recommendations
apply to one engine's endpoint/cadence contract; a caller must allocate the
shared PPI budget once across engines instead of adding independent limits.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

from co_market_sessions_hf6 import byma_paper_spot_phase
from .common import digest, identity, number, percentile, stamp


def _integer(value, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError("INVALID_INTEGER")
    return value


def safe_capacity(report, *, endpoints, cadence_seconds, window_seconds,
                  required_samples, latency_budget_seconds, min_cycles=3,
                  safety_factor=0.75, as_of):
    """Return no capacity when endpoint, cadence or evidence is unverified.

    Windows bound evidence age, not fabricated observation horizon. Every
    required endpoint is verified on the *same* completed batch/cycles. Useful
    distinct coverage must intersect by full identity. Raw global cycle load
    includes other endpoints in that lane, preserving the serial shared budget.
    Faster cadence than the measured lane is never extrapolated.
    """
    result = {"status": "NO_VERIFICADO", "safe_limit": 0, "slots_by_endpoint": {},
              "reason_codes": [], "configuration_fingerprint": digest({
                  "endpoints": endpoints, "cadence_seconds": str(cadence_seconds),
                  "window_seconds": str(window_seconds), "required_samples": str(required_samples),
                  "latency_budget_seconds": str(latency_budget_seconds), "min_cycles": str(min_cycles),
                  "safety_factor": str(safety_factor),
                  "evidence_digest": report.get("evidence_digest") if isinstance(report, dict) else None}),
              "mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
              "shared_budget_policy": "ALLOCATE_ONCE_ACROSS_ENGINES",
              "production_limit_modified": False}
    try:
        endpoints = tuple(endpoints)
        if (not endpoints or len(set(endpoints)) != len(endpoints)
                or any(x not in {"current", "book", "intraday"} for x in endpoints)):
            raise ValueError("ENDPOINT_CONTRACT_INVALID")
        cadence = number(cadence_seconds, minimum=.001)
        window = number(window_seconds, minimum=.001)
        budget = number(latency_budget_seconds, minimum=.001)
        factor = number(safety_factor, minimum=.001)
        samples = _integer(required_samples, 1)
        cycles_needed = _integer(min_cycles, 3)
        if factor > 1:
            raise ValueError("SAFETY_FACTOR_INVALID")
        now = stamp(as_of)
        config = report["configuration"]
        result["configuration_fingerprint"] = digest({"benchmark": report.get("configuration_fingerprint"),
            "evidence_digest": report.get("evidence_digest"),
            "endpoints": endpoints, "cadence_seconds": cadence, "window_seconds": window,
            "required_samples": samples, "latency_budget_seconds": budget,
            "min_cycles": cycles_needed, "safety_factor": factor})
        if report.get("evidence_digest") != digest({key: value for key, value in report.items()
                                                   if key != "evidence_digest"}):
            raise ValueError("CAPACITY_EVIDENCE_DIGEST_MISMATCH")
        if report["configuration_fingerprint"] != digest(config):
            raise ValueError("CAPACITY_CONFIGURATION_MISMATCH")
        if (report.get("schema_version") != 1 or report.get("mode") != "SHADOW"
                or report.get("real_orders_sent") != 0 or report.get("real_routes") != "NOT_CALLED"
                or report.get("runtime_modified") is not False
                or report.get("market_phase") != "OPEN"
                or report.get("status") != "OPEN_OBSERVATIONS_RECORDED"):
            raise ValueError("OPEN_READONLY_EVIDENCE_REQUIRED")
        if (byma_paper_spot_phase(now) != "OPEN" or
                byma_paper_spot_phase(stamp(report["generated_at"])) != "OPEN"):
            raise ValueError("CAPACITY_SESSION_NOT_OPEN")
        if not 0 <= (now - stamp(report["generated_at"])).total_seconds() <= window:
            raise ValueError("CAPACITY_EVIDENCE_STALE_OR_FUTURE")
        if (config.get("source") != "PPI_PRODUCTION_READONLY_SDK"
                or config.get("parallel_requests") != 1 or config.get("read_retries") != 0
                or not set(endpoints).issubset(config["endpoints"])):
            raise ValueError("CAPACITY_ENDPOINTS_NOT_MEASURED")
        if cadence < number(config["cadence_seconds"], minimum=.001):
            raise ValueError("CAPACITY_FASTER_CADENCE_UNVERIFIED")
        rows = report["observations"]
        wire = report["wire_metrics"]
        reader = report["reader_metrics"]
        wire_count = _integer(wire["requests"], 1)
        if (wire_count != len(rows) + 1 or reader.get("http_allowed") != wire_count
                or reader.get("http_blocked") != 0 or reader.get("login_calls") != 1
                or reader.get("authenticated") is not True or wire.get("retries") != 0
                or wire_count > config["max_requests"]):
            raise ValueError("CAPACITY_WIRE_ACCOUNTING_UNVERIFIED")
        # Any broker/backpressure/session uncertainty invalidates this run's
        # capacity; instrument-specific gaps remain per-identity exclusions.
        if any(row.get("error_code") not in {None, "PPI_INSTRUMENT_NOT_FOUND", "STALE_OR_UNUSABLE_QUOTES"}
               for row in rows):
            raise ValueError("CAPACITY_PROVIDER_ERRORS")
        if report.get("stop_reason") not in {None, "REQUEST_BUDGET_EXHAUSTED", "TIME_BUDGET_EXHAUSTED"}:
            raise ValueError("CAPACITY_RUN_STOPPED_UNSAFE")
        previous = {}
        for row in sorted(rows, key=lambda item: stamp(item["finished_at"])):
            if row["useful"]:
                key = (row["endpoint"], tuple(row["identity"]))
                prior = previous.get(key)
                if row["distinct"] and prior is not None and (
                        row["observation_fingerprint"] == prior[1] or
                        stamp(row["source_at"]) < stamp(prior[0])):
                    raise ValueError("CAPACITY_DISTINCT_EVIDENCE_INVALID")
                previous[key] = (row["source_at"], row["observation_fingerprint"])
            elif row["distinct"]:
                raise ValueError("CAPACITY_DISTINCT_EVIDENCE_INVALID")
        lanes = []
        for lane in report["lanes"]:
            if lane["status"] != "MEASURED_OPEN" or lane["completed_cycles"] < cycles_needed:
                continue
            batch = _integer(lane["batch"], 1)
            if batch not in config["batches"]:
                raise ValueError("CAPACITY_UNTESTED_BATCH")
            lane_rows = [row for row in rows if row["batch"] == batch]
            cycle_rows = [cycle for cycle in report["cycle_metrics"] if cycle["batch"] == batch]
            measured_cycles = _integer(lane["completed_cycles"], cycles_needed)
            if measured_cycles != config["cycles"] or len(cycle_rows) != measured_cycles:
                continue
            if len(lane_rows) != batch * measured_cycles * len(config["endpoints"]):
                continue
            all_keys = set()
            per_cycle = defaultdict(list)
            valid_lane = True
            for row in lane_rows:
                endpoint = row["endpoint"]
                cycle = _integer(row["cycle"], 0)
                key = identity(dict(zip(("ticker", "instrument_type", "market", "currency", "settlement"),
                                         row["identity"])))
                compound = (cycle, endpoint, key)
                if endpoint not in config["endpoints"] or cycle >= measured_cycles or compound in all_keys:
                    valid_lane = False
                    break
                all_keys.add(compound)
                start, finish = stamp(row["started_at"]), stamp(row["finished_at"])
                latency = number(row["latency_seconds"])
                if (row.get("market_phase") != "OPEN" or byma_paper_spot_phase(start) != "OPEN"
                        or byma_paper_spot_phase(finish) != "OPEN" or start > finish or finish > now
                        or not 0 <= (now - start).total_seconds() <= window
                        or abs((finish - start).total_seconds() - latency) > .05):
                    valid_lane = False
                    break
                useful, distinct = row["useful"], row["distinct"]
                if (not isinstance(useful, bool) or not isinstance(distinct, bool)
                        or row["useful_distinct"] != (useful and distinct)):
                    valid_lane = False
                    break
                if useful:
                    source = stamp(row["source_at"])
                    age = (finish - source).total_seconds()
                    if (not 0 <= age <= config["freshness_seconds"]
                            or abs(age - number(row["freshness_seconds"])) > .05
                            or not isinstance(row["observation_fingerprint"], str)
                            or len(row["observation_fingerprint"]) != 64):
                        valid_lane = False
                        break
                per_cycle[cycle].append(row)
            if not valid_lane or len(per_cycle) != measured_cycles:
                continue
            durations, cycle_starts = [], []
            expected_identities = None
            for cycle in sorted(cycle_rows, key=lambda item: item["cycle"]):
                cycle_index = cycle["cycle"]
                observed = per_cycle[cycle_index]
                start, finish = stamp(cycle["started_at"]), stamp(cycle["finished_at"])
                duration = number(cycle["duration_seconds"], minimum=.000001)
                identities = {tuple(row["identity"]) for row in observed}
                if (cycle.get("complete") is not True or len(identities) != batch
                        or cycle["requests"] != len(observed)
                        or len(observed) != batch * len(config["endpoints"])
                        or abs((finish - start).total_seconds() - duration) > .05
                        or any(stamp(row["started_at"]) < start or stamp(row["finished_at"]) > finish
                               for row in observed)
                        or (expected_identities is not None and identities != expected_identities)):
                    valid_lane = False
                    break
                if any(sum(row["endpoint"] == endpoint and tuple(row["identity"]) == key
                           for row in observed) != 1 for endpoint in config["endpoints"] for key in identities):
                    valid_lane = False
                    break
                ordered = sorted(observed, key=lambda item: stamp(item["started_at"]))
                if any(stamp(left["finished_at"]) > stamp(right["started_at"])
                       for left, right in zip(ordered, ordered[1:])):
                    valid_lane = False
                    break
                if sum(row["latency_seconds"] for row in observed) > duration + .05:
                    valid_lane = False
                    break
                expected_identities = identities
                durations.append(duration)
                cycle_starts.append(start)
            if not valid_lane or any((right - left).total_seconds() + .05 < config["cadence_seconds"]
                                     for left, right in zip(cycle_starts, cycle_starts[1:])):
                continue
            slots, endpoint_evidence = {}, {}
            density_eligible = set(expected_identities)
            for endpoint in endpoints:
                endpoint_rows = [row for row in lane_rows if row["endpoint"] == endpoint]
                distinct_count = sum(row["useful_distinct"] for row in endpoint_rows)
                if len(endpoint_rows) < samples or distinct_count < samples:
                    valid_lane = False
                    break
                p95 = percentile([row["latency_seconds"] for row in endpoint_rows], .95)
                if p95 <= 0 or p95 > budget:
                    valid_lane = False
                    break
                fraction = sum(row["useful_distinct"] for row in endpoint_rows) / len(endpoint_rows)
                # This is a coverage estimate at the measured cadence, never
                # actual strategy warmup. The strategy requires its own causal
                # per-identity observations before SIGNAL_READY.
                availability = {}
                for key in expected_identities:
                    per_identity = [row for row in endpoint_rows if tuple(row["identity"]) == key]
                    useful_fraction = sum(row["useful_distinct"] for row in per_identity) / len(per_identity)
                    availability[key] = math.floor(window / cadence * useful_fraction)
                density_eligible &= {key for key, available in availability.items() if available >= samples}
                slots[endpoint] = math.floor(min(batch * fraction * factor, cadence * factor / p95))
                endpoint_evidence[endpoint] = {"samples": len(endpoint_rows), "latency_p95_seconds": p95,
                                               "useful_distinct_samples": distinct_count,
                                               "useful_distinct_fraction": fraction,
                                               "estimated_distinct_samples_per_identity_window_min": min(availability.values())}
            if not valid_lane:
                continue
            joint = []
            for cycle in per_cycle.values():
                useful_sets = [{tuple(row["identity"]) for row in cycle
                                if row["endpoint"] == endpoint and row["useful_distinct"]
                                and tuple(row["identity"]) in density_eligible}
                               for endpoint in endpoints]
                joint.append(len(set.intersection(*useful_sets)) / batch)
            joint_fraction = sum(joint) / len(joint)
            cycle_p95 = percentile(durations, .95)
            # Keep measured endpoint mix/load, even if one engine consumes a
            # subset. Conservative common allocation avoids triple-counting.
            global_slots = math.floor(batch * cadence * factor / cycle_p95)
            useful_slots = math.floor(batch * joint_fraction * factor)
            limit = min(batch, global_slots, useful_slots, *slots.values())
            if limit > 0:
                lanes.append({"safe_limit": limit, "slots_by_endpoint": slots, "tested_batch": batch,
                              "cycle_p95_seconds": cycle_p95, "joint_useful_distinct_fraction": joint_fraction,
                              "endpoint_evidence": endpoint_evidence,
                              "oldest_observation_at": min(stamp(row["started_at"]) for row in lane_rows).isoformat(),
                              "shared_global_slots": global_slots, "completed_cycles": measured_cycles})
        if not lanes:
            raise ValueError("CAPACITY_INSUFFICIENT_COMPLETE_OPEN_EVIDENCE")
        chosen = max(lanes, key=lambda item: item["safe_limit"])
        result.update(chosen, status="SHADOW_RECOMMENDATION", reason_codes=[])
        result["evidence_digest"] = report["evidence_digest"]
        result["measured_at"] = report["generated_at"]
        result["generated_at"] = now.isoformat()
        result["validated_as_of"] = now.isoformat()
        local = now.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
        session_close = datetime.combine(local.date(), datetime.min.time(), tzinfo=local.tzinfo).replace(hour=17)
        result["expires_at"] = min(stamp(result["oldest_observation_at"]) + timedelta(seconds=window),
                                   session_close.astimezone(now.tzinfo)).isoformat()
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        # Error text is produced locally by validation only; external data
        # parsing messages never enter reports.
        local_codes = {"ENDPOINT_CONTRACT_INVALID", "INVALID_INTEGER", "SAFETY_FACTOR_INVALID",
                       "CAPACITY_EVIDENCE_DIGEST_MISMATCH", "CAPACITY_CONFIGURATION_MISMATCH",
                       "OPEN_READONLY_EVIDENCE_REQUIRED", "CAPACITY_SESSION_NOT_OPEN",
                       "CAPACITY_EVIDENCE_STALE_OR_FUTURE", "CAPACITY_ENDPOINTS_NOT_MEASURED",
                       "CAPACITY_FASTER_CADENCE_UNVERIFIED", "CAPACITY_WIRE_ACCOUNTING_UNVERIFIED",
                       "CAPACITY_DISTINCT_EVIDENCE_INVALID",
                       "CAPACITY_PROVIDER_ERRORS", "CAPACITY_RUN_STOPPED_UNSAFE", "CAPACITY_UNTESTED_BATCH",
                       "CAPACITY_INSUFFICIENT_COMPLETE_OPEN_EVIDENCE"}
        code = str(error)
        result["reason_codes"] = [code if code in local_codes else "CAPACITY_EVIDENCE_INVALID"]
    return result
