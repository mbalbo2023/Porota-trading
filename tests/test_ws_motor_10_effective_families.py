import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import cp_contract_evidence_v2_hf6 as evidence
import cq_contract_readiness_hf6 as legacy
import cq_family_contract_rules_hf6 as rules


NOW = datetime(2026, 9, 28, 18, 0, tzinfo=timezone.utc)


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def _record(family, payload, source="PPI_STRUCTURED_API", observed_at=None):
    return {"source_class": source, "observed_at": observed_at or NOW.isoformat(),
            "evidence": payload}


def _valid_fields(family, *, omit=()):
    payload = {field: "X" for field in rules.FAMILY_CONTRACT_FIELDS[family]
               if field not in set(omit)}
    for field in rules.POSITIVE_CONTRACT_FIELDS & set(payload):
        payload[field] = 1
    return payload


def _old_schema(connection):
    connection.executescript("""
    CREATE TABLE contract_evidence_v2_snapshots(
      snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, family TEXT NOT NULL,
      ticker TEXT NOT NULL, market TEXT NOT NULL, settlement TEXT NOT NULL,
      source_class TEXT NOT NULL, source_ref TEXT NOT NULL, observed_at TEXT NOT NULL,
      effective_at TEXT, evidence_hash TEXT NOT NULL, evidence_json TEXT NOT NULL,
      UNIQUE(family,ticker,market,settlement,source_class,evidence_hash));
    CREATE TABLE contract_evidence_v2_current(
      family TEXT NOT NULL,ticker TEXT NOT NULL,market TEXT NOT NULL,
      settlement TEXT NOT NULL,source_class TEXT NOT NULL,snapshot_id INTEGER NOT NULL,
      evidence_hash TEXT NOT NULL,observed_at TEXT NOT NULL,
      PRIMARY KEY(family,ticker,market,settlement,source_class));
    CREATE TABLE contract_evidence_v2_changes(
      change_id INTEGER PRIMARY KEY AUTOINCREMENT,family TEXT NOT NULL,
      ticker TEXT NOT NULL,market TEXT NOT NULL,settlement TEXT NOT NULL,
      source_class TEXT NOT NULL,previous_hash TEXT,current_hash TEXT NOT NULL,
      detected_at TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL);
    """)


def test_currency_key_migration_recovers_variants_from_append_only_history(tmp_path):
    store = Store(tmp_path / "currencyless.db")
    with store.connect() as connection:
        _old_schema(connection)
        hashes = []
        for snapshot_id, currency in enumerate(("ARS", "USD"), 1):
            payload = {"currency": currency, "quantity_min": 1}
            digest = evidence.evidence_hash(payload)
            hashes.append(digest)
            connection.execute("""INSERT INTO contract_evidence_v2_snapshots
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (snapshot_id, "BONOS", "GD30", "BYMA",
              "A-24HS", "IOL_STRUCTURED_API", "fixture", NOW.isoformat(), None,
              digest, json.dumps(payload)))
            connection.execute("""INSERT INTO contract_evidence_v2_changes
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (snapshot_id, "BONOS", "GD30", "BYMA",
              "A-24HS", "IOL_STRUCTURED_API", None, digest, NOW.isoformat(),
              "FIRST_SEEN", "fixture"))
        connection.execute("""INSERT INTO contract_evidence_v2_current
          VALUES(?,?,?,?,?,?,?,?)""", ("BONOS", "GD30", "BYMA", "A-24HS",
          "IOL_STRUCTURED_API", 2, hashes[1], NOW.isoformat()))

    evidence.init_schema(store)
    assert {row["currency"] for row in evidence.current_records(store)} == {"ARS", "USD"}
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM contract_evidence_v2_snapshots").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM contract_evidence_v2_changes").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM contract_evidence_v2_snapshots_currencyless_backup").fetchone()[0] == 2
    evidence.init_schema(store)  # idempotent after forward migration


def test_storage_currency_must_match_payload(tmp_path):
    with pytest.raises(ValueError, match="CONTRACT_V2_CURRENCY_MISMATCH"):
        evidence.record_snapshot(Store(tmp_path / "mismatch.db"), family="BONOS",
            ticker="GD30", market="BYMA", currency="USD", settlement="A-24HS",
            source_class="IOL_STRUCTURED_API", source_ref="fixture",
            evidence={"currency": "ARS", "quantity_min": 1})


def test_open_does_not_require_future_event_but_full_and_event_do():
    event_fields = rules.EVENT_CONDITIONAL_FIELDS["BONOS"]
    payload = _valid_fields("BONOS", omit=event_fields)
    payload.update({"operable": True, "market_session_state": "OPEN"})
    records = [_record("BONOS", payload)]
    opened = rules.evaluate_family("BONOS", records, profile="OPEN", now=NOW)
    assert opened["status"] == "READY_PAPER_CANDIDATE"
    assert set(opened["event_missing"]) == event_fields
    assert opened["event_classification"] == "EVENT_CONDITIONAL"
    assert rules.evaluate_family("BONOS", records, profile="FULL", now=NOW)["status"] == "MISSING_CONTRACT"
    assert rules.evaluate_family("BONOS", records, profile="EVENT", now=NOW)["status"] == "MISSING_CONTRACT"


