"""A248: los ocho focos históricos no tienen autoridad sobre un PAPER Spot.

Todo es sintético: no hay PPI, host, broker real, órdenes ni Action. La decisión
de scanner se prueba aparte del gate de ejecución PAPER/0 del broker.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

import bf_production_paper_observer as observer
from be_paper_engine import PaperStore, now_iso
from rc6_dynamic_universe import promotion


FOCUS = ("GGAL", "ACCIONES", "A-24HS")
ALTERNATE = ("ZTEST", "ACCIONES", "A-24HS")
CEDEAR = ("XTEST", "CEDEARS", "A-24HS")


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
