"""A602: authenticated PPI XHR terms must never fan out by ticker.

Synthetic, offhost SQLite fixtures only. No browser, provider, SSH or orders.
These tests are a contract-importer regression, NOT runtime or PIT evidence.
"""
import json
import sqlite3
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

import rc4_contract_import_job as importer


def _store(tmp_path, rows):
    path = tmp_path / "a602-catalog.db"
    con = sqlite3.connect(path)
    try:
        con.execute("""CREATE TABLE financial_instrument_catalog(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, settlement_source TEXT, status TEXT, metadata_json TEXT, last_seen_at TEXT)""")
        con.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    return importer.Store(str(path))


def _row(*, currency="ARS", source="PPI_FIELD", status="AVAILABLE",
         family="ACCIONES", market="BYMA", settlement="A-24HS", ticker="GGAL", metadata=None, seen=None):
    raw = {"_discovery_source": "PPI_PRIMARY", "ticker": ticker, "type": family,
           "market": market, "currency": currency, "settlement": settlement}
    return (ticker, family, market, currency, settlement, source, status,
            json.dumps(raw) if metadata is None else metadata,
            seen if seen is not None else (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat())


def _capture(tmp_path, *, kind="InstrumentosOperables", rows=None, row=None,
             auth="AUTHENTICATED_TRUSTED_DEVICE"):
    item = {"kind": kind, "status": 200}
    if kind == "DatosTecnicos":
        item["row"] = row or {}
    else:
        item["rows"] = rows or []
    path = tmp_path / "a602-sanitary-capture.json"
    path.write_text(json.dumps({"schema": "POROTA_RC4_PPI_TRUSTED_CONTRACT_1",
        "generated_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
        "jobs": ["CONTRACT_EVIDENCE_STATIC"], "routes": [], "blocked_nonread": [],
        "continue_clicked": False, "amount_filled": False, "price_filled": False,
        "real_orders_sent": 0,
        "auth_status": auth, "endpoints": {"https://trading.portfoliopersonal.com/InstrumentosOperables": item}}),
        encoding="utf-8")
    return path


def _run(monkeypatch, store, path):
    calls = []
    monkeypatch.setattr(importer, "start_run", lambda *a, **kw: None)
    monkeypatch.setattr(importer, "finish_run", lambda *a, **kw: None)

    def collect(*args, **kwargs):
        calls.append(kwargs)
        return {"changed": False}

    monkeypatch.setattr(importer, "record_snapshot", collect)
    result = importer.import_capture(store, path)
    return result, calls


def test_request_candidate_without_raw_ppi_settlement_cannot_attest_contract(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row(source="REQUEST_CANDIDATE", metadata="{}")])
    capture = _capture(tmp_path, rows=[{"ticker": "GGAL", "instrument_id": 123}])
    result, calls = _run(monkeypatch, store, capture)
    assert result["records"] == 0
    assert calls == []


def test_forged_capture_with_missing_envelope_has_no_side_effects(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row()])
    capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); raw.pop("schema")
    capture.write_text(json.dumps(raw))
    effects = []
    monkeypatch.setattr(importer, "start_run", lambda *a, **kw: effects.append("start"))
    monkeypatch.setattr(importer, "finish_run", lambda *a, **kw: effects.append("finish"))
    monkeypatch.setattr(importer, "record_snapshot", lambda *a, **kw: effects.append("snapshot") or {})
    importer.import_capture(store, capture)
    assert effects == []


def test_invalid_envelope_does_not_create_evidence_tables_in_real_sqlite(tmp_path):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); raw["real_orders_sent"] = 1
    capture.write_text(json.dumps(raw))
    result = importer.import_capture(store, capture)
    with store.connect() as con:
        names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert result["state"] == "BLOCKED_CAPTURE_INVALID"
    assert names == {"financial_instrument_catalog"}


