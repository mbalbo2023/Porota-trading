"""R136 actual final gate over synthetic in-memory persisted option evidence.

Only the independent global GO and wall clock are controlled. Catalog lookup,
projection, option producer, snapshot persistence and contract bridge are real.
No provider, filesystem DB, host, SDK or broker route is accessed.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import sqlite3

import pytest

from be_paper_engine import Quote
import bu_instrument_catalog as catalog
import cp_contract_evidence_v2_hf6 as evidence
import cr_contract_evidence_v2_mass_hf6 as mass
import rc6_contract_bridge as bridge
import rc6_paper_admission_gate as gate
import rc6_ppi_option_contract_policy as policy


NOW = datetime(2026, 11, 11, 14, tzinfo=timezone.utc)
DESCRIPTION = "Opción compra YPFD AR$ 40500.00 Vto. 18/12/2026"


class Store:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    def connect(self):
        return self.connection


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def _raw(symbol, family, description=""):
    return {"ticker": symbol, "type": family, "market": "BYMA", "currency": "ARS",
            "settlement": "INMEDIATA", "description": description}


def _rows(store):
    return [dict(r) for r in store.connection.execute("SELECT * FROM financial_instrument_catalog")]


def _edit(store, symbol, *, raw=None, **fields):
    current = dict(store.connection.execute(
        "SELECT * FROM financial_instrument_catalog WHERE ticker=?", (symbol,)).fetchone())
    if raw is not None:
        current["metadata_json"] = json.dumps(raw)
    current.update(fields)
    with store.connect() as connection:
        connection.execute("""UPDATE financial_instrument_catalog SET metadata_json=?,description=?,
            status=?,capability=?,last_seen_at=? WHERE ticker=?""", (
            current["metadata_json"], current["description"], current["status"], current["capability"],
            current["last_seen_at"], symbol))


def _dump(store):
    return tuple(store.connection.iterdump())


@pytest.fixture
def admitted(monkeypatch):
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    monkeypatch.setattr(gate, "datetime", Clock)
    store = Store()
    catalog.init_schema(store)
    store.connection.execute("""CREATE TABLE candidate_universe(
        ticker TEXT,instrument_type TEXT,settlement TEXT,market TEXT,can_simulate INTEGER,
        status TEXT,detail TEXT,checked_at TEXT,PRIMARY KEY(ticker,instrument_type,market))""")
    for raw in (_raw("YPFD", "ACCIONES"), _raw("YPFC40500D", "OPCIONES", DESCRIPTION),
                _raw("OTHER", "ACCIONES"), _raw("BOND", "BONOS")):
        primary = catalog.normalize_record(raw, "INMEDIATA", NOW.isoformat(), "SYNTHETIC_R136")
        with store.connect() as connection:
            catalog.persist(connection, primary)
    planned, _, _ = mass.planned_records(_rows(store))
    evidence.record_snapshots(store, planned)
    claim = next(c for c in bridge.complements_from_store(store, now=NOW)
                 if c["instrument_type"] == "OPCIONES")
    primary = next(r for r in _rows(store) if r["ticker"] == "YPFC40500D")
    primary["raw"] = json.loads(primary.pop("metadata_json"))
    primary = catalog.complete_with_complement(primary, claim)
    assert primary["capability"] == "READY_PAPER_OPTION_LONG"
    with store.connect() as connection:
        catalog.persist(connection, primary)
        catalog.sync_candidate_universe(connection, NOW.isoformat())
    contract = catalog.contract_for(primary)
    quote = Quote("YPFC40500D", "OPCIONES", "INMEDIATA", Decimal("20"), Decimal("19"),
                  Decimal("20"), Decimal("10"), Decimal("10"), NOW.isoformat(),
                  contract=contract, currency="ARS", market="BYMA", metadata_source="PPI_CATALOG:"+NOW.isoformat())
    yield store, quote
    store.connection.close()


def test_valid_current_option_is_admitted_without_rederiving_ready(admitted):
    store, quote = admitted
    before = _dump(store)
    primary = catalog.lookup(store, quote.symbol, quote.asset_class, quote.settlement,
                             market="BYMA", currency="ARS")
    producer = {**primary, "metadata_json": primary["raw"]}
    assert policy.standard_long_option_evidence(producer, {"YPFD": "ACCIONES"}) is None
    assert gate.paper_admission_guard(quote, store) == (True, "")
    assert _dump(store) == before


@pytest.mark.parametrize("target", ["YPFD", "YPFC40500D"])
@pytest.mark.parametrize("field,value", [("symbol", "OTHER"), ("instrumentType", "FCI")])
def test_alias_conflict_closes_final_admission(admitted, target, field, value):
    store, quote = admitted
    raw = json.loads(next(r for r in _rows(store) if r["ticker"] == target)["metadata_json"])
    raw[field] = value
    _edit(store, target, raw=raw)
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


def test_previous_derived_survives_but_missing_underlying_raw_settlement_blocks_and_recovers(admitted):
    store, quote = admitted
    original = json.loads(next(r for r in _rows(store) if r["ticker"] == "YPFD")["metadata_json"])
    broken = deepcopy(original)
    broken.pop("settlement")
    _edit(store, "YPFD", raw=broken)
    planned, _, _ = mass.planned_records(_rows(store))
    assert not any(r["family"] == "OPCIONES" and r["source_class"] == "DERIVED_OFFICIAL_RULE"
                   for r in planned)
    evidence.record_snapshots(store, planned)
    persisted = next(c for c in bridge.complements_from_store(store, now=NOW)
                     if c["instrument_type"] == "OPCIONES")
    assert persisted["contract_bridge"]["status"] == "NORMALIZED"
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before
    _edit(store, "YPFD", raw=original)
    assert gate.paper_admission_guard(quote, store) == (True, "")


@pytest.mark.parametrize("hazard", ["missing_raw_description", "different_raw_description", "both_new_terms"])
def test_raw_catalog_and_persisted_contract_terms_must_agree(admitted, hazard):
    store, quote = admitted
    raw = json.loads(next(r for r in _rows(store) if r["ticker"] == quote.symbol)["metadata_json"])
    description = DESCRIPTION
    if hazard == "missing_raw_description":
        raw.pop("description")
    else:
        raw["description"] = DESCRIPTION.replace("40500.00", "40600.00")
        if hazard == "both_new_terms":
            description = raw["description"]
    _edit(store, quote.symbol, raw=raw, description=description)
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


@pytest.mark.parametrize("field,value", [
    ("strike", Decimal("40600")), ("underlying", "OTHER"), ("option_right", "PUT"),
    ("cash_multiplier", Decimal("10")), ("quantity_step", Decimal("2")),
    ("expires_at", "2026-12-19T15:30:00-03:00"),
])
def test_old_or_different_quote_contract_cannot_borrow_current_identity(admitted, field, value):
    store, quote = admitted
    changes = {field: value}
    if field == "quantity_step":
        changes["minimum_quantity"] = value  # A valid, but different, contract.
    altered = replace(quote, contract=replace(quote.contract, **changes))
    assert gate.paper_admission_guard(altered, store)[0] is False


@pytest.mark.parametrize("change", ["status", "review", "stale", "future", "naive", "missing"])
def test_underlying_current_proof_is_required_without_mutating_history(admitted, change):
    store, quote = admitted
    fields = {}
    if change == "status": fields["status"] = "STALE"
    elif change == "review": fields["capability"] = "CONTRACT_EVIDENCE_REVIEW_REQUIRED"
    elif change == "stale": fields["last_seen_at"] = (NOW-timedelta(days=15)).isoformat()
    elif change == "future": fields["last_seen_at"] = (NOW+timedelta(seconds=1)).isoformat()
    elif change == "naive": fields["last_seen_at"] = NOW.replace(tzinfo=None).isoformat()
    if change == "missing":
        store.connection.execute("DELETE FROM financial_instrument_catalog WHERE ticker='YPFD'")
        store.connection.commit()
    else:
        _edit(store, "YPFD", **fields)
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


@pytest.mark.parametrize("capability", ["CONTRACT_EVIDENCE_REVIEW_REQUIRED", "NEEDS_OPTION_CONTRACT"])
def test_nonready_option_is_not_reactivated_by_static_valid_terms(admitted, capability):
    store, quote = admitted
    _edit(store, quote.symbol, capability=capability)
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store) == (False, "PAPER_EXACT_CONTRACT_NOT_READY")
    assert _dump(store) == before


@pytest.mark.parametrize("hazard", ["changed_strike", "revoked", "adjusted_series_unverified"])
def test_live_material_snapshot_review_blocks_stale_ready_catalog(admitted, hazard):
    store, quote = admitted
    current = next(r for r in evidence.current_records(store, family="OPCIONES")
                   if r["source_class"] == "DERIVED_OFFICIAL_RULE")
    payload = deepcopy(current["evidence"])
    if hazard == "changed_strike":
        payload["strike"] = "40600"
    else:
        payload[hazard] = True
    evidence.record_snapshot(
        store, family="OPCIONES", ticker=quote.symbol, market="BYMA", currency="ARS",
        settlement="INMEDIATA", source_class=current["source_class"],
        source_ref=current["source_ref"], observed_at=NOW.isoformat(), evidence=payload)
    assert catalog.lookup(store, quote.symbol, "OPCIONES", "INMEDIATA", market="BYMA",
                          currency="ARS")["capability"] == "READY_PAPER_OPTION_LONG"
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


def test_description_format_equivalence_is_not_a_material_contract_change(admitted):
    store, quote = admitted
    raw = json.loads(next(r for r in _rows(store) if r["ticker"] == quote.symbol)["metadata_json"])
    raw["description"] = DESCRIPTION.replace("40500.00", "40500")
    _edit(store, quote.symbol, raw=raw)
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store) == (True, "")
    assert _dump(store) == before


def test_historical_provenance_only_review_is_not_a_new_option_veto(admitted):
    store, quote = admitted
    current = next(r for r in evidence.current_records(store, family="OPCIONES")
                   if r["source_class"] == "DERIVED_OFFICIAL_RULE")
    payload = deepcopy(current["evidence"])
    payload["readiness_guard"] = "UPDATED_SYNTHETIC_PROVENANCE_ONLY"
    assert evidence.material_evidence_hash(payload) == evidence.material_evidence_hash(current["evidence"])
    evidence.record_snapshot(
        store, family="OPCIONES", ticker=quote.symbol, market="BYMA", currency="ARS",
        settlement="INMEDIATA", source_class=current["source_class"],
        source_ref=current["source_ref"], observed_at=NOW.isoformat(), evidence=payload)
    # Emulate an old classifier's false material-review label. Neither snapshot
    # nor this historical label may be rewritten by the final admission gate.
    store.connection.execute("""INSERT INTO contract_evidence_v2_changes
        (family,ticker,market,currency,settlement,source_class,previous_hash,current_hash,
         detected_at,status,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
        "OPCIONES", quote.symbol, "BYMA", "ARS", "INMEDIATA", current["source_class"],
        current["evidence_hash"], evidence.evidence_hash(payload), NOW.isoformat(),
        "CHANGED_REVIEW_REQUIRED", "SYNTHETIC_OLD_PROVENANCE_CLASSIFIER"))
    store.connection.commit()
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store) == (True, "")
    assert _dump(store) == before


