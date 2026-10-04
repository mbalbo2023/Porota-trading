"""F-02 regression through the long-lived native worker and real SQLite inputs.

Only the read-only PPI boundary is substituted. Selection, session gates,
normalization, persistence, signals and entry guards are the production code.
"""
from datetime import datetime, timedelta
import json

import pytest

import bd_ppi_readonly_guard as readonly
import bf_production_paper_observer as observer
from be_paper_engine import PaperStore, Quote
import bu_instrument_catalog as catalog
import cf_intraday_scalping as scalping


DAY = datetime.fromisoformat("2026-10-05T10:46:00-03:00")


def instrument(ticker="GGAL", **changes):
    return {"ticker": ticker, "instrument_type": "ACCIONES", "market": "BYMA",
            "currency": "ARS", "settlement": "A-24HS", "capability": "READY_PAPER_SPOT",
            "status": "AVAILABLE", **changes}


def payload(at):
    start = at.replace(hour=10, minute=30, second=0, microsecond=0)
    count = max(0, int((at - start).total_seconds() // 60))
    return [{"date": (start + timedelta(minutes=i)).isoformat(),
             "price": str(100 + i / 2), "volume": str(20 if i % 2 == 0 else 10)}
            for i in range(count)]


class Harness:
    def __init__(self, tmp_path, monkeypatch, *, times, records=None, behavior=None):
        self.at = times[0]
        self.times = times
        self.cycle = 0
        self.records = records or [instrument()]
        self.store = PaperStore(str(tmp_path / "paper.db"))
        self.calls = []
        self.readers = []
        self.cuts = []
        self.behavior = behavior or (lambda _h, _request: None)
        self.before_cycle = lambda _h: None
        self.stopped = False
        self.monotonic = 0.
        observer._support_schema(self.store)
        catalog.init_schema(self.store)
        scalping.init_schema(self.store)
        with self.store.connect() as c:
            for row in self.records:
                catalog.persist(c, {**row, "settlement_source": "PPI_FIELD",
                    "description": "offline F-02 fixture", "last_seen_at": self.at.isoformat(),
                    "run_id": "issue465", "raw": {"_discovery_source": "PPI_PRIMARY"}})
            catalog.sync_candidate_universe(c, self.at.isoformat())
        harness = self

        class Reader:
            budget_enabled = False
            metrics = {"http_blocked": 0}

            def __init__(self, *_args, **_kwargs):
                harness.readers.append(self)
                self.closed = False

            def login_once(self):
                pass

            def intraday(self, ticker, kind, settlement):
                request = (ticker, kind, settlement)
                harness.calls.append((harness.cycle, harness.at, request))
                outcome = harness.behavior(harness, request)
                if isinstance(outcome, Exception):
                    raise outcome
                return payload(harness.at) if outcome is None else outcome

            def close(self):
                self.closed = True

        monkeypatch.setattr(readonly, "ProductionMarketReader", Reader)
        monkeypatch.setattr(observer, "_secret", lambda: ("OFFLINE_FAKE_KEY", "OFFLINE_FAKE_SECRET"))
        monkeypatch.setattr(scalping.time, "monotonic", lambda: self.monotonic)
        monkeypatch.setattr(readonly, "retry_read", lambda call, **kwargs: retry_offline(call, **kwargs))
        monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
        monkeypatch.setenv("PAPER_INTRADAY_SCAN_SECONDS", "60")
        monkeypatch.setenv("POROTA_DYNAMIC_CAPACITY_MODE", "OFF")
        import bv_paper_runtime as runtime
        import be_paper_engine as engine
        from bm_exit_supervisor import PositionExitSupervisor, init_schema as init_exit_schema
        monkeypatch.setattr(runtime, "now_iso", lambda: self.at.isoformat())
        monkeypatch.setattr(engine, "now_iso", lambda: self.at.isoformat())
        init_exit_schema(self.store)
        self.supervisor = PositionExitSupervisor(runtime.broker_from_environment(self.store),
            clock_fn=lambda: self.at.isoformat())

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        if seconds < 20:
            return False
        self.capture()
        self.cycle += 1
        if self.cycle >= len(self.times):
            self.stopped = True
            return True
        self.monotonic += max(0., (self.times[self.cycle] - self.at).total_seconds())
        self.at = self.times[self.cycle]
        self.before_cycle(self)
        self.quote()
        return False

    def quote(self):
        from decimal import Decimal
        for row in self.records:
            p = Decimal(payload(self.at)[-1]["price"])
            self.store.add_quote(Quote(symbol=row["ticker"], asset_class=row["instrument_type"],
                market=row["market"], currency=row["currency"], settlement=row["settlement"],
                bid=p, ask=p + Decimal(".05"), last=p, bid_size=Decimal("1000"),
                ask_size=Decimal("1000"), book_at=self.at.isoformat(), trade_at=self.at.isoformat(),
                observed_at=self.at.isoformat(), metadata_source="PPI_PRIMARY_FIXTURE", last_kind="TRADE"))
        # Real exit authority runs offline too; no admission guard is disabled.
        self.supervisor.tick()
        with self.store.connect() as c:
            c.execute("INSERT OR REPLACE INTO paper_exit_reader_state VALUES(1,?,'READY','offline fixture book')",
                (self.at.isoformat(),))

    def capture(self):
        with self.store.connect() as c:
            self.cuts.append({
                "states": [dict(r) for r in c.execute("SELECT * FROM ppi_intraday_contract_state ORDER BY symbol")],
                "heartbeat": dict(c.execute("SELECT * FROM intraday_scalping_worker_state WHERE id=1").fetchone()),
                "fills": c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0],
                "candidates": [dict(r) for r in c.execute("SELECT * FROM scalping_candidates ORDER BY id")],
                "events": [dict(r) for r in c.execute("SELECT * FROM paper_events ORDER BY id")],
            })

    def run(self):
        self.quote()
        scalping.run_worker(self.store, self, clock_fn=lambda: self.at)
        with self.store.connect() as c:
            assert c.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0
        assert all(r.closed for r in self.readers)
        return self


def retry_offline(call, **kwargs):
    # The real classifier and bounded retry implementation, with no wall sleep.
    return NATIVE_RETRY(call, pause=lambda _seconds: None, **kwargs)


NATIVE_RETRY = readonly.retry_read


def missing_once(h, _request):
    return Exception("Instrument not found") if h.cycle == 0 else None


def test_new_session_recovers_same_worker_without_restart(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(minutes=1), DAY + timedelta(days=1)],
                behavior=missing_once).run()
    assert [call[0] for call in h.calls] == [0, 2]
    assert len(h.readers) == 1
    assert h.cuts[2]["heartbeat"]["successful"] == 1
    assert h.cuts[2]["states"][0]["state"] == "PENDING_LIVE_CONFIRMATION"
    assert h.cuts[2]["fills"] == 0


