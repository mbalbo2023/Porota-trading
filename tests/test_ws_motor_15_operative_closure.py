import json
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import au_fee_schedule as fees
import bu_instrument_catalog as catalog
import cf_intraday_scalping as scalping
import rc6_iol_family_reference as iol
from be_paper_engine import PaperStore
from bt_caucion_paper import CaucionOffer
from df_caucion_end_of_day_sweep_hf6 import plan_sweep
from di_caucion_cash_sweep_runtime_hf6 import (
    fee_authorized_offers,
    obligation_snapshot_from_ledger,
    paper_schedule,
    run_worker,
)

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
NOW = datetime(2026, 9, 29, 16, 40, tzinfo=TZ)


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _paper_offer(now=NOW):
    return CaucionOffer(
        instrument_id="PESOS1", currency="ARS",
        annual_rate_fraction=Decimal("0.30"),
        start_date=now.date().isoformat(),
        maturity_at=(now + timedelta(hours=17)).isoformat(),
        quoted_at=now.isoformat(), available_principal=Decimal("100000"),
        minimum_principal=Decimal("1000"), principal_step=Decimal("0.01"),
        day_count_basis=365, fee_payment="MATURITY",
        metadata_source="CONTRACT_EVIDENCE_V2:TEST",
        paper_fill_policy="CONSERVATIVE_NOTIONAL_CAP",
        fee_authority=fees.CAUCION_PAPER_FEE_AUTHORITY,
    )


def test_paper_notional_cap_and_versioned_tariff_replace_neither_broker_term():
    offer = _paper_offer()
    assert fee_authorized_offers([offer]) == [offer]
    plan = plan_sweep(
        offers=[offer], currency="ARS", as_of=NOW,
        available_cash="80000", required_reserve="10000",
        sweep_start_at=NOW-timedelta(minutes=10),
        order_cutoff_at=NOW+timedelta(minutes=10),
        liquidity_deadline=NOW+timedelta(hours=18),
        schedule_source="POROTA_PAPER_CAUCION_EOD_POLICY:v1",
        participation=Decimal("0.10"))
    assert plan.state == "PAPER_CANDIDATE"
    assert plan.principal == Decimal("70000.00")
    assert plan.principal > offer.available_principal * Decimal("0.10")
    assert offer.paper_fill_policy == "CONSERVATIVE_NOTIONAL_CAP"
    assert "TARIFF" in offer.fee_authority


def test_paper_notional_cap_requires_v2_provenance():
    values = _paper_offer().__dict__ | {"metadata_source":"UNBOUND_TEST"}
    try:
        CaucionOffer(**values)
    except ValueError as exc:
        assert "provenance v2" in str(exc)
    else:
        raise AssertionError("unbound PAPER cap must fail closed")


def test_iol_envelopes_and_lkg_are_not_synthetic_zero():
    assert iol._response_rows({"structuredContent": {"rates": [{"rate": 20}]}}) == [{"rate": 20}]
    assert iol._response_rows({"data": {"items": [{"rate": 20}]}}) == [{"rate": 20}]
    prior = {"section_observed_at": {"fci": NOW.astimezone(timezone.utc).isoformat()}}
    assert iol._lkg_section_state(
        prior, "fci", True, NOW.astimezone(timezone.utc)
    ) == "LKG_FRESH_SOURCE_UNAVAILABLE"
    assert iol._lkg_section_state(
        prior, "fci", False, NOW.astimezone(timezone.utc)
    ) == "SOURCE_UNAVAILABLE_NO_LKG"


def _candidate_schema(connection):
    connection.executescript("""
      CREATE TABLE financial_instrument_catalog(
        ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
        settlement_source TEXT,description TEXT,last_seen_at TEXT,run_id TEXT,
        status TEXT,capability TEXT,metadata_json TEXT,
        PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
      CREATE TABLE candidate_universe(
        ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,settlement TEXT NOT NULL,
        market TEXT NOT NULL,can_simulate INTEGER NOT NULL,status TEXT NOT NULL,
        detail TEXT NOT NULL,last_checked_at TEXT NOT NULL,
        PRIMARY KEY(ticker,instrument_type,market));
      CREATE TABLE complementary_contract_retry(
        ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
        source TEXT,observed_at TEXT,state TEXT,reason TEXT,last_attempt_at TEXT,
        attempts INTEGER);
    """)


def _catalog_row(ticker, family, capability):
    return (ticker, family, "BYMA" if family != "FUTUROS" else "A3", "ARS",
            "INMEDIATA", "PPI_FIELD", "", NOW.isoformat(), "ws15", "AVAILABLE",
            capability, json.dumps({"_discovery_source":"PPI_PRIMARY"}))


