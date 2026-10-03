"""Bounded PPI market-data measurement. This never changes runtime capacity.

The SDK retries 401 and discards HTTP status in its public exceptions. A local
response gate preserves that status *before* SDK handling, prevents those
retries, and persists only counters and normalized observation fingerprints.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from json import JSONDecodeError
import time
from types import SimpleNamespace

import requests

from bd_ppi_readonly_guard import (ProductionMarketReader, instrument_not_found,
                                   session_invalid, ReadOnlyPolicyViolation)
from co_market_sessions_hf6 import byma_paper_spot_phase, byma_schedule_status
from .common import digest, identity, number, percentile, stamp

ENDPOINTS = ("current", "book", "intraday")
BATCHES = (20, 40, 60, 80, 100)
IDENTITY_FIELDS = ("ticker", "instrument_type", "market", "currency", "settlement")


class BenchmarkReadError(RuntimeError):
    def __init__(self, code, status=None):
        super().__init__(code)
        self.code = code
        self.response = SimpleNamespace(status_code=status)


def error_code(error):
    """Never persist arbitrary exception text, SDK payloads or credentials."""
    if isinstance(error, BenchmarkReadError):
        return error.code
    if session_invalid(error):
        return "PPI_SESSION_INVALID"
    if instrument_not_found(error):
        return "PPI_INSTRUMENT_NOT_FOUND"
    current = error
    for _ in range(6):
        status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int) and 100 <= status <= 599:
            return "PPI_SESSION_INVALID" if status in {401, 403} else f"PPI_HTTP_{status}"
        if isinstance(current, (JSONDecodeError, requests.exceptions.JSONDecodeError)):
            return "PPI_EMPTY_OR_NON_JSON"
        if isinstance(current, requests.exceptions.Timeout):
            return "PPI_HTTP_408"
        if isinstance(current, ReadOnlyPolicyViolation):
            return "PPI_READONLY_POLICY_BLOCKED"
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if current is None:
            break
    return "PPI_READ_ERROR"


@contextmanager
def _wire_gate(*, max_requests, deadline, monotonic):
    """Serial CLI only; sits above the existing installed HTTP guard."""
    original = requests.sessions.Session.send
    state = {"requests": 0, "status_codes": Counter(), "retries": 0}

    def send(session, request, **kwargs):
        if state["requests"] >= max_requests:
            raise BenchmarkReadError("REQUEST_BUDGET_EXHAUSTED")
        if monotonic() >= deadline:
            raise BenchmarkReadError("TIME_BUDGET_EXHAUSTED")
        state["requests"] += 1
        response = original(session, request, **kwargs)
        status = response.status_code
        state["status_codes"][str(status)] += 1
        # Stop session handling before SDK refresh/print/retry. Read failures
        # retain only the HTTP status; the SDK never sees their raw body.
        if status in {401, 403}:
            response.close()
            raise BenchmarkReadError("PPI_SESSION_INVALID", status)
        if status != 200:
            if status in {400, 404}:
                try:
                    missing = response.json() == "Instrument not found"
                except (ValueError, TypeError):
                    missing = False
                if missing:
                    response.close()
                    raise BenchmarkReadError("PPI_INSTRUMENT_NOT_FOUND", status)
            response.close()
            raise BenchmarkReadError(f"PPI_HTTP_{status}", status)
        return response

    requests.sessions.Session.send = send
    try:
        yield state
    finally:
        requests.sessions.Session.send = original


def _config(*, batches, endpoints, cycles, cadence_seconds, freshness_seconds,
            max_requests, max_runtime_seconds, repeated_server_errors):
    batches = tuple(batches)
    endpoints = tuple(endpoints)
    if (not batches or len(set(batches)) != len(batches)
            or any(isinstance(x, bool) or x not in BATCHES for x in batches)
            or batches != tuple(sorted(batches))):
        raise ValueError("BENCHMARK_BATCHES_INVALID")
    if not endpoints or len(set(endpoints)) != len(endpoints) or any(x not in ENDPOINTS for x in endpoints):
        raise ValueError("BENCHMARK_ENDPOINTS_INVALID")
    if isinstance(cycles, bool) or not isinstance(cycles, int) or not 3 <= cycles <= 10:
        raise ValueError("BENCHMARK_CYCLES_INVALID")
    cadence = number(cadence_seconds, minimum=1)
    freshness = number(freshness_seconds, minimum=1)
    runtime = number(max_runtime_seconds, minimum=60)
    if cadence > 600 or freshness > 600 or runtime > 1800:
        raise ValueError("BENCHMARK_TIME_BOUND_INVALID")
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or not 1 <= max_requests <= 3000:
        raise ValueError("BENCHMARK_REQUEST_BOUND_INVALID")
    if (isinstance(repeated_server_errors, bool) or not isinstance(repeated_server_errors, int)
            or not 1 <= repeated_server_errors <= 3):
        raise ValueError("BENCHMARK_CIRCUIT_BOUND_INVALID")
    return {"batches": list(batches), "endpoints": list(endpoints), "cycles": cycles,
            "cadence_seconds": cadence, "freshness_seconds": freshness,
            "max_requests": max_requests, "max_runtime_seconds": runtime,
            "repeated_server_errors": repeated_server_errors, "parallel_requests": 1,
            "read_retries": 0, "source": "PPI_PRODUCTION_READONLY_SDK"}


def _candidates(records):
    """PPI's route lacks market/currency: ambiguous literal requests excluded."""
    grouped = defaultdict(list)
    seen = set()
    for raw in records:
        key = identity(raw)
        if key in seen:
            raise ValueError("BENCHMARK_DUPLICATE_IDENTITY")
        seen.add(key)
        row = dict(zip(IDENTITY_FIELDS, key))
        if row["market"] != "BYMA" or row["instrument_type"] not in {"ACCIONES", "CEDEARS", "ETFS"}:
            raise ValueError("BENCHMARK_SPOT_SCOPE_REQUIRED")
        provider_type = str(raw.get("provider_instrument_type") or row["instrument_type"]).upper()
        if provider_type not in {"ACCIONES", "CEDEARS", "ETF", "ETFS"}:
            raise ValueError("BENCHMARK_PROVIDER_TYPE_INVALID")
        row["provider_instrument_type"] = provider_type
        grouped[(key[0], provider_type, key[-1])].append(row)
    accepted = [rows[0] for rows in grouped.values() if len(rows) == 1]
    excluded = [{"identity": list(identity(row)), "reason_code": "AMBIGUOUS_PPI_REQUEST_IDENTITY"}
                for rows in grouped.values() if len(rows) > 1 for row in rows]
    return sorted(accepted, key=identity), excluded