def test_static_contract_does_not_expire_with_dynamic_quote_ttl():
    old = (NOW - timedelta(days=180)).isoformat()
    static = {field: "X" for field in rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    dynamic = {"operable": True, "market_session_state": "OPEN"}
    result = rules.evaluate_family("ACCIONES", [
        _record("ACCIONES", static, observed_at=old),
        _record("ACCIONES", dynamic, observed_at=NOW.isoformat()),
    ], now=NOW)
    assert result["status"] == "READY_PAPER_CANDIDATE"
    assert result["legacy_global_ttl_ignored"] is True


def test_real_account_controls_are_never_paper_requirements():
    for fields in rules.FAMILY_CONTRACT_FIELDS.values():
        assert not rules.REAL_ACCOUNT_ONLY_FIELDS & fields
    for fields in rules.FAMILY_DYNAMIC_FIELDS.values():
        assert not rules.REAL_ACCOUNT_ONLY_FIELDS & fields
    payload = _valid_fields("FUTUROS")
    payload.update({"margin_requirement": 100,
                    "provider_timestamp": NOW.isoformat(),
                    "freshness_basis": "PROVIDER_TIMESTAMP",
                    "account_balance": 0, "broker_account_permission": False,
                    "ppi_rofex_enabled": False})
    result = rules.evaluate_family("FUTUROS", [_record("FUTUROS", payload)], now=NOW)
    assert result["status"] == "READY_PAPER_CANDIDATE"
    assert set(result["ignored_real_account_fields"]) == {
        "account_balance", "broker_account_permission", "ppi_rofex_enabled"}


def test_legacy_facade_and_canonical_authority_agree():
    payload = {field: "X" for field in rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    payload.update({"operable": True, "market_session_state": "OPEN"})
    canonical = rules.evaluate_family("ACCIONES", [_record("ACCIONES", payload)], now=NOW)
    facade = legacy.evaluate("ACCIONES", payload, simulator_ready=True,
                             cost_ready=True, freshness_ok=True, now=NOW)
    assert facade["status"] == canonical["status"] == "READY_PAPER_CANDIDATE"
    assert facade["missing"] == canonical["missing_contract"] + canonical["missing_dynamic"]


def test_source_conflict_remains_fail_closed():
    base = {field: "X" for field in rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    base.update({"operable": True, "market_session_state": "OPEN"})
    other = dict(base, market="A3")
    result = rules.evaluate_family("ACCIONES", [
        _record("ACCIONES", base, "PPI_STRUCTURED_API"),
        _record("ACCIONES", other, "IOL_STRUCTURED_API"),
    ], now=NOW)
    assert result["status"] == "CONFLICT"
    assert "market" in result["conflicts"]


def test_different_capture_metadata_does_not_create_business_conflict():
    base = {field: "X" for field in rules.FAMILY_CONTRACT_FIELDS["ACCIONES"]}
    records = [
        _record("ACCIONES", {**base, "capture_timestamp": "2026-09-28T17:00:00Z",
                              "freshness_basis": "CAPTURE_TIMESTAMP_STATIC_ONLY"},
                "PPI_STRUCTURED_API"),
        _record("ACCIONES", {**base, "capture_timestamp": "2026-09-28T17:01:00Z",
                              "freshness_basis": "PROVIDER_TIMESTAMP"},
                "IOL_STRUCTURED_API"),
    ]
    result = rules.evaluate_family("ACCIONES", records, now=NOW)
    assert result["status"] == "READY_PAPER_CANDIDATE"
    assert result["conflicts"] == {}


def test_every_blocking_field_has_a_named_paper_consumer():
    blocking = set().union(*rules.FAMILY_CONTRACT_FIELDS.values(),
                           *rules.FAMILY_DYNAMIC_FIELDS.values())
    assert blocking <= set(rules.FIELD_CONSUMERS)
    assert "fee_schedule" not in blocking
    assert "trading_session" not in blocking
    assert "isin" not in blocking


def test_nonpositive_financial_terms_cannot_be_ready():
    payload = _valid_fields("BONOS", omit=rules.EVENT_CONDITIONAL_FIELDS["BONOS"])
    payload["cash_multiplier"] = 0
    result = rules.evaluate_family("BONOS", [_record("BONOS", payload)],
                                   profile="OPEN", now=NOW)
    assert result["status"] == "MISSING_CONTRACT"
    assert result["invalid_contract"] == ["cash_multiplier"]
