"""OFFHOST: the real SDK adapter, Gateway, catalog and PAPER gate for ten families."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from ppi_client.ppi import PPI
import pytest
import requests

from bd_ppi_readonly_guard import ProductionMarketReader
from be_paper_engine import PaperBroker, PaperStore
import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
from bm_exit_supervisor import PositionExitSupervisor
from bq_exit_policy import PaperSessionPolicy
from bv_paper_runtime import (
    _close_ppi_gateway_server, _start_ppi_gateway_server, collect_exit_books,
)
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge
import rc6_paper_admission_gate as admission
from rc6_paper_family_lifecycle import FUTURE_QUOTE_PROOF_SCHEMA
from rc6_ppi_gateway_reader_adapter import SharedPPIReaderAdapter
from rc6_ppi_shared_feed import ReadOnlyUnixFeedClient, ReadOnlyUnixFeedServer
import rc6_ppi_native_request_receipt as settlement_proof
from rc6_ppi_shared_session_owner import (
    PPISharedSessionOwner, PrimaryPPICatalogAuthority, load_primary_ppi_catalog_snapshot,
)
from rc6_shadow_runtime.entry_signals import evaluate_runtime_entry_signals
from rc6_shadow_runtime.funnel import evaluate_runtime_funnel
from tests.test_rc6_a680_ppi_guard_wire_receipt import FakeBudget
from tests.test_rc6_etf_cost_cycle import synthetic_terms
from tests.test_rc6_ppi_native_request_receipt import PARTS, RELEASE, pin


AT = datetime(2026, 11, 24, 14, 30, tzinfo=timezone.utc)
CASES = [
    ("GGAL", "ACCIONES", "BYMA", "A-24HS"),
    ("AAPL", "CEDEARS", "BYMA", "A-24HS"),
    ("SPY", "ETFS", "BYMA", "A-24HS"),
    ("AL30", "BONOS", "BYMA", "A-24HS"),
    ("QA_LETRA", "LETRAS", "BYMA", "A-24HS"),
    ("QA_ON", "OBLIGACIONES", "BYMA", "A-24HS"),
    ("YPFC40500D", "OPCIONES", "BYMA", "INMEDIATA"),
    ("DLR/NOV26", "FUTUROS", "A3", "INMEDIATA"),
    ("PESOS1", "CAUCIONES", "BYMA", "INMEDIATA"),
    ("QA_FCI", "FCI", "FCI", "INMEDIATA"),
]
FIXED_INCOME = {"BONOS", "LETRAS", "OBLIGACIONES"}
SPOT = {"ACCIONES", "CEDEARS", "ETFS"}
OPTION_UNDERLYING = ("YPFD", "ACCIONES", "BYMA", "ARS", "INMEDIATA")


class ReplayClock(datetime):
    value = AT

    @classmethod
    def now(cls, tz=None):
        return cls.value.astimezone(tz) if tz is not None else cls.value.replace(tzinfo=None)


class ReplayBudget(FakeBudget):
    def acquire(self, endpoint, *, consumer, priority):
        super().acquire(endpoint, consumer=consumer, priority=priority)
        return {"allowed": True, "lease": f"OFFHOST-LEASE-{len(self.calls)}"}


def _record(ticker, family, market, settlement, *, option_underlying=OPTION_UNDERLYING[0],
            option_strike="40500.00"):
    raw = {"ticker": ticker, "type": family, "market": market, "currency": "ARS"}
    if family == "OPCIONES":
        raw["description"] = f"Opción compra {option_underlying} AR$ {option_strike} Vto. 18/12/2026"
    if family in FIXED_INCOME:
        spec = {"market": market, "currency": "ARS", "settlement": settlement,
                "quantity_step": "1", "cash_multiplier": ".01",
                "metadata_source": "OFFHOST_FIXTURE_ECONOMIC_TERMS"}
        raw["financial_contract_v17"] = spec
    return catalog.normalize_record(raw, settlement, AT.isoformat(), "OFFHOST_FAMILY_TEST")


def _derivative_economics(store, family):
    with store.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM financial_instrument_catalog")]
    planned, _, _ = mass.planned_records(rows)
    evidence.record_snapshots(store, planned)
    claim = next(c for c in bridge.complements_from_store(store, now=AT)
                 if c["instrument_type"] == family)
    primary = next(r for r in rows if r["instrument_type"] == family)
    primary["raw"] = json.loads(primary.pop("metadata_json"))
    with store.connect() as con:
        catalog.persist(con, catalog.complete_with_complement(primary, claim))
        catalog.sync_candidate_universe(con, AT.isoformat())


@pytest.fixture
def gateway_socket(tmp_path_factory):
    private = tmp_path_factory.mktemp("rc6-socket")
    private.chmod(0o700)
    return private / "market.sock"


def _preopen(tmp_path, monkeypatch):
    receipt_path = tmp_path / "preopen.json"
    state_path = tmp_path / "release-state.json"
    state_path.write_text(json.dumps({
        "candidate_sha": RELEASE[0], "validation_status": "VALIDATED_RUNTIME",
        "mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
    }))
    # Only the modeled operator rearm is injected. Receipt publication, day,
    # clock, release and LIVE PAPER admission use the real implementation.
    monkeypatch.setattr(admission, "evaluate_rearm", lambda **_: (True, "", {}))
    evaluate = admission.admission_status
    monkeypatch.setattr(admission, "admission_status", lambda: evaluate(
        now=ReplayClock.value, candidate_sha=RELEASE[0], receipt_path=receipt_path))
    assert not admission.admission_status()[0]
    preopen = AT.astimezone(admission.TZ).replace(hour=10, minute=21)
    assert observer._market_phase(preopen) == "PREOPEN"
    receipt = admission.publish_preopen({"status": "GREEN", "phase": "T_MINUS_10"},
        now=preopen, state_path=state_path, receipt_path=receipt_path)
    assert receipt["status"] == "GO_PAPER_SCALPING"
    assert observer._market_phase(AT.astimezone(admission.TZ)) == "OPEN"
    assert admission.admission_status() == (True, "")


@pytest.mark.parametrize(("ticker", "family", "market", "term", "trade_age", "book_age", "test_fees"), [
    *(pytest.param(*case, 0, 0, False, id=case[1]) for case in CASES),
    pytest.param(*CASES[0], 45, 45, False, id="GGAL_SPOT_CLOCKS_OUTSIDE_CACHE"),
    pytest.param(*CASES[0], 121, 0, False, id="GGAL_STALE_CURRENT_DOES_NOT_REQUEST_BOOK"),
    pytest.param("MERV", "ETFS", "BYMA", "A-24HS", 0, 0, True, id="MERV_TEST_FEE_CYCLE"),
])
def test_one_instrument_per_family_through_gateway_and_engine(
        tmp_path, monkeypatch, gateway_socket, ticker, family, market, term, trade_age, book_age, test_fees):
    replay_native_family(tmp_path, monkeypatch, gateway_socket, ticker, family, market, term,
                         trade_age, book_age, test_fees)


def replay_native_family(tmp_path, monkeypatch, gateway_socket, ticker, family, market, term,
                         trade_age, book_age, test_fees, *, option_underlying=OPTION_UNDERLYING,
                         option_strike="40500.00"):
    key = (ticker, family, market, "ARS", term)
    store = PaperStore(str(tmp_path / "paper.db"))
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    observer._support_schema(store)
    monkeypatch.setenv("PAPER_V17_DB_PATH", store.path)
    monkeypatch.setenv("POROTA_BUILD_SHA", RELEASE[0])
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", RELEASE[1])
    monkeypatch.setattr("bd_ppi_readonly_guard.datetime", ReplayClock)
    monkeypatch.setattr(catalog, "datetime", ReplayClock)
    monkeypatch.setattr(settlement_proof, "datetime", ReplayClock)
    monkeypatch.setattr(observer, "now_iso", lambda: ReplayClock.value.isoformat())
    monkeypatch.setattr(admission, "datetime", ReplayClock)
    _preopen(tmp_path, monkeypatch)
    with store.connect() as con:
        if family == "OPCIONES":
            (underlying_ticker, underlying_family, underlying_market,
             underlying_currency, underlying_term) = option_underlying
            underlying = catalog.normalize_record(
                {"ticker": underlying_ticker, "type": underlying_family, "market": underlying_market,
                 "currency": underlying_currency, "settlement": underlying_term},
                underlying_term, AT.isoformat(), "OFFHOST_UNDERLYING")
            catalog.persist(con, underlying)
        record = _record(ticker, family, market, term, option_underlying=option_underlying[0],
                         option_strike=option_strike)
        catalog.persist(con, record)
        con.commit()
        catalog.sync_candidate_universe(con, AT.isoformat(), bounded_batch=128)
    rows, keys, digest, literals = load_primary_ppi_catalog_snapshot(
        store.path, include_diagnostic_candidates=True)
    assert key in keys and key not in literals
    assert catalog.lookup(store, ticker, family, term, market=market, currency="ARS")[
        "_admission_reason"] == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"

    calls, budget = [], ReplayBudget()
    price = 1500.0 if family == "FUTUROS" else 100.0

    def send(_, request, **kwargs):
        endpoint = urlsplit(request.url).path.rsplit("/", 1)[-1]
        query = {k.lower(): v for k, v in parse_qs(urlsplit(request.url).query).items()}
        assert endpoint in {"Current", "Book"} and request.method == "GET"
        subyacente = family == "OPCIONES" and query.get("ticker") == [option_underlying[0]]
        trading_key = option_underlying if subyacente else key
        assert budget.in_wire_scope and query == {
            "ticker": [trading_key[0]], "type": [trading_key[1]], "settlement": [trading_key[4]]}
        calls.append(endpoint)
        age = trade_age if endpoint == "Current" else book_age
        body = {"date": (ReplayClock.value - timedelta(seconds=age)).isoformat()}
        last = (float(option_strike) + 10 + (ReplayClock.value - AT).total_seconds()
                if subyacente else price)
        if endpoint == "Current":
            body.update(price=last, volume=1000, openingPrice=last, max=last, min=last)
        else:
            half_spread = .5 if family == "FUTUROS" else .1
            body.update(bids=[{"position": 0, "price": round(last - half_spread, 8), "quantity": 10000}],
                        offers=[{"position": 0, "price": round(last + half_spread, 8), "quantity": 10000}])
        response = requests.Response()
        response.status_code, response.url, response.request = 200, request.url, request
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(body).encode()
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    reader = ProductionMarketReader("OFFHOST_KEY", "OFFHOST_SECRET", budget=budget, consumer="SCANNER")
    # Only an offline authenticated-session fixture. Every market GET still
    # crosses the project's real SDK guard and the injected HTTPAdapter.send.
    reader._ProductionMarketReader__client = PPI(sandbox=False)
    reader._ProductionMarketReader__authenticated = True
    reader._ProductionMarketReader__guard.login_calls = 1
    signer = settlement_proof.SettlementReceiptSigner(RELEASE)
    pin(store.path, signer)
    # The test process supplies the supervisor and role PIDs. Registration,
    # peer credentials, request framing and the priority router use real IPC.
    with monkeypatch.context() as setup:
        setup.setattr(os, "getppid", os.getpid)
        gateway = PPISharedSessionOwner(
            reader_factory=lambda: reader, instrument_factory=lambda *args: args,
            stream_authority=SimpleNamespace(permits=lambda *_a, **_k: False),
            external_capacity_authority=SimpleNamespace(permits=lambda *_a, **_k: True),
            catalog_authority=PrimaryPPICatalogAuthority(literals, query_keys=keys),
            ppi_catalog_rows=rows, catalog_five_keys=keys, catalog_source_digest=digest,
            consumer_five_keys={"scanner": keys, "exit_reader": keys},
            socket_path=gateway_socket, settlement_signer=signer,
        )
    gateway._native.clock = lambda: ReplayClock.value
    gateway.open_once()
    server_thread = None
    try:
        if os.environ.get("POROTA_IOL_OFFHOST_IPC") == "1":
            server_thread = _start_ppi_gateway_server(gateway)
            client = ReadOnlyUnixFeedClient(socket_path=gateway_socket, timeout_seconds=5)
            assert client.owner_status()["native_router"]["running"]
            assert client.acquire_native("scanner", method="current", five_key=key,
                                         priority="DISCOVERY")["state"] == "PPI_GATEWAY_UNREGISTERED_CLIENT"
            assert calls == []
            for role in ("scanner", "exit_reader"):
                registered = client.register_client(role, pid=os.getpid(),
                    proc_starttime=ReadOnlyUnixFeedServer.process_starttime(os.getpid()))
                assert registered["state"] == "REGISTERED"
        else:
            # Keep the deterministic tests usable in sandboxes without sockets.
            # The dedicated GitHub job requests real IPC and cannot fall back.
            client = SimpleNamespace(owner_status=gateway.status,
                pull=lambda *_: pytest.fail("stream not expected"),
                acquire_native=lambda role, **kw: gateway.dispatch_native_acquire(consumer=role, **kw))
        adapter = SharedPPIReaderAdapter(
            consumer="scanner", catalog_source_digest=digest, client=client)
        adapter.login_once()
        observed = observer._ppi_direct_rest_observe_only(
            adapter, store, family=family, confirm_settlement=True)
        assert not observed["paper_ready"]
        if trade_age > settlement_proof.SETTLEMENT_PAIR_MAX_SECONDS:
            assert calls == ["Current"]
            assert observed["reason"] == "PPI_DISCOVERY_CURRENT_STALE_OR_INVALID"
            assert not observed["settlement_attested"]
            assert not store.open_positions()
            return
        if family in {"CAUCIONES", "FCI"}:
            assert calls == ["Current"]
            assert observed["status"] == observer._PPI_REFERENCE_OBSERVED[family]
            assert catalog.lookup(store, ticker, family, term, market=market, currency="ARS")[
                "_admission_reason"] == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
            return
        assert observed["status"] == "FRESH_OBSERVED"
        assert observed["settlement_attested"]
        assert calls == ["Current", "Book"]
        from bl_candle_engine import SampleMaterializer

        SampleMaterializer(store).tick(AT + timedelta(minutes=5))
        with store.connect() as connection:
            guarded_bars = connection.execute("""SELECT json_extract(s.identity_json,'$.resolution'),
                v.body_json FROM candle_versions v JOIN candle_series s USING(series_id)
                WHERE json_extract(s.identity_json,'$.source')='PPI_GUARDED_CURRENT'
                ORDER BY 1""").fetchall()
        assert [row[0] for row in guarded_bars] == ["1m", "5m"]
        for _, body in guarded_bars:
            candle = json.loads(body)
            assert candle["quality"] == "SAMPLED" and candle["volume"] is None
        if family in {"OPCIONES", "FUTUROS"}:
            _derivative_economics(store, family)
        item = catalog.lookup(store, ticker, family, term, market=market, currency="ARS")
        assert item["settlement_source"] == "REQUEST_CANDIDATE"
        assert item["raw"].get("settlement") is None
        assert settlement_proof.FIELD in item["raw"]
        assert "_admission_reason" not in item, item.get("_admission_reason")
        assert item["capability"].startswith("READY_PAPER_")
        assert observer._eligible_scanner_identities(store).get((ticker, family, term)) == key
        monkeypatch.setattr(ReplayClock, "value", AT + timedelta(seconds=1))
        live_quote = observer._read_scanner_quote(adapter, store, ticker, family, term, identity=key)
        assert calls == ["Current", "Book", "Current", "Book"]
        assert not live_quote.opening_block_reason
        assert not live_quote.time_error(ReplayClock.value.isoformat(), require_trade=True)
        assert admission.paper_admission_guard(live_quote, store) == (True, "")
        assert not store.open_positions()
        if family not in SPOT | {"OPCIONES", "FUTUROS"} or trade_age or book_age:
            return

        sector_map = tmp_path / "sectors.csv"
        sector_map.write_text("ticker,family,market,currency,settlement,sector,source,author,effective_at,reviewed\n"
            + ",".join((*key, "TEST_SECTOR", "REVIEWED_FIXTURE", "RC6_TEST", "2026-09-10", "true")) + "\n")
        monkeypatch.setenv("POROTA_SECTOR_MAP_PATH", str(sector_map))
        monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
        if test_fees:
            from rc6_performance import costs

            reviewed_test_policy = synthetic_terms()
            exact_cost = costs.paper_cost_contract

            def test_fee_contract(family, currency, decision_at, **identity):
                if (family, currency, identity.get("symbol"), identity.get("market"),
                        identity.get("settlement")) == ("ETFS", "ARS", "MERV", "BYMA", "A-24HS"):
                    return reviewed_test_policy
                return exact_cost(family, currency, decision_at, **identity)

            monkeypatch.setattr(costs, "paper_cost_contract", test_fee_contract)
        policy = PaperSessionPolicy()
        broker = PaperBroker(store, clock_fn=lambda: ReplayClock.value.isoformat(), ai_mode="OFF",
                             economics_mode="SHADOW", admission_guard=admission.paper_admission_guard,
                             session_policy=policy, require_supervisor=False, risk_pct=".02",
                             initial_cash="10000000" if family == "FUTUROS" else "1000000",
                             max_position_pct=".30" if family == "FUTUROS" else ".10")
        request = (ticker, family, term)
        focus, selected, _, _, _, feasibility = observer._runtime_readiness(store)
        assert request in selected
        allowed, reason = observer._scanner_opening_capacity(request, focus=focus, feasibility=feasibility)
        assert allowed, reason
        _, checkpoint = evaluate_runtime_funnel(store.path, as_of=ReplayClock.value, planner_report={})
        if family == "FUTUROS":
            _, entry_checkpoint = evaluate_runtime_entry_signals(store.path, as_of=ReplayClock.value)
        prices = range(1490, 1535, 5) if family == "FUTUROS" else range(95, 104)
        for turn, price in enumerate(prices, start=1):
            ReplayClock.value = AT + timedelta(minutes=turn)
            if family == "OPCIONES":
                underlying = option_underlying
                under_quote = observer._read_scanner_quote(
                    adapter, store, underlying[0], underlying[1], underlying[4], identity=underlying)
                store.add_quote(under_quote)
            sampled = observer._read_scanner_quote(adapter, store, *request, identity=key)
            store.add_quote(sampled)
            assert not observer._scanner_trade_temporal_rejection(sampled, broker, store,
                                                                  checked_at=ReplayClock.value.isoformat())
        broker.on_quote(sampled, allow_new_openings=allowed, opening_block_reason=reason)
        with store.connect() as db:
            decisions = db.execute("SELECT action,reason FROM paper_decisions").fetchall()
            gates = db.execute("SELECT final_result,reason FROM trade_gate_evaluations").fetchall()
        if family == "ETFS" and not test_fees:
            assert len(decisions) == 1 and decisions[0][0] == "BUY"
            assert [tuple(row) for row in gates] == [("BLOCKED", "COST_COMPONENT_UNKNOWN:rights")]
            assert store.open_positions() == []
            assert calls == ["Current", "Book"] * 11
            return
        positions = store.active_future_positions() if family == "FUTUROS" else store.open_positions()
        assert len(positions) == 1, ([tuple(row) for row in decisions], [tuple(row) for row in gates])
        position, = positions
        if family == "FUTUROS":
            position = {**position, "asset_class": family}
            price += 10
            ReplayClock.value = AT.astimezone(admission.TZ).replace(hour=14, minute=50).astimezone(timezone.utc)
        else:
            price = float(Decimal(position["target_price"]) + Decimal(".20"))
            ReplayClock.value += timedelta(minutes=5)
        at = ReplayClock.value.isoformat()
        exit_reader = SharedPPIReaderAdapter(consumer="exit_reader", catalog_source_digest=digest, client=client)
        exit_reader.login_once()
        before_exit = len(calls)
        assert collect_exit_books(exit_reader, store, policy, at) == 0
        assert calls[before_exit:] == ["Book"]
        if family == "FUTUROS":
            broker.on_quote(store.latest_quote(position), allow_new_openings=False)
            assert store.active_future_positions() == []
            with store.connect() as db:
                assert [row[0] for row in db.execute(
                    "SELECT to_state FROM paper_family_lifecycle_events WHERE family='FUTUROS' ORDER BY rowid")] == [
                    "OPEN", "MARGIN_RESERVED", "DAILY_VARIATION", "MARGIN_OK", "CLOSE"]
                assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
            assert calls == ["Current", "Book"] * 11 + ["Book"]
        else:
            exit_key = tuple(position[name] for name in ("symbol", "asset_class", "settlement", "currency", "market"))
            verdict, = PositionExitSupervisor(broker, clock_fn=lambda: at).tick({exit_key: store.latest_quote(position)})
            assert (verdict.state, verdict.cause) == ("CLOSED", "TAKE_PROFIT_PAPER")
            assert store.open_positions() == []
            with store.connect() as db:
                assert [row[0] for row in db.execute("SELECT side FROM paper_fills ORDER BY id")] == [
                    "BUY_SIMULATED", "SELL_SIMULATED"]
                assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state").fetchone()) == (
                    "PRODUCTION_PAPER", 0)
            assert calls == ["Current", "Book"] * (20 if family == "OPCIONES" else 11) + ["Book"]
        as_of = ReplayClock.value + timedelta(seconds=1)
        report, _ = evaluate_runtime_funnel(store.path, as_of=as_of, planner_report={}, previous=checkpoint)
        native, = (row for row in report["by_currency_channel"] if row["channel"] == "NATIVE_FACTUAL")
        assert all(native["stages"][stage] == 1 for stage in ("PAPER_OPENED", "EXIT_REASON", "NET_PNL"))
        if family == "FUTUROS":
            entry, _ = evaluate_runtime_entry_signals(store.path, as_of=as_of, previous=entry_checkpoint)
            assert entry["native_decisions_observed"] == 1
            with store.connect() as db:
                assert db.execute("UPDATE decision_evidence_snapshots SET payload_sha256=? WHERE schema_version=?",
                                  ("f" * 64, FUTURE_QUOTE_PROOF_SCHEMA)).rowcount == 3
            with pytest.raises(ValueError, match="IMMUTABLE_DECISION_HASH_MISMATCH"):
                evaluate_runtime_funnel(store.path, as_of=as_of, planner_report={}, previous=checkpoint)
            with pytest.raises(ValueError, match="IMMUTABLE_DECISION_HASH_MISMATCH"):
                evaluate_runtime_entry_signals(store.path, as_of=as_of, previous=entry_checkpoint)
        return store
    finally:
        ReplayClock.value = AT
        _close_ppi_gateway_server(gateway, server_thread)
        gateway.close()


@pytest.mark.parametrize(("reason", "attempts"), [
    ("PPI_DIRECT_PROVIDER_429_BREAKER", 0), ("PPI_NATIVE_HTTP_429", 1),
])
def test_discovery_resumes_same_key_after_provider_pause(monkeypatch, reason, attempts):
    import time

    key = (*CASES[0][:3], "ARS", CASES[0][3])
    outcomes = iter([
        {"status": "PROVIDER_OR_CONTRACT_HOLD", "reason": reason, "native_receipts": []},
        {"status": "FRESH_OBSERVED", "settlement_attested": True,
         "native_receipts": ["current", "book"]},
    ])
    seen = []

    def observe(_reader, _store, candidate, **kwargs):
        seen.append(candidate)
        return next(outcomes)

    monkeypatch.setattr(observer, "_ppi_direct_rest_probe_candidates", lambda *a, **k: [key])
    monkeypatch.setattr(observer, "_observe_native_candidate", observe)
    monkeypatch.setattr(observer, "_pulse_observed_progress", lambda *a: 0)
    pending = {}
    deadline = time.monotonic() + 10
    cursor, _, paused = observer._discover_native_settlements(
        object(), object(), slots=1, deadline=deadline, cursor=0, after=pending, last_pulse=0)
    assert paused["attempted"] == attempts and cursor == 0 and pending == {}
    _, _, resumed = observer._discover_native_settlements(
        object(), object(), slots=1, deadline=deadline, cursor=cursor, after=pending, last_pulse=0)
    assert seen == [key, key] and resumed["attested"] == 1