def _observation(endpoint, payload, row, received, freshness_seconds, previous):
    # Reuse audited SDK-shape normalization; receipts never stand in for
    # missing provider timestamps. No raw response or market prices persisted.
    if payload in (None, "", [], {}):
        raise BenchmarkReadError("PPI_EMPTY_OR_NON_JSON")
    if endpoint == "intraday":
        from cf_intraday_scalping import normalize_payload
        points = normalize_payload(payload, received_at=received.isoformat())
        if not points:
            raise BenchmarkReadError("PPI_EMPTY_OR_NON_JSON")
        last = points[-1]
        source_at = stamp(last[0])
        normalized = [last[0], str(last[1]), str(last[2])]
        valid = True
    else:
        from bf_production_paper_observer import normalize_quote
        if not isinstance(payload, dict):
            raise BenchmarkReadError("PPI_INVALID_PAYLOAD")
        quote = normalize_quote(row["ticker"], row["instrument_type"], row["settlement"],
                                payload if endpoint == "current" else {},
                                payload if endpoint == "book" else {})
        source_at = stamp(quote.trade_at if endpoint == "current" else quote.book_at)
        if endpoint == "current":
            normalized = [source_at.isoformat(), str(quote.last)]
            valid = quote.last > 0
        else:
            normalized = [source_at.isoformat(), *(str(x) for x in
                          (quote.bid, quote.ask, quote.bid_size, quote.ask_size))]
            valid = 0 < quote.bid <= quote.ask and quote.bid_size > 0 and quote.ask_size > 0
    age = (received - source_at).total_seconds()
    fingerprint = digest(normalized)
    useful = bool(valid and 0 <= age <= freshness_seconds)
    # Older changed payloads are revisions, never new useful observations.
    distinct = bool(useful and (previous is None or
                    (source_at >= stamp(previous[0]) and fingerprint != previous[1])))
    return {"source_at": source_at.isoformat(), "freshness_seconds": age,
            "useful": useful, "distinct": distinct, "useful_distinct": useful and distinct,
            "observation_fingerprint": fingerprint,
            "error_code": None if useful else "STALE_OR_UNUSABLE_QUOTES"}