def capability(cut, symbol="GGAL"):
    row = next(r for r in cut["states"] if r["symbol"] == symbol)
    return json.loads(row["detail"])["capability"]


def test_instrument_negative_is_scoped_observable_not_global_failure(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY], behavior=missing_once).run()
    value = capability(h.cuts[0])
    assert value["last_result"] == value["reason_code"] == "PPI_INSTRUMENT_NOT_FOUND"
    assert value["first_seen_at"] == value["last_seen_at"] == scalping._stamp(DAY)
    assert value["session_id"] == "2026-10-05" and value["attempts"] == 1
    assert value["recovered_at"] is None and value["warmup_reset_required"]
    assert datetime.fromisoformat(value["retry_due_at"]) == DAY + timedelta(seconds=900)
    assert h.cuts[0]["heartbeat"]["state"] == "RUNNING"
    assert h.cuts[0]["heartbeat"]["failed"] == 0
    assert "PPI_INSTRUMENT_NOT_FOUND" in h.cuts[0]["events"][-1]["detail"]
    assert h.cuts[0]["fills"] == 0 and h.cuts[0]["candidates"] == []


def test_many_cycles_inside_ttl_never_hammer(tmp_path, monkeypatch):
    times = [DAY + timedelta(seconds=i * 60) for i in range(15)]
    h = Harness(tmp_path, monkeypatch, times=times, behavior=missing_once).run()
    assert len(h.calls) == 1
    assert all(cut["fills"] == 0 and cut["candidates"] == [] for cut in h.cuts)
    assert all("capability_cooldown_skipped=1" in cut["heartbeat"]["detail"] for cut in h.cuts[1:])
    assert capability(h.cuts[-1])["attempts"] == 1


