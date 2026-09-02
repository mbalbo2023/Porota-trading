"""Tests agrupados HF6-v2: históricos, Data912 y readiness contractual."""
from datetime import datetime, timezone
import sqlite3

import ba_data912_history as data912
import cp_history_ingest_policy_hf6 as policy
import cq_family_contract_rules_hf6 as contract_rules
import cr_data912_reconcile_hf6 as reconcile


def test_data912_execution_is_permanently_disabled():
    assert data912.DATA912_EXECUTION_ALLOWED is False


def test_data912_never_overwrites_authoritative_sources():
    assert data912._is_authoritative("PPI_PRODUCTION_HISTORY", 0) is True
    assert data912._is_authoritative("BYMA_EOD", 0) is True
    assert data912._is_authoritative("IOL", 0) is True
    assert data912._is_authoritative("DATA912", 0) is False
    assert data912._is_authoritative("YAHOO", 1) is True


def test_partial_payload_salvages_valid_rows_without_inventing():
    payload = [
        {"date":"2026-09-01T00:00:00-03:00","openingPrice":100,"max":110,"min":95,"price":105,"volume":1000},
        {"date":"2026-08-31T00:00:00-03:00","openingPrice":0,"max":110,"min":95,"price":105,"volume":1000},
        {"date":"2026-08-28T00:00:00-03:00","openingPrice":100,"max":90,"min":95,"price":105,"volume":1000},
    ]
    result = policy.validate_provider_history(
        payload, as_of=datetime.fromisoformat("2026-09-02T20:00:00-03:00")
    )
    assert result.valid_count == 1
    assert result.rejected_count == 2
    assert result.storage_quality == "VALID_ROWS_WITH_REJECTIONS"
    assert {r.reason for r in result.rejected_rows} == {"OPEN_NONPOSITIVE","OHLC_INCONSISTENT"}


def test_context_thresholds_match_audited_policy():
    assert policy.context_state(0) == "NO_VALID_HISTORY"
    assert policy.context_state(29) == "INSUFFICIENT_CONTEXT"
    assert policy.context_state(30) == "MINIMUM_CONTEXT"
    assert policy.context_state(89) == "MINIMUM_CONTEXT"
    assert policy.context_state(90) == "PREFERRED_CONTEXT"
    assert policy.context_state(179) == "PREFERRED_CONTEXT"
    assert policy.context_state(180) == "STRONG_CONTEXT"


def test_history_collection_does_not_equal_ready_paper():
    assert policy.history_collection_capability(
        "BONOS", status="AVAILABLE", identity_complete=True
    ) == "READONLY_HISTORY_ALLOWED"
    assert "READY_PAPER" not in policy.history_collection_capability(
        "BONOS", status="AVAILABLE", identity_complete=True
    )


def test_data912_fallback_only_supported_families_and_under_90():
    assert policy.needs_data912_reconciliation("ACCIONES", 89, "PARTIAL") is True
    assert policy.needs_data912_reconciliation("CEDEARS", 90, "PARTIAL") is False
    assert policy.needs_data912_reconciliation("BONOS", 180, "EMPTY_OR_INVALID") is True
    assert policy.needs_data912_reconciliation("OPCIONES", 0, "EMPTY_OR_INVALID") is False
    assert policy.needs_data912_reconciliation("FUTUROS", 0, "ERROR") is False


