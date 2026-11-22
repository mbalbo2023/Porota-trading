"""A248: los ocho focos históricos no tienen autoridad sobre un PAPER Spot.

Todo es sintético: no hay PPI, host, broker real, órdenes ni Action. La decisión
de scanner se prueba aparte del gate de ejecución PAPER/0 del broker.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

import bf_production_paper_observer as observer
import be_paper_engine as paper
import bv_paper_runtime as runtime
import dj_caucion_live_ppi_rc6 as caucion_live
import rc6_ppi_gateway_reader_adapter as gateway
from be_paper_engine import D, PaperBroker, PaperStore, now_iso
from bm_exit_supervisor import PositionExitSupervisor
from rc6_dynamic_universe import promotion
from tests.test_production_paper_v1634 import caucion_offer, quote


FOCUS = ("GGAL", "ACCIONES", "A-24HS")
ALTERNATE = ("ZTEST", "ACCIONES", "A-24HS")
CEDEAR = ("XTEST", "CEDEARS", "A-24HS")
POSITION_OPENED_AT = "2026-08-28T11:00:00-03:00"
SCAN_AT = "2026-08-28T11:01:00-03:00"


@pytest.fixture
def store(tmp_path, monkeypatch):
    value = PaperStore(str(tmp_path / "paper.db"))
    observer._support_schema(value)
    with value.connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
            ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,
            currency TEXT NOT NULL,settlement TEXT NOT NULL,can_simulate INTEGER NOT NULL,
            status TEXT NOT NULL,detail TEXT NOT NULL,checked_at TEXT NOT NULL,
            PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
    monkeypatch.setattr(promotion, "capacity_controller_from_environment",
                        lambda *_: SimpleNamespace(state=lambda *_: {"status": "NOT_APPROVED"}))
    return value


def identity(store, request, *, market="BYMA", currency="ARS", capability="READY_PAPER_SPOT",
             catalog_status="AVAILABLE", ledger_status="AVAILABLE", can_simulate=1):
    ticker, kind, settlement = request
    with store.connect() as connection:
        connection.execute("""INSERT INTO financial_instrument_catalog
            (ticker,instrument_type,market,currency,settlement,settlement_source,
             description,last_seen_at,run_id,status,capability,metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, kind, market, currency, settlement, "PPI_FIELD",
             "A248 synthetic", now_iso(), "A248-READ_ONLY", catalog_status,
             capability, json.dumps({"ticker": ticker, "instrumentType": kind,
                                     "market": market, "currency": currency,
                                     "settlement": settlement})))
        connection.execute("""INSERT INTO candidate_identity_v2
            (ticker,instrument_type,market,currency,settlement,can_simulate,
             status,detail,checked_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (ticker, kind, market, currency, settlement, can_simulate,
             ledger_status, "A248 synthetic", now_iso()))


def capacity(store, request):
    focus, symbols, _, _, _, feasibility = observer._runtime_readiness(store)
    return focus, symbols, feasibility, observer._scanner_opening_capacity(
        request, focus=focus, feasibility=feasibility)


@pytest.fixture
def scan_once(store, tmp_path, monkeypatch):
    """Real scanner and Gateway adapter against SQLite and a fake owner wire."""
    calls, maintenance, states = [], [], []
    digest, generation = "a" * 64, "b" * 64
    state = {
        "state": "OPEN", "owner_pid": 123, "session_generation": generation,
        "catalog_source_digest": digest, "login_invocations_by_owner": 1,
        "guard_login_wire_attempts": 1, "guard_http_allowed": 0, "guard_http_blocked": 0,
    }

    def native(consumer, *, method, five_key, priority):
        calls.append((consumer, method, five_key, priority))
        ticker, family, market, currency, settlement = five_key
        payload = {"ticker": ticker, "instrumentType": family, "market": market,
                   "currency": currency, "settlement": settlement, "date": SCAN_AT}
        if method == "current":
            payload["price"] = 90
        else:
            assert method == "book"
            payload.update(bids=[{"price": 89.8, "quantity": 1000}],
                           offers=[{"price": 90.2, "quantity": 1000}])
        return {"state": "OK", "receipt": {"raw_payload": payload}}

    def attach(database, *, consumer):
        assert database == store.path and consumer == "scanner"
        reader = gateway.SharedPPIReaderAdapter(
            client=SimpleNamespace(owner_status=lambda: state, acquire_native=native,
                                   pull=lambda *_, **__: pytest.fail("Unexpected stream pull")),
            consumer=consumer, catalog_source_digest=digest)
        reader.login_once()
        return reader

    def pause(_):
        with store.connect() as connection:
            states.append(dict(connection.execute("SELECT * FROM observer_state").fetchone()))
        monkeypatch.setattr(observer, "STOP", True)

    monkeypatch.setattr(observer, "STOP", False)
    monkeypatch.setattr(observer, "DB_PATH", store.path)
    monkeypatch.delenv("POROTA_RUNTIME_SCHEMA_READY", raising=False)
    monkeypatch.setattr(observer, "runtime_store", lambda _: store)
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("POROTA_PPI_DIRECT_REST_RECOVERY", raising=False)
    monkeypatch.setattr(observer.signal, "signal", lambda *_: None)
    monkeypatch.setattr(observer, "_market_phase", lambda: "OPEN")
    monkeypatch.setattr(observer, "_announce_phase", lambda *_: None)
    monkeypatch.setattr(observer, "_daily_sync", lambda *_: maintenance.append("SYNC"))
    monkeypatch.setattr(observer, "_public_probe", lambda *_, **__: maintenance.append("PUBLIC"))
    monkeypatch.setattr(observer.operations, "service_tick", lambda *_: maintenance.append("SERVICE"))
    monkeypatch.setattr(caucion_live, "collection_window", lambda *_: False)
    monkeypatch.setattr(observer, "now_iso", lambda: SCAN_AT)
    monkeypatch.setattr(paper, "now_iso", lambda: SCAN_AT)
    monkeypatch.setattr(observer, "time", SimpleNamespace(
        time=lambda: 100_000, monotonic=lambda: 100_000, perf_counter=lambda: 1, sleep=pause))
    monkeypatch.setattr(gateway, "connect_paper_gateway_reader", attach)

    def run(broker):
        monkeypatch.setattr(runtime, "broker_from_environment", lambda *_, **__: broker)
        assert observer.run() == 0
        return calls, maintenance, states[0]

    return run


@pytest.mark.parametrize("dynamic", [False, True], ids=["base", "dynamic"])
def test_scanner_dispatches_only_unique_full_requests(store, scan_once, monkeypatch, dynamic):
    immediate = ("GGAL", "ACCIONES", "INMEDIATA")
    identity(store, FOCUS)
    identity(store, immediate, currency="USD")
    identity(store, ALTERNATE)
    identity(store, ALTERNATE, currency="USD", ledger_status="BLOCKED", can_simulate=0)
    expected = {(*request[:2], "BYMA", currency, request[2])
                for request, currency in ((FOCUS, "ARS"), (immediate, "USD"))}
    if dynamic:
        # Only the selection is mocked. The existing dynamic eligibility
        # guard must veto the ambiguous full key before the real adapter.
        proposed = [*sorted(expected), (*ALTERNATE[:2], "BYMA", "ARS", ALTERNATE[2])]
        selection = {
            "dynamic": True, "selected": proposed, "opened_priority": [],
            "rows": {key: {"state": "WARM"} for key in proposed},
            "entry_identities": [], "policy_state": {"fingerprint": "TEST"},
            "cadence_seconds": 60, "limit": 20,
        }
        monkeypatch.setattr(promotion, "capacity_controller_from_environment", lambda *_:
            SimpleNamespace(state=lambda *_: {"status": "APPROVED_DYNAMIC"},
                            selection=lambda *_, **__: selection))
    broker = PaperBroker(store)
    calls, _, state = scan_once(broker)
    assert len(calls) == 4
    assert {(method, key, priority) for _, method, key, priority in calls} == {
        (method, key, "WARM" if dynamic else "DISCOVERY")
        for method in ("current", "book") for key in expected
    }
    assert state["process_state"] == "RUNNING"
    assert state["real_orders_sent"] == 0
    with store.connect() as connection:
        row = connection.execute("""SELECT eligible_total,selected_count,successful_count,
            failed_count,detail FROM universe_cycle_metrics""").fetchone()
        assert tuple(row)[:4] == (2, 2, 2, 0)
        assert json.loads(row[4][row[4].index("{"):])["observation_counts"] == {
            "selected": 2, "instruments_dispatched": 2,
            "identity_rejections": 0, "temporal_rejections": 0,
        }
        assert {tuple(row) for row in connection.execute(
            "SELECT symbol,settlement,currency,market FROM market_snapshots")} == {
            ("GGAL", "A-24HS", "ARS", "BYMA"), ("GGAL", "INMEDIATA", "USD", "BYMA"),
        }


def test_base_scanner_separates_empty_pit_from_sqlite_error(store, scan_once):
    assert observer._cycle_plan(store)[-1]["catalog_status"] == "NO_ELIGIBLE_INSTRUMENTS"
    broker = PaperBroker(store)
    assert broker._open(quote(at=POSITION_OPENED_AT), D("0.8"), {})[0]
    with store.connect() as connection:
        connection.execute("ALTER TABLE candidate_identity_v2 DROP COLUMN can_simulate")
    _, symbols, feasibility, reason = capacity(store, FOCUS)
    assert symbols == (FOCUS,)  # Existing exposure stays observable.
    assert feasibility["active_plan"]["catalog_status"] == "CATALOG_READ_ERROR"
    assert feasibility["active_plan"]["catalog_error"] == "SQLITE_ERROR"
    assert reason == (False, "CATALOG_READ_ERROR")
    with store.connect() as connection:
        state, detail = connection.execute("""SELECT state,detail FROM api_health
            WHERE component='PAPER_SIGNAL_SAMPLING'""").fetchone()
    assert state == "ROJO" and detail.startswith("CATALOG_READ_ERROR")
    calls, _, scanner_state = scan_once(broker)
    expected = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    assert calls == [("scanner", method, expected, "OPENED_CRITICAL") for method in ("current", "book")]
    assert scanner_state["process_state"] == "DEGRADED"
    q = store.latest_quote(store.open_positions()[0])
    assert q.opening_block_reason == "CATALOG_READ_ERROR"
    assert q.monetary_identity() == ("ARS", "BYMA")


def test_invalid_caucion_keeps_exact_opened_book_for_independent_exit(store, scan_once, monkeypatch):
    identity(store, FOCUS)
    identity(store, ALTERNATE)
    seed = PaperBroker(store, initial_cash="10000")
    seed.place_caucion(caucion_offer(), "1000", "scanner-open-test", POSITION_OPENED_AT)
    assert seed._open(quote(at=POSITION_OPENED_AT), D("0.8"), {})[0]
    with store.connect() as connection:
        connection.execute("UPDATE paper_cauciones SET total_fees='-1'")
        before = tuple(connection.execute("SELECT * FROM paper_cauciones").fetchone())
    broker = PaperBroker(store, initial_cash="10000", daily_loss_pct="2.5")
    def forbidden(*_, **__):
        pytest.fail("Quarantined scanner reached an entry or equity callback")
    monkeypatch.setattr(broker, "mark_equity", forbidden)
    monkeypatch.setattr(broker, "on_quote", forbidden)
    calls, maintenance, state = scan_once(broker)
    assert maintenance == []
    assert state["process_state"] == "DEGRADED"
    assert state["detail"].startswith("CAUCION_LEDGER_INVALID")
    assert state["real_orders_sent"] == 0
    position = store.open_positions()[0]
    expected = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    assert calls[:2] == [("scanner", method, expected, "OPENED_CRITICAL") for method in ("current", "book")]
    assert [call[3] for call in calls] == ["OPENED_CRITICAL"] * 2 + ["DISCOVERY"] * 2
    q = store.latest_quote(position)
    assert q.opening_block_reason == "CAUCION_LEDGER_INVALID"
    assert not q.time_error(SCAN_AT)
    with pytest.raises(ValueError, match="CAUCION_LEDGER_INVALID"):
        broker._cash(SCAN_AT)

    supervisor = PositionExitSupervisor(broker, clock_fn=lambda: SCAN_AT)
    assert [verdict.state for verdict in supervisor.tick()] == ["CLOSED"]
    assert store.recent_closed()[0]["close_reason"] == "STOP_PAPER"
    with store.connect() as connection:
        assert tuple(connection.execute("SELECT * FROM paper_cauciones").fetchone()) == before
        assert [tuple(row) for row in connection.execute(
            "SELECT side,COUNT(*) FROM paper_fills GROUP BY side ORDER BY side"
        )] == [("BUY_SIMULATED", 1), ("SELL_SIMULATED", 1)]
        assert connection.execute("SELECT COUNT(*) FROM paper_equity").fetchone()[0] == 0


def test_caucion_failure_during_valuation_publishes_hold_without_restarting(store, scan_once, monkeypatch):
    identity(store, FOCUS)
    broker = PaperBroker(store, initial_cash="10000")
    broker.place_caucion(caucion_offer(), "1000", "scanner-valuation-test", POSITION_OPENED_AT)
    mark_equity = broker.mark_equity

    def invalidate_during_cycle(quotes):
        with store.connect() as connection:
            connection.execute("UPDATE paper_cauciones SET total_fees='-1'")
        return mark_equity(quotes)

    monkeypatch.setattr(broker, "mark_equity", invalidate_during_cycle)
    calls, _, state = scan_once(broker)
    assert [call[1] for call in calls] == ["current", "book"]
    assert state["process_state"] == "DEGRADED"
    assert state["detail"].startswith("CAUCION_LEDGER_INVALID")
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_equity").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_one_of_eight_exact_focus_ready_is_enough_for_its_own_spot(store):
    identity(store, FOCUS)
    assert observer._focus_coverage(store)["matched_count"] == 1
    assert observer._focus_coverage(store)["allow_new_openings"] is False
    focus, symbols, feasibility, decision = capacity(store, FOCUS)
    assert FOCUS in symbols
    assert focus["source"] == "POINT_IN_TIME_EXACT_SPOT_FALLBACK"
    assert focus["matched"] == [FOCUS]
    assert focus["allow_new_openings"] is True
    assert feasibility["feasible"] is True
    assert FOCUS in map(tuple, feasibility["active_plan"]["opening_identities"])
    assert decision == (True, "")
    with store.connect() as connection:
        assert connection.execute("SELECT state FROM api_health WHERE component='PAPER_FOCUS_COVERAGE'").fetchone()[0] == "AMARILLO"
        assert connection.execute("SELECT state FROM api_health WHERE component='PAPER_SPOT_PIT_READINESS'").fetchone()[0] == "VERDE"
        assert connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0


def test_nonfocus_pit_ready_spot_is_not_vetoed_by_zero_of_eight(store):
    identity(store, ALTERNATE)
    assert observer._focus_coverage(store)["matched_count"] == 0
    focus, symbols, feasibility, decision = capacity(store, ALTERNATE)
    assert ALTERNATE in symbols and ALTERNATE in focus["matched"]
    assert feasibility["active_plan"]["feasible"] is True
    assert decision == (True, "")


@pytest.mark.parametrize("change", [
    {"capability": "NEEDS_CONTRACT"},
    {"catalog_status": "STALE"},
    {"ledger_status": "BLOCKED"},
    {"can_simulate": 0},
])
def test_unverified_exact_spot_cannot_borrow_healthy_focus_or_signal_budget(store, change):
    identity(store, FOCUS)
    identity(store, ALTERNATE, **change)
    focus, symbols, feasibility, decision = capacity(store, ALTERNATE)
    assert FOCUS in focus["matched"]
    assert feasibility["feasible"] is True
    if change.get("can_simulate") == 0 or change.get("ledger_status") == "BLOCKED":
        # Una identidad sin autorización PIT ni siquiera entra en la rotación.
        assert ALTERNATE not in symbols
        assert decision == (False, "COLD_DISCOVERY_NO_SIGNAL_BUDGET")
    else:
        # Una candidata observada no presta por eso autoridad de apertura.
        assert ALTERNATE in symbols
        assert ALTERNATE in map(tuple, feasibility["active_plan"]["opening_identities"])
        assert decision == (False, "PAPER_SPOT_EXACT_PIT_NOT_READY")


def test_ambiguous_monetary_identity_remains_hold_even_when_both_are_ready(store):
    identity(store, FOCUS)
    identity(store, ALTERNATE, currency="ARS")
    identity(store, ALTERNATE, currency="USD")
    assert ALTERNATE not in observer._exact_pit_ready_spot_requests(store)
    focus, _, _, decision = capacity(store, ALTERNATE)
    assert ALTERNATE not in focus["matched"]
    assert decision[0] is False


@pytest.mark.parametrize("duplicate_source", ["catalog", "ledger"])
def test_raw_full_key_duplicate_ready_and_hold_cannot_be_hidden_by_filter(
        tmp_path, duplicate_source):
    # La fuente real tiene PK; un snapshot importado/malformado podría no
    # tenerla. La consulta tampoco debe confiar en esa característica física.
    path = tmp_path / "malformed-identity.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""CREATE TABLE financial_instrument_catalog(
          ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
          settlement TEXT,status TEXT,capability TEXT);
          CREATE TABLE candidate_identity_v2(
          ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
          settlement TEXT,status TEXT,can_simulate INTEGER);""")
        full = (*ALTERNATE[:2], "BYMA", "ARS", ALTERNATE[2])
        connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                           (*full, "AVAILABLE", "READY_PAPER_SPOT"))
        connection.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?)",
                           (*full, "AVAILABLE", 1))
        if duplicate_source == "catalog":
            connection.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)",
                               (*full, "BLOCKED", "NEEDS_CONTRACT"))
        else:
            connection.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?)",
                               (*full, "BLOCKED", 0))
    snapshot = SimpleNamespace(connect=lambda: sqlite3.connect(path))
    assert observer._exact_pit_ready_spot_requests(snapshot) == ()


