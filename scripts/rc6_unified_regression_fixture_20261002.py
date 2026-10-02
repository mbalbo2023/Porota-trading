"""Cross-workstream regression: synthetic data, real local PAPER code, no network."""
import inspect
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import dj_caucion_live_ppi_rc6 as live
import rc6_contract_bridge as bridge
import di_caucion_cash_sweep_runtime_hf6 as sweep
import zz_wave8_dashboard_live_rc6 as dashboard
from be_paper_engine import PaperBroker, PaperStore

NOW = datetime(2026, 10, 2, 16, 45, tzinfo=ZoneInfo("America/Argentina/Buenos_Aires"))


def ready_store(tmp_path, monkeypatch):
    store = PaperStore(str(tmp_path / "synthetic-unified.db"))
    observer._support_schema(store)
    raw = {"ticker": "PESOS7", "description": "SYNTHETIC PESOS 7", "currency": "Pesos",
           "type": "CAUCIONES", "market": "BYMA", "nominalInPrice": 1,
           "_provider_instrument_type": "CAUCIONES", "_discovery_source": "PPI_PRIMARY"}
    primary = catalog.normalize_record(raw, "INMEDIATA", NOW.isoformat(), "fixture-only")
    with store.connect() as connection:
        catalog.persist(connection, primary)
    mass.collect(store, run_id="synthetic-unified")
    for claim in bridge.complements_from_store(store, now=NOW):
        with store.connect() as connection:
            row = dict(connection.execute(
                "SELECT * FROM financial_instrument_catalog WHERE ticker='PESOS7'"
            ).fetchone())
        row["raw"] = json.loads(row.pop("metadata_json"))
        completed = catalog.complete_with_complement(row, claim)
        with store.connect() as connection:
            catalog.persist(connection, completed)

    class ReadOnlyFixture:
        def book(self, ticker, kind, settlement):
            assert (ticker, kind, settlement) == ("PESOS7", "CAUCIONES", "INMEDIATA")
            return {"date": NOW.isoformat(), "bids": [{"price": 19.1, "quantity": 30000000}],
                    "offers": []}

    monkeypatch.setattr(live, "collection_window", lambda at: True)
    result = live.refresh(ReadOnlyFixture(), store, now=NOW)
    assert result["ready"] == 1 and result["real_routes"] == []
    offers, errors = sweep.offers_from_store(store, now=NOW)
    assert len(offers) == 1 and not errors
    return store


def append_change(store, *, material):
    with store.connect() as connection:
        old = dict(connection.execute("""SELECT * FROM contract_evidence_v2_snapshots
          WHERE family='CAUCIONES' AND ticker='PESOS7'
            AND source_class='DERIVED_OFFICIAL_RULE'
          ORDER BY snapshot_id DESC LIMIT 1""").fetchone())
    payload = json.loads(old["evidence_json"])
    if material:
        payload["minimum_principal"] = "999999"
    else:
        payload["capture_timestamp"] = (NOW + timedelta(seconds=1)).isoformat()
    return evidence.record_snapshot(
        store, family=old["family"], ticker=old["ticker"], market=old["market"],
        currency=old["currency"], settlement=old["settlement"],
        source_class=old["source_class"], source_ref=old["source_ref"],
        observed_at=NOW.isoformat(), evidence=payload)


@pytest.mark.parametrize("material", [False, True])
def test_live_caucion_rechecks_material_contract_even_if_catalog_ready(tmp_path, monkeypatch, material):
    store = ready_store(tmp_path, monkeypatch)
    change = append_change(store, material=material)
    assert change["changed"] is material
    with store.connect() as connection:
        assert connection.execute("SELECT capability FROM financial_instrument_catalog").fetchone()[0] == "READY_PAPER_CAUCION_PLACING"
        count = connection.execute("SELECT COUNT(*) FROM contract_evidence_v2_snapshots").fetchone()[0]
    offers, errors = sweep.offers_from_store(store, now=NOW)
    if material:
        assert offers == [] and errors == ["PESOS7:CHANGED_REVIEW_REQUIRED"]
    else:
        assert len(offers) == 1 and not errors
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM contract_evidence_v2_snapshots").fetchone()[0] == count