@pytest.mark.parametrize("key,value", [
    ("schema", "unknown"), ("generated_at", "garbage"),
    ("generated_at", "2000-01-01T00:00:00Z"),
    ("generated_at", "2999-01-01T00:00:00Z"),
    ("generated_at", "2026-11-01T00:00:00"),
    ("jobs", ["UNKNOWN"]), ("jobs", "CONTRACT_EVIDENCE_STATIC"),
    ("endpoints", []), ("routes", {}), ("blocked_nonread", {}),
    ("routes", ["not-a-route"]), ("blocked_nonread", ["not-a-record"]),
    ("real_orders_sent", False), ("real_orders_sent", 1),
    ("continue_clicked", 0), ("amount_filled", True), ("price_filled", None),
])
def test_invalid_envelope_causes_no_side_effects(tmp_path, monkeypatch, key, value):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); raw[key] = value; capture.write_text(json.dumps(raw))
    effects = []
    monkeypatch.setattr(importer, "start_run", lambda *a, **kw: effects.append("start"))
    monkeypatch.setattr(importer, "finish_run", lambda *a, **kw: effects.append("finish"))
    monkeypatch.setattr(importer, "record_snapshot", lambda *a, **kw: effects.append("snapshot") or {})
    result = importer.import_capture(store, capture)
    assert result["state"].startswith("BLOCKED_") and effects == []


@pytest.mark.parametrize("status", [201, 204, 401])
def test_bad_endpoint_status_causes_no_side_effects(tmp_path, monkeypatch, status):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); next(iter(raw["endpoints"].values()))["status"] = status
    capture.write_text(json.dumps(raw))
    effects = []
    monkeypatch.setattr(importer, "start_run", lambda *a, **kw: effects.append("start"))
    monkeypatch.setattr(importer, "finish_run", lambda *a, **kw: effects.append("finish"))
    monkeypatch.setattr(importer, "record_snapshot", lambda *a, **kw: effects.append("snapshot") or {})
    result = importer.import_capture(store, capture)
    assert result["state"].startswith("BLOCKED_") and effects == []


def test_malformed_rows_fail_before_side_effects(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); next(iter(raw["endpoints"].values()))["rows"] = "not-a-list"
    capture.write_text(json.dumps(raw)); effects = []
    monkeypatch.setattr(importer, "start_run", lambda *a, **kw: effects.append("start"))
    monkeypatch.setattr(importer, "finish_run", lambda *a, **kw: effects.append("finish"))
    result = importer.import_capture(store, capture)
    assert result["state"].startswith("BLOCKED_") and effects == []


def test_importer_cli_invalid_capture_exits_nonzero_without_database(tmp_path):
    capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); next(iter(raw["endpoints"].values()))["status"] = 401
    capture.write_text(json.dumps(raw)); database = tmp_path / "never-created.db"
    env = dict(os.environ, PAPER_V17_DB_PATH=str(database))
    result = subprocess.run([sys.executable, "rc4_contract_import_job.py", "--input", str(capture)], env=env, capture_output=True, text=True)
    assert json.loads(result.stdout)["state"] == "BLOCKED_CAPTURE_INVALID"
    assert result.returncode != 0 and not database.exists()


def test_importer_cli_empty_authenticated_capture_exits_nonzero(tmp_path):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[])
    env = dict(os.environ, PAPER_V17_DB_PATH=store.path)
    result = subprocess.run([sys.executable, "rc4_contract_import_job.py", "--input", str(capture)], env=env, capture_output=True, text=True)
    receipt = json.loads(result.stdout)
    assert receipt["auth_status"] == "AUTHENTICATED_TRUSTED_DEVICE"
    assert receipt["state"] == "AMARILLO" and receipt["records"] == 0
    assert result.returncode != 0


@pytest.mark.parametrize("state,records,want", [
    ("VERDE", 1, 0), ("VERDE", 0, 1), ("VERDE", True, 1),
    ("AMARILLO", 1, 1), ("BLOCKED_CAPTURE_INVALID", 0, 1), ("UNKNOWN", 1, 1),
])
def test_only_green_with_real_positive_record_count_has_success_exit(state, records, want):
    assert importer.import_exit_code({"state": state, "records": records}) == want


@pytest.mark.parametrize("job", ["CONTRACT_EVIDENCE_FUND_TERMS", "CONTRACT_EVIDENCE_TARIFF"])
def test_unbound_jobs_are_rejected_before_run(tmp_path, monkeypatch, job):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); raw["jobs"] = [job]; capture.write_text(json.dumps(raw))
    effects=[]; monkeypatch.setattr(importer, "start_run", lambda *a, **kw: effects.append("start"))
    result = importer.import_capture(store, capture)
    assert result["state"].startswith("BLOCKED_") and effects == []


@pytest.mark.parametrize("url", ["http://trading.portfoliopersonal.com/InstrumentosOperables", "https://attacker.example/InstrumentosOperables"])
def test_untrusted_capture_endpoint_origin_cannot_write(tmp_path, monkeypatch, url):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); item = next(iter(raw["endpoints"].values()))
    raw["endpoints"] = {url: item}; capture.write_text(json.dumps(raw))
    effects=[]; monkeypatch.setattr(importer, "start_run", lambda *a, **kw: effects.append("start"))
    result = importer.import_capture(store, capture)
    assert result["state"].startswith("BLOCKED_") and effects == []


