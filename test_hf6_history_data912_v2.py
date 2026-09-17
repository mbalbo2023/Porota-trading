"""Tests agrupados HF6-v2: históricos, Data912 y readiness contractual."""
from datetime import datetime, timezone
import sqlite3

import ba_data912_history as data912
import cp_history_ingest_policy_hf6 as policy
import cq_family_contract_rules_hf6 as contract_rules
import cr_data912_reconcile_hf6 as reconcile
import cu_history_store_v2_hf6 as history_v2


class MemoryStore:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
    def connect(self):
        return _NonClosing(self.connection)


class _NonClosing:
    def __init__(self, connection): self.connection=connection
    def __enter__(self): return self.connection
    def __exit__(self, *_): self.connection.commit(); return False


def test_data912_execution_is_permanently_disabled():
    assert data912.DATA912_EXECUTION_ALLOWED is False


def test_partial_payload_salvages_valid_rows_without_inventing():
    payload = [
        {"date":"2026-09-01T00:00:00-03:00","openingPrice":100,"max":110,"min":95,"price":105,"volume":1000},
        {"date":"2026-08-31T00:00:00-03:00","openingPrice":0,"max":110,"min":95,"price":105,"volume":1000},
        {"date":"2026-08-28T00:00:00-03:00","openingPrice":100,"max":90,"min":95,"price":105,"volume":1000},
    ]
    result=policy.validate_provider_history(payload,as_of=datetime.fromisoformat("2026-09-02T20:00:00-03:00"))
    assert result.valid_count==1
    assert result.rejected_count==2
    assert result.storage_quality=="VALID_ROWS_WITH_REJECTIONS"
    assert {r.reason for r in result.rejected_rows}=={"OPEN_NONPOSITIVE","OHLC_INCONSISTENT"}


def test_context_thresholds_match_audited_policy():
    assert policy.context_state(0)=="NO_VALID_HISTORY"
    assert policy.context_state(29)=="INSUFFICIENT_CONTEXT"
    assert policy.context_state(30)=="MINIMUM_CONTEXT"
    assert policy.context_state(89)=="MINIMUM_CONTEXT"
    assert policy.context_state(90)=="PREFERRED_CONTEXT"
    assert policy.context_state(179)=="PREFERRED_CONTEXT"
    assert policy.context_state(180)=="STRONG_CONTEXT"


def test_history_collection_does_not_equal_ready_paper():
    capability=policy.history_collection_capability("BONOS",status="AVAILABLE",identity_complete=True)
    assert capability=="OUT_OF_SCOPE_READONLY_LEGACY"
    assert "READY_PAPER" not in capability


def test_data912_fallback_only_supported_families_and_under_90():
    assert policy.needs_data912_reconciliation("ACCIONES",89,"PARTIAL") is True
    assert policy.needs_data912_reconciliation("CEDEARS",90,"PARTIAL") is False
    assert policy.needs_data912_reconciliation("BONOS",180,"EMPTY_OR_INVALID") is False
    assert policy.needs_data912_reconciliation("OPCIONES",0,"EMPTY_OR_INVALID") is False
    assert policy.needs_data912_reconciliation("FUTUROS",0,"ERROR") is False


def test_reconciler_uses_porota_universe_with_full_identity():
    c=sqlite3.connect(":memory:")
    c.execute("""CREATE TABLE candidate_universe(
      ticker TEXT,instrument_type TEXT,market TEXT,settlement TEXT,status TEXT)""")
    c.execute("""CREATE TABLE production_history_attempts(
      symbol TEXT,instrument_type TEXT,settlement TEXT,state TEXT,valid_rows INTEGER)""")
    c.executemany("INSERT INTO candidate_universe VALUES(?,?,?,?,?)",[
        ("AAA","ACCIONES","BYMA","A-24HS","AVAILABLE"),
        ("BBB","CEDEARS","BYMA","A-24HS","AVAILABLE"),
        ("CCC","BONOS","BYMA","A-24HS","AVAILABLE"),
        ("DDD","OPCIONES","BYMA","INMEDIATA","AVAILABLE"),
        ("EEE","ACCIONES","BYMA","A-24HS","BLOCKED"),
        ("FFF","ACCIONES","UNKNOWN","A-24HS","AVAILABLE"),
    ])
    c.executemany("INSERT INTO production_history_attempts VALUES(?,?,?,?,?)",[
        ("AAA","ACCIONES","A-24HS","PARTIAL",20),
        ("BBB","CEDEARS","A-24HS","PARTIAL",120),
        ("CCC","BONOS","A-24HS","EMPTY_OR_INVALID",0),
        ("DDD","OPCIONES","INMEDIATA","EMPTY_OR_INVALID",0),
    ])
    targets=reconcile.load_targets(c)
    assert [(x.symbol,x.instrument_type,x.market,x.settlement) for x in targets]==[
        ("AAA","ACCIONES","BYMA","A-24HS"),
    ]


def test_reconcile_priority_prefers_empty_then_low_context():
    rows=[
        reconcile.HistoricalIdentity("A","ACCIONES","BYMA","A-24HS",40,"PARTIAL"),
        reconcile.HistoricalIdentity("B","CEDEARS","BYMA","A-24HS",0,"EMPTY_OR_INVALID"),
        reconcile.HistoricalIdentity("C","BONOS","BYMA","A-24HS",10,"ERROR"),
    ]
    assert [x.symbol for x in reconcile.prioritize(rows)]==["B","C","A"]


def _candle(symbol,family,market,settlement,source,close=100,adjusted=False,observed="2026-09-02T20:00:00+00:00"):
    return history_v2.Candle(
        symbol,family,market,settlement,"2026-09-01",95,105,90,close,1000,
        source,adjusted,observed,{}
    )