def test_ready_spot_projection_never_upgrades_futures_options_or_funds(store):
    for family in ("FUTUROS", "OPCIONES", "FCI", "CAUCIONES", "BONOS"):
        identity(store, ("OTHER" + family, family, "A-24HS"))
    assert observer._exact_pit_ready_spot_requests(store) == ()
    assert observer.PAPER_SPOT_FAMILIES == {"ACCIONES", "CEDEARS", "ETFS"}


def test_cold_or_infeasible_plan_cannot_become_opening_authority(store):
    identity(store, FOCUS)
    focus, _, feasibility, decision = capacity(store, FOCUS)
    assert decision == (True, "")
    without_warm = dict(feasibility,
                        active_plan=dict(feasibility["active_plan"], opening_identities=[]))
    assert observer._scanner_opening_capacity(FOCUS, focus=focus, feasibility=without_warm) == (
        False, "COLD_DISCOVERY_NO_SIGNAL_BUDGET")
    infeasible = dict(feasibility, feasible=False)
    assert observer._scanner_opening_capacity(FOCUS, focus=focus, feasibility=infeasible) == (
        False, "SCANNER_INFEASIBLE")


def test_cedear_needs_same_exact_pit_but_us_session_remains_separate(store):
    identity(store, FOCUS)
    identity(store, CEDEAR, currency="ARS")
    focus, _, feasibility, decision = capacity(store, CEDEAR)
    assert CEDEAR in focus["matched"]
    assert decision == (True, "")  # Solo capacidad; NO es GO de sesión US.
    assert "cedear_opening_gate" in observer.run.__code__.co_names
    assert "paper_admission_guard" in observer.run.__code__.co_names


def test_missing_pit_ledger_is_fail_closed_even_with_catalog_focus_ready(tmp_path, monkeypatch):
    # Simular la ausencia del ledger SIN inventar permisiones a partir de 8/8.
    value = PaperStore(str(tmp_path / "missing-ledger.db"))
    observer._support_schema(value)
    with value.connect() as connection:
        connection.execute("""INSERT INTO financial_instrument_catalog VALUES(
            'GGAL','ACCIONES','BYMA','ARS','A-24HS','PPI_FIELD','Synthetic',
            '2026-10-29T10:00:00-03:00','A248','AVAILABLE','READY_PAPER_SPOT','{}')""")
    assert observer._focus_coverage(value)["matched_count"] == 1
    assert observer._exact_pit_ready_spot_requests(value) == ()