def _summary(rows, elapsed_seconds):
    latencies = [row["latency_seconds"] for row in rows]
    fresh = [row["freshness_seconds"] for row in rows if row["freshness_seconds"] is not None]
    count = len(rows)
    return {"requests": count, "requests_per_minute": count * 60 / max(elapsed_seconds, 1e-9),
            "latency_seconds": {"p50": percentile(latencies, .5), "p95": percentile(latencies, .95),
                                "p99": percentile(latencies, .99), "max": max(latencies, default=None)},
            "freshness_seconds": {"p50": percentile(fresh, .5), "p95": percentile(fresh, .95),
                                  "max": max(fresh, default=None)},
            "usable_observation_fraction": sum(row["useful"] for row in rows) / count if count else 0,
            "useful_distinct_fraction": sum(row["useful_distinct"] for row in rows) / count if count else 0,
            "error_codes": dict(Counter(row["error_code"] for row in rows if row["error_code"]))}


def run_benchmark(candidates, *, api_key="", api_secret="", batches=BATCHES,
                  endpoints=ENDPOINTS, cycles=3, cadence_seconds=60, freshness_seconds=120,
                  max_requests=2701, max_runtime_seconds=1800, repeated_server_errors=2,
                  reader_factory=ProductionMarketReader,
                  clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
                  pause=time.sleep):
    configuration = _config(batches=batches, endpoints=endpoints, cycles=cycles,
        cadence_seconds=cadence_seconds, freshness_seconds=freshness_seconds,
        max_requests=max_requests, max_runtime_seconds=max_runtime_seconds,
        repeated_server_errors=repeated_server_errors)
    started = stamp(clock())
    start_mono = monotonic()
    phase = byma_paper_spot_phase(started)
    report = {"schema_version": 1, "status": "NO_VERIFICADO", "market_phase": phase,
              "mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
              "started_at": started.isoformat(), "configuration": configuration,
              "calendar": byma_schedule_status(), "observations": [], "cycle_metrics": [],
              "lanes": [], "candidate_exclusions": [], "stop_reason": None,
              "capacity_claim": "NO_VERIFICADO", "runtime_modified": False}
    report["configuration_fingerprint"] = digest(configuration)
    # Actual calendar gate precedes credential access, reader construction and
    # candidate inspection. A Saturday/PREOPEN run cannot touch PPI.
    if phase != "OPEN":
        report["stop_reason"] = "MARKET_NOT_OPEN"
    elif not api_key or not api_secret:
        report["stop_reason"] = "PPI_CREDENTIALS_UNAVAILABLE"
    else:
        records, report["candidate_exclusions"] = _candidates(candidates)
        if not records:
            report["stop_reason"] = "CANDIDATES_UNAVAILABLE"
        else:
            reader = None
            logical_requests = server_errors = 0
            previous = {}
            report["candidate_manifest_fingerprint"] = digest(
                [list(identity(record)) for record in records])
            deadline = start_mono + configuration["max_runtime_seconds"]
            try:
                reader = reader_factory(api_key, api_secret)
                with _wire_gate(max_requests=max_requests, deadline=deadline, monotonic=monotonic) as wire:
                    try:
                        reader.login_once()
                    except Exception as error:
                        report["stop_reason"] = error_code(error)
                    for batch in configuration["batches"]:
                        if report["stop_reason"]:
                            break
                        if len(records) < batch:
                            report["lanes"].append({"batch": batch, "status": "NO_VERIFICADO",
                                                    "reason_code": "INSUFFICIENT_CANDIDATES"})
                            continue
                        lane = {"batch": batch, "status": "INCOMPLETE", "completed_cycles": 0}
                        report["lanes"].append(lane)
                        prior_cycle_start = None
                        for cycle in range(configuration["cycles"]):
                            if report["stop_reason"]:
                                break
                            if prior_cycle_start is not None:
                                remaining = prior_cycle_start + configuration["cadence_seconds"] - monotonic()
                                # Short waits keep the runner cancellable and recheck market/bounds.
                                while remaining > 0 and monotonic() < deadline:
                                    pause(min(remaining, 5))
                                    remaining = prior_cycle_start + configuration["cadence_seconds"] - monotonic()
                            cycle_start = stamp(clock())
                            cycle_mono = monotonic()
                            prior_cycle_start = cycle_mono
                            first_index = len(report["observations"])
                            for row in records[:batch]:
                                for endpoint in configuration["endpoints"]:
                                    at = stamp(clock())
                                    if byma_paper_spot_phase(at) != "OPEN":
                                        report["stop_reason"] = "MARKET_NOT_OPEN"
                                    elif logical_requests >= max_requests - 1 or wire["requests"] >= max_requests:
                                        report["stop_reason"] = "REQUEST_BUDGET_EXHAUSTED"
                                    # Existing transport timeouts: 10s connect + 30s read.
                                    elif monotonic() + 40 >= deadline:
                                        report["stop_reason"] = "TIME_BUDGET_EXHAUSTED"
                                    if report["stop_reason"]:
                                        break
                                    request_mono = monotonic()
                                    result = {"batch": batch, "cycle": cycle, "endpoint": endpoint,
                                              "identity": list(identity(row)), "started_at": at.isoformat(),
                                              "market_phase": "OPEN", "source_at": None,
                                              "freshness_seconds": None, "useful": False, "distinct": False,
                                              "useful_distinct": False, "observation_fingerprint": None}
                                    logical_requests += 1
                                    try:
                                        payload = getattr(reader, endpoint)(row["ticker"],
                                            row["provider_instrument_type"], row["settlement"])
                                        received = stamp(clock())
                                        key = (endpoint, identity(row))
                                        result.update(_observation(endpoint, payload, row, received,
                                            configuration["freshness_seconds"], previous.get(key)))
                                        if result["useful"]:
                                            previous[key] = (result["source_at"], result["observation_fingerprint"])
                                    except (ValueError, TypeError, KeyError) as error:
                                        result["error_code"] = (error_code(error)
                                            if isinstance(error, (JSONDecodeError, requests.exceptions.JSONDecodeError))
                                            else "PPI_INVALID_PAYLOAD")
                                    except Exception as error:
                                        result["error_code"] = error_code(error)
                                    result["finished_at"] = stamp(clock()).isoformat()
                                    result["latency_seconds"] = max(0, monotonic() - request_mono)
                                    if byma_paper_spot_phase(stamp(result["finished_at"])) != "OPEN":
                                        result.update(useful=False, useful_distinct=False, distinct=False,
                                                      error_code="MARKET_NOT_OPEN")
                                        report["stop_reason"] = "MARKET_NOT_OPEN"
                                    report["observations"].append(result)
                                    code = result["error_code"]
                                    if code in {"PPI_HTTP_429", "PPI_SESSION_INVALID", "PPI_READONLY_POLICY_BLOCKED",
                                                "REQUEST_BUDGET_EXHAUSTED", "TIME_BUDGET_EXHAUSTED"}:
                                        report["stop_reason"] = code
                                    if code and (code == "PPI_HTTP_408" or code.startswith("PPI_HTTP_5")):
                                        server_errors += 1
                                        if server_errors >= configuration["repeated_server_errors"]:
                                            report["stop_reason"] = "PPI_REPEATED_408_5XX"
                                    if report["stop_reason"]:
                                        break
                                if report["stop_reason"]:
                                    break
                            observed = len(report["observations"]) - first_index
                            complete = observed == batch * len(configuration["endpoints"])
                            if complete:
                                lane["completed_cycles"] += 1
                            report["cycle_metrics"].append({"batch": batch, "cycle": cycle,
                                "started_at": cycle_start.isoformat(), "finished_at": stamp(clock()).isoformat(),
                                "duration_seconds": max(0, monotonic() - cycle_mono),
                                "requests": observed, "complete": complete})
                        if lane["completed_cycles"] == configuration["cycles"] and not report["stop_reason"]:
                            lane["status"] = "MEASURED_OPEN"
                    report["wire_metrics"] = {**wire, "status_codes": dict(wire["status_codes"])}
                    report["reader_metrics"] = reader.metrics
            except Exception as error:
                report["stop_reason"] = error_code(error)
            finally:
                if reader is not None:
                    reader.close()
            if any(lane["status"] == "MEASURED_OPEN" for lane in report["lanes"]):
                report["status"] = "OPEN_OBSERVATIONS_RECORDED"
    report["generated_at"] = stamp(clock()).isoformat()
    report["elapsed_seconds"] = max(0, monotonic() - start_mono)
    report["global_metrics"] = _summary(report["observations"], report["elapsed_seconds"])
    report["endpoint_metrics"] = {endpoint: _summary(
        [row for row in report["observations"] if row["endpoint"] == endpoint], report["elapsed_seconds"])
        for endpoint in configuration["endpoints"]}
    identities = defaultdict(list)
    for row in report["observations"]:
        identities[tuple(row["identity"])].append(row)
    report["identity_metrics"] = [{"identity": list(key), **_summary(rows, report["elapsed_seconds"]),
                                   "endpoints": {endpoint: _summary(
                                       [row for row in rows if row["endpoint"] == endpoint], report["elapsed_seconds"])
                                       for endpoint in configuration["endpoints"]}}
                                  for key, rows in sorted(identities.items())]
    report["evidence_digest"] = digest(report)
    return report
