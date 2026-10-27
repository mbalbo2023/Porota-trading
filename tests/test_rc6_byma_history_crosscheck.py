"""Bounded, value-free BYMADATA historical crosscheck guards."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
from datetime import datetime, time, timedelta, timezone
from email.message import Message
from hashlib import sha256
from zoneinfo import ZoneInfo

import pytest

from scripts.rc6_history_fresh_sidecar import capture
from scripts.rc6_byma_history_crosscheck import (
    ENDPOINT, MAX_BODY_BYTES, compare, parse_response, public_post,
    request_body, write_receipt,
)

ART = ZoneInfo("America/Argentina/Buenos_Aires")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("NETWORK_FORBIDDEN_IN_BYMA_CROSSCHECK_TEST")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _bar(day, opening, high, low, close, volume):
    return {"date": day.isoformat(),
            "time": int(datetime.combine(day, time(12), tzinfo=ART).timestamp()),
            "open": opening, "high": high, "low": low, "close": close,
            "volume": volume}


def _sidecar(tmp_path, bars, *, symbol="GGALD", family="ACCIONES", currency="USD", settlement="T1"):
    path = tmp_path / "fresh.sqlite"
    capture(path, [(symbol, family, "BCBA", currency, settlement)], "IOL_CONNECTOR",
            lambda *_: bars, now=datetime.now(timezone.utc))
    return path


def _response(day, opening, high, low, close, volume):
    return json.dumps({"content": {"page_number": 1, "page_count": 1,
                                   "page_size": 50, "total_elements_count": 1},
                       "data": [{"fecha": day.isoformat() + " 00:00:00.0",
                                 "PrecioAper": opening, "precioMaximo": high,
                                 "precioMinimo": low, "precioCierre": close,
                                 "volumenNominal": volume}],
                       "empty": False, "upgrade": False}).encode()


def test_three_observed_iol_anomaly_patterns_remain_quarantined_on_independent_disagreement(tmp_path):
    # IOL MCP values actually observed in 2026-03-27 and 2026-05-11;
    # shift days into any future CI retention window. BYMA values here are
    # synthetic, because its data must not be republished as a fixture.
    today = datetime.now(timezone.utc).astimezone(ART).date()
    first, second = today - timedelta(days=2), today - timedelta(days=1)
    bars = [_bar(first, 4.55, 4.72, 4.39, 4.30, 40711),
            _bar(second, 4.285, 4.515, 4.26, 4.52, 39072)]
    sidecar = _sidecar(tmp_path, bars)
    original_hash = sha256(sidecar.read_bytes()).hexdigest()
    calls = []

    def public_stub(symbol, day, variant):
        calls.append((symbol, day, variant))
        date = first if day == first.isoformat() else second
        if date == first:
            return _response(date, 4.55, 4.72, 4.30, 4.40, 40810)
        return _response(date, 4.285, 4.52, 4.26, 4.51, 39000)

    result = compare(sidecar, [first.isoformat(), second.isoformat()], post=public_stub)
    assert result["state"] == "EVIDENCE_ONLY_NO_GO"
    assert result["selected_days"] == 2 and result["provider_post_invocations"] == 4
    assert calls == [("GGALD", first.isoformat(), "0"), ("GGALD", first.isoformat(), "1"),
                     ("GGALD", second.isoformat(), "0"), ("GGALD", second.isoformat(), "1")]
    assert all(row["iol_row_state"] == "QUARANTINED_OHLC_RANGE_CONFLICT" and
               row["post_crosscheck_state"] == "QUARANTINED_REVIEW_REQUIRED"
               for row in result["rows"])
    assert result["rows"][0]["variants"][0]["matching_fields"] == ["high", "open"]
    assert result["rows"][0]["variants"][0]["differing_fields"] == ["close", "low", "volume"]
    assert all(variant["byma_ohlc_valid"] and not variant["https_origin_observed"]
               for row in result["rows"] for variant in row["variants"])
    assert all(variant["requested_identity"]["semantics"] == "REQUEST_ASSERTION_ONLY"
               and variant["response_identity_fields"] == []
               for row in result["rows"] for variant in row["variants"])
    assert all(variant["requested_identity"]["iol_capture_currency_not_in_byma_request"] == "USD"
               and variant["requested_identity"]["iol_capture_settlement_not_in_byma_response"] == "T1"
               and "requested_currency" not in variant["requested_identity"]
               for row in result["rows"] for variant in row["variants"])
    assert not any(result[key] for key in (
        "historical_currency_verified", "historical_settlement_verified",
        "adjustment_basis_verified", "provider_original_pit_verified",
        "same_row_identity_provider_attested", "execution_allowed"))
    assert result["history_v2_writes"] == 0 and sha256(sidecar.read_bytes()).hexdigest() == original_hash
    rendered = json.dumps(result)
    assert not any(name in rendered for name in ('"PrecioAper"', '"precioCierre"',
                                                '"volumenNominal"', '4.55', '4.72'))


def test_al30_real_iol_range_conflict_with_independent_synthetic_low_is_no_go(tmp_path):
    today = datetime.now(timezone.utc).astimezone(ART).date()
    day = today - timedelta(days=1)
    sidecar = _sidecar(tmp_path, [_bar(day, 94300, 94540, 93610, 93600, 138266907)],
                       symbol="AL30", family="BONOS", currency="ARS")
    result = compare(sidecar, [day.isoformat()], post=lambda *_: _response(
        day, 94300, 94540, 93590, 93600, 138266900))
    assert result["rows"][0]["variants"][0]["differing_fields"] == ["low", "volume"]
    assert result["rows"][0]["post_crosscheck_state"] == "QUARANTINED_REVIEW_REQUIRED"


@pytest.mark.parametrize("mutate,reason", [
    (lambda p: p["data"][0].update(fecha="2020-01-01 00:00:00.0"), "BYMA_HISTORY_DATE_MISMATCH"),
    (lambda p: p["data"][0].update(currency="ARS"), "BYMA_HISTORY_RESPONSE_SHAPE_INVALID"),
    (lambda p: p["data"][0].update(precioCierre=True), "BYMA_HISTORY_NUMBER_INVALID"),
    (lambda p: p["content"].update(total_elements_count=2), "BYMA_HISTORY_RESPONSE_SHAPE_INVALID"),
    (lambda p: p.update(upgrade=True), "BYMA_HISTORY_RESPONSE_SHAPE_INVALID"),
])
def test_response_conflicts_are_rejected(mutate, reason):
    day = datetime(2026, 3, 27).date()
    payload = json.loads(_response(day, 4.55, 4.72, 4.30, 4.4, 40810))
    mutate(payload)
    with pytest.raises(ValueError, match=reason):
        parse_response(json.dumps(payload).encode(), day.isoformat())


def test_duplicate_json_nonfinite_and_size_are_rejected():
    day = "2026-03-27"
    with pytest.raises(ValueError, match="BYMA_HISTORY_DUPLICATE_KEY"):
        parse_response(b'{"data":[],"data":[]}', day)
    with pytest.raises(ValueError, match="BYMA_HISTORY_NUMBER_INVALID"):
        parse_response(b'{"data":[{"close":NaN}]}', day)
    with pytest.raises(ValueError, match="BYMA_HISTORY_BODY_BUDGET"):
        parse_response(b"x" * (MAX_BODY_BYTES + 1), day)


def test_no_non_anomalous_or_ambiguous_or_more_than_three_days_are_queried(tmp_path):
    today = datetime.now(timezone.utc).astimezone(ART).date()
    anomaly = today - timedelta(days=1)
    clean = today - timedelta(days=2)
    sidecar = _sidecar(tmp_path, [_bar(anomaly, 4.55, 4.72, 4.39, 4.30, 40711),
                                  _bar(clean, 4.55, 4.72, 4.39, 4.50, 40711)])
    called = []
    post = lambda *_: called.append(True)
    with pytest.raises(ValueError, match="BYMA_HISTORY_ANOMALY_REQUIRED"):
        compare(sidecar, [clean.isoformat()], post=post)
    with pytest.raises(ValueError, match="BYMA_HISTORY_DAY_BUDGET_INVALID"):
        compare(sidecar, [anomaly.isoformat()] * 2, post=post)
    with pytest.raises(ValueError, match="BYMA_HISTORY_DAY_BUDGET_INVALID"):
        compare(sidecar, [(today - timedelta(days=i)).isoformat() for i in range(1, 5)], post=post)
    assert not called


def test_failed_provider_response_is_sanitized_and_iol_still_quarantined(tmp_path):
    day = datetime.now(timezone.utc).astimezone(ART).date() - timedelta(days=1)
    sidecar = _sidecar(tmp_path, [_bar(day, 4.55, 4.72, 4.39, 4.30, 40711)])
    def failed(*_):
        raise RuntimeError("Authorization: Bearer NEVER-LOG")
    result = compare(sidecar, [day.isoformat()], post=failed)
    assert [v["failure_reason"] for v in result["rows"][0]["variants"]] == [
        "BYMA_HISTORY_SOURCE_UNAVAILABLE"] * 2
    assert result["rows"][0]["post_crosscheck_state"] == "QUARANTINED_REVIEW_REQUIRED"
    assert "NEVER-LOG" not in json.dumps(result)


def test_private_receipt_fsync_and_destination_guard(tmp_path, monkeypatch):
    import scripts.rc6_byma_history_crosscheck as module
    day = datetime.now(timezone.utc).astimezone(ART).date() - timedelta(days=1)
    sidecar = _sidecar(tmp_path, [_bar(day, 4.55, 4.72, 4.39, 4.30, 40711)])
    receipt = tmp_path / "receipt.json"
    legacy = tmp_path / "market_history.db"
    legacy.write_bytes(b"unchanged")
    real_fsync = os.fsync
    synced = []
    def fsync(fd):
        synced.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)
    monkeypatch.setattr(module.os, "fsync", fsync)
    result = write_receipt(sidecar, [day.isoformat()], receipt, post=lambda *_: _response(
        day, 4.55, 4.72, 4.30, 4.40, 40810))
    assert json.loads(receipt.read_text()) == result and synced == ["file", "directory"]
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert legacy.read_bytes() == b"unchanged"
    with pytest.raises(ValueError, match="FRESH_GENERATION_ALREADY_EXISTS"):
        write_receipt(sidecar, [day.isoformat()], receipt, post=lambda *_: b"unused")
    with pytest.raises(ValueError, match="FRESH_LEGACY_DESTINATION_FORBIDDEN"):
        write_receipt(sidecar, [day.isoformat()], legacy, post=lambda *_: b"unused")


def test_public_post_exact_single_request_without_auth_or_redirect(monkeypatch):
    import scripts.rc6_byma_history_crosscheck as module
    day = "2026-03-27"
    response_body = _response(datetime.fromisoformat(day).date(), 4.55, 4.72, 4.30, 4.4, 40810)
    calls = []
    class Response:
        status = 200
        headers = Message()
        headers.add_header("Content-Type", "application/json; charset=utf-8")
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def geturl(self): return ENDPOINT
        def read(self, size):
            assert size == MAX_BODY_BYTES + 1
            return response_body
    class Opener:
        def open(self, request, *, timeout):
            calls.append(request)
            assert timeout == 10
            return Response()
    monkeypatch.setattr(module, "build_opener", lambda *_: Opener())
    assert public_post("GGALD", day, "0") == response_body
    assert len(calls) == 1 and calls[0].get_method() == "POST" and calls[0].full_url == ENDPOINT
    assert json.loads(calls[0].data) == {
        "symbol": "GGALD", "maturity": "2", "fromDate": day,
        "toDate": day, "homogeneous": "0", "period": "1"}
    assert not any(name.lower() in {"authorization", "cookie"} for name in calls[0].headers)
    with pytest.raises(ValueError, match="BYMA_HISTORY_REDIRECT_FORBIDDEN"):
        module._NoRedirect().redirect_request(None, None, 302, None, None)


def test_request_rejects_symbol_or_variant_injection():
    with pytest.raises(ValueError, match="BYMA_HISTORY_REQUEST_INVALID"):
        request_body("GGALD&account=123", "2026-03-27", "0")
    with pytest.raises(ValueError, match="BYMA_HISTORY_REQUEST_INVALID"):
        request_body("GGALD", "2026-03-27", "raw")
