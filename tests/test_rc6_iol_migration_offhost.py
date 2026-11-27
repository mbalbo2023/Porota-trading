"""Run IOL through the real client, clock guards, PAPER broker and isolated SQLite."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from be_paper_engine import PaperBroker, PaperStore
from bf_production_paper_observer import _scanner_trade_temporal_rejection
from bl_candle_engine import SampleMaterializer, record_iol_trade_sample
from bm_exit_supervisor import PositionExitSupervisor
from cu_history_store_v2_hf6 import Candle, append_candle, read_as_of
from cv_history_store_adapter_hf6 import HistoricalStore
from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly
from iol_shadow_collector_rc6 import CollectionPolicy, RateGovernor
from rc6_iol_history_reference import ingest_daily_reference
from rc6_iol_market_data import IOLMarketReader
from rc6_iol_market_gateway import CONTROL_SCHEMA, IOLGatewayClient, IOLGatewayMarketReader, IOLMarketGateway
from rc6_ppi_shared_feed import ReadOnlyUnixFeedServer
from scripts.rc6_candle_inmarket_evidence import IOL_SAMPLE_SOURCE, main as verify_inmarket_candles
from scripts.rc6_history_import_source_reported import _source_store
from scripts.porota_actions_release import promotion_source
from scripts.porota_build_deploy_bundle_v2 import build_bundle
from scripts.porota_predeploy_impact import classify
from scripts.porota_testing_scope import RELEASE_BLOCKED, TESTING_ONLY_MARKER


AT = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
GGAL = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
FAMILIES = (
    GGAL,
    ("AAPL", "CEDEARS", "BYMA", "ARS", "A-24HS"),
    ("QAETF", "ETFS", "BYMA", "ARS", "A-24HS"),
    ("AL30", "BONOS", "BYMA", "ARS", "A-24HS"),
    ("QALETRA", "LETRAS", "BYMA", "ARS", "A-24HS"),
    ("YMCJO", "OBLIGACIONES", "BYMA", "ARS", "A-24HS"),
    ("GFGC5000D", "OPCIONES", "BYMA", "ARS", "INMEDIATA"),
    ("DLR/NOV26", "FUTUROS", "A3", "ARS", "INMEDIATA"),
    ("PESOS1", "CAUCIONES", "BYMA", "ARS", "INMEDIATA"),
    ("IOLCAMA", "FCI", "BYMA", "ARS", "INMEDIATA"),
)
FAMILY_REASON = {
    "FUTUROS": "IOL_A3_MARKET_UNVERIFIED",
    "CAUCIONES": "IOL_REPO_BOOK_CLOCK_UNVERIFIED",
    "FCI": "IOL_FUND_TERMS_AND_NAV_UNVERIFIED",
}


class ReplayHTTP:
    def __init__(self, identity):
        self.symbol, self.family, _, self.currency, term = identity
        self.term = "T1" if term == "A-24HS" else "T0"
        self.now = AT
        self.price = 100.0
        self.calls = []
        self.overrides = {}
        self.reject = None

    def open(self, request, timeout):
        url = urlsplit(request.full_url)
        assert url.scheme == "https" and url.hostname == "api.iolinversiones.com"
        assert timeout == 8
        path = url.path.split("/")[-1]
        self.calls.append(path)
        if url.path == "/auth/token":
            assert request.get_method() == "POST"
            assert json.loads(request.data)["grant_type"] == "password"
            row = {"access_token": "OFFHOST_ACCESS_ONLY_FIXTURE", "refresh_token": "OFFHOST_REFRESH_ONLY_FIXTURE",
                   "expires_in": 900}
        else:
            assert request.get_method() == "GET"
            assert request.get_header("Authorization") == "Bearer OFFHOST_ACCESS_ONLY_FIXTURE"
            if path == self.reject:
                raise HTTPError(request.full_url, 429, "fixture", {"Retry-After": "7"}, BytesIO())
            base = {"symbol": self.symbol, "market": "BCBA", "settlement_term": self.term}
            if url.path.startswith("/broker/assets/"):
                assert parse_qs(url.query) == {"market": ["BCBA"]}
                row = {**base, "currency": self.currency, "asset_type": self.family}
            elif path in {"quote", "depth"}:
                assert parse_qs(url.query) == {"market": ["BCBA"], "term": [self.term]}
                row = {**base, "last_updated": (self.now - timedelta(seconds=1)).isoformat()}
                if path == "quote":
                    row.update(currency=self.currency, prices={"current": self.price}, volume={"shares": 100})
                else:
                    row.update(bids=[{"price": self.price - .1, "size": 10000}],
                               asks=[{"price": self.price + .1, "size": 10000}])
            elif path == "terms":
                row = [{"term_days": 1, "term_id": 10, "due_date": "2026-11-25", "min_amount": 1000}]
            elif path == "quotes":
                row = [{"bid": {"rate": 30, "amount": 100000}}]
            elif path == "bars":
                assert parse_qs(url.query) == {"market": ["BCBA"], "from": ["2026-11-23 00:00:00"],
                                                "to": ["2026-11-23 23:59:59"]}
                at = AT - timedelta(days=1)
                row = {"symbol": self.symbol, "market": "BCBA", "total_bars": 1, "bars": [
                    {"timestamp": int(at.timestamp()), "datetime": at.isoformat(), "open": 100,
                     "high": 102, "low": 99, "close": 101, "volume": 200}]}
            else:
                pytest.fail(f"unexpected fixture route {url.path}")
        if path in self.overrides:
            row.update(self.overrides[path])
        return BytesIO(json.dumps({"data": row, "error": None}).encode())


def reader(tmp_path, identity=GGAL):
    transport = ReplayHTTP(identity)
    rest = IOLRestReadOnly(tmp_path / "iol-fixture", clock=lambda: transport.now.timestamp(),
                           opener=transport)
    # Credential acquisition is supplied by the private fixture. Auth, quote,
    # depth and asset still run through IOLRestReadOnly's real HTTP boundary.
    rest._credentials = lambda: {"grant_type": "password", "username": "offhost", "password": "fixture"}
    return IOLMarketReader(rest, trade_clock=lambda row: row["last_updated"],
                           clock=lambda: transport.now), transport


@contextmanager
def open_fixture_iol_gateway(tmp_path, tmp_path_factory, identity=GGAL):
    market, transport = reader(tmp_path, identity)
    tick = {"monotonic": 0.0}

    def advance(seconds):
        tick["monotonic"] += seconds

    market.rest.governor = RateGovernor(
        CollectionPolicy(), clock=lambda: tick["monotonic"], sleep=advance, max_wait_seconds=1)
    directory = tmp_path_factory.mktemp("iol-socket")
    directory.chmod(0o700)
    server = IOLMarketGateway(market.rest, allowed={"scanner": (identity,), "exit_reader": (identity,)},
        socket_path=directory / "gateway.sock", supervisor_pid=os.getpid(), clock=lambda: transport.now)

    class InProcessClient(IOLGatewayClient):
        def _request(self, packet, **kwargs):
            return server.dispatch(packet, peer_pid=os.getpid())

    thread = None
    if os.environ.get("POROTA_IOL_OFFHOST_IPC") == "1":
        server.start()
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = IOLGatewayClient(socket_path=server.socket_path, timeout_seconds=5)
    else:
        server.start_native_priority_router()
        client = InProcessClient(socket_path=server.socket_path)
    assert client.owner_status()["schema"] == CONTROL_SCHEMA
    try:
        yield server, client, transport, advance
    finally:
        if thread is not None:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()
        server.close()


@pytest.fixture
def iol_gateway(tmp_path, tmp_path_factory):
    with open_fixture_iol_gateway(tmp_path, tmp_path_factory) as gateway:
        yield gateway


def register(client, role, pid=None):
    pid = os.getpid() if pid is None else pid
    registration = client.register_client(role, pid=pid, proc_starttime=ReadOnlyUnixFeedServer.process_starttime(pid))
    assert registration["registered_role"] == role


def test_runtime_iol_profile_starts_local_workers_without_ppi(tmp_path, monkeypatch):
    import bv_paper_runtime as runtime

    database = tmp_path / "paper.db"
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "0")
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    controller = {}

    def inspect_clock(store, children, _stop):
        controller.update(children.commands)
        assert store.path == str(database)
        assert children.gateway_role == "iol_gateway"

    monkeypatch.setattr(runtime, "run_clock", inspect_clock)
    assert runtime.main([]) == 0
    assert set(controller) == {"iol_gateway", "scanner", "exit_reader", "dynamic_shadow",
                               "candles", "notifications", "intraday_scalping", "caucion_cash_sweep"}
    assert controller["iol_gateway"][-1] == "--iol-gateway-worker"
    with pytest.raises(ValueError, match="RC6_WORKER_MARKET_PROVIDER_MISMATCH"):
        runtime.main(["--ppi-gateway-worker"])
    with PaperStore(str(database)).connect() as connection:
        assert tuple(connection.execute("SELECT mode,real_orders_sent FROM observer_state").fetchone()) == (
            "PRODUCTION_PAPER", 0)


def test_iol_workers_observe_samples_and_hold_unqualified_cauciones(tmp_path, monkeypatch):
    import bv_paper_runtime as runtime
    import cf_intraday_scalping as scalping
    import di_caucion_cash_sweep_runtime_hf6 as cauciones
    import rc6_ppi_gateway_reader_adapter as ppi

    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    monkeypatch.setenv("PAPER_SCALPING_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    monkeypatch.setattr(runtime, "broker_from_environment",
                        lambda *a, **kw: pytest.fail("IOL observation constructed a broker"))
    monkeypatch.setattr(ppi, "connect_paper_gateway_reader",
                        lambda *a, **kw: pytest.fail("IOL observation opened PPI"))
    store = PaperStore(str(tmp_path / "paper.db"))
    market, transport = reader(tmp_path, GGAL)
    assert record_iol_trade_sample(store, market.observe(GGAL), as_of=AT)
    initial_calls = list(transport.calls)

    class ObserveOneCycle(Event):
        def __init__(self, table):
            super().__init__()
            self.table = table
            self.row = None

        def wait(self, timeout=None):
            with store.connect() as conn:
                self.row = dict(conn.execute(f"SELECT * FROM {self.table} WHERE id=1").fetchone())
            self.set()
            return True

    scalp = ObserveOneCycle("intraday_scalping_worker_state")
    scalping.run_worker(store, scalp, clock_fn=lambda: AT)
    assert (scalp.row["state"], scalp.row["selected"], scalp.row["successful"],
            scalp.row["candidates"]) == ("OBSERVING_IOL", 1, 1, 0)
    assert "IOL_INTERVAL_VOLUME_UNVERIFIED" in scalp.row["detail"]

    sweep = ObserveOneCycle("paper_caucion_cash_sweep_state")
    cutoff = cauciones.paper_schedule(AT)["order_cutoff_at"]
    cauciones.run_worker(store, sweep, clock_fn=lambda: cutoff - timedelta(minutes=1))
    assert (sweep.row["state"], sweep.row["code"], sweep.row["real_orders_sent"]) == (
        "HOLD", "IOL_REPO_BOOK_CLOCK_UNVERIFIED", 0)
    assert transport.calls == initial_calls
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM paper_cauciones").fetchone()[0] == 0


def test_runtime_parent_registers_iol_scanner_and_exit_on_owner_rotation(tmp_path, monkeypatch, iol_gateway):
    import bv_paper_runtime as runtime
    import rc6_iol_market_gateway as gateway
    from rc6_ppi_shared_feed import SharedFeedUnavailable

    server, client, transport, _advance = iol_gateway
    monkeypatch.setattr(runtime, "database_path", lambda: tmp_path / "paper.db")
    monkeypatch.setattr(gateway, "iol_gateway_socket_path", lambda _database: server.socket_path)
    monkeypatch.setattr(gateway, "IOLGatewayClient", type(client))
    pid = os.getpid()

    class Child:
        def poll(self):
            return None

    processes = {role: Child() for role in ("iol_gateway", "scanner", "exit_reader")}
    for process in processes.values():
        process.pid = pid
    children = runtime.ChildProcesses({role: [] for role in processes})
    children.processes.update(processes)
    children._register_gateway_clients(0)
    starttime = ReadOnlyUnixFeedServer.process_starttime(pid)
    registered = {role: {"pid": pid, "proc_starttime": starttime}
                  for role in ("scanner", "exit_reader")}
    assert client.owner_status()["registered_clients"] == registered
    scanner = gateway.connect_iol_gateway_reader(tmp_path / "paper.db", consumer="scanner")
    assert scanner.metrics["wire_attempts"] == 0
    assert scanner.metrics["authenticated"] is False

    # A fresh owner generation has no inherited registrations or client pin.
    server._generation = "b" * 64
    server._registered_clients.clear()
    with pytest.raises(SharedFeedUnavailable, match="GATEWAY_OWNER_CHANGED"):
        _ = scanner.metrics
    children._register_gateway_clients(16)
    scanner = gateway.connect_iol_gateway_reader(tmp_path / "paper.db", consumer="scanner")
    assert scanner.metrics["registered_clients"] == registered
    assert scanner.metrics["session_generation"] == "b" * 64
    assert transport.calls == []


def test_iol_profile_rejects_legacy_provider_commands_before_gateway_attach(tmp_path, monkeypatch):
    import bf_production_paper_observer as observer
    import rc6_ppi_gateway_reader_adapter as ppi

    store = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(store)
    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    monkeypatch.setattr(ppi, "connect_paper_gateway_reader",
                        lambda *a, **kw: pytest.fail("unexpected PPI gateway attach"))
    commands = ["LOGIN_AND_SYNC", observer._PPI_OPEN_COMMAND_PREFIX + "probe",
                observer._PPI_POSTCLOSE_COMMAND_PREFIX + "probe"]
    with store.connect() as connection:
        connection.executemany("INSERT INTO observer_commands(command,status,created_at)"
                               " VALUES (?,'QUEUED',?)", ((name, AT.isoformat()) for name in commands))
    for name in commands:
        command = observer._claim_command(store)
        assert command[1] == name
        assert observer._run_command(store, None, command) is None
    with store.connect() as connection:
        rows = connection.execute("SELECT status,result FROM observer_commands ORDER BY id").fetchall()
    assert [tuple(row) for row in rows] == [("NOT_APPLICABLE", "IOL_REST_PROFILE_PPI_COMMAND_RETIRED")] * 3


def test_iol_scanner_phases_use_only_iol_and_hold_unqualified_trade_clock(tmp_path, monkeypatch, iol_gateway):
    import bf_production_paper_observer as observer
    import bu_instrument_catalog as catalog
    import rc6_iol_market_gateway as gateway
    import rc6_ppi_gateway_reader_adapter as ppi
    from tests.test_rc6_ppi_gateway_family_ready import ReplayClock

    server, client, transport, _advance = iol_gateway
    register(client, "scanner")
    monkeypatch.setattr(gateway, "iol_gateway_socket_path", lambda _database: server.socket_path)
    monkeypatch.setattr(gateway, "IOLGatewayClient", type(client))
    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    monkeypatch.setenv("POROTA_PPI_DIRECT_REST_RECOVERY", "1")
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "0")
    monkeypatch.setattr(observer, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(observer, "STOP", False)
    monkeypatch.setattr(observer.signal, "signal", lambda *_: None)
    monkeypatch.setattr(observer.operations, "service_tick", lambda *_: None)
    monkeypatch.setattr(observer, "_public_probe", lambda *a, **kw: None)
    monkeypatch.setattr(observer, "_publish_economic_shadow_health", lambda *_: None)

    def unexpected_ppi(*args, **kwargs):
        pytest.fail("the IOL scanner reached a legacy PPI operation")

    monkeypatch.setattr(ppi, "connect_paper_gateway_reader", unexpected_ppi)
    for operation in ("_daily_sync", "_background_ingest", "_discover_native_settlements",
                      "_periodic_complementary_reconcile"):
        monkeypatch.setattr(observer, operation, unexpected_ppi)
    phases = ["CLOSED", "PREOPEN", "OPEN"]
    monkeypatch.setattr(observer, "_market_phase", lambda: phases[0])
    request = (GGAL[0], GGAL[1], GGAL[4])
    monkeypatch.setattr(catalog, "datetime", ReplayClock)
    monkeypatch.setattr(observer, "now_iso", lambda: transport.now.isoformat())
    store = observer.runtime_store(observer.DB_PATH)
    observer._support_schema(store)
    with store.connect() as db:
        primary = catalog.normalize_record(
            {"ticker": GGAL[0], "type": GGAL[1], "market": GGAL[2], "currency": GGAL[3],
             "_discovery_source": "PPI_PRIMARY"}, GGAL[4], AT.isoformat(), "OFFHOST_PPI_IDENTITY_FIXTURE")
        catalog.persist(db, primary)
        catalog.sync_candidate_universe(db, AT.isoformat())

    def finish_phase(_seconds):
        phases.pop(0)
        if not phases:
            observer.STOP = True

    def readiness(_store, **_kw):
        plan = {"catalog_status": "AVAILABLE", "read_scopes": [
            {"request": request, "identity": GGAL, "priority": "HOT"}]}
        return {}, [request], 1, 0, 0, {"active_plan": plan}

    monkeypatch.setattr(observer, "_runtime_readiness", readiness)
    monkeypatch.setattr(observer, "time", SimpleNamespace(
        **{name: getattr(observer.time, name) for name in ("time", "monotonic", "perf_counter")},
        sleep=finish_phase))

    assert observer.run() == 0
    assert transport.calls == ["token", "GGAL", "quote"]
    store = PaperStore(observer.DB_PATH)
    assert not store.open_positions()
    with store.connect() as connection:
        auth, count = connection.execute("SELECT ppi_auth,real_orders_sent FROM observer_state").fetchone()
        events = connection.execute("SELECT detail FROM paper_events WHERE event_type='DATA_ERROR'").fetchall()
    assert (auth, count) == ("NOT_ATTEMPTED", 0)
    assert [row[0] for row in events] == ["GGAL: IOL_LAST_TRADE_CLOCK_UNVERIFIED"]


@pytest.mark.parametrize(("seconds", "opened_count", "expected"), [
    (60, 0, 12), (60, 2, 11), (30, 0, 6), (60, 21, 0),
])
def test_iol_scanner_plan_respects_wire_limit_and_leaves_room_for_exit(
        tmp_path, monkeypatch, seconds, opened_count, expected):
    import bf_production_paper_observer as observer
    from be_paper_engine import PaperStore
    from iol_shadow_collector_rc6 import CollectionPolicy

    keys = [(f"TEST{i}", "ACCIONES", GGAL[2], GGAL[3], GGAL[4]) for i in range(64)]
    store = PaperStore(tmp_path / "wire-budget.sqlite")
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS universe_cycle_metrics(cursor_after, detail, id)")
    opened = [
        {"symbol": key[0], "asset_class": key[1], "settlement": key[4]}
        for key in keys[:opened_count]
    ]
    monkeypatch.setattr(store, "open_positions", lambda: opened)
    monkeypatch.setattr(observer.financial_catalog, "iol_spot_catalog", lambda *_, **__: keys)
    monkeypatch.setattr(observer, "INTERVAL", seconds)
    monkeypatch.setattr(observer, "ACTIVE_SYMBOL_LIMIT", 20)
    monkeypatch.setattr(observer, "now_iso", lambda: AT.isoformat())

    selected, eligible, _, _, plan = observer._cycle_plan(store, iol=True)

    budget = plan["iol_rest_budget"]
    assert eligible == len(keys)
    assert len(selected) == budget["scanner_batch_limit"] == expected
    opened_requests = tuple((key[0], key[1], key[4]) for key in keys[:min(opened_count, expected)])
    assert selected[:opened_count] == opened_requests
    assert set(map(tuple, plan["cold"])).isdisjoint(map(tuple, plan["opening_identities"]))
    if expected:
        planned_calls = expected * budget["scanner_calls_per_instrument"] * budget["batches_per_minute"]
        assert planned_calls + budget["planned_exit_calls"] + 1 <= CollectionPolicy().max_calls_per_minute
    else:
        assert not plan["opening_identities"]


@pytest.mark.parametrize("identity", FAMILIES, ids=[case[1] for case in FAMILIES])
def test_iol_family_contract(identity, tmp_path):
    market, transport = reader(tmp_path, identity)
    family = identity[1]
    if family in FAMILY_REASON:
        with pytest.raises(IOLRestError, match="IOL_MARKET_IDENTITY_UNSUPPORTED"):
            market.observe(identity)
        assert transport.calls == []
        if family == "CAUCIONES":
            rates = market.rest.call("get_caucion_rates", {"currency": "ARS", "caucion_type": "colocadora"})
            assert rates["rates"][0]["reference_only"] is True
        elif family == "FCI":
            asset = market.rest.call("get_asset_info", {"market": "BCBA", "symbol": identity[0]})
            assert asset["type"] == "FCI" and asset["currency"] == "ARS"
        return

    sampled = market.observe(identity)
    quote = sampled.quote
    assert (quote.symbol, quote.asset_class, quote.market, quote.currency, quote.settlement) == identity
    assert quote.time_error(AT.isoformat(), require_trade=True) == ""
    assert sampled.current.provider_at == quote.trade_at
    assert sampled.book.provider_at == quote.book_at
    assert sampled.current.data_sha256 != sampled.book.data_sha256
    assert transport.calls == ["token", identity[0], "quote", "depth"]
    store = PaperStore(str(tmp_path / "sample.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    assert record_iol_trade_sample(store, sampled, as_of=AT.isoformat())
    assert not record_iol_trade_sample(store, sampled, as_of=AT.isoformat())
    SampleMaterializer(store).tick(AT + timedelta(minutes=5))
    with store.connect() as db:
        rows = db.execute("""SELECT s.identity_json, v.body_json FROM candle_series s
            JOIN candle_versions v USING(series_id) ORDER BY s.identity_json""").fetchall()
    sources = {json.loads(row[0])["source"] for row in rows}
    assert sources == {"IOL_REST_TRADE_SAMPLE"}
    assert {json.loads(row[0])["resolution"] for row in rows} == {"1m", "5m"}
    assert all(json.loads(row[1])["quality"] == "SAMPLED" and json.loads(row[1])["volume"] is None
               for row in rows)


def test_iol_inmarket_candle_evidence_binds_both_frames_to_receipts(tmp_path, monkeypatch):
    path = tmp_path / "sample.db"
    store = PaperStore(str(path))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    market, _ = reader(tmp_path, GGAL)
    sampled = market.observe(GGAL)
    assert record_iol_trade_sample(store, sampled, as_of=AT.isoformat())
    observed = (AT + timedelta(minutes=5)).isoformat()
    materialized = SampleMaterializer(store).tick(observed)
    assert (materialized["versions"], materialized["cursor"]) == (2, 0)
    result_path = tmp_path / "evidence.json"
    args = ["verify-candles", "--baseline-cursor", "0", "--observed-at", observed,
            "--db-path", str(path), "--summary-path", str(result_path)]
    monkeypatch.setattr(sys, "argv", args)
    before = path.read_bytes()
    assert verify_inmarket_candles() == 1
    assert not json.loads(result_path.read_text())["checks"]["cursor_strictly_advanced"]
    monkeypatch.setattr(sys, "argv", args + ["--sample-source", IOL_SAMPLE_SOURCE])
    assert verify_inmarket_candles() == 0
    result = json.loads(result_path.read_text())
    assert result["source"] == "porota-rc6-candle-inmarket-iol-evidence"
    assert result["iol_sampled_progress"] == {
        "identity": list(GGAL), "known_at_by_resolution": {"1m": observed, "5m": observed},
    }
    assert not result["guarded_current_sampled_progress"]
    assert path.read_bytes() == before
    with store.connect() as db:
        db.execute("UPDATE candle_worker_state SET cursor=5")
        db.execute("UPDATE candle_samples SET snapshot_id=0 WHERE series_id IN "
                   "(SELECT series_id FROM candle_series WHERE json_extract(identity_json,'$.resolution')='5m')")
    assert verify_inmarket_candles() == 1
    result = json.loads(result_path.read_text())
    assert result["current_cursor"] > result["baseline_cursor"]
    assert not result["checks"]["iol_receipts_both_resolutions"]
    assert not result["iol_sampled_progress"]


def test_iol_local_eod_archive_does_not_certify_postclose_provider(tmp_path, monkeypatch):
    from bf_production_paper_observer import _support_schema
    from cg_paper_workspace import artifact_root
    from rc6_decision_evidence_report import publish
    from rc6_local_eod_history import RESERVE, consolidate
    from rc6_postclose_review import build_review
    from rc6_postclose_source_contract import PENDING, postclose_eod_receipt
    from rc6_snapshot import build_snapshot, write_snapshot

    database = tmp_path / "paper.db"
    history = tmp_path / "local-eod.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
    scratch = artifact_root(database) / "sqlite-read-scratch"
    scratch.mkdir(mode=0o700, parents=True)
    store = PaperStore(str(database))
    _support_schema(store)
    market, _ = reader(tmp_path, GGAL)
    preopen = AT.replace(hour=13, minute=16)
    postclose = AT.replace(hour=22, minute=30)
    assert record_iol_trade_sample(store, market.observe(GGAL), as_of=AT.isoformat())
    assert SampleMaterializer(store).tick(AT + timedelta(minutes=5))["versions"] == 2
    store.state(session_state="MARKET_CLOSED", heartbeat_at=postclose.isoformat())
    with store.connect() as db:
        state = dict(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone())
    source = database.read_bytes()
    assert build_snapshot("preopen", preopen, {"state": state})["status"] == "VERIFIED"
    archive = consolidate(database, history, as_of=postclose, free_bytes=RESERVE)
    assert (archive["state"], archive["imported"], archive["provider_requests"]) == ("LOCAL_EOD_READY", 2, 0)
    receipt = postclose_eod_receipt(postclose, db_path=database)
    assert receipt["reason"] == "PPI_EOD_SOURCE_NOT_GREEN"
    snapshot = build_snapshot("postclose", postclose, {"state": state}, eod_source=receipt)
    assert snapshot["status"] == PENDING and snapshot["paper_safety_status"] == "VERIFIED"
    assert build_review(snapshot, postclose)["status"] == PENDING
    target = tmp_path / "snapshot.json"
    write_snapshot(snapshot, target)
    report = publish(root=tmp_path / "reports", postclose=True, snapshot_path=target, source_at=postclose)
    assert report["projection_status"] == "VERIFIED_PRIVATE_SOURCE"
    assert report["postclose_report_status"] == PENDING
    assert snapshot["real_orders_sent"] == 0
    assert database.read_bytes() == source


def test_ggal_paper_ledger_and_exit(tmp_path, monkeypatch, iol_gateway):
    replay_iol_spot_paper(tmp_path, monkeypatch, iol_gateway, GGAL)


def replay_iol_spot_paper(tmp_path, monkeypatch, gateway, identity, *, price_scale=1.0):
    import bf_production_paper_observer as observer
    import bu_instrument_catalog as catalog
    from bq_exit_policy import PaperSessionPolicy
    from bv_paper_runtime import _collect_iol_exit_round
    from rc6_dynamic_universe.common import digest
    from rc6_dynamic_universe.orchestrator import EnginePolicy, UniverseOrchestrator
    from rc6_dynamic_universe.runtime import read_runtime
    from rc6_iol_trade_stream import last_trade
    import rc6_paper_admission_gate as admission
    from rc6_paper_admission_gate import paper_admission_guard
    from tests.test_rc6_ppi_gateway_family_ready import ReplayClock, _preopen

    ticker, family, market, currency, term = identity
    server, client, transport, _ = gateway
    register(client, "scanner")
    register(client, "exit_reader")

    def trade_evidence(key):
        event = {"type": "T", "code": 200, "msg": {"S": ticker, "M": "BCBA", "T": transport.term,
                 "P": transport.price, "X": 100, "D": (transport.now - timedelta(seconds=3)).isoformat()}}
        wire, evidence = last_trade(event, transport.now - timedelta(seconds=2))
        assert key == wire
        return evidence

    server.trade_stream = SimpleNamespace(want=lambda key: None, evidence=trade_evidence, state="SUBSCRIBED")
    scanner = IOLGatewayMarketReader(client, consumer="scanner", clock=lambda: transport.now)
    exit_reader = IOLGatewayMarketReader(client, consumer="exit_reader", clock=lambda: transport.now)
    store = PaperStore(str(tmp_path / "paper.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    observer._support_schema(store)
    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    monkeypatch.setattr(ReplayClock, "value", AT)
    monkeypatch.setattr(catalog, "datetime", ReplayClock)
    monkeypatch.setattr(admission, "datetime", ReplayClock)
    monkeypatch.setattr(observer, "now_iso", lambda: transport.now.isoformat())
    _preopen(tmp_path, monkeypatch, iol=True)
    request = ticker, family, term
    assert request not in observer._runtime_readiness(store)[1]
    opening = AT.replace(hour=13, minute=30)
    checked_at = (opening - timedelta(minutes=15)).isoformat()
    primary = catalog.normalize_record({
        "ticker": ticker, "type": family, "market": market, "currency": currency,
        "_discovery_source": "PPI_PRIMARY",
    }, term, checked_at, "OFFHOST_PPI_IDENTITY_FIXTURE")
    with store.connect() as db:
        catalog.persist(db, primary)
        catalog.sync_candidate_universe(db, checked_at)
    static = catalog.lookup(store, *request, market=market, currency=currency)
    assert static["_admission_reason"] == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
    assert identity in catalog.iol_spot_catalog(store, now=AT)
    contract = catalog.contract_for(static)
    focus, selected, _, _, _, feasibility = observer._runtime_readiness(store)
    assert request in selected
    allowed, opening_reason = observer._scanner_opening_capacity(request, focus=focus, feasibility=feasibility)
    assert (allowed, opening_reason) == (True, "")
    with pytest.raises(IOLRestError, match="IOL_EXACT_CONTRACT_MISMATCH"):
        scanner.observe(identity, contract=replace(contract, settlement="INMEDIATA"))
    assert transport.calls == []
    sector_map = tmp_path / "sector-map.csv"
    sector_map.write_text(
        "ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n"
        + ",".join((*identity, "TEST_SECTOR", "TEST_REVIEWED_FIXTURE", "RC6_TEST", "2026-09-10", "true"))
        + "\n")
    monkeypatch.setenv("POROTA_SECTOR_MAP_PATH", str(sector_map))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
    if family == "ETFS":
        from rc6_performance import costs
        from tests.test_rc6_etf_cost_cycle import synthetic_terms

        fee_policy = replace(synthetic_terms(), family=family)
        registered_cost = costs.paper_cost_contract

        def replay_cost(asset_class, cash_currency, decision_at, **fields):
            key = (fields.get("symbol"), asset_class, fields.get("market"), cash_currency,
                   fields.get("settlement"))
            if key == identity:
                return fee_policy
            return registered_cost(asset_class, cash_currency, decision_at, **fields)

        monkeypatch.setattr(costs, "paper_cost_contract", replay_cost)
    broker = PaperBroker(store, clock_fn=lambda: transport.now.isoformat(), ai_mode="OFF",
                         economics_mode="SHADOW", session_policy=PaperSessionPolicy(),
                         admission_guard=paper_admission_guard)
    freeze = {"cutoff": (opening - timedelta(days=1)).isoformat(),
              "frozen_at": (opening - timedelta(minutes=10)).isoformat(), "session_open": opening.isoformat(),
              "rows": [{"identity": identity, "rank": 1, "tradeable": True, "tradeability_score": 1}]}
    profile = dict(session_open=opening, frozen={"payload": freeze, "digest": digest(freeze)},
                   market_provider="IOL_REST")
    capacity = {"status": "SHADOW_RECOMMENDATION", "safe_limit": 8, "slots_by_endpoint": {"current": 8, "book": 8},
                "generated_at": opening.isoformat(), "expires_at": (AT + timedelta(hours=1)).isoformat()}
    eligibility = read_runtime(store.path, as_of=opening)["catalog"]
    planner = UniverseOrchestrator(eligibility, policy=EnginePolicy(engine="EQUITY_SPOT", warmup_samples=3))
    funnel = planner.plan(at=opening, capacity={}, **profile)
    assert funnel["telemetry"][0]["state"] == "DISCOVERY"
    funnel = planner.plan(at=opening, capacity=capacity, previous=funnel, **profile)
    assert funnel["telemetry"][0]["state"] == "WARM"
    for index, price in enumerate(range(95, 104)):
        transport.now = AT + timedelta(minutes=index)
        ReplayClock.value = transport.now
        transport.price = round(price * price_scale, 2)
        trade_at = (transport.now - timedelta(seconds=3)).isoformat()
        observed = observer._read_scanner_quote(scanner, store, *request, identity=identity, priority="HOT")
        assert observed.metadata_source == "IOL_REST" and not observed.opening_block_reason
        assert observed.trade_at == trade_at
        assert observed.iol_trade_evidence["identity"] == list(identity)
        assert observed.book_at == (transport.now - timedelta(seconds=1)).isoformat()
        assert paper_admission_guard(observed, store) == (True, "")
        store.add_quote(observed)
        snapshots = read_runtime(store.path, as_of=transport.now)["observations"]
        funnel = planner.plan(at=transport.now, capacity=capacity, observations=snapshots, previous=funnel, **profile)
        assert not _scanner_trade_temporal_rejection(
            observed, broker, store, checked_at=transport.now.isoformat())
        if index == 0:
            assert paper_admission_guard(replace(observed, iol_trade_evidence=None), store) == (
                False, "IOL_LAST_TRADE_EVIDENCE_MISSING")
            bad = {**observed.iol_trade_evidence, "price": "1.23"}
            assert paper_admission_guard(replace(observed, iol_trade_evidence=bad), store) == (
                False, "IOL_LAST_TRADE_EVIDENCE_MISMATCH")
            action, _, _, fields = broker.decide(observed)
            assert (action, fields["reason_code"]) == ("HOLD", "SIGNAL_WARMUP")
    reached = funnel["telemetry"][0]
    assert tuple(reached["identity"]) == identity and reached["state"] == "HOT"
    assert reached["signal_result"] == "SAMPLES_READY" and not reached["entry_authority"]
    idle, detail, _ = _collect_iol_exit_round(
        exit_reader, store, PaperSessionPolicy(), transport.now, Event(),
        beat=lambda: None, deadline_seconds=5)
    assert idle == "READY" and "budget_round=IDLE_NO_OPEN_POSITIONS" in detail
    broker.on_quote(observed, allow_new_openings=allowed, opening_block_reason=opening_reason)
    position, = store.open_positions()
    assert tuple(position[field] for field in ("symbol", "asset_class", "market", "currency", "settlement")) == identity
    transport.price = float(Decimal(position["target_price"]) + Decimal("0.20"))
    transport.now += timedelta(minutes=5)
    ready, detail, _ = _collect_iol_exit_round(
        exit_reader, store, PaperSessionPolicy(), transport.now, Event(),
        beat=lambda: None, deadline_seconds=5)
    assert ready == "READY" and "verified_books=1; budget_round=COMPLETE" in detail
    closing = store.latest_quote(position)
    assert closing.metadata_source == "IOL_REST" and closing.trade_at is None
    assert closing.opening_block_reason == "IOL_EXIT_OBSERVATION_ONLY"
    assert transport.calls == ["token"] + [ticker, "quote", "depth"] * 9 + [ticker, "depth"]
    key = tuple(position[name] for name in ("symbol", "asset_class", "settlement", "currency", "market"))
    verdict = PositionExitSupervisor(broker, clock_fn=lambda: transport.now.isoformat()).tick({key: closing})[0]
    assert (verdict.state, verdict.cause) == ("CLOSED", "TAKE_PROFIT_PAPER")
    assert not store.open_positions()
    SampleMaterializer(store).tick(transport.now)
    with store.connect() as db:
        rows = db.execute("""SELECT DISTINCT json_extract(identity_json,'$.resolution') FROM candle_series
            WHERE json_extract(identity_json,'$.source')='IOL_REST_TRADE_SAMPLE'""").fetchall()
        assert {row[0] for row in rows} == {"1m", "5m"}
        decisions = db.execute("SELECT symbol,action FROM paper_decisions WHERE action='BUY'").fetchall()
        assert [tuple(row) for row in decisions] == [(ticker, "BUY")]
        gates = db.execute("SELECT symbol,final_result,paper_id FROM trade_gate_evaluations").fetchall()
        assert [tuple(row) for row in gates] == [(ticker, "OPENED_SIMULATED", position["paper_id"])]
        fills = db.execute("SELECT paper_id,side FROM paper_fills ORDER BY id").fetchall()
        assert [tuple(row) for row in fills] == [(position["paper_id"], "BUY_SIMULATED"),
                                                (position["paper_id"], "SELL_SIMULATED")]
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
            "PRODUCTION_PAPER", 0)



def test_iol_trade_stream_authenticates_and_keeps_only_the_subscribed_trade():
    from rc6_iol_trade_stream import IOLTradeStream, matched_trade

    key = ("GGAL", "BCBA", "T1")
    stop = Event()
    subscribed = {"type": "Success", "code": 200, "msg": "Subscribed"}
    connected = {"type": "Success", "code": 200, "msg": "Connected"}
    fields = {"S": key[0], "M": key[1], "T": key[2], "P": 100, "X": 15, "D": AT.isoformat()}
    messages = iter((connected, subscribed, {"type": "T", "code": 200, "msg": fields},
                     {"type": "Q", "code": 200, "msg": {**fields, "P": 200}},
                     {"type": "T", "code": 200, "msg": {**fields, "T": "T0"}}))
    outgoing = []

    def receive(*, timeout):
        assert timeout > 0
        message = next(messages, None)
        if message is None:
            stop.set()
            raise TimeoutError
        return json.dumps(message)

    socket = SimpleNamespace(send=lambda value: outgoing.append(json.loads(value)), recv=receive)
    now = AT + timedelta(seconds=1)
    stream = IOLTradeStream(lambda: "fixture-bearer", clock=lambda: now)
    stream.want(key)
    stream._session(socket, "fixture-bearer", stop)
    assert [message["action"] for message in outgoing] == ["auth_token", "subscribe", "disconnect"]
    assert outgoing[1]["trades"] == ["GGAL|BCBA|T1"]
    evidence = stream.evidence(key)
    assert stream.evidence(("GGAL", "BCBA", "T0")) is None
    quote = {"prices": {"current": 100}, "volume": {"shares": 15}}
    assert matched_trade(evidence, quote, now) == AT.isoformat()
    assert matched_trade(evidence, {**quote, "volume": {"shares": 14}}, now) is None

def test_iol_gateway_429_blocks_opening_and_exit_until_the_shared_budget_recovers(iol_gateway):
    _, client, transport, advance = iol_gateway
    register(client, "scanner")
    register(client, "exit_reader")
    scanner = IOLGatewayMarketReader(client, consumer="scanner", trade_clock=lambda row: row["last_updated"],
                                    clock=lambda: transport.now)
    exit_reader = IOLGatewayMarketReader(client, consumer="exit_reader", clock=lambda: transport.now)
    transport.reject = "quote"
    with pytest.raises(IOLRestError, match="IOL_REST_HTTP_ERROR") as hold:
        scanner.observe(GGAL)
    assert (hold.value.status_code, hold.value.retry_after) == (429, 7)
    assert transport.calls == ["token", "GGAL", "quote"]
    with pytest.raises(IOLRestError, match="IOL_GATEWAY_RATE_HOLD"):
        exit_reader.observe(GGAL, priority="EXIT_CRITICAL", exit_only=True)
    assert transport.calls == ["token", "GGAL", "quote"]
    advance(7)
    observation = exit_reader.observe(GGAL, priority="EXIT_CRITICAL", exit_only=True)
    assert observation.current is None and transport.calls[-2:] == ["GGAL", "depth"]


def test_iol_gateway_rolling_budget_defers_without_waiting_or_requesting_book(iol_gateway):
    server, client, transport, advance = iol_gateway
    server._rest.governor.policy = CollectionPolicy(max_calls_per_minute=1)
    register(client, "scanner")
    with pytest.raises(IOLRestError, match="IOL_GATEWAY_RATE_HOLD"):
        client.market_read("quote", GGAL, consumer="scanner", priority="HOT")
    assert transport.calls == ["token"]
    advance(60)
    data, _ = client.market_read("quote", GGAL, consumer="scanner", priority="HOT")
    assert data["symbol"] == "GGAL" and transport.calls == ["token", "quote"]


def test_iol_gateway_binds_roles_to_separate_processes_and_rejects_stale_generation(iol_gateway):
    server, client, transport, _ = iol_gateway
    with pytest.raises(IOLRestError, match="IOL_GATEWAY_UNREGISTERED_CLIENT"):
        client.market_read("quote", GGAL, consumer="scanner", priority="HOT")
    register(client, "scanner")
    with pytest.raises(IOLRestError, match="IOL_GATEWAY_IDENTITY_SCOPE_DENIED"):
        client.market_read("quote", (*GGAL[:4], "INMEDIATA"), consumer="scanner", priority="HOT")
    stale = {"op": "iol_market_read", "consumer": "scanner", "endpoint": "quote",
             "identity": list(GGAL), "priority": "HOT", "session_generation": "0" * 64}
    assert client._request(stale, require_owner_identity=True)["state"] == "IOL_GATEWAY_OWNER_CHANGED"
    assert client._request({"op": "submit_order"}, require_owner_identity=True)["state"] == "IOL_GATEWAY_REQUEST_DENIED"
    assert transport.calls == []
    # The foreign process uses the same Unix UID. Only its registered PID/birth
    # tick can use the EXIT role, and it cannot request a last trade for EXIT.
    child_code = """import json, sys