def test_underlying_material_review_blocks_option_without_rewriting_spot_history(admitted):
    store, quote = admitted
    current = next(r for r in evidence.current_records(store, family="ACCIONES", ticker="YPFD")
                   if r["source_class"] == "DERIVED_OFFICIAL_RULE")
    payload = deepcopy(current["evidence"])
    payload["cash_multiplier"] = "2"
    evidence.record_snapshot(
        store, family="ACCIONES", ticker="YPFD", market="BYMA", currency="ARS",
        settlement="INMEDIATA", source_class=current["source_class"], source_ref=current["source_ref"],
        observed_at=NOW.isoformat(), evidence=payload)
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


@pytest.mark.parametrize("stamp", [(NOW-timedelta(days=15)).isoformat(),
                                  (NOW+timedelta(seconds=1)).isoformat()])
def test_old_ready_option_does_not_bypass_current_identity_clock(admitted, stamp):
    store, quote = admitted
    _edit(store, quote.symbol, last_seen_at=stamp)
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


def test_unknown_current_evidence_table_fails_closed_without_ddl(admitted):
    store, quote = admitted
    store.connection.execute("DROP TABLE contract_evidence_v2_current")
    store.connection.commit()
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


def test_dangling_identity_snapshot_is_not_silently_omitted(admitted):
    store, quote = admitted
    store.connection.execute("""UPDATE contract_evidence_v2_current SET snapshot_id=-1
        WHERE family='OPCIONES' AND ticker=? AND source_class='PPI_STRUCTURED_API'""", (quote.symbol,))
    store.connection.commit()
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