def test_source_ref_never_persists_userinfo_query_or_fragment(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row()]); capture = _capture(tmp_path, rows=[{"ticker": "GGAL"}])
    raw = json.loads(capture.read_text()); item = next(iter(raw["endpoints"].values()))
    raw["endpoints"] = {"https://name:secret@trading.portfoliopersonal.com/InstrumentosOperables?token=secret#fragment": item}
    capture.write_text(json.dumps(raw)); result, calls = _run(monkeypatch, store, capture)
    assert result["records"] == 1
    assert calls[0]["source_ref"] == "https://trading.portfoliopersonal.com/InstrumentosOperables"


def test_request_candidate_with_complete_raw_still_cannot_attest_contract(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row(source="REQUEST_CANDIDATE")])
    result, calls = _run(monkeypatch, store, _capture(tmp_path, rows=[{"ticker": "GGAL"}]))
    assert result["records"] == 0 and not calls


def test_one_complete_ppi_identity_with_iol_shadow_records_once(
        tmp_path, monkeypatch):
    store = _store(tmp_path, [
        _row(),
        _row(currency="USD_CCL", source="IOL_COMPLEMENTARY",
             status="OBSERVED_SHADOW")])
    raw_currency = {"description": "Pesos", "symbol": "$", "provider_id": 1}
    capture = _capture(tmp_path, rows=[{"ticker": "GGAL",
        "instrument_id": 123, "currency": raw_currency,
        "quantity_decimal_places": 2, "commission_rate_estimated": 0.004,
        "semantic_guard": "DECIMAL_PLACES_ARE_NOT_STEP_OR_TICK"}])
    result, calls = _run(monkeypatch, store, capture)
    assert result["records"] == 1
    assert result["conflicts"] == 0
    assert len(calls) == 1
    assert (calls[0]["ticker"], calls[0]["family"], calls[0]["market"],
            calls[0]["currency"], calls[0]["settlement"]) == (
                "GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
    evidence = calls[0]["evidence"]
    assert "currency" not in evidence
    assert evidence["provider_currency"] == raw_currency
    assert evidence["evidence_scope"] == "PARTIAL_AUTHENTICATED_XHR"
    assert evidence["readiness_guard"] == "NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"
    assert evidence["semantic_guard"] == "DECIMAL_PLACES_ARE_NOT_STEP_OR_TICK"
    assert "commission_rate_estimated" not in evidence
    assert "PPI_XHR_FEE_ESTIMATE_UNBOUND_GGAL" in result["notes"]


@pytest.mark.parametrize("metadata", [
    "{}", "not-json",
    json.dumps({"_discovery_source": "PPI_PRIMARY", "ticker": "GGAL", "type": "ACCIONES", "market": "BYMA", "currency": "USD", "settlement": "A-24HS"}),
    json.dumps({"_discovery_source": "PPI_PRIMARY", "ticker": "GGAL", "type": "ACCIONES", "market": "BYMA", "currency": "ARS", "settlement": "CI"}),
])
def test_unattested_raw_identity_cannot_record_contract(tmp_path, monkeypatch, metadata):
    store = _store(tmp_path, [_row(metadata=metadata)])
    result, calls = _run(monkeypatch, store, _capture(tmp_path, rows=[{"ticker": "GGAL"}]))
    assert result["records"] == 0 and not calls


def test_legacy_catalog_without_metadata_fails_closed(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT, settlement TEXT, settlement_source TEXT, status TEXT)")
        con.execute("INSERT INTO financial_instrument_catalog VALUES('GGAL','ACCIONES','BYMA','ARS','A-24HS','PPI_FIELD','AVAILABLE')")
    assert importer.candidates(importer.Store(str(path))).get("GGAL", []) == []


@pytest.mark.parametrize("seen", [
    (datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
    (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    "invalid", "",
])
def test_available_identity_with_untrustworthy_timestamp_cannot_record(tmp_path, monkeypatch, seen):
    store = _store(tmp_path, [_row(seen=seen)])
    result, calls = _run(monkeypatch, store, _capture(tmp_path, rows=[{"ticker": "GGAL"}]))
    assert result["records"] == 0 and not calls


@pytest.mark.parametrize("field", ["instrument_type", "type"])
def test_provider_explicit_contradictory_family_cannot_record(tmp_path, monkeypatch, field):
    store = _store(tmp_path, [_row()])
    result, calls = _run(monkeypatch, store, _capture(tmp_path, rows=[{"ticker": "GGAL", field: "BONOS"}]))
    assert result["records"] == 0 and not calls


def test_schema_with_metadata_but_without_timestamp_fails_closed(tmp_path):
    path = tmp_path / "no-seen.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE financial_instrument_catalog(ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT, settlement TEXT, settlement_source TEXT, status TEXT, metadata_json TEXT)")
        con.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?)", _row()[:-1])
    assert importer.candidates(importer.Store(str(path))).get("GGAL", []) == []


@pytest.mark.parametrize("status", ["STALE", "HOLD", "AVAILABLE"])
def test_second_ppi_primary_blocks_even_with_one_available(
        tmp_path, monkeypatch, status):
    store = _store(tmp_path, [
        _row(),
        _row(currency="USD_CCL", source="REQUEST_CANDIDATE", status=status)])
    result, calls = _run(monkeypatch, store,
                         _capture(tmp_path, rows=[{"ticker": "GGAL", "instrument_id": 4}]))
    assert calls == []
    assert result["records"] == 0
    assert result["conflicts"] == 1
    assert "PPI_FULL_IDENTITY_ABSENT_AMBIGUOUS_OR_MISMATCH_GGAL" in result["notes"]


def test_complement_only_can_never_attest_ppi_contract(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row(source="IOL_COMPLEMENTARY")])
    result, calls = _run(monkeypatch, store,
                         _capture(tmp_path, rows=[{"ticker": "GGAL", "instrument_id": 5}]))
    assert calls == []
    assert result["records"] == 0 and result["conflicts"] == 1


