from __future__ import annotations

import builtins
from datetime import datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

import ops_runtime_evidence_rc6 as evidence
from bu_instrument_catalog import PAPER_PPI_IDENTITY_LKG_SECONDS
from scripts.porota_consume_runtime_evidence_rc6 import ConsumerError, consume


NOW = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)
PPI_NATIVE_IDENTITY = {"ticker": "GGAL", "type": "ACCIONES", "market": "BYMA",
                       "currency": "Pesos", "settlement": "A-24HS",
                       "_discovery_source": "PPI_PRIMARY"}


class SystemctlResult:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def ppi_watch_contract(*expected):
    contract = copy.deepcopy(evidence._ppi_watch_contract())
    contract["expected_unit_names"] = list(expected)
    return contract


def install_systemctl_fixture(monkeypatch, tmp_path, listing, *, enabled="enabled",
                              active="active", enumeration_returncode=0,
                              fragment_exists=True):
    unit = tmp_path / "porota-ppi-watch.service"
    if fragment_exists:
        unit.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(tuple(command))
        verb = command[1]
        if verb == "list-unit-files":
            return SystemctlResult(listing, enumeration_returncode)
        if verb == "is-enabled":
            return SystemctlResult(enabled, 0 if enabled == "enabled" else 1)
        if verb == "is-active":
            return SystemctlResult(active, 0 if active == "active" else 3)
        if verb == "show":
            return SystemctlResult(str(unit))
        raise AssertionError(command)

    monkeypatch.setattr(evidence.subprocess, "run", fake_run)
    return unit, calls


def make_db(path: Path, *, last_seen="2026-09-27T16:59:00+00:00",
            mode="PRODUCTION_PAPER", real_orders_sent=0) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE observer_state(
          id INTEGER PRIMARY KEY, mode TEXT, process_state TEXT, session_state TEXT,
          ppi_auth TEXT, heartbeat_at TEXT, last_market_data_at TEXT,
          real_orders_sent INTEGER, http_allowed INTEGER, http_blocked INTEGER);
        CREATE TABLE financial_instrument_catalog(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, settlement_source TEXT, last_seen_at TEXT, run_id TEXT,
          status TEXT, capability TEXT, metadata_json TEXT);
        CREATE TABLE complementary_contract_retry(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, source TEXT, observed_at TEXT, state TEXT, reason TEXT,
          last_attempt_at TEXT, attempts INTEGER);
        CREATE TABLE candidate_universe(
          ticker TEXT, instrument_type TEXT, market TEXT, settlement TEXT,
          can_simulate INTEGER, status TEXT, detail TEXT, last_checked_at TEXT);
        CREATE TABLE source_sync(
          source TEXT, status TEXT, last_attempt_at TEXT, last_success_at TEXT,
          items INTEGER, detail TEXT);
        """
    )
    conn.execute(
        "INSERT INTO observer_state VALUES(1,?,?,?,?,?,?,?,?,?)",
        (mode, "WAITING_MARKET", "MARKET_CLOSED", "OK",
         "2026-09-27T16:59:30+00:00", "2026-09-25T19:59:00+00:00",
         real_orders_sent, 10, 0),
    )
    metadata = {
        **PPI_NATIVE_IDENTITY,
        "_freshness_by_source": {
            "IOL_COMPLEMENTARY": "2026-09-27T16:58:00+00:00",
            "BYMA_PUBLIC_COMPLEMENTARY": "2026-09-27T16:57:00+00:00",
        },
    }
    conn.execute(
        "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS", "PPI_FIELD",
         last_seen, "run-1", "AVAILABLE", "READY_PAPER_SPOT", json.dumps(metadata)),
    )
    conn.execute(
        "INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
        ("GGAL", "ACCIONES", "BYMA", "A-24HS", 1, "AVAILABLE",
         "READY_PAPER_SPOT", "2026-09-27T16:59:00+00:00"),
    )
    conn.execute(
        "INSERT INTO source_sync VALUES(?,?,?,?,?,?)",
        ("PPI_PRIMARY", "OK", "2026-09-27T16:59:00+00:00",
         "2026-09-27T16:59:00+00:00", 1, "OK"),
    )
    conn.commit()
    conn.close()


def json_file(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def inputs(tmp_path: Path, **db_options):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "observer.db"
    make_db(db, **db_options)
    mode = json_file(tmp_path / "operation_mode.json", {
        "mode": "PRODUCTION_PAPER", "execution": "SIMULATED",
        "apis": {"PPI_ORDERS": "BLOCKED"},
    })
    deploy = json_file(tmp_path / "CURRENT_STATE_V2.json", {
        "candidate_sha": "a" * 40, "deploy_sha": "b" * 40,
        "image_id": "sha256:" + "c" * 64,
        "recorded_at": "2026-09-26T20:00:00+00:00",
        "real_order_routes": "NOT_CALLED",
    })
    frozen = json_file(tmp_path / "frozen.json", {
        "candidate_sha": "a" * 40, "candidate_tree_sha": "d" * 40,
        "image_id": "sha256:" + "c" * 64, "image_tar_sha256": "e" * 64,
    })
    manifest = json_file(tmp_path / "manifest.json", {
        "schema_version": 2, "bundle_sha256": "f" * 64,
    })
    return db, mode, deploy, frozen, manifest


def build(tmp_path: Path, **db_options):
    db, mode, deploy, frozen, manifest = inputs(tmp_path, **db_options)
    return evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )


def exact_ledger(db: Path, *rows):
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE candidate_identity_v2(
          ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
          settlement TEXT,can_simulate INTEGER,status TEXT,detail TEXT,
          checked_at TEXT)""")
        conn.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)", rows)