def test_ttl_boundary_performs_one_readonly_reprobe(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(seconds=899),
                DAY + timedelta(seconds=900)], behavior=missing_once).run()
    assert [call[0] for call in h.calls] == [0, 2]
    recovered = capability(h.cuts[-1])
    assert recovered["last_result"] == "READ_ONLY_RECOVERED" and recovered["attempts"] == 2
    assert recovered["recovered_at"] == scalping._stamp(DAY + timedelta(seconds=900))
    assert "capability_reprobes=1; capability_recovered=1" in h.cuts[-1]["heartbeat"]["detail"]
    assert h.cuts[-1]["candidates"] == [] and h.cuts[-1]["fills"] == 0


def test_failed_reprobe_renews_bounded_exponential_cooldown(tmp_path, monkeypatch):
    times = [DAY, DAY + timedelta(seconds=900), DAY + timedelta(seconds=2699),
             DAY + timedelta(seconds=2700), DAY + timedelta(seconds=6299),
             DAY + timedelta(seconds=6300)]
    h = Harness(tmp_path, monkeypatch, times=times,
                behavior=lambda _h, _r: Exception("Instrument not found")).run()
    assert [call[0] for call in h.calls] == [0, 1, 3, 5]
    for cycle, cooldown in ((0, 900), (1, 1800), (3, 3600), (5, 3600)):
        meta = capability(h.cuts[cycle])
        assert (datetime.fromisoformat(meta["retry_due_at"]) -
                datetime.fromisoformat(meta["last_seen_at"])).total_seconds() == cooldown
    assert capability(h.cuts[-1])["attempts"] == 4
    assert all(cut["heartbeat"]["failed"] == 0 and cut["fills"] == 0 for cut in h.cuts)


def test_valid_next_session_exits_negative_requires_new_native_points(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(days=1),
                DAY + timedelta(days=1, minutes=1)], behavior=missing_once).run()
    assert [call[0] for call in h.calls] == [0, 1, 2]
    for cut in h.cuts[1:]:
        meta = capability(cut)
        assert not meta["warmup_reset_required"] and meta["session_id"] == "2026-10-06"
        assert meta["recovered_at"] is not None
        assert cut["states"][0]["observations"] <= 2
        assert cut["states"][0]["state"] == "PENDING_LIVE_CONFIRMATION"
        assert cut["fills"] == 0
        assert not any(row["action"] == "BUY_CANDIDATE" for row in cut["candidates"])


def test_old_confirmed_history_cannot_supply_recovery_warmup_or_candidate(tmp_path, monkeypatch):
    minutes = [0, 15, 16, 21, 24, 29, 31, 32]
    h = Harness(tmp_path, monkeypatch, times=[DAY + timedelta(minutes=m) for m in minutes],
                behavior=missing_once)
    row = h.records[0]
    for minutes_ago in (1, 0):
        at = DAY - timedelta(minutes=minutes_ago)
        points = scalping.normalize_payload(payload(at), received_at=at)
        scalping.persist_payload(h.store, row, points, received_at=at.isoformat())
    h.quote()
    assert scalping.evaluate_candidate(h.store, row, at=DAY) == "BUY_CANDIDATE"
    historical = None
    with h.store.connect() as c:
        historical = [tuple(r) for r in c.execute("SELECT * FROM ppi_intraday_points ORDER BY event_at")]
    checked = []
    def before_cycle(worker):
        if worker.cycle == 1:
            assert scalping.promote_paper_candidate(worker.store, row, at=worker.at) == "PPI_INSTRUMENT_NOT_FOUND"
        if worker.cycle == 5:
            # Interval contract can re-confirm while 15 wholly new samples are
            # still unavailable; an old BUY row must remain unexecutable.
            assert scalping.promote_paper_candidate(worker.store, row,
                at=worker.times[worker.cycle - 1]) == "CANDIDATE_NOT_AVAILABLE"
            checked.append(True)
    h.before_cycle = before_cycle
    h.run()
    assert checked
    assert h.cuts[0]["states"][0]["observations"] == 0
    assert h.cuts[1]["states"][0]["observations"] == 0
    assert h.cuts[1]["states"][0]["stable_overlap"] == h.cuts[1]["states"][0]["new_points"] == 0
    assert all(cut["fills"] == 0 for cut in h.cuts[:6])
    assert h.cuts[6]["candidates"][-1]["action"] == "BUY_CANDIDATE"
    assert h.cuts[6]["candidates"][-1]["points"] == 15
    assert h.cuts[6]["fills"] == 1
    with h.store.connect() as c:
        retained = [tuple(r) for r in c.execute("SELECT * FROM ppi_intraday_points WHERE event_at<=? ORDER BY event_at", (historical[-1][5],))]
    assert retained == historical  # immutable historical evidence preserved