def test_reconciler_uses_porota_universe_not_provider_universe():
    c=sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE candidate_universe(
      ticker TEXT,instrument_type TEXT,settlement TEXT,status TEXT)""")
    c.execute("""CREATE TABLE production_history_attempts(
      symbol TEXT,instrument_type TEXT,settlement TEXT,state TEXT,valid_rows INTEGER)""")
    c.executemany("INSERT INTO candidate_universe VALUES(?,?,?,?)",[
        ("AAA","ACCIONES","A-24HS","AVAILABLE"),
        ("BBB","CEDEARS","A-24HS","AVAILABLE"),
        ("CCC","BONOS","A-24HS","AVAILABLE"),
        ("DDD","OPCIONES","INMEDIATA","AVAILABLE"),
        ("EEE","ACCIONES","A-24HS","BLOCKED"),
    ])
    c.executemany("INSERT INTO production_history_attempts VALUES(?,?,?,?,?)",[
        ("AAA","ACCIONES","A-24HS","PARTIAL",20),
        ("BBB","CEDEARS","A-24HS","PARTIAL",120),
        ("CCC","BONOS","A-24HS","EMPTY_OR_INVALID",0),
        ("DDD","OPCIONES","INMEDIATA","EMPTY_OR_INVALID",0),
    ])
    targets=reconcile.load_targets(c)
    assert [(x.symbol,x.instrument_type) for x in targets] == [
        ("AAA","ACCIONES"),("CCC","BONOS")
    ]


def test_reconcile_priority_prefers_empty_then_low_context():
    rows=[
        reconcile.HistoricalIdentity("A","ACCIONES","A-24HS",40,"PARTIAL"),
        reconcile.HistoricalIdentity("B","CEDEARS","A-24HS",0,"EMPTY_OR_INVALID"),
        reconcile.HistoricalIdentity("C","BONOS","A-24HS",10,"ERROR"),
    ]
    ordered=reconcile.prioritize(rows)
    assert [x.symbol for x in ordered] == ["B","C","A"]


def _record(source, observed_at, evidence):
    return {"source_class": source, "observed_at": observed_at, "evidence": evidence}


def test_caucion_tna_is_dynamic_not_static_contract():
    assert "tna" not in contract_rules.FAMILY_CONTRACT_FIELDS["CAUCIONES"]
    assert "tna" in contract_rules.FAMILY_DYNAMIC_FIELDS["CAUCIONES"]
    assert contract_rules.DYNAMIC_TTL_HOURS["tna"] == 1/12


def test_futures_margin_is_dynamic_and_contract_multiplier_is_static():
    assert "contract_multiplier" in contract_rules.FAMILY_CONTRACT_FIELDS["FUTUROS"]
    assert "margin_requirement" not in contract_rules.FAMILY_CONTRACT_FIELDS["FUTUROS"]
    assert "margin_requirement" in contract_rules.FAMILY_DYNAMIC_FIELDS["FUTUROS"]


def test_complete_static_contract_does_not_expire_only_by_age():
    old = "2026-01-01T00:00:00+00:00"
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    fields = {name: "X" for name in contract_rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    # Dynamic fields are current, while the static contract is intentionally old.
    dynamic = {name: True for name in contract_rules.FAMILY_DYNAMIC_FIELDS["ACCIONES"]}
    records = [
        _record("PPI_API", old, fields),
        _record("PPI_API", now.isoformat(), dynamic),
    ]
    result = contract_rules.evaluate_family("ACCIONES", records, now=now)
    assert result["status"] == "READY_PAPER_CANDIDATE"


def test_stale_dynamic_evidence_blocks_candidate():
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    old_dynamic = "2026-09-02T20:00:00+00:00"
    fields = {name: "X" for name in contract_rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    dynamic = {name: True for name in contract_rules.FAMILY_DYNAMIC_FIELDS["ACCIONES"]}
    result = contract_rules.evaluate_family("ACCIONES", [
        _record("PPI_API", now.isoformat(), fields),
        _record("PPI_API", old_dynamic, dynamic),
    ], now=now)
    assert result["status"] == "STALE_DYNAMIC"
    assert result["stale_dynamic"]


def test_contract_missing_stays_fail_closed_before_dynamic_layer():
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    result = contract_rules.evaluate_family("FUTUROS", [
        _record("PPI_API", now.isoformat(), {"market":"ROFEX","currency":"ARS"})
    ], now=now)
    assert result["status"] == "MISSING_CONTRACT"
    assert "contract_multiplier" in result["missing_contract"]