def exact_ggal(*, currency="ARS", ready=1):
    return ("GGAL", "ACCIONES", "BYMA", currency, "A-24HS", ready,
            "AVAILABLE", "READY_PAPER_SPOT", "2026-09-27T16:59:00+00:00")


@pytest.mark.parametrize(("source", "raw_literal", "state", "expected_reason"), [
    ("PPI_FIELD", "A-24HS", "LITERAL_MATCH", None),
    ("REQUEST_CANDIDATE", None, "REQUEST_ONLY", "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"),
    ("PPI_FIELD", None, "NO_VERIFICADO", "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"),
    ("PPI_FIELD", "INMEDIATA", "LITERAL_MISMATCH", "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"),
    ("PPI_FIELD", "PRIVATE_EXTERNAL_VALUE", "NO_VERIFICADO", "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"),
])
def test_exact_settlement_witness_and_ledger_publish_only_causal_safe_evidence(
        tmp_path, source, raw_literal, state, expected_reason):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    raw = dict(PPI_NATIVE_IDENTITY, description="PRIVATE_FREE_TEXT")
    raw.pop("settlement")
    if raw_literal is not None:
        raw["settlement"] = raw_literal
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE financial_instrument_catalog SET settlement_source=?,metadata_json=?",
                     (source, json.dumps(raw)))
    ledger_reason = "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED;PRIVATE_OTHER_REASON"
    row = exact_ggal() if source == "PPI_FIELD" else (
        *exact_ggal(ready=0)[:7], ledger_reason, "2026-09-27T16:59:00+00:00")
    exact_ledger(db, row)

    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    evidence.validate_bundle(payload)
    instrument = payload["instruments"][0]
    witness = instrument["catalog"]
    assert witness["settlement_source"] == source
    assert witness.get("ppi_settlement_literal") == (
        raw_literal if state in {"LITERAL_MATCH", "LITERAL_MISMATCH"} else None)
    assert witness.get("settlement_state") == ("LITERAL_MISMATCH" if state == "LITERAL_MISMATCH" else None)
    assert witness.get("settlement_observed_at") == (
        "2026-09-27T16:59:00+00:00" if state == "NO_VERIFICADO" else None)
    assert witness["last_seen_at"] == "2026-09-27T16:59:00+00:00"
    ledger = instrument["readiness"]
    assert "candidate_checked_at" not in ledger and "candidate_status" not in ledger
    assert ledger["candidate_can_simulate"] == row[5]
    assert [code for code in ledger["reasons"] if code.startswith("CANDIDATE_REASON:")] == (
        ["CANDIDATE_REASON:" + expected_reason] if source == "REQUEST_CANDIDATE" else [])
    if expected_reason:
        assert ledger["status"] == "NO_READY"
        assert ledger["first_block_stage"] == "PPI_IDENTITY"
        assert "first_block_reason" not in ledger and ledger["reasons"][0] == expected_reason
        assert ("CANDIDATE_LEDGER_CONTRADICTION" in payload["gaps"]) == bool(row[5])
    else:
        assert ledger["status"] == "READY_PAPER"
        assert "first_block_stage" not in ledger and ledger["unverified_stage"] == "MARKET_OBSERVATION"
    assert "market_observation" not in instrument
    assert "PRIVATE_" not in json.dumps(payload)