def test_reprobe_never_invokes_signal_or_promotion(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(seconds=900)], behavior=missing_once)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("read-only capability probe reached entry authority")
    monkeypatch.setattr(scalping, "evaluate_candidate", forbidden)
    monkeypatch.setattr(scalping, "promote_paper_candidate", forbidden)
    h.run()
    assert h.cuts[-1]["heartbeat"]["successful"] == 1
    assert h.cuts[-1]["heartbeat"]["failed"] == 0
    assert h.cuts[-1]["fills"] == 0


@pytest.mark.parametrize("change", ["catalog", "config"])
def test_semantic_catalog_or_config_change_revalidates_before_ttl(tmp_path, monkeypatch, change):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(seconds=60)], behavior=missing_once)
    def mutate(worker):
        if change == "config":
            monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_OBSERVE")
        else:
            with worker.store.connect() as c:
                c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='GGAL'",
                    (json.dumps({"_discovery_source": "PPI_PRIMARY", "financial_contract_v17": {"cash_multiplier": "2"}}),))
    h.before_cycle = mutate
    h.run()
    assert [call[0] for call in h.calls] == [0, 1]
    assert capability(h.cuts[0])["catalog_config_fingerprint"] != capability(h.cuts[1])["catalog_config_fingerprint"]
    assert h.cuts[1]["fills"] == 0


def test_catalog_refresh_and_other_identity_do_not_contaminate_exact_request(tmp_path, monkeypatch):
    records = [instrument(), instrument("YPFD", settlement="INMEDIATA")]
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(minutes=1), DAY + timedelta(minutes=2)],
        records=records, behavior=lambda worker, r: Exception("Instrument not found") if r[0] == "GGAL" else None)
    def refresh(worker):
        with worker.store.connect() as c:
            c.execute("UPDATE financial_instrument_catalog SET last_seen_at=?,run_id=?,metadata_json=? WHERE ticker='GGAL'",
                (worker.at.isoformat(), str(worker.cycle), json.dumps({"_discovery_source": "PPI_PRIMARY", "captured_at": worker.at.isoformat()})))
            c.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='YPFD'",
                (json.dumps({"_discovery_source": "PPI_PRIMARY", "financial_contract_v17": {"cash_multiplier": str(worker.cycle + 1)}}),))
    h.before_cycle = refresh
    h.run()
    assert [call[0] for call in h.calls if call[2][0] == "GGAL"] == [0]
    assert [call[0] for call in h.calls if call[2][0] == "YPFD"] == [0, 1, 2]
    assert all(cut["heartbeat"]["failed"] == 0 for cut in h.cuts)
    assert capability(h.cuts[-1])["identity"] == list(scalping._identity(records[0]))


@pytest.mark.parametrize("outcome", [[], [{"date": (DAY - timedelta(minutes=5)).isoformat(), "price": "100", "volume": "10"}],
    [{"date": "not-a-timestamp", "price": "100", "volume": "10"}]])
def test_empty_stale_malformed_reprobe_cannot_recover_or_hammer(tmp_path, monkeypatch, outcome):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(minutes=15), DAY + timedelta(minutes=16)],
        behavior=lambda worker, _r: Exception("Instrument not found") if worker.cycle == 0 else outcome).run()
    assert [call[0] for call in h.calls] == [0, 1]
    meta = capability(h.cuts[-1])
    assert meta["recovered_at"] is None and meta["warmup_reset_required"]
    assert meta["attempts"] == 2
    assert all(cut["fills"] == 0 and cut["candidates"] == [] for cut in h.cuts)
    assert h.cuts[1]["heartbeat"]["failed"] == 1


