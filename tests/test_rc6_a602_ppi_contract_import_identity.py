"""A602: authenticated PPI XHR terms must never fan out by ticker.

Synthetic, offhost SQLite fixtures only. No browser, provider, SSH or orders.
These tests are a contract-importer regression, NOT runtime or PIT evidence.
"""
import json
import sqlite3

import pytest

import rc4_contract_import_job as importer


def _store(tmp_path, rows):
    path = tmp_path / "a602-catalog.db"
    con = sqlite3.connect(path)
    try:
        con.execute("""CREATE TABLE financial_instrument_catalog(
          ticker TEXT, instrument_type TEXT, market TEXT, currency TEXT,
          settlement TEXT, settlement_source TEXT, status TEXT)""")
        con.executemany("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()
    return importer.Store(str(path))


def _row(*, currency="ARS", source="PPI_FIELD", status="AVAILABLE",
         family="ACCIONES", market="BYMA", settlement="A-24HS", ticker="GGAL"):
    return (ticker, family, market, currency, settlement, source, status)


def _capture(tmp_path, *, kind="InstrumentosOperables", rows=None, row=None,
             auth="AUTHENTICATED_TRUSTED_DEVICE"):
    item = {"kind": kind}
    if kind == "DatosTecnicos":
        item["row"] = row or {}
    else:
        item["rows"] = rows or []
    path = tmp_path / "a602-sanitary-capture.json"
    path.write_text(json.dumps({"jobs": ["CONTRACT_EVIDENCE_STATIC"],
        "auth_status": auth, "endpoints": {"PPI-TRUSTED-GET": item}}),
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


@pytest.mark.parametrize("primary_source", ["PPI_FIELD", "REQUEST_CANDIDATE"])
def test_one_complete_ppi_identity_with_iol_shadow_records_once(
        tmp_path, monkeypatch, primary_source):
    store = _store(tmp_path, [
        _row(source=primary_source),
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
