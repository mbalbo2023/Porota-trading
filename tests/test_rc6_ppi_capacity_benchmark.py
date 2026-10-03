"""Real SDK/guard over a fake wire: no live credentials or provider traffic."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader, ReadOnlyPolicyViolation, ReadOnlyTransportGuard
from rc6_dynamic_universe.benchmark import run_benchmark
from rc6_dynamic_universe.capacity import safe_capacity
from rc6_dynamic_universe.common import digest


class Clock:
    def __init__(self, at="2026-10-05T14:00:00+00:00"):
        self.at = datetime.fromisoformat(at)
        self.seconds = 0.

    def now(self):
        return self.at + timedelta(seconds=self.seconds)

    def mono(self):
        return self.seconds

    def advance(self, seconds):
        self.seconds += seconds


def candidates(count=100):
    return [{"ticker": f"TEST{index:03d}", "instrument_type": "ACCIONES", "market": "BYMA",
             "currency": "ARS", "settlement": "A-24HS"} for index in range(count)]


@pytest.fixture
def wire(monkeypatch):
    clock = Clock()
    calls = []
    behavior = {"status": 200, "mode": "fresh", "latency": .02, "fail_calls": set(),
                "endpoint": None, "ticker": None}

    def send(adapter, request, **kwargs):
        calls.append((request.method, request.url))
        endpoint = urlsplit(request.url).path.lower().rsplit("/", 1)[-1]
        query = {key.lower(): value[0] for key, value in parse_qs(urlsplit(request.url).query).items()}
        clock.advance(behavior["latency"])
        event = clock.now()
        if behavior["mode"] == "stale":
            event -= timedelta(minutes=10)
        elif behavior["mode"] == "future":
            event += timedelta(minutes=1)
        elif behavior["mode"] == "repeated":
            event = clock.at
        source = event.isoformat()
        if endpoint == "loginapi":
            payload = {"accessToken": "NEVER_PERSIST_THIS_TOKEN", "refreshToken": "NO_RETRY_TOKEN"}
            status = 200
        else:
            selected = (behavior["endpoint"] in {None, endpoint}
                        and behavior["ticker"] in {None, query.get("ticker")}
                        and (not behavior["fail_calls"] or len(calls) in behavior["fail_calls"]))
            status = behavior["status"] if selected else 200
            if status != 200:
                payload = "Instrument not found" if behavior["mode"] == "missing" else "RAW_PRIVATE_BROKER_BODY"
            elif behavior["mode"] == "empty" and selected:
                payload = ""
            elif endpoint == "current":
                payload = {"date": source, "price": 100}
            elif endpoint == "book":
                payload = {"date": source, "bids": [{"price": 99, "quantity": 20}],
                           "offers": [{"price": 101, "quantity": 20}]}
            else:
                payload = [{"date": source, "price": 100, "volume": 10}]
        response = requests.Response()
        response.status_code = status
        response.url = request.url
        response.request = request
        response._content = (b"NOT JSON PRIVATE BODY" if behavior["mode"] == "nonjson" and endpoint != "loginapi"
                             else (json.dumps(payload).encode() if payload != "" else b""))
        response._content_consumed = True
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return clock, calls, behavior


def measure(wire, **kwargs):
    clock, _, _ = wire
    options = dict(api_key="PRIVATE_KEY", api_secret="PRIVATE_SECRET", batches=(20,),
                   clock=clock.now, monotonic=clock.mono, pause=clock.advance,
                   max_runtime_seconds=1800, cadence_seconds=10)
    options.update(kwargs)
    return run_benchmark(candidates(), **options)


def capacity(report, clock, **kwargs):
    options = dict(endpoints=("current", "book", "intraday"), cadence_seconds=10,
                   window_seconds=300, required_samples=15, latency_budget_seconds=1,
                   as_of=clock.now())
    options.update(kwargs)
    return safe_capacity(report, **options)


def reseal(report):
    report.pop("evidence_digest", None)
    report["evidence_digest"] = digest(report)
    return report


@pytest.mark.parametrize("at,phase", [
    ("2026-10-03T14:00:00+00:00", "CLOSED"),  # actual Saturday
    ("2026-10-05T13:20:00+00:00", "PREOPEN"),
    ("2026-11-09T14:00:00+00:00", "CLOSED"),  # audited exceptional calendar closure
    ("2027-10-05T14:00:00+00:00", "CLOSED"),  # unknown year fails closed
])
def test_actual_calendar_before_credentials_network_or_reader(at, phase):
    clock = Clock(at)

    def forbidden(*args, **kwargs):
        raise AssertionError("reader/network was touched outside OPEN")

    report = run_benchmark(None, api_key="unused", api_secret="unused", reader_factory=forbidden,
                           clock=clock.now, monotonic=clock.mono, pause=clock.advance)
    assert report["market_phase"] == phase
    assert report["status"] == "NO_VERIFICADO"
    assert report["stop_reason"] == "MARKET_NOT_OPEN"
    assert report["observations"] == []
    assert report["real_orders_sent"] == 0 and report["real_routes"] == "NOT_CALLED"


def test_credentials_absent_has_no_network(wire):
    clock, calls, _ = wire
    report = measure(wire, api_key="", api_secret="")
    assert report["stop_reason"] == "PPI_CREDENTIALS_UNAVAILABLE" and calls == []
    assert capacity(report, clock)["safe_limit"] == 0


def test_three_endpoints_real_sdk_normalization_global_and_identity_counters(wire):
    clock, calls, _ = wire
    before_request = requests.sessions.Session.request
    before_send = requests.sessions.Session.send
    before_adapter = requests.adapters.HTTPAdapter.send
    report = measure(wire)
    assert report["status"] == "OPEN_OBSERVATIONS_RECORDED"
    assert len(calls) == 181
    assert report["wire_metrics"]["requests"] == 181
    assert report["reader_metrics"] == {"http_allowed": 181, "http_blocked": 0,
                                         "login_calls": 1, "authenticated": True}
    assert report["global_metrics"]["requests"] == 180
    assert report["global_metrics"]["requests_per_minute"] > 0
    assert all(report["global_metrics"]["latency_seconds"][field] > 0
               for field in ("p50", "p95", "p99", "max"))
    assert set(report["endpoint_metrics"]) == {"current", "book", "intraday"}
    assert len(report["identity_metrics"]) == 20
    assert all(row["requests"] == 9 and row["useful_distinct_fraction"] == 1
               for row in report["identity_metrics"])
    assert all(row["usable_observation_fraction"] == 1 for row in report["endpoint_metrics"].values())
    assert all(row["complete"] for row in report["cycle_metrics"])
    assert requests.sessions.Session.request is before_request
    assert requests.sessions.Session.send is before_send
    assert requests.adapters.HTTPAdapter.send is before_adapter
    text = json.dumps(report)
    for private in ("PRIVATE_KEY", "PRIVATE_SECRET", "NEVER_PERSIST_THIS_TOKEN", "RAW_PRIVATE_BROKER_BODY"):
        assert private not in text
    result = capacity(report, clock)
    assert result["status"] == "SHADOW_RECOMMENDATION"
    assert result["safe_limit"] == 15
    assert result["tested_batch"] == 20
    assert result["slots_by_endpoint"] == {"current": 15, "book": 15, "intraday": 15}
    assert result["expires_at"] > result["generated_at"]
    assert datetime.fromisoformat(result["expires_at"]) <= datetime.fromisoformat(
        result["oldest_observation_at"]) + timedelta(seconds=300)
    assert result["production_limit_modified"] is False


def test_empirically_larger_batch_supported_without_twenty_rate_limit_claim(wire):
    clock, _, _ = wire
    report = measure(wire, batches=(20, 40, 60, 80, 100), cadence_seconds=60)
    result = capacity(report, clock, cadence_seconds=60, window_seconds=1800)
    assert len(report["lanes"]) == 5
    assert result["status"] == "SHADOW_RECOMMENDATION"
    assert result["safe_limit"] == 75 and result["tested_batch"] == 100
    assert report["capacity_claim"] == "NO_VERIFICADO"  # tooling never promotes capacity


@pytest.mark.parametrize("status,code,max_calls", [
    (429, "PPI_HTTP_429", 2), (401, "PPI_SESSION_INVALID", 2),
    (403, "PPI_SESSION_INVALID", 2), (408, "PPI_REPEATED_408_5XX", 3),
    (500, "PPI_REPEATED_408_5XX", 3), (503, "PPI_REPEATED_408_5XX", 3),
])
def test_http_errors_retained_circuit_breaker_and_no_sdk_retry(wire, capsys, status, code, max_calls):
    clock, calls, behavior = wire
    behavior["status"] = status
    report = measure(wire)
    assert report["stop_reason"] == code
    assert len(calls) == max_calls
    assert report["global_metrics"]["error_codes"]
    assert report["global_metrics"]["usable_observation_fraction"] == 0
    assert capacity(report, clock)["safe_limit"] == 0
    assert "RAW_PRIVATE_BROKER_BODY" not in json.dumps(report)
    assert "RAW_PRIVATE_BROKER_BODY" not in capsys.readouterr().out
    assert not any("refreshtoken" in url.lower() for _, url in calls)


@pytest.mark.parametrize("mode,code", [("empty", "PPI_EMPTY_OR_NON_JSON"),
                                       ("nonjson", "PPI_EMPTY_OR_NON_JSON")])
def test_empty_nonjson_not_provider_zero_and_fail_closed(wire, mode, code):
    clock, _, behavior = wire
    behavior["mode"] = mode
    report = measure(wire)
    assert report["endpoint_metrics"]["current"]["error_codes"] == {code: 60}
    assert capacity(report, clock)["reason_codes"] == ["CAPACITY_PROVIDER_ERRORS"]
    assert "NOT JSON PRIVATE BODY" not in json.dumps(report)


def test_instrument_not_found_scoped_per_identity_not_global_failure(wire):
    clock, calls, behavior = wire
    behavior.update(status=404, mode="missing", endpoint="intraday", ticker="TEST000")
    report = measure(wire)
    assert report["stop_reason"] is None and len(calls) == 181
    failed = [row for row in report["identity_metrics"] if row["identity"][0] == "TEST000"][0]
    assert failed["endpoints"]["intraday"]["error_codes"] == {"PPI_INSTRUMENT_NOT_FOUND": 3}
    assert all(row["error_codes"] == {} for row in report["identity_metrics"]
               if row["identity"][0] != "TEST000")
    assert capacity(report, clock)["safe_limit"] == 14


@pytest.mark.parametrize("mode", ["stale", "future", "repeated"])
def test_useful_distinct_measures_fresh_updates_not_requests(wire, mode):
    clock, _, behavior = wire
    behavior["mode"] = mode
    report = measure(wire)
    result = capacity(report, clock)
    if mode == "repeated":
        assert report["global_metrics"]["usable_observation_fraction"] == 1
        assert report["global_metrics"]["useful_distinct_fraction"] == pytest.approx(1 / 3)
        assert result["safe_limit"] == 0  # insufficient distinct density for 15 samples/window
    else:
        assert report["global_metrics"]["usable_observation_fraction"] == 0
        assert result["safe_limit"] == 0


def test_bounded_request_budget_no_unmeasured_lanes_or_capacity_extrapolation(wire):
    clock, calls, _ = wire
    report = measure(wire, batches=(20, 40), max_requests=182)
    assert len(calls) <= 182
    assert report["stop_reason"] == "REQUEST_BUDGET_EXHAUSTED"
    assert report["lanes"][0]["status"] == "MEASURED_OPEN"
    assert report["lanes"][1]["status"] == "INCOMPLETE"
    result = capacity(report, clock)
    assert result["tested_batch"] == 20 and result["safe_limit"] == 15


def test_runtime_and_close_bounds_stop_without_reinterpreting_closed_samples(wire):
    clock, calls, _ = wire
    report = measure(wire, max_runtime_seconds=60, cadence_seconds=15)
    assert report["stop_reason"] == "TIME_BUDGET_EXHAUSTED" and len(calls) < 181
    assert capacity(report, clock)["safe_limit"] == 0
    clock.at = datetime.fromisoformat("2026-10-05T19:59:58+00:00")
    clock.seconds = 0
    calls.clear()
    closed = measure(wire)
    assert closed["stop_reason"] == "MARKET_NOT_OPEN"
    assert capacity(closed, clock)["safe_limit"] == 0


def test_missing_endpoint_samples_age_latency_and_cadence_fail_closed(wire):
    clock, _, _ = wire
    report = measure(wire, endpoints=("current",))
    assert capacity(report, clock)["reason_codes"] == ["CAPACITY_ENDPOINTS_NOT_MEASURED"]
    assert capacity(report, clock, endpoints=("current",), required_samples=61)["safe_limit"] == 0
    assert capacity(report, clock, endpoints=("current",), cadence_seconds=5)["reason_codes"] == [
        "CAPACITY_FASTER_CADENCE_UNVERIFIED"]
    assert capacity(report, clock, endpoints=("current",), latency_budget_seconds=.001)["safe_limit"] == 0
    clock.advance(301)
    assert capacity(report, clock, endpoints=("current",))["reason_codes"] == ["CAPACITY_EVIDENCE_STALE_OR_FUTURE"]


def test_shared_global_load_not_independent_endpoint_limits(wire):
    clock, _, behavior = wire
    behavior["latency"] = .1
    report = measure(wire, cadence_seconds=1)
    result = capacity(report, clock, cadence_seconds=1)
    # Twenty * three endpoints * .1s => six seconds global cycle. One-second
    # scheduling cannot claim endpoint capacities independently.
    assert result["safe_limit"] == 2
    assert result["shared_global_slots"] == 2


def test_capacity_configuration_and_exact_evidence_invalidate_checkpoint(wire):
    clock, _, _ = wire
    report = measure(wire)
    initial = capacity(report, clock)
    later = capacity(report, clock, as_of=clock.now() + timedelta(seconds=1))
    assert initial["configuration_fingerprint"] == later["configuration_fingerprint"]
    changed = capacity(report, clock, safety_factor=.5)
    assert changed["configuration_fingerprint"] != initial["configuration_fingerprint"]
    altered = deepcopy(report)
    altered["generated_at"] = (clock.now() - timedelta(seconds=.01)).isoformat()
    reseal(altered)
    assert capacity(altered, clock)["configuration_fingerprint"] != initial["configuration_fingerprint"]
    assert capacity(report, clock, safety_factor=float("nan"))["safe_limit"] == 0


def test_summary_or_distinct_flags_cannot_fabricate_capacity(wire):
    clock, _, _ = wire
    report = measure(wire)
    tampered = deepcopy(report)
    tampered["global_metrics"]["requests"] = 999999
    assert capacity(tampered, clock)["reason_codes"] == ["CAPACITY_EVIDENCE_DIGEST_MISMATCH"]
    tampered = deepcopy(report)
    for row in tampered["observations"]:
        row["observation_fingerprint"] = "a" * 64
    reseal(tampered)
    assert capacity(tampered, clock)["reason_codes"] == ["CAPACITY_DISTINCT_EVIDENCE_INVALID"]
    incomplete = deepcopy(report)
    incomplete["observations"] = incomplete["observations"][:-1]
    reseal(incomplete)
    assert capacity(incomplete, clock)["safe_limit"] == 0


def test_ambiguous_full_identity_excluded_before_sdk_read(wire):
    clock, calls, _ = wire
    values = candidates(21)
    values.append({**values[0], "currency": "USD"})
    report = run_benchmark(values,
        api_key="PRIVATE_KEY", api_secret="PRIVATE_SECRET", batches=(20,), cadence_seconds=10,
        clock=clock.now, monotonic=clock.mono, pause=clock.advance)
    assert len(report["candidate_exclusions"]) == 2
    assert all("TEST000" not in url for _, url in calls)


@pytest.mark.parametrize("route", ["Order/Confirm", "Order/Budget", "Account/Movements", "Order/Cancel"])
def test_existing_http_guard_real_routes_blocked_before_adapter(wire, route):
    _, calls, _ = wire
    guard = ReadOnlyTransportGuard().install()
    try:
        with pytest.raises(ReadOnlyPolicyViolation):
            requests.post(f"https://clientapi.portfoliopersonal.com/api/1.0/{route}")
        assert calls == [] and guard.calls_blocked == 1
    finally:
        guard.restore()


def test_workflow_manual_bounded_no_production_runtime_or_orders():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/rc6-ppi-capacity-shadow.yml").read_text()
    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow
    assert "timeout-minutes: 35" in workflow
    assert "--max-requests 2701 --max-runtime-seconds 1800" in workflow
    assert "PPI_CAPACITY_READONLY_API_KEY" in workflow
    assert "candidate_sha:" in workflow and "inputs.candidate_sha" in workflow
    for forbidden in ("ssh ", "scp ", "DEPLOY_OWNER", "secrets.POROTA", "BYMA_API", "Order/Confirm"):
        assert forbidden not in workflow
    script = (root / "scripts/rc6_ppi_capacity_benchmark.py").read_text()
    assert "--as-of" not in script and "/opt/" not in script and "PPI_PRODUCTION_SECRET_FILE" not in script


def test_missing_evidence_still_has_stable_contract_fingerprint(wire):
    clock, _, _ = wire
    result = capacity({}, clock)
    assert result["status"] == "NO_VERIFICADO" and result["safe_limit"] == 0
    assert isinstance(result["configuration_fingerprint"], str) and len(result["configuration_fingerprint"]) == 64
    assert result["configuration_fingerprint"] == capacity({}, clock)["configuration_fingerprint"]
    assert result["configuration_fingerprint"] != capacity({}, clock, safety_factor=.5)["configuration_fingerprint"]


def test_minimum_useful_distinct_samples_and_window_density_both_required(wire):
    clock, _, behavior = wire
    behavior["mode"] = "repeated"
    report = measure(wire)
    assert capacity(report, clock, required_samples=21, window_seconds=3000)["safe_limit"] == 0
    assert capacity(report, clock, required_samples=15)["safe_limit"] == 0
    assert capacity(report, clock, required_samples=15, window_seconds=3000)["safe_limit"] == 5