def test_restart_preserves_cooldown_and_fresh_warmup_safety(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY], behavior=missing_once).run()
    h.times = [DAY + timedelta(minutes=1), DAY + timedelta(minutes=15)]
    h.at = h.times[0]
    h.cycle = 0
    h.stopped = False
    h.behavior = lambda _worker, _r: None
    h.before_cycle = lambda _worker: None
    h.run()
    assert len(h.readers) == 2
    assert len(h.calls) == 2 and h.calls[-1][1] == DAY + timedelta(minutes=15)
    assert h.cuts[-1]["states"][0]["observations"] == 0
    assert h.cuts[-1]["fills"] == 0 and h.cuts[-1]["candidates"] == []
    assert capability(h.cuts[-1])["attempts"] == 2


def test_memory_bound_eviction_cannot_cancel_durable_cooldown(tmp_path, monkeypatch):
    native_cache = scalping.IntradayCapabilityCache
    instances = []
    class LimitedCache(native_cache):
        def __init__(self):
            super().__init__(maximum=2)
            instances.append(self)
    monkeypatch.setattr(scalping, "IntradayCapabilityCache", LimitedCache)
    records = [instrument("GGAL"), instrument("YPFD"), instrument("PAMP")]
    h = Harness(tmp_path, monkeypatch, records=records,
        times=[DAY, DAY + timedelta(minutes=1), DAY + timedelta(minutes=2), DAY + timedelta(days=1)],
        behavior=missing_once).run()
    assert len(instances) == 1 and len(instances[0].entries) <= 2
    assert [call[0] for call in h.calls] == [0, 0, 0, 3, 3, 3]
    assert len(h.cuts[0]["states"]) == 3  # eviction drops memory, never durable denial
    assert all("capability_cache_bound=2" in cut["heartbeat"]["detail"] for cut in h.cuts)
    assert all(cut["fills"] == 0 for cut in h.cuts)


def test_clock_rollback_and_rapid_config_changes_never_bypass_spacing(tmp_path, monkeypatch):
    times = [DAY, DAY - timedelta(seconds=1), DAY + timedelta(seconds=30), DAY + timedelta(seconds=60)]
    h = Harness(tmp_path, monkeypatch, times=times, behavior=missing_once)
    def change(_worker):
        monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_OBSERVE" if _worker.cycle % 2 else "ACTIVE_PAPER")
    h.before_cycle = change
    h.run()
    assert [call[0] for call in h.calls] == [0, 3]
    assert all(cut["fills"] == 0 and cut["candidates"] == [] for cut in h.cuts)


# Native facade/SDK/HTTP guard and SQLite breaker are exercised together here.
from tests.test_rc6_ppi_capacity_benchmark import wire
from tests.test_rc6_ppi_global_budget import policy
from rc6_ppi_global_budget import GlobalPPIBudget

NATIVE_READER = readonly.ProductionMarketReader


@pytest.mark.parametrize("status,code,reader_count", [
    (429, "PPI_HTTP_429", 1), (401, "PPI_SESSION_INVALID", 2), (403, "PPI_SESSION_INVALID", 2)])