def test_1181_options_134_futures_and_16_ambiguous_on_are_explicit():
    connection = sqlite3.connect(":memory:")
    _candidate_schema(connection)
    rows = []
    rows.extend(_catalog_row(f"OPT{i:04d}", "OPCIONES", "NEEDS_OPTION_CONTRACT")
                for i in range(1181))
    rows.extend(_catalog_row(f"FUT{i:03d}", "FUTUROS", "NEEDS_FUTURES_CONTRACT")
                for i in range(134))
    rows.extend(_catalog_row(f"ON{i:02d}", "OBLIGACIONES", "READY_PAPER_FIXED_INCOME")
                for i in range(16))
    connection.executemany(
        "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.executemany(
        "INSERT INTO complementary_contract_retry VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            (f"ON{i:02d}", "OBLIGACIONES", "BYMA", "ARS", "INMEDIATA",
             "PPI", NOW.isoformat(), "PENDING_RETRY", "IDENTITY_AMBIGUOUS",
             NOW.isoformat(), 1) for i in range(16)])
    catalog.sync_candidate_universe(connection, NOW.isoformat())
    classified = connection.execute("""SELECT instrument_type,status,COUNT(*)
      FROM candidate_identity_v2 GROUP BY instrument_type,status""").fetchall()
    assert set(classified) == {
        ("OPCIONES", "PAUSED_EXPLICIT", 1181),
        ("FUTUROS", "PAUSED_EXPLICIT", 134),
        ("OBLIGACIONES", "PAUSED_EXPLICIT", 16),
    }
    assert connection.execute("""SELECT COUNT(*) FROM candidate_identity_v2
      WHERE status NOT IN ('AVAILABLE','PAUSED_EXPLICIT')""").fetchone()[0] == 0
    assert connection.execute("""SELECT COUNT(*) FROM candidate_identity_v2
      WHERE detail LIKE '%PENDING%'""").fetchone()[0] == 0


def test_scanner_excludes_ambiguous_literal_request(tmp_path):
    store = PaperStore(str(tmp_path / "scanner.db"))
    catalog.init_schema(store)
    with store.connect() as connection:
        connection.execute("""CREATE TABLE candidate_identity_v2(
          ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,
          currency TEXT NOT NULL,settlement TEXT NOT NULL,can_simulate INTEGER NOT NULL,
          status TEXT NOT NULL,detail TEXT NOT NULL,checked_at TEXT NOT NULL,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
        for currency in ("ARS", "USD_CCL"):
            row = _catalog_row("DUAL", "CEDEARS", "READY_PAPER_SPOT")
            row = (*row[:3], currency, *row[4:])
            connection.execute(
                "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", row)
            connection.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                ("DUAL","CEDEARS","BYMA",currency,"INMEDIATA",1,"AVAILABLE",
                 "READY_PAPER_SPOT",NOW.isoformat()))
    selected, _, total = scalping.select_batch(store, limit=8)
    assert selected == [] and total == 0


def test_schedule_is_t45_t10_independent_and_sweep_is_eod_internal():
    schedule = paper_schedule(NOW, start_minutes=30, cutoff_minutes=5)
    assert schedule["sweep_start_at"].strftime("%H:%M") == "16:30"
    assert schedule["order_cutoff_at"].strftime("%H:%M") == "16:55"
    timer = Path("systemd/porota-preopen-rc6.timer").read_text(encoding="utf-8")
    assert "09:45:00 America/Argentina/Buenos_Aires" in timer
    assert "10:20:00 America/Argentina/Buenos_Aires" in timer


def test_preopen_uses_current_health_split_and_bounded_db_probe():
    source = Path("rc6_preopen.py").read_text(encoding="utf-8")
    assert "'porota-fast-functional-health-rc6.timer'" in source
    assert "'porota-full-db-integrity-rc6.timer'" in source
    required_block = source.split("REQUIRED_TIMERS = (", 1)[1].split(")", 1)[0]
    assert "'porota-functional-health-rc6.timer'" not in required_block
    assert "PRAGMA quick_check" not in source
    assert "bounded_readonly_probe" in source
    assert "--phase" in source


def test_obligation_snapshot_reserves_negative_family_ledger(tmp_path):
    store = PaperStore(str(tmp_path / "ledger.db"))
    with store.connect() as connection:
        connection.execute("""INSERT INTO paper_family_lifecycle VALUES(
          'fci-1','FCI','FUND','ARS','SUBSCRIBE_REQUESTED',?,'-25000',?)""",
          (NOW.isoformat(),json.dumps({"settlement_at":(NOW+timedelta(hours=1)).isoformat()})))
    snapshot = obligation_snapshot_from_ledger(
        store, observed_at=NOW, liquidity_deadline=NOW+timedelta(hours=18))
    assert snapshot.complete is True
    assert snapshot.obligations[0].amount == Decimal("25000")


class OneLoopStop:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, _seconds):
        self.stopped = True
        return True


def test_canonical_worker_runs_without_network_or_real_route(tmp_path):
    store = PaperStore(str(tmp_path / "runtime.db"))
    catalog.init_schema(store)
    stop = OneLoopStop()
    run_worker(store, stop, clock_fn=lambda: NOW.isoformat())
    with store.connect() as connection:
        state = dict(connection.execute(
            "SELECT * FROM paper_caucion_cash_sweep_state WHERE id=1").fetchone())
        attempts = connection.execute(
            "SELECT COUNT(*) FROM paper_caucion_cash_sweep_attempts").fetchone()[0]
    assert state["state"] == "STOPPED"
    assert state["real_orders_sent"] == 0
    assert json.loads(state["routes_json"]) == []
    assert attempts == 1