def test_history_v2_identity_prevents_cross_family_collision():
    store=MemoryStore()
    history_v2.append_candle(store,_candle("ABC","ACCIONES","BYMA","A-24HS","PPI_PRODUCTION_HISTORY",100))
    # Keep the second candle financially valid: the purpose of this test is
    # identity isolation, not rejection of malformed OHLC rows.
    history_v2.append_candle(store,_candle("ABC","BONOS","BYMA","A-24HS","PPI_PRODUCTION_HISTORY",92))
    with store.connect() as c:
        rows=c.execute("SELECT instrument_type,close FROM history_canonical_v2 ORDER BY instrument_type").fetchall()
    assert [(r[0],r[1]) for r in rows]==[("ACCIONES",100.0),("BONOS",92.0)]


def test_data912_cannot_replace_ppi_but_version_is_preserved():
    store=MemoryStore()
    ppi=history_v2.append_candle(store,_candle("ABC","ACCIONES","BYMA","A-24HS","PPI_PRODUCTION_HISTORY",100))
    d912=history_v2.append_candle(store,_candle("ABC","ACCIONES","BYMA","A-24HS","DATA912_POROTA_BATCH",99,observed="2026-09-02T21:00:00+00:00"))
    assert ppi["canonical_updated"] is True
    assert d912["canonical_updated"] is False
    with store.connect() as c:
        canonical=c.execute("SELECT close,source FROM history_canonical_v2").fetchone()
        versions=c.execute("SELECT COUNT(*) FROM history_versions_v2").fetchone()[0]
    assert canonical[0]==100.0
    assert canonical[1]=="PPI_PRODUCTION_HISTORY"
    assert versions==2


def test_adjusted_series_can_replace_unadjusted_same_identity():
    store=MemoryStore()
    history_v2.append_candle(store,_candle("ABC","ACCIONES","BYMA","A-24HS","PPI_PRODUCTION_HISTORY",100,False))
    result=history_v2.append_candle(store,_candle("ABC","ACCIONES","BYMA","A-24HS","IOL",98,True))
    assert result["canonical_updated"] is True
    with store.connect() as c:
        row=c.execute("SELECT close,adjusted,source FROM history_canonical_v2").fetchone()
    assert tuple(row)==(98.0,1,"IOL")


def _record(source,observed_at,evidence):
    return {"source_class":source,"observed_at":observed_at,"evidence":evidence}


def test_caucion_tna_is_dynamic_not_static_contract():
    assert "tna" not in contract_rules.FAMILY_CONTRACT_FIELDS["CAUCIONES"]
    assert "tna" in contract_rules.FAMILY_DYNAMIC_FIELDS["CAUCIONES"]
    assert contract_rules.DYNAMIC_TTL_HOURS["tna"]==1/12


def test_futures_margin_is_dynamic_and_contract_multiplier_is_static():
    assert "contract_multiplier" in contract_rules.FAMILY_CONTRACT_FIELDS["FUTUROS"]
    assert "margin_requirement" not in contract_rules.FAMILY_CONTRACT_FIELDS["FUTUROS"]
    assert "margin_requirement" in contract_rules.FAMILY_DYNAMIC_FIELDS["FUTUROS"]


def test_complete_static_contract_does_not_expire_only_by_age():
    old="2026-01-01T00:00:00+00:00"
    now=datetime(2026,9,2,22,0,tzinfo=timezone.utc)
    fields={name:"X" for name in contract_rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    dynamic={name:True for name in contract_rules.FAMILY_DYNAMIC_FIELDS["ACCIONES"]}
    result=contract_rules.evaluate_family("ACCIONES",[
        _record("PPI_STRUCTURED_API",old,fields),
        _record("PPI_STRUCTURED_API",now.isoformat(),dynamic),
    ],now=now)
    assert result["status"]=="READY_PAPER_CANDIDATE"


def test_stale_dynamic_evidence_blocks_candidate():
    now=datetime(2026,9,2,22,0,tzinfo=timezone.utc)
    fields={name:"X" for name in contract_rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    dynamic={name:True for name in contract_rules.FAMILY_DYNAMIC_FIELDS["ACCIONES"]}
    result=contract_rules.evaluate_family("ACCIONES",[
        _record("PPI_STRUCTURED_API",now.isoformat(),fields),
        _record("PPI_STRUCTURED_API","2026-09-02T20:00:00+00:00",dynamic),
    ],now=now)
    assert result["status"]=="STALE_DYNAMIC"
    assert result["stale_dynamic"]


def test_contract_missing_stays_fail_closed_before_dynamic_layer():
    now=datetime(2026,9,2,22,0,tzinfo=timezone.utc)
    result=contract_rules.evaluate_family("FUTUROS",[
        _record("PPI_STRUCTURED_API",now.isoformat(),{"market":"ROFEX","currency":"ARS"})
    ],now=now)
    assert result["status"]=="MISSING_CONTRACT"
    assert "contract_multiplier" in result["missing_contract"]


def test_unknown_source_class_is_ignored_fail_closed():
    now=datetime(2026,9,2,22,0,tzinfo=timezone.utc)
    fields={name:"X" for name in contract_rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    dynamic={name:True for name in contract_rules.FAMILY_DYNAMIC_FIELDS["ACCIONES"]}
    result=contract_rules.evaluate_family("ACCIONES",[
        _record("PPI_API",now.isoformat(),fields | dynamic)
    ],now=now)
    assert result["status"]=="MISSING_CONTRACT"