@pytest.mark.parametrize("response_mode", ["fresh", "missing"])
def test_native_sdk_reprobe_preserves_global_breaker_and_session_semantics(
        tmp_path, monkeypatch, wire, status, code, reader_count, response_mode):
    import requests
    from urllib.parse import urlsplit
    clock, calls, behavior = wire
    clock.at = DAY
    clock.seconds = 0.
    config = policy(clock, limits={"current": 5, "book": 5, "intraday": 5})
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", config, clock=clock.now)
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(minutes=15), DAY + timedelta(minutes=16)])
    wire_send = requests.adapters.HTTPAdapter.send
    def send(adapter, request, **kwargs):
        # The real SDK renews a 401 before retrying. Supply the native token
        # shape so the real global circuit must stop its internal second send.
        if urlsplit(request.url).path.lower().endswith("/refreshtoken"):
            calls.append((request.method, request.url))
            response = requests.Response()
            response.status_code = 200
            response.url = request.url
            response.request = request
            response._content = json.dumps({"accessToken": "NEVER_PERSIST_THIS_TOKEN",
                "refreshToken": "OFFLINE_RENEW_TOKEN"}).encode()
            response._content_consumed = True
            return response
        return wire_send(adapter, request, **kwargs)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    def native_reader(*args, **kwargs):
        reader = NATIVE_READER(*args, **kwargs, budget=budget)
        close = reader.close
        reader.closed = False
        def tracked_close():
            close()
            reader.closed = True
        reader.close = tracked_close
        h.readers.append(reader)
        return reader
    monkeypatch.setattr(readonly, "ProductionMarketReader", native_reader)
    behavior.update(status=404, mode="missing", endpoint="intraday", ticker="GGAL")
    def next_cycle(worker):
        if worker.cycle == 1:
            assert budget.metrics()["circuits"] == {}  # instrument gap is never a global outage
            behavior.update(status=status, mode=response_mode)
        clock.at = worker.at
        clock.seconds = 0.
    h.before_cycle = next_cycle
    h.run()
    intraday = [url for method, url in calls if "/Intraday?" in url]
    assert len(intraday) == 2  # no retry within the read-only re-probe or later cooldown
    assert len(h.readers) == reader_count
    assert budget.metrics()["circuits"]["global"]["code"] == f"PPI_HTTP_{status}"
    assert capability(h.cuts[1])["last_result"] == code
    assert capability(h.cuts[-1])["warmup_reset_required"]
    assert h.cuts[0]["heartbeat"]["state"] == "RUNNING" and h.cuts[0]["heartbeat"]["failed"] == 0
    assert h.cuts[1]["heartbeat"]["failed"] == 1
    assert all(cut["fills"] == 0 for cut in h.cuts)
    assert all("/Order/" not in url and "/Account/Movements" not in url for _, url in calls)
    text = json.dumps(h.cuts)
    for secret in ("RAW_PRIVATE_BROKER_BODY", "OFFLINE_FAKE_KEY", "NEVER_PERSIST_THIS_TOKEN"):
        assert secret not in text