def test_unique_ppi_but_unknown_monetary_identity_stays_blocked(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row(currency="UNKNOWN")])
    result, calls = _run(monkeypatch, store,
                         _capture(tmp_path, rows=[{"ticker": "GGAL", "instrument_id": 6}]))
    assert calls == [] and result["records"] == 0


def test_contradictory_explicit_xhr_currency_never_overrides_primary(
        tmp_path, monkeypatch):
    store = _store(tmp_path, [_row()])
    result, calls = _run(monkeypatch, store,
                         _capture(tmp_path, rows=[{"ticker": "GGAL", "currency": "USD_CCL"}]))
    assert calls == [] and result["records"] == 0 and result["conflicts"] == 1


@pytest.mark.parametrize("currency", [None, "ARS"])
def test_provider_currency_cannot_contradict_even_if_currency_is_null_or_matching(tmp_path, monkeypatch, currency):
    store = _store(tmp_path, [_row()])
    result, calls = _run(monkeypatch, store, _capture(tmp_path, rows=[{
        "ticker": "GGAL", "currency": currency, "provider_currency": "USD_CCL"}]))
    assert result["records"] == 0 and not calls


def test_exchange_conversion_rate_is_never_bound_as_contract_evidence(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row()])
    result, calls = _run(monkeypatch, store, _capture(tmp_path, rows=[{
        "ticker": "GGAL", "exchangeConversionRate": 1100}]))
    assert result["records"] == 1 and len(calls) == 1
    assert "exchangeConversionRate" not in calls[0]["evidence"]


@pytest.mark.parametrize("description", [
    "DOLARES DIVISA | CCL", "unrecognized cash denomination"
])
def test_native_ppi_xhr_descriptor_cannot_contradict_full_catalog_currency(
        tmp_path, monkeypatch, description):
    store = _store(tmp_path, [_row(currency="ARS")])
    result, calls = _run(monkeypatch, store, _capture(
        tmp_path, rows=[{"ticker": "GGAL", "instrument_id": 9,
                         "currency": {"description": description, "provider_id": 4}}]))
    assert calls == []
    assert result["records"] == 0 and result["conflicts"] == 1
    assert "PPI_FULL_IDENTITY_ABSENT_AMBIGUOUS_OR_MISMATCH_GGAL" in result["notes"]


def test_duplicate_provider_ticker_cannot_overwrite_contract(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row()])
    result, calls = _run(monkeypatch, store,
                         _capture(tmp_path, rows=[
                             {"ticker": "GGAL", "instrument_id": 1},
                             {"ticker": "GGAL", "instrument_id": 2}]))
    assert calls == [] and result["records"] == 0 and result["conflicts"] == 2
    assert "PPI_XHR_DUPLICATE_TICKER_GGAL" in result["notes"]