def test_last_exact_market_snapshot_retains_clocks_without_claiming_provider_identity(tmp_path, monkeypatch):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    exact_ledger(db, exact_ggal())
    monkeypatch.setattr(evidence, "MARKET_SNAPSHOT_LIMIT", 2)
    base = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    at = "2026-09-27T16:59:50+00:00"
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE market_snapshots(id INTEGER PRIMARY KEY, symbol TEXT,
         asset_class TEXT,market TEXT,currency TEXT,settlement TEXT,observed_at TEXT,trade_at TEXT,
         book_at TEXT,last_kind TEXT,opening_block_reason TEXT)""")
        conn.executemany("INSERT INTO market_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            (1, *base, at, at, at, "TRADE", "PPI_DIRECT_REST_OBSERVE_ONLY"),
            (2, *base, at, at, "2026-09-27T16:59:59+00:00", "TRADE", "PRIVATE_IGNORED"),
            (3, *base[:3], "USD", base[4], at, at, at, "TRADE", ""),
        ])
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    instrument = payload["instruments"][0]
    quote = instrument["market_observation"]
    assert quote["state"] == "CLOCK_ORDER_INVALID"
    assert quote["received_at"] == at and quote["trade_at"] == at
    assert quote["book_at"] == "2026-09-27T16:59:59+00:00"
    assert payload["database"]["market_snapshot_window"] == {
        "state": "READ", "provider_origin": "NO_VERIFICADO", "rows": 2,
        "row_limit": 2, "truncated": True,
    }
    assert instrument["readiness"]["unverified_stage"] == "MARKET_OBSERVATION"
    assert "first_block_stage" not in instrument["readiness"]
    assert "PRIVATE_" not in json.dumps(payload)


def test_runtime_evidence_binds_settlement_pin_to_the_database_being_read(tmp_path, monkeypatch):
    from datetime import timedelta

    from rc6_ppi_native_request_receipt import FIELD, SOURCE, SettlementReceiptSigner
    from tests.test_rc6_ppi_native_request_receipt import AT, KEY, RELEASE, pin, sign_pair

    db, mode, deploy, frozen, manifest = inputs(tmp_path, last_seen=AT.isoformat())
    signer = SettlementReceiptSigner(RELEASE)
    proof = sign_pair(signer)
    pin(db, signer)
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "different.db"))
    monkeypatch.setenv("POROTA_BUILD_SHA", RELEASE[0])
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", RELEASE[1])
    ticker, family, market, currency, settlement = KEY
    raw = {"ticker": ticker, "type": family, "market": market, "currency": currency,
           "_discovery_source": "PPI_PRIMARY", FIELD: proof}
    with sqlite3.connect(db) as conn:
        conn.execute("""UPDATE financial_instrument_catalog
            SET ticker=?,instrument_type=?,market=?,currency=?,settlement=?,
                settlement_source='REQUEST_CANDIDATE',metadata_json=?""",
                     (*KEY, json.dumps(raw)))

    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=AT + timedelta(seconds=3),
    )
    instrument = payload["instruments"][0]
    assert instrument["catalog"]["settlement_source"] == SOURCE
    assert "PPI_NATIVE_SETTLEMENT_RECEIPT_INVALID" not in instrument["readiness"]["reasons"]


def test_missing_host_verifier_keeps_the_catalog_evidence_readable(tmp_path, monkeypatch):
    from rc6_ppi_native_request_receipt import FIELD

    import_module = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "rc6_ppi_native_request_receipt":
            raise ImportError(name)
        return import_module(name, *args, **kwargs)

    row = {"settlement_source": "REQUEST_CANDIDATE"}
    monkeypatch.setattr(builtins, "__import__", unavailable)
    _, reason = evidence._settlement_witness(row, {}, NOW, database=tmp_path / "observer.db")
    assert reason == "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
    _, reason = evidence._settlement_witness(row, {FIELD: {}}, NOW,
                                           database=tmp_path / "observer.db")
    assert reason == "PPI_NATIVE_SETTLEMENT_VERIFIER_UNAVAILABLE"


def test_bounded_wire_receipts_distinguish_returned_literal_from_requested_term(tmp_path, monkeypatch):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    exact_ledger(db, exact_ggal())
    at = "2026-09-27T16:59:55+00:00"
    received = "2026-09-27T16:59:53+00:00"
    market_at = "2026-09-27T16:59:51+00:00"
    shared = ("identity5=GGAL:ACCIONES:BYMA:ARS:A-24HS;http_status=200;paper_ready=false;"
              "wire_sha256=" + "a" * 64 + ";market_timestamp_utc=" + market_at)
    monkeypatch.setattr(evidence, "NATIVE_WIRE_LIMIT", 5)
    with sqlite3.connect(db) as conn:
        conn.execute("""CREATE TABLE paper_events(id INTEGER PRIMARY KEY,event_at TEXT,
          event_type TEXT,detail TEXT)""")
        conn.execute("CREATE INDEX event_clock ON paper_events(event_type,julianday(event_at))")
        conn.executemany("INSERT INTO paper_events VALUES(?, ?, ?, ?)", [
            (1, at, evidence.NATIVE_WIRE_EVENT, shared + ";endpoint=current;wire_received_at_utc=" + received),
            (2, at, evidence.NATIVE_WIRE_EVENT, shared + ";command_id=22;endpoint=current;"
             "response_settlement_status=EXPLICIT;response_settlement_literal=INMEDIATA;wire_received_at_utc=" + received),
            (3, at, evidence.NATIVE_WIRE_EVENT, shared + ";command_id=22;endpoint=book;"
             "response_settlement_status=ABSENT;response_settlement_literal=;wire_received_at_utc=" + received),
            (4, at, evidence.NATIVE_WIRE_EVENT, shared + ";endpoint=book;"
             "response_settlement_status=EXPLICIT;response_settlement_literal=PRIVATE_VALUE;"
             "wire_received_at_utc=2026-09-27T17:00:01+00:00"),
            (5, at, "ANOTHER_EVENT", shared + ";endpoint=current;wire_received_at_utc=" + received),
            (6, at, evidence.NATIVE_WIRE_EVENT, shared + ";endpoint=book;endpoint=current;"
             "wire_received_at_utc=" + received),
            (7, at, evidence.NATIVE_WIRE_EVENT, shared + ";endpoint=current;wire_received_at_utc="
             + received + ";" + "a" * 2048),
        ])
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    evidence.validate_bundle(payload)
    market = payload["instruments"][0]["market_observation"]
    assert set(market) == {"native_reads"}
    assert payload["instruments"][0]["readiness"]["unverified_stage"] == "MARKET_OBSERVATION"
    assert payload["database"]["market_snapshot_window"]["provider_origin"] == "NO_VERIFICADO"
    assert [read["endpoint"] for read in market["native_reads"]] == ["current", "book"]
    current, book = market["native_reads"]
    assert current["response_settlement_status"] == "EXPLICIT"
    assert current["response_settlement_literal"] == "INMEDIATA"
    assert current["settlement_matches_request"] is False
    assert book["response_settlement_status"] == "ABSENT"
    assert book["response_settlement_literal"] is None and book["settlement_matches_request"] is None
    assert all(read["received_at"] == received and read["market_at"] == market_at
               and read["command_id"] == 22 and read["paper_ready"] is False
               for read in market["native_reads"])
    assert "PRIVATE_VALUE" not in json.dumps(payload)


def test_missing_or_future_exact_ledger_never_claims_candidate_admission(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    exact_ledger(db, (*exact_ggal()[:-1], "2026-09-27T17:00:01+00:00"))
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    gate = payload["instruments"][0]["readiness"]
    assert gate["status"] == "NO_READY" and "candidate_can_simulate" not in gate
    assert gate["candidate_status"] == "AVAILABLE" and "candidate_checked_at" not in gate
    assert gate["unverified_stage"] == "CANDIDATE_LEDGER" and "first_block_stage" not in gate
    assert "EXACT_CANDIDATE_LEDGER_CLOCK_NO_VERIFICADO" in payload["gaps"]


def test_compact_readiness_keeps_distinct_candidate_clock_and_first_reason(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    at = "2026-09-27T16:59:10+00:00"
    exact_ledger(db, (*exact_ggal(ready=0)[:7], "RETRY_IDENTITY_AMBIGUOUS;PRIVATE_VALUE", at))
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    gate = payload["instruments"][0]["readiness"]
    assert gate["candidate_can_simulate"] == 0 and gate["candidate_checked_at"] == at
    assert "candidate_status" not in gate
    assert gate["first_block_stage"] == "CANDIDATE_LEDGER"
    assert gate["first_block_reason"] == "RETRY_IDENTITY_AMBIGUOUS"
    assert gate["reasons"] == ["CANDIDATE_GATE:NO_GO", "CANDIDATE_REASON:RETRY_IDENTITY_AMBIGUOUS"]


def test_evidence_and_candidate_writer_use_the_same_identity_lkg():
    assert evidence.PPI_IDENTITY_LKG_SECONDS == PAPER_PPI_IDENTITY_LKG_SECONDS


def test_running_candidate_refresh_and_allowlisted_pause_are_public(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE candidate_refresh_state(id,state,checked_at)")
        conn.execute("INSERT INTO candidate_refresh_state VALUES(1,'RUNNING',?)", (NOW.isoformat(),))
    exact_ledger(db, (*exact_ggal(ready=0)[:6], "PAUSED_EXPLICIT",
                      "PAUSED_EXPLICIT:CATALOG_RECONCILIATION;private /some/local/path", NOW.isoformat()))
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    evidence.validate_bundle(payload)
    assert payload["status"] == "INCOMPLETE"
    assert "CANDIDATE_REFRESH_IN_PROGRESS" in payload["gaps"]
    assert payload["database"]["candidate_refresh"] == {"state": "RUNNING", "checked_at": NOW.isoformat()}
    assert payload["database"]["candidate_readiness_source"] == "candidate_identity_v2"
    assert payload["families"][0]["ready_paper"] == 0
    assert payload["families"][0]["not_ready_reasons"] == {
        "CANDIDATE_GATE:NO_GO": 1,
        "CANDIDATE_REASON:CATALOG_RECONCILIATION": 1,
        "CANDIDATE_STATUS:PAUSED_EXPLICIT": 1,
    }


def test_deployment_sha_uses_validated_download_evidence_and_keeps_watch_gap(tmp_path, monkeypatch):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    marker = json.loads(deploy.read_text())
    marker.pop("deploy_sha")
    marker.update(deployment_sha="b" * 40, download_receipt_sha256="e" * 64,
                  validation_status="VALIDATED_RUNTIME")
    install_systemctl_fixture(monkeypatch, tmp_path, "", enumeration_returncode=1)
    for legacy_sha in (None, "a" * 40):
        if legacy_sha:
            marker["deploy_sha"] = legacy_sha
        json_file(deploy, marker)
        payload = evidence.build_bundle(
            db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
            frozen_path=frozen, manifest_path=manifest, previous_path=None,
            freshness_seconds=3600, current=NOW,
        )
        evidence.validate_bundle(payload)
        assert payload["status"] == "INCOMPLETE"
        assert "PPI_WATCH_NO_VERIFICADO" in payload["gaps"]
        assert payload["provenance"]["deploy_sha"] == ("NO_VERIFICADO" if legacy_sha else "b" * 40)
        assert payload["provenance"]["download_receipt_sha256"] == "e" * 64
        assert ("PROVENANCE_DEPLOYMENT_SHA_UNVERIFIED" in payload["gaps"]) is bool(legacy_sha)


def test_documentary_freshness_is_not_a_candidate_ledger_contradiction(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(
        tmp_path, last_seen="2026-09-20T16:59:00+00:00")
    exact_ledger(db, exact_ggal())
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    evidence.validate_bundle(payload)
    assert payload["status"] == "COMPLETE"
    assert payload["database"]["exact_candidate_rows"] == 1
    row = payload["instruments"][0]
    assert row["readiness"]["status"] == "NO_READY"
    assert "PPI_FRESHNESS_STALE" in row["readiness"]["reasons"]
    assert "CANDIDATE_LEDGER_CONTRADICTION" not in payload["gaps"]


def test_exact_ledger_keeps_unique_ready_currency_without_promoting_sibling(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                     ("GGAL", "ACCIONES", "BYMA", "USD_CCL", "A-24HS", "PPI_FIELD",
                      "2026-09-27T16:59:00+00:00", "run-2", "AVAILABLE",
                      "READY_PAPER_SPOT", json.dumps({"_discovery_source": "PPI_PRIMARY"})))
    exact_ledger(db, exact_ggal(), exact_ggal(currency="USD_CCL", ready=0))
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    evidence.validate_bundle(payload)
    by_currency = {row["identity"]["currency"]: row for row in payload["instruments"]}
    assert by_currency["ARS"]["readiness"]["status"] == "READY_PAPER"
    assert by_currency["USD_CCL"]["readiness"]["status"] == "NO_READY"
    assert "CANDIDATE_GATE:NO_GO" in by_currency["USD_CCL"]["readiness"]["reasons"]
    assert by_currency["ARS"]["identity_flags"]["duplicate"] is True
    assert by_currency["ARS"]["identity_flags"]["ambiguous"] is False
    assert "CANDIDATE_LEDGER_CONTRADICTION" not in payload["gaps"]


def test_exact_ledger_still_rejects_genuinely_expired_identity(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(
        tmp_path, last_seen="2026-09-10T16:59:00+00:00")
    exact_ledger(db, exact_ggal())
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert payload["status"] == "INCOMPLETE"
    assert "CANDIDATE_LEDGER_CONTRADICTION" in payload["gaps"]


def test_exact_ledger_missing_row_is_never_ready(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    exact_ledger(db)
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert "CANDIDATE_LEDGER_CARDINALITY" in payload["gaps"]
    assert payload["instruments"][0]["readiness"]["status"] == "NO_READY"


def test_complete_bundle_has_readonly_proof_and_instrument_contract(tmp_path):
    payload = build(tmp_path)
    evidence.validate_bundle(payload)
    assert payload["status"] == "COMPLETE"
    assert payload["database"]["open_mode"] == "mode=ro"
    assert payload["database"]["query_only"] is True
    assert payload["database"]["catalog_rows"] == 1
    assert payload["database"]["candidate_rows"] == 1
    assert payload["database"]["ledger_rows"] == 1
    assert payload["safety"] == {
        "mode": "PRODUCTION_PAPER",
        "operation_mode": "PRODUCTION_PAPER",
        "execution": "SIMULATED",
        "real_orders_sent": 0,
        "real_order_route_capability": "BLOCKED",
        "real_order_routes": "NOT_CALLED",
        "paper_shadow_only": True,
    }
    row = payload["instruments"][0]
    assert row["identity"]["primary_source"] == "PPI"
    assert row["readiness"]["status"] == "READY_PAPER"
    assert set(row["sources"]) == set(evidence.SOURCES)


def test_complementary_shadow_does_not_contradict_unique_primary_candidate(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("GGAL", "ACCIONES", "BYMA", "UNKNOWN", "A-24HS", "IOL_COMPLEMENTARY",
         "2026-09-27T16:59:00+00:00", "iol", "OBSERVED_SHADOW",
         "MISSING_CURRENCY_OR_MARKET",
         json.dumps({"_discovery_source": "IOL_COMPLEMENTARY"})),
    )
    conn.commit()
    conn.close()
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    primary = next(row for row in payload["instruments"]
                   if row["identity"]["currency"] == "ARS")
    assert primary["readiness"]["status"] == "READY_PAPER"
    assert primary["identity_flags"]["ambiguous"] is False
    assert "CANDIDATE_LEDGER_CONTRADICTION" not in payload["gaps"]


def test_mixed_generation_provenance_is_explicitly_incomplete(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    frozen.write_text(json.dumps({
        "candidate_sha": "9" * 40,
        "candidate_tree_sha": "d" * 40,
        "image_id": "sha256:" + "c" * 64,
        "image_tar_sha256": "e" * 64,
    }), encoding="utf-8")
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert payload["status"] == "INCOMPLETE"
    assert payload["gaps"] == ["PROVENANCE_CANDIDATE_MISMATCH"]


def test_unknown_or_stale_freshness_never_produces_ready(tmp_path):
    payload = build(tmp_path, last_seen=None)
    row = payload["instruments"][0]
    assert row["readiness"]["status"] == "NO_READY"
    assert "PPI_FRESHNESS_UNKNOWN" in row["readiness"]["reasons"]
    assert "CANDIDATE_LEDGER_CONTRADICTION" in payload["gaps"]


def test_candidate_ghost_evidence_is_sanitized_and_counts_actual_ledger(tmp_path):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
        ("DIA", "ETF", "BYMA", "A-24HS", 0, "STALE",
         "CATALOG_MISSING", "2026-09-27T16:59:00+00:00"),
    )
    conn.commit()
    conn.close()
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert payload["database"]["catalog_rows"] == 1
    assert payload["database"]["candidate_rows"] == 2
    assert payload["database"]["ledger_rows"] == 2
    assert "CANDIDATE_GHOSTS:1" in payload["gaps"]
    assert payload["identity_anomalies"]["ghosts"] == [{
        "ticker": "DIA", "family": "ETF", "market": "BYMA",
        "settlement": "A-24HS", "can_simulate": 0, "status": "STALE",
        "detail": "CATALOG_MISSING",
        "last_checked_at": "2026-09-27T16:59:00+00:00",
    }]


@pytest.mark.parametrize(
    "case,expected_reason",
    [
        ("freshness", "PPI_FRESHNESS_STALE"),
        ("primary", "PPI_PRIMARY_IDENTITY_NOT_VERIFIED"),
        ("ambiguity", "IDENTITY_AMBIGUOUS"),
        ("status", "CATALOG_STATUS:OBSERVED_SHADOW"),
        ("capability", "CAPABILITY:NEEDS_NOMINAL_UNITS"),
    ],
)
def test_candidate_ledger_contradiction_is_exact_for_each_fail_closed_gate(
        tmp_path, case, expected_reason):
    db, mode, deploy, frozen, manifest = inputs(tmp_path)
    conn = sqlite3.connect(db)
    if case == "freshness":
        conn.execute("UPDATE financial_instrument_catalog SET last_seen_at=?",
                     ("2026-09-10T16:59:00+00:00",))
    elif case == "primary":
        conn.execute("""UPDATE financial_instrument_catalog
          SET settlement_source='IOL_COMPLEMENTARY',metadata_json='{}'""")
    elif case == "ambiguity":
        conn.execute(
            "INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("GGAL", "ACCIONES", "BYMA", "USD_CCL", "A-24HS", "PPI_FIELD",
             "2026-09-27T16:59:00+00:00", "run-2", "AVAILABLE",
             "READY_PAPER_SPOT", json.dumps({"_discovery_source": "PPI_PRIMARY"})),
        )
    elif case == "status":
        conn.execute("UPDATE financial_instrument_catalog SET status='OBSERVED_SHADOW'")
    elif case == "capability":
        conn.execute("UPDATE financial_instrument_catalog SET capability='NEEDS_NOMINAL_UNITS'")
    conn.commit()
    conn.close()
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=mode, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert "CANDIDATE_LEDGER_CONTRADICTION" in payload["gaps"]
    assert expected_reason in payload["instruments"][0]["readiness"]["reasons"]
    assert "CANDIDATE_LEDGER_CONTRADICTS_FAIL_CLOSED" in (
        payload["instruments"][0]["readiness"]["reasons"]
    )


@pytest.mark.parametrize(
    "mode,real_orders,reason",
    [("REAL", 0, "NON_PAPER_MODE"), ("PRODUCTION_PAPER", 1, "REAL_ORDERS_SENT_NONZERO")],
)
def test_non_paper_or_real_orders_blocks_bundle(tmp_path, mode, real_orders, reason):
    db, operation, deploy, frozen, manifest = inputs(
        tmp_path, mode=mode, real_orders_sent=real_orders
    )
    if mode != "PRODUCTION_PAPER":
        json_file(operation, {"mode": mode, "execution": "REAL", "apis": {"PPI_ORDERS": "ENABLED"}})
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=operation, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert payload["status"] == "BLOCKED"
    assert reason in payload["blockers"]


def test_missing_required_table_is_incomplete(tmp_path):
    db, operation, deploy, frozen, manifest = inputs(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("DROP TABLE complementary_contract_retry")
    conn.commit()
    conn.close()
    payload = evidence.build_bundle(
        db_path=db, operation_mode_path=operation, deploy_state_path=deploy,
        frozen_path=frozen, manifest_path=manifest, previous_path=None,
        freshness_seconds=3600, include_ppi_watch=False, current=NOW,
    )
    assert payload["status"] == "INCOMPLETE"
    assert "MISSING_TABLE:complementary_contract_retry" in payload["gaps"]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO observer_state(id,mode) VALUES(2,'X')",
        "UPDATE observer_state SET mode='X' WHERE id=1",
        "DELETE FROM observer_state",
        "CREATE TABLE forbidden(x INTEGER)",
        "VACUUM",
        "ATTACH DATABASE ':memory:' AS other",
    ],
)
def test_query_only_connection_rejects_every_write_class(tmp_path, statement):
    db, *_ = inputs(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    conn = evidence.connect_readonly(db)
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.Error):
        conn.execute(statement)
    conn.close()
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_atomic_output_is_confined_to_staging(tmp_path):
    payload = build(tmp_path)
    stage = tmp_path / "stage"
    evidence.atomic_json(stage / "latest.json", stage, payload)
    assert json.loads((stage / "latest.json").read_text())["schema"] == evidence.SCHEMA
    with pytest.raises(evidence.EvidenceError, match="OUTPUT_OUTSIDE_STAGING_ROOT"):
        evidence.atomic_json(tmp_path / "outside.json", stage, payload)


def test_sanitizer_rejects_secret_keys_paths_and_payloads():
    with pytest.raises(evidence.EvidenceError, match="SANITIZER_FORBIDDEN_KEY"):
        evidence.assert_sanitized({"access_token": "redacted"})
    with pytest.raises(evidence.EvidenceError, match="SANITIZER_FORBIDDEN_VALUE"):
        evidence.assert_sanitized({"detail": "/opt/private/secret.json"})


def test_ppi_watch_probe_uses_read_only_systemd_verbs(monkeypatch, tmp_path):
    unit, calls = install_systemctl_fixture(
        monkeypatch, tmp_path, "porota-ppi-watch.service enabled\n"
    )
    contract = ppi_watch_contract("porota-ppi-watch.service")
    result = evidence.ppi_watch_status(NOW, contract=contract)
    assert result["state"] == "VERIFIED_READ_ONLY"
    assert result["reason"] == "EXACT_EXPECTED_UNIT_VERIFIED"
    assert result["enumeration_succeeded"] is True
    assert result["mutation_attempted"] is False
    assert result["units"][0]["unit_sha256"] == hashlib.sha256(unit.read_bytes()).hexdigest()
    assert {call[1] for call in calls} == {"list-unit-files", "is-enabled", "is-active", "show"}


def test_ppi_watch_zero_units_is_not_present_and_never_green(monkeypatch, tmp_path):
    _unit, calls = install_systemctl_fixture(monkeypatch, tmp_path, "")
    result = evidence.ppi_watch_status(NOW, contract=ppi_watch_contract())
    assert result["state"] == "NOT_PRESENT"
    assert result["reason"] == "NO_MATCHING_UNIT"
    assert result["units"] == []
    assert result["mutation_attempted"] is False
    assert {call[1] for call in calls} == {"list-unit-files"}


def test_ppi_watch_absence_is_green_only_when_versioned_contract_requires_it(monkeypatch, tmp_path):
    install_systemctl_fixture(monkeypatch, tmp_path, "")
    contract = ppi_watch_contract()
    contract["systemd_presence_expectation"] = "ABSENT"
    result = evidence.ppi_watch_status(NOW, contract=contract)
    assert result["state"] == "VERIFIED_ABSENT"
    assert result["reason"] == "ABSENCE_REQUIRED_BY_CONTRACT"


def test_ppi_watch_multiple_candidates_are_ambiguous_without_metadata_queries(monkeypatch, tmp_path):
    _unit, calls = install_systemctl_fixture(
        monkeypatch, tmp_path,
        "alpha-ppi-watch.service enabled\nbeta-ppi-watch.timer enabled\n",
    )
    result = evidence.ppi_watch_status(
        NOW, contract=ppi_watch_contract("alpha-ppi-watch.service")
    )
    assert result["state"] == "AMBIGUOUS"
    assert result["reason"] == "MULTIPLE_MATCHING_UNITS"
    assert len(result["units"]) == 2
    assert {call[1] for call in calls} == {"list-unit-files"}


def test_ppi_watch_enumeration_failure_is_no_verificado(monkeypatch, tmp_path):
    install_systemctl_fixture(
        monkeypatch, tmp_path, "permission denied", enumeration_returncode=1
    )
    result = evidence.ppi_watch_status(NOW, contract=ppi_watch_contract())
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "ENUMERATION_FAILED"
    assert result["enumeration_succeeded"] is False
    assert result["mutation_attempted"] is False


def test_ppi_watch_unregistered_single_candidate_is_no_verificado(monkeypatch, tmp_path):
    _unit, calls = install_systemctl_fixture(
        monkeypatch, tmp_path, "porota-ppi-watch.service enabled\n"
    )
    result = evidence.ppi_watch_status(NOW, contract=ppi_watch_contract())
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "UNIT_NOT_AUTHORIZED_BY_CONTRACT"
    assert result["units"] == [{"unit": "porota-ppi-watch.service"}]
    assert {call[1] for call in calls} == {"list-unit-files"}


@pytest.mark.parametrize(
    "enabled,active,fragment_exists",
    [("disabled", "active", True), ("enabled", "inactive", True),
     ("enabled", "active", False)],
)
def test_ppi_watch_expected_unit_requires_enabled_active_and_hash(
        monkeypatch, tmp_path, enabled, active, fragment_exists):
    install_systemctl_fixture(
        monkeypatch, tmp_path, "porota-ppi-watch.service enabled\n",
        enabled=enabled, active=active, fragment_exists=fragment_exists,
    )
    result = evidence.ppi_watch_status(
        NOW, contract=ppi_watch_contract("porota-ppi-watch.service")
    )
    assert result["state"] == "NO_VERIFICADO"
    assert result["reason"] == "EXPECTED_UNIT_NOT_VERIFIED"
    assert result["mutation_attempted"] is False


def test_ppi_watch_bundle_validator_rejects_false_green(tmp_path):
    payload = build(tmp_path)
    payload["ppi_watch"] = {
        "state": "VERIFIED_READ_ONLY", "reason": "forged",
        "checked_at": NOW.isoformat(), "contract_schema_version": 1,
        "owner": "EXTERNAL_SEPARATE_OWNER",
        "systemd_presence_expectation": "UNKNOWN",
        "enumeration_succeeded": True, "units": [], "mutation_attempted": False,
    }
    with pytest.raises(evidence.EvidenceError, match="PPI_WATCH_FALSE_VERIFIED_UNIT"):
        evidence.validate_bundle(payload)


def test_ppi_watch_bundle_validator_accepts_all_fail_closed_states(tmp_path):
    for state in ("NOT_PRESENT", "AMBIGUOUS", "NO_VERIFICADO"):
        payload = build(tmp_path / state)
        payload["ppi_watch"]["state"] = state
        evidence.validate_bundle(payload)


def test_delta_reports_promotion_from_fixed_previous_bundle(tmp_path):
    payload = build(tmp_path)
    old = json.loads(json.dumps(payload["instruments"][0]))
    old["readiness"]["status"] = "NO_READY"
    previous = {"schema": evidence.SCHEMA, "instruments": [old]}
    delta = evidence._delta(previous, payload["instruments"])
    assert delta["state"] == "VERIFIED"
    assert delta["promotions"][0]["before"] == "NO_READY"


def test_collector_and_publisher_have_no_forbidden_dependencies_or_mutations():
    root = Path(__file__).parents[1]
    collector = (root / "ops_runtime_evidence_rc6.py").read_text(encoding="utf-8")
    publisher = (root / "scripts/porota_publish_runtime_evidence_github_rc6.sh").read_text(encoding="utf-8")
    assert collector.count("sqlite3.connect(") == 1
    assert "?mode=ro" in collector
    assert "PRAGMA query_only=ON" in collector
    for forbidden in ("order_router", "broker_client", "enqueue_critical", "paper_notification_outbox"):
        assert forbidden not in collector.lower()
    for forbidden in ("docker ", "systemctl start", "systemctl stop", "systemctl restart",
                      "systemctl enable", "systemctl disable", "git checkout"):
        assert forbidden not in publisher.lower()
    assert "runtime/evidence/latest.json" in publisher
    assert "runtime-observability" in publisher
    legacy_publisher = (root / "scripts/porota_publish_introspection_github_rc6.sh").read_text(encoding="utf-8")
    assert "/run/porota-observability-publish/lock" in publisher
    assert "/run/porota-observability-publish/lock" in legacy_publisher


def test_consumer_requires_exact_commit_and_blob(tmp_path):
    payload = build(tmp_path / "data")
    repo = tmp_path / "sink"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "runtime-observability"], cwd=repo, check=True)
    target = repo / "runtime/evidence/latest.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-qm", "fixture"], cwd=repo, check=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    blob = subprocess.check_output(
        ["git", "rev-parse", f"{commit}:runtime/evidence/latest.json"], cwd=repo, text=True
    ).strip()
    result = consume(repo, commit, blob)
    assert result["commit_sha"] == commit
    assert result["blob_sha"] == blob
    with pytest.raises(ConsumerError, match="BLOB_MISMATCH"):
        consume(repo, commit, "0" * 40)


def test_systemd_unit_is_hardened_and_has_no_docker_group():
    root = Path(__file__).parents[1]
    unit = (root / "systemd/porota-runtime-evidence-rc6.service").read_text(encoding="utf-8")
    for required in (
        "User=porotaadmin", "NoNewPrivileges=true", "ProtectSystem=strict",
        "ProtectHome=read-only", "ReadWritePaths=/var/lib/porota-runtime-evidence",
        "StateDirectory=porota-runtime-evidence", "RuntimeDirectoryPreserve=yes",
    ):
        assert required in unit
    assert "ProtectHome=false" not in unit
    assert "SupplementaryGroups=docker" not in unit


def test_systemd_home_is_read_only_and_git_credential_fixture_remains_readable(tmp_path):
    root = Path(__file__).parents[1]
    unit = (root / "systemd/porota-runtime-evidence-rc6.service").read_text(encoding="utf-8")
    fixture_home = tmp_path / "home" / "porotaadmin"
    fixture_home.mkdir(parents=True)
    credential_config = fixture_home / ".gitconfig"
    credential_config.write_text(
        "[credential]\n\thelper = fixture-readonly-helper\n", encoding="utf-8"
    )
    assert "ProtectHome=read-only" in unit
    assert credential_config.read_text(encoding="utf-8").endswith(
        "helper = fixture-readonly-helper\n"
    )
    write_paths = {
        line.split("=", 1)[1] for line in unit.splitlines()
        if line.startswith("ReadWritePaths=")
    }
    assert write_paths == {
        "/var/lib/porota-runtime-evidence",
        "/var/lib/porota-observability/repo",
        "/run/porota-observability-publish",
    }
    assert not any(path.startswith("/home") for path in write_paths)


def test_host_policy_and_manifest_remain_green_with_ppi_watch_external_contract():
    from scripts.porota_host_manifest_v2 import build_manifest, tracked_modes
    from scripts.porota_host_policy_v2 import validate_policy

    root = Path(__file__).parents[1]
    policy = json.loads(
        (root / "ops/policy/host-control-plane-reconciliation-v2.json").read_text(
            encoding="utf-8"
        )
    )
    contract = policy["ppi_watch_evidence_contract"]
    assert contract["managed_by_porota_host_control_plane"] is False
    assert contract["mutation_allowed"] is False
    assert contract["expected_unit_names"] == []
    assert contract["systemd_presence_expectation"] == "UNKNOWN"
    manifest = build_manifest(root, policy, "a" * 40, modes=tracked_modes(root))
    assert manifest["status"] == "GREEN"
    assert validate_policy(manifest, policy)["status"] == "GREEN"