def test_live_caucion_requires_exact_source_provenance(tmp_path, monkeypatch):
    store = ready_store(tmp_path, monkeypatch)
    with store.connect() as connection:
        connection.execute("UPDATE paper_caucion_live_book SET source='UNKNOWN'")
    offers, errors = sweep.offers_from_store(store, now=NOW)
    assert offers == [] and errors == ["PESOS7:CAUCION_LIVE_SOURCE_UNVERIFIED"]


@pytest.mark.parametrize("delta", [-1, 91])
def test_live_caucion_rechecks_dynamic_freshness(tmp_path, monkeypatch, delta):
    store = ready_store(tmp_path, monkeypatch)
    offers, errors = sweep.offers_from_store(store, now=NOW + timedelta(seconds=delta))
    assert offers == [] and errors == ["PESOS7:CAUCION_LIVE_BOOK_STALE"]


def test_live_book_reaches_paper_tariff_and_real_simulated_ledger(tmp_path, monkeypatch):
    store = ready_store(tmp_path, monkeypatch)
    offers, errors = sweep.offers_from_store(store, now=NOW)
    assert not errors and sweep.fee_authorized_offers(offers) == offers
    broker = PaperBroker(store, initial_cash="1000000", daily_loss_pct="2.5",
                         clock_fn=lambda: NOW.isoformat())
    snapshot = sweep.ObligationSnapshot(NOW.isoformat(), "SYNTHETIC_COMPLETE_LEDGER", True, ())
    request_id = "paper-caucion-cash-sweep:2026-10-02:ARS:v1"
    result = sweep.run_paper_sweep(
        broker, offers, obligation_snapshot=snapshot, currency="ARS", as_of=NOW,
        sweep_start_at=NOW - timedelta(minutes=15), order_cutoff_at=NOW + timedelta(minutes=10),
        liquidity_deadline=offers[0].maturity_at, schedule_source="SYNTHETIC_PAPER_TEST_WINDOW",
        request_id=request_id, participation=Decimal("0.10"), max_quote_age_seconds=90)
    assert result["status"] == "PLACED_SIMULATED", result
    assert result["allocation"]["paper_id"]
    assert sweep.CASH_SWEEP_ORDER_ROUTING_ALLOWED is False
    recovered = sweep._existing_daily_allocation(PaperStore(store.path), request_id)
    assert recovered["idempotent"] is True
    assert recovered["allocation"]["paper_id"] == result["allocation"]["paper_id"]
    with store.connect() as connection:
        rows = connection.execute("SELECT principal,source FROM paper_cauciones").fetchall()
    assert len(rows) == 1 and rows[0][1] == "PRODUCTION_PAPER"
    assert Decimal(str(rows[0][0])) <= Decimal("3000000")
    assert broker._cash(as_of=NOW, currency="ARS") >= 0


def test_gdelt_retired_from_engine_and_dashboard_without_active_cards():
    page = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert "import rc6_gdelt_shadow" not in page
    assert "Noticias GDELT — SHADOW" not in page
    assert "Event Risk GDELT — SHADOW" not in page
    assert "GDELT SHADOW sin registro" not in page
    engine = inspect.getsource(PaperBroker.decide)
    assert "import rc6_gdelt_shadow" not in engine
    assert '"decision_effect": "EXCLUDED"' in engine
    assert '"state": "DEPRECATED_EXCLUDED"' in engine


@pytest.mark.parametrize("raw", ["MFE=0; MAE=0.0123", "MFE=0; MAE=0,0123", "MFE=0; MAE=01"])
def test_zero_mfe_does_not_rewrite_nonzero_mae_prefix(raw):
    assert dashboard._truthful_operator_terms(raw) == raw


def test_stop_explanation_does_not_duplicate_on_repeated_normalization():
    once = dashboard._truthful_operator_terms("causa STOP_PAPER")
    assert dashboard._truthful_operator_terms(once) == once