def test_native_negative_event_remains_consumable_by_actual_shadow_metadata(tmp_path, monkeypatch):
    from rc6_shadow_runtime.worker import ShadowRuntime
    h = Harness(tmp_path, monkeypatch, times=[DAY], behavior=missing_once).run()
    shadow = ShadowRuntime(h.store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    _source_id, failures = shadow._metadata(DAY, (DAY - timedelta(minutes=1)).isoformat())
    assert len(failures) == 1
    assert failures[0]["identity"] == list(scalping._identity(h.records[0]))
    assert failures[0]["native_reason"] == "PPI_INSTRUMENT_NOT_FOUND"


def test_explicit_session_invalid_reprobe_closes_reader_and_obeys_login_cooldown(tmp_path, monkeypatch):
    times = [DAY, DAY + timedelta(seconds=900), DAY + timedelta(seconds=930), DAY + timedelta(seconds=960)]
    h = Harness(tmp_path, monkeypatch, times=times,
        behavior=lambda worker, _r: Exception("Instrument not found") if worker.cycle == 0 else
            Exception("Unauthorized") if worker.cycle == 1 else None).run()
    assert [call[0] for call in h.calls] == [0, 1]
    assert len(h.readers) == 2
    assert h.cuts[2]["heartbeat"]["state"] == "LOGIN_COOLDOWN"
    assert capability(h.cuts[1])["last_result"] == "PPI_SESSION_INVALID"
    assert capability(h.cuts[1])["capability_status"] == "REPROBE_FAILED"
    assert h.cuts[1]["states"][0]["state"] == "INTRADAY_CAPABILITY_REPROBE_FAILED"
    assert all(cut["fills"] == 0 for cut in h.cuts)


def test_generic_global_error_never_creates_instrument_negative_cache(tmp_path, monkeypatch):
    import requests
    response = requests.Response()
    response.status_code = 429
    error = requests.HTTPError("sanitized offline 429", response=response)
    h = Harness(tmp_path, monkeypatch, times=[DAY], behavior=lambda _h, _r: error).run()
    assert len(h.calls) == 2  # original bounded retry remains, both off-provider fake wire
    assert h.cuts[0]["heartbeat"]["failed"] == 1
    assert h.cuts[0]["states"] == []
    assert "unsupported_cached=0" in h.cuts[0]["heartbeat"]["detail"]
    assert h.cuts[0]["fills"] == 0
    assert not any(row["event_type"] == "INTRADAY_SCALPING_UNSUPPORTED" for row in h.cuts[0]["events"])


@pytest.mark.parametrize("corrupt", ["truncated", "missing_epoch", "wrong_identity", "oversized", "future_due", "retry_counter"])
def test_corrupt_durable_capability_fails_closed_in_real_worker(tmp_path, monkeypatch, corrupt):
    h = Harness(tmp_path, monkeypatch, times=[DAY], behavior=missing_once).run()
    with h.store.connect() as c:
        row = dict(c.execute("SELECT * FROM ppi_intraday_contract_state").fetchone())
        parsed = json.loads(row["detail"])
        if corrupt == "truncated":
            detail = row["detail"][:-10]
        elif corrupt == "oversized":
            detail = "x" * (16 * 1024 + 1)
        else:
            if corrupt == "missing_epoch":
                parsed["capability"]["warmup_reset_required"] = False
                parsed["capability"]["warmup_after"] = None
            elif corrupt == "wrong_identity":
                parsed["capability"]["identity"][3] = "USD_MEP"
            elif corrupt == "retry_counter":
                parsed["capability"]["consecutive_failures"] = "invalid-durable-counter"
            else:
                parsed["capability"]["retry_due_at"] = (DAY + timedelta(days=3)).isoformat()
            detail = json.dumps(parsed)
        c.execute("UPDATE ppi_intraday_contract_state SET detail=?", (detail,))
    h.times = [DAY + timedelta(minutes=15)]
    h.at = h.times[0]
    h.cycle = 0
    h.stopped = False
    h.behavior = lambda _h, _r: None
    h.run()
    assert len(h.calls) == 1  # only the initial error; corrupt state is rejected before wire
    assert h.cuts[-1]["heartbeat"]["failed"] == 1
    assert h.cuts[-1]["fills"] == 0 and h.cuts[-1]["candidates"] == []


def test_capability_recovery_never_clears_closed_minute_revision_rejection(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(minutes=15), DAY + timedelta(minutes=16)],
        behavior=missing_once)
    row = h.records[0]
    scalping.persist_payload(h.store, row, scalping.normalize_payload(payload(DAY), received_at=DAY),
        received_at=DAY.isoformat())
    with h.store.connect() as c:
        c.execute("UPDATE ppi_intraday_contract_state SET changed_closed_points=1,state='REJECTED_MUTABLE_CLOSED_POINTS'")
    h.run()
    assert all(cut["states"][0]["changed_closed_points"] == 1 for cut in h.cuts)
    assert h.cuts[-1]["states"][0]["state"] == "REJECTED_MUTABLE_CLOSED_POINTS"
    assert all(cut["fills"] == 0 for cut in h.cuts)


def test_same_ticker_other_settlement_has_independent_negative_capability(tmp_path, monkeypatch):
    rows = [instrument(), instrument(settlement="INMEDIATA")]
    h = Harness(tmp_path, monkeypatch, records=rows, times=[DAY, DAY + timedelta(minutes=1)],
        behavior=lambda _h, request: Exception("Instrument not found") if request[2] == "A-24HS" else None).run()
    assert [call[0] for call in h.calls if call[2][2] == "A-24HS"] == [0]
    assert [call[0] for call in h.calls if call[2][2] == "INMEDIATA"] == [0, 1]
    with h.store.connect() as c:
        state = c.execute("SELECT state FROM ppi_intraday_contract_state WHERE settlement='INMEDIATA'").fetchone()[0]
    assert state == "CONFIRMED_INTERVAL_VOLUME"