def test_fixed_income_native_terms_bind_only_to_unique_ppi_key(
        tmp_path, monkeypatch):
    store = _store(tmp_path, [_row(ticker="AL30", family="BONOS", currency="ARS")])
    result, calls = _run(monkeypatch, store, _capture(tmp_path, kind="DatosTecnicos",
        row={"ticker": "AL30", "laminaMinima": 1, "multiploMinimo": 1,
             "provider_currency": {"description": "Pesos", "provider_id": 1}}))
    assert result["records"] == 1 and result["conflicts"] == 0
    assert len(calls) == 1 and calls[0]["currency"] == "ARS"
    assert calls[0]["evidence"]["laminaMinima"] == 1
    assert calls[0]["evidence"]["readiness_guard"] == "NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"


def test_obligaciones_literal_ppi_identity_keeps_family_and_partial_readiness(
        tmp_path, monkeypatch):
    # PPI's literal OBLIGACIONES identity is NOT the legacy ON alias.
    store = _store(tmp_path, [_row(ticker="YMCJO", family="OBLIGACIONES")])
    result, calls = _run(monkeypatch, store, _capture(
        tmp_path, kind="DatosTecnicos", row={"ticker": "YMCJO",
            "currency": {"description": "Pesos"}, "laminaMinima": 1,
            "multiploMinimo": 1, "redemption_value": "100"}))
    assert result["records"] == 1 and result["conflicts"] == 0
    assert len(calls) == 1
    assert calls[0]["family"] == "OBLIGACIONES"
    assert calls[0]["currency"] == "ARS"
    assert calls[0]["evidence"]["readiness_guard"] == "NO_AUTO_ACTIVATION_MISSING_FIELDS_REMAIN"
    assert calls[0]["evidence"]["evidence_scope"] == "PARTIAL_AUTHENTICATED_XHR"


def test_obligaciones_and_legacy_on_parallel_primary_rows_remain_ambiguous(
        tmp_path, monkeypatch):
    store = _store(tmp_path, [_row(ticker="YMCJO", family="OBLIGACIONES"),
                              _row(ticker="YMCJO", family="ON")])
    result, calls = _run(monkeypatch, store, _capture(
        tmp_path, kind="DatosTecnicos", row={"ticker": "YMCJO", "laminaMinima": 1}))
    assert calls == []
    assert result["records"] == 0 and result["conflicts"] == 1
    assert "PPI_FULL_IDENTITY_ABSENT_AMBIGUOUS_OR_MISMATCH_YMCJO" in result["notes"]


def test_stale_single_ppi_key_is_not_current_contract_authority(
        tmp_path, monkeypatch):
    store = _store(tmp_path, [_row(status="STALE")])
    result, calls = _run(monkeypatch, store,
                         _capture(tmp_path, rows=[{"ticker": "GGAL", "instrument_id": 8}]))
    assert calls == [] and result["records"] == 0


def test_auth_unchanged_fail_closed(tmp_path, monkeypatch):
    store = _store(tmp_path, [_row()])
    result, calls = _run(monkeypatch, store, _capture(
        tmp_path, rows=[{"ticker": "GGAL", "instrument_id": 9}],
        auth="BLOCKED_AUTH_2FA_REQUIRED"))
    assert calls == []
    assert result["state"] == "BLOCKED_AUTH" and result["records"] == 0


def test_a615_caucion_partial_unit_never_turns_import_green(
        tmp_path, monkeypatch):
    store = _store(tmp_path, [
        _row(ticker="CACC1", family="CAUCIONES", currency="ARS")])
    capture = _capture(tmp_path, kind="CaucionesOperables", rows=[{
        "ticker": "CACC1", "currency": "ARS", "quantity": 1,
        "applied_exchange_rate": 1, "amount_invested_ars": 835.7,
    }])
    result, calls = _run(monkeypatch, store, capture)
    assert result["state"] == "AMARILLO"
    assert result["records"] == 1 and result["conflicts"] == 0
    assert "CAUCIONES_PARTIAL_UNIT_UNVERIFIED" in result["notes"]
    assert len(calls) == 1
    evidence = calls[0]["evidence"]
    assert evidence["book_quantity_unit_status"] == "BOOK_QUANTITY_UNIT_UNVERIFIED"
    assert evidence["provider_quantity_unverified"] == {"quantity": 1}
    assert "quantity" not in evidence
    assert "applied_exchange_rate" not in evidence
    assert "amount_invested_ars" not in evidence