@pytest.mark.parametrize("hazard", ["revoked", "adjusted_series_unverified", "missing_current"])
def test_current_underlying_evidence_is_checked_even_without_pending_change(admitted, hazard):
    store, quote = admitted
    if hazard == "missing_current":
        store.connection.execute("DELETE FROM contract_evidence_v2_current WHERE ticker='YPFD'")
    else:
        current = next(r for r in evidence.current_records(store, family="ACCIONES", ticker="YPFD")
                       if r["source_class"] == "DERIVED_OFFICIAL_RULE")
        payload = deepcopy(current["evidence"])
        payload[hazard] = True
        evidence.record_snapshot(
            store, family="ACCIONES", ticker="YPFD", market="BYMA", currency="ARS",
            settlement="INMEDIATA", source_class=current["source_class"], source_ref=current["source_ref"],
            observed_at=NOW.isoformat(), evidence=payload)
        store.connection.execute("""UPDATE contract_evidence_v2_changes SET status='REVIEWED'
            WHERE family='ACCIONES' AND ticker='YPFD'""")
    store.connection.commit()
    before = _dump(store)
    assert gate.paper_admission_guard(quote, store)[0] is False
    assert _dump(store) == before


def test_unrelated_spot_and_fixed_income_admission_do_not_require_option_proof(admitted):
    store, quote = admitted
    _edit(store, "YPFD", status="STALE")
    for symbol, family in (("OTHER", "ACCIONES"), ("BOND", "BONOS")):
        # BOND needs its own valid explicit contract, never option-derived data.
        if family == "BONOS":
            primary = next(r for r in _rows(store) if r["ticker"] == symbol)
            raw = json.loads(primary["metadata_json"])
            raw["financial_contract_v17"] = {
                "currency": "ARS", "market": "BYMA", "settlement": "INMEDIATA",
                "cash_multiplier": "0.01", "quantity_step": "1", "metadata_source": "SYNTHETIC_FI"}
            _edit(store, symbol, raw=raw, capability="READY_PAPER_SPOT")
            with store.connect() as connection:
                catalog.sync_candidate_universe(connection, NOW.isoformat())
        other = replace(quote, symbol=symbol, asset_class=family, contract=None)
        assert gate.paper_admission_guard(other, store) == (True, "")