def test_catalog_rebind_of_same_wire_request_requires_readonly_fresh_warmup(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch, times=[DAY, DAY + timedelta(seconds=60), DAY + timedelta(seconds=120)],
        behavior=missing_once)
    original = h.records[0]
    replacement = instrument(currency="USD_MEP")
    def rebind(worker):
        if worker.cycle != 1:
            return
        with worker.store.connect() as c:
            c.execute("UPDATE financial_instrument_catalog SET status='RETIRED'")
            catalog.persist(c, {**replacement, "settlement_source": "PPI_FIELD", "description": "catalog rebind",
                "last_seen_at": worker.at.isoformat(), "run_id": "issue465-rebind", "raw": {"_discovery_source": "PPI_PRIMARY"}})
            catalog.sync_candidate_universe(c, worker.at.isoformat())
        worker.records = [replacement]
    h.before_cycle = rebind
    h.run()
    assert [call[0] for call in h.calls] == [0, 1, 2]
    with h.store.connect() as c:
        rebound = dict(c.execute("SELECT * FROM ppi_intraday_contract_state WHERE currency='USD_MEP'").fetchone())
        meta = json.loads(rebound["detail"])["capability"]
        assert c.execute("SELECT COUNT(*) FROM ppi_intraday_points WHERE currency='USD_MEP'").fetchone()[0] == 0
    assert meta["negative_origin_identity"] == list(scalping._identity(original))
    assert meta["identity"] == list(scalping._identity(replacement))
    assert meta["reprobe_reason"] == "INTRADAY_CAPABILITY_CATALOG_CONFIG_REPROBE"
    assert rebound["state"] == "PENDING_LIVE_CONFIRMATION" and rebound["observations"] == 0
    assert all(cut["fills"] == 0 and cut["candidates"] == [] for cut in h.cuts[:2])


def test_rotating_universe_pressure_preserves_durable_denials_after_lru_eviction(tmp_path, monkeypatch):
    native_cache = scalping.IntradayCapabilityCache
    instances = []
    class LimitedCache(native_cache):
        def __init__(self):
            super().__init__(maximum=16)
            instances.append(self)
    monkeypatch.setattr(scalping, "IntradayCapabilityCache", LimitedCache)
    records = [instrument(f"TEST{index:04d}") for index in range(256)]
    h = Harness(tmp_path, monkeypatch, records=records,
        times=[DAY + timedelta(seconds=30 * index) for index in range(20)],
        behavior=lambda _worker, _request: Exception("Instrument not found")).run()
    assert len(h.calls) == 256
    assert len({call[2] for call in h.calls}) == 256
    assert len(h.cuts[-1]["states"]) == 256
    assert len(instances) == 1 and len(instances[0].entries) == 16
    assert instances[0].evictions > 256
    assert all("capability_cache_entries=16; capability_cache_bound=16" in cut["heartbeat"]["detail"] for cut in h.cuts)
    assert all(cut["heartbeat"]["failed"] == 0 and cut["fills"] == 0 and cut["candidates"] == [] for cut in h.cuts)


@pytest.mark.parametrize("age_seconds", [121, -1])
def test_direct_persistence_cannot_recover_negative_from_stale_or_future_source(tmp_path, monkeypatch, age_seconds):
    h = Harness(tmp_path, monkeypatch, times=[DAY], behavior=missing_once).run()
    received = DAY + timedelta(minutes=15)
    source = received - timedelta(seconds=age_seconds)
    points = scalping.normalize_payload([{"date": source.isoformat(), "price": "100", "volume": "10"}],
        received_at=received)
    with pytest.raises(ValueError, match="INTRADAY_REPROBE_NO_FRESH_SOURCE"):
        scalping.persist_payload(h.store, h.records[0], points, received_at=received.isoformat())
    state = scalping._state(h.store, scalping._identity(h.records[0]))
    assert state["state"] == "PPI_INSTRUMENT_NOT_FOUND" and state["observations"] == 0
    assert json.loads(state["detail"])["capability"]["warmup_reset_required"]


def test_invalid_candidate_timestamp_after_recovery_is_rejected_before_broker(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch,
        times=[DAY + timedelta(minutes=minutes) for minutes in (0, 15, 21, 24, 31)],
        behavior=missing_once)
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_OBSERVE")
    h.run()
    assert h.cuts[-1]["states"][0]["state"] == "CONFIRMED_INTERVAL_VOLUME"
    assert h.cuts[-1]["candidates"][-1]["action"] == "BUY_CANDIDATE"
    assert h.cuts[-1]["fills"] == 0
    with h.store.connect() as c:
        c.execute("UPDATE scalping_candidates SET evaluated_at='invalid-source-clock' WHERE id=(SELECT MAX(id) FROM scalping_candidates)")
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    assert scalping.promote_paper_candidate(h.store, h.records[0], at=h.times[-1]) == "INVALID_SCALPING_CANDIDATE_TIMESTAMP"
    with h.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