from pathlib import Path
from rc6_iol_market_gateway import IOLGatewayClient
from iol_rest_readonly_rc6 import IOLRestError
run_socket, identity = json.loads(sys.stdin.readline())
if run_socket:
    client = IOLGatewayClient(socket_path=Path(sys.argv[1]), timeout_seconds=5)
    client.owner_status()
    failures = []
    for role, priority in (("scanner", "HOT"), ("exit_reader", "EXIT_CRITICAL")):
        try:
            client.market_read("quote", identity, consumer=role, priority=priority)
        except IOLRestError as error:
            failures.append(str(error))
    payload, _ = client.market_read("depth", identity, consumer="exit_reader", priority="EXIT_CRITICAL")
    print(json.dumps([failures, payload["symbol"]]))
"""
    with subprocess.Popen([sys.executable, "-B", "-c", child_code, str(server.socket_path)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
        register(client, "exit_reader", child.pid)
        with pytest.raises(IOLRestError, match="IOL_GATEWAY_UNREGISTERED_CLIENT"):
            client.market_read("depth", GGAL, consumer="exit_reader", priority="EXIT_CRITICAL")
        if os.environ.get("POROTA_IOL_OFFHOST_IPC") == "1":
            output, error = child.communicate(json.dumps([True, GGAL]) + "\n", timeout=10)
            assert child.returncode == 0, error
            assert json.loads(output) == [
                ["IOL_GATEWAY_UNREGISTERED_CLIENT", "IOL_GATEWAY_ROLE_SCOPE_DENIED"], "GGAL"]
        else:
            for role, priority, state in [
                    ("scanner", "HOT", "IOL_GATEWAY_UNREGISTERED_CLIENT"),
                    ("exit_reader", "EXIT_CRITICAL", "IOL_GATEWAY_ROLE_SCOPE_DENIED")]:
                packet = stale | {"consumer": role, "priority": priority,
                                  "session_generation": client._pinned_owner[1]}
                assert server.dispatch(packet, peer_pid=child.pid)["state"] == state
            packet = packet | {"endpoint": "depth"}
            assert server.dispatch(packet, peer_pid=child.pid)["payload"]["symbol"] == "GGAL"
            child.communicate(json.dumps([False, GGAL]) + "\n", timeout=10)
    assert transport.calls == ["token", "depth"]


def test_iol_book_selects_best_prices_and_rejects_ambiguous_best_size(tmp_path):
    market, transport = reader(tmp_path)
    depth = {"bids": [{"price": 98, "size": 4}, {"price": 99, "size": 5}],
             "asks": [{"price": 103, "size": 6}, {"price": 101, "size": 7}]}
    transport.overrides["depth"] = depth
    quote = market.observe(GGAL).quote
    assert (quote.bid, quote.bid_size, quote.ask, quote.ask_size) == (99, 5, 101, 7)
    depth["bids"].append({"price": 99, "size": 1})
    with pytest.raises(IOLRestError, match="IOL_BOOK_BEST_LEVEL_AMBIGUOUS"):
        market.observe(GGAL)


@pytest.mark.parametrize(("report", "fields", "reason"), [
    ("quote", {"currency": "USD"}, "IOL_QUOTE_CURRENCY_UNVERIFIED"),
    ("quote", {"settlement_term": "T0"}, "IOL_MARKET_IDENTITY_MISMATCH"),
    ("quote", {"last_updated": "2026-11-24T14:30:00"}, "IOL_MARKET_CLOCK_UNVERIFIED"),
    ("quote", {"last_updated": (AT - timedelta(seconds=121)).isoformat()}, "IOL_LAST_TRADE_STALE"),
    ("depth", {"last_updated": (AT + timedelta(seconds=1)).isoformat()}, "IOL_MARKET_CLOCK_IN_FUTURE"),
    ("depth", {"asks": []}, "IOL_BOOK_TWO_SIDED_UNVERIFIED"),
    ("depth", {"bids": [{"price": 101, "size": 1}]}, "IOL_BOOK_CROSSED"),
])
def test_iol_pair_rejects_unusable_data(tmp_path, report, fields, reason):
    market, transport = reader(tmp_path)
    transport.overrides[report] = fields
    with pytest.raises(IOLRestError, match=reason):
        market.observe(GGAL)
    assert ("depth" in transport.calls) == (report == "depth")


def test_iol_current_rejection_preserves_status_and_does_not_consume_depth(tmp_path):
    market, transport = reader(tmp_path)
    transport.reject = "quote"
    with pytest.raises(IOLRestError) as failure:
        market.observe(GGAL)
    assert (failure.value.status_code, failure.value.retry_after) == (429, 7)
    assert "depth" not in transport.calls
    assert market.observe(GGAL, exit_only=True).quote.last_kind == "REFERENCE"


def test_iol_response_update_clock_is_not_a_trade_clock(tmp_path):
    market, transport = reader(tmp_path)
    observation = IOLMarketReader(market.rest, clock=lambda: transport.now)
    with pytest.raises(IOLRestError, match="IOL_LAST_TRADE_CLOCK_UNVERIFIED"):
        observation.observe(GGAL)
    assert observation.observe(GGAL, exit_only=True).current is None


def test_iol_daily_ingest_preserves_canonical_history(tmp_path):
    market, transport = reader(tmp_path)
    history = HistoricalStore(str(tmp_path / "history.db"))
    kwargs = {"symbol": "GGAL", "instrument_type": "ACCIONES", "market": "BYMA", "currency": "ARS",
              "settlement": "A-24HS"}
    previous = AT - timedelta(days=4)
    append_candle(history, Candle(**kwargs, date=previous.date().isoformat(), open=98, high=102,
                                  low=97, close=101, volume=100, source="PPI_PRODUCTION_HISTORY",
                                  observed_at=previous.replace(hour=21, minute=1), volume_kind="QUANTITY"))
    with history.connect() as db:
        before = read_as_of(db, **kwargs, as_of=AT.isoformat())
    assert len(before) == 1
    reference = _source_store(tmp_path / "reference.db")
    day = (AT - timedelta(days=1)).date()
    query = {"symbol": "GGAL", "family": "ACCIONES", "market": "BCBA", "start": day, "end": day,
             "now": lambda: transport.now}
    with pytest.raises(ValueError, match="IOL_HISTORY_REFERENCE_STORE_REQUIRED"):
        ingest_daily_reference(market.rest, history, **query)
    assert transport.calls == []
    assert ingest_daily_reference(market.rest, reference, **query)["versions_appended"] == 1
    assert ingest_daily_reference(market.rest, reference, **query)["versions_appended"] == 0
    with reference.connect() as db:
        row = db.execute("SELECT source,price_basis,currency,settlement FROM history_versions_v2").fetchone()
        assert tuple(row) == ("IOL_REST", "SOURCE_REPORTED", "UNKNOWN", "UNKNOWN")
    with history.connect() as db:
        assert read_as_of(db, **kwargs, as_of=AT.isoformat()) == before


def test_predeploy_packaging_and_promotion_reject_testing_tree(tmp_path):
    repository = tmp_path / "project"
    repository.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repository), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "ci@example.invalid")
    git("config", "user.name", "CI")
    (repository / "fixture.py").write_text("pass\n")
    git("add", "fixture.py")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    marker = repository / TESTING_ONLY_MARKER
    marker.parent.mkdir(parents=True)
    marker.write_text('{"release_allowed":false}\n')
    git("add", TESTING_ONLY_MARKER)
    git("commit", "-qm", "test candidate")
    head = git("rev-parse", "HEAD")
    with pytest.raises(ValueError, match=RELEASE_BLOCKED):
        classify(repository, base, head)
    bundle = tmp_path / "release.tgz"
    with pytest.raises(ValueError, match=RELEASE_BLOCKED):
        build_bundle(repository, bundle)
    assert not bundle.exists()
    with pytest.raises(ValueError, match=RELEASE_BLOCKED):
        promotion_source(repository, head)


@pytest.mark.parametrize(("counter", "expected"), [(1800, "1800"), (-1, None), (None, None)])
def test_iol_archive_distinguishes_day_nominals_from_trade_matching_field(tmp_path, iol_gateway, counter, expected):
    from rc6_iol_trade_stream import last_trade

    server, client, transport, _ = iol_gateway
    register(client, "scanner")
    event = {"type": "T", "code": 200, "msg": {"S": GGAL[0], "M": "BCBA", "T": transport.term,
             "D": (transport.now - timedelta(seconds=2)).isoformat(), "P": transport.price, "X": 100}}
    if counter is not None:
        event["msg"]["TS"] = counter
    wire, evidence = last_trade(event, transport.now - timedelta(seconds=1))
    server.trade_stream = SimpleNamespace(want=lambda key: None,
                                          evidence=lambda key: evidence if key == wire else None, state="SUBSCRIBED")
    market = IOLGatewayMarketReader(client, consumer="scanner", clock=lambda: transport.now)
    store = PaperStore(str(tmp_path / "volume.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    assert record_iol_trade_sample(store, market.observe(GGAL), as_of=transport.now)
    with store.connect() as db:
        raw, = db.execute("SELECT body_json FROM historical_raw_archive").fetchone()
    archived = json.loads(raw).get("iol_day_counter")
    if expected is None:
        assert archived is None
    else:
        assert archived == {"nominals": expected, "gateway_generation": server._generation}
