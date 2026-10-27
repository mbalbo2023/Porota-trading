"""Offline guards for IOL's two historical views and private row quarantine."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
from email.message import Message
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.rc6_history_fresh_sidecar import capture
from scripts.rc6_iol_mcp_history_reconcile import (
    audit, fetch_public_history, parse_public_history, public_url, write_receipt,
)

ART = ZoneInfo("America/Argentina/Buenos_Aires")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("NETWORK_FORBIDDEN_IN_RECONCILE_TEST")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _bar(day, open_, high, low, close, volume):
    return {"date": day.isoformat(),
            "time": int(datetime.combine(day, time(12), tzinfo=ART).timestamp()),
            "open": open_, "high": high, "low": low, "close": close,
            "volume": volume}


def _page(symbol, rows):
    head = ("Fecha Cotizaci&oacute;n", "Apertura", "M&aacute;ximo", "M&iacute;nimo", "Cierre",
            "Cierre ajustado", "Volumen Monto", "Volumen Nominal")
    cells = lambda tag, fields: "".join(f"<{tag}>{field}</{tag}>" for field in fields)
    table = "".join(f"<tr>{cells('td', row)}</tr>" for row in rows)
    return (f'<input id="Simbolo" value="{symbol}" />'
            '<table id="tbcotizaciones"><thead><tr>'
            + cells("th", head) + "</tr></thead><tbody>" + table
            + "</tbody></table>").encode("utf-8")


def _row(day, opening, high, low, close, nominal):
    return (day.strftime("%m/%d/%Y"), opening, high, low, close, close,
            "100.00", nominal)


def _sidecar(tmp_path, bars, *, symbol="GGALD", family="ACCIONES", currency="USD"):
    path = tmp_path / "fresh.sqlite"
    capture(path, [(symbol, family, "BCBA", currency, "T1")], "IOL_CONNECTOR",
            lambda *_: bars, now=datetime.now(timezone.utc))
    return path


def test_real_mcp_anomaly_values_against_official_public_display_stay_quarantined(tmp_path):
    # MCP exact values from GGALD 2026-03-27 and 2026-05-11, with dates
    # shifted into today's retention window to keep this regression durable.
    today = datetime.now(timezone.utc).astimezone(ART).date()
    day_a, day_b = today - timedelta(days=2), today - timedelta(days=1)
    a = _bar(day_a, 4.55, 4.72, 4.39, 4.30, 40711)
    b = _bar(day_b, 4.285, 4.515, 4.26, 4.52, 39072)
    sidecar = _sidecar(tmp_path, [a, b])
    page = _page("GGALD", [
        _row(day_a, "4.55", "4.72", "4.39", "4.30", "40,711.00"),
        # Public page prints two decimal places, whereas MCP high=4.515.
        _row(day_b, "4.29", "4.52", "4.26", "4.52", "39,072.00"),
    ])
    result = audit(sidecar, public_get=lambda *_: page)
    assert result["state"] == "EVIDENCE_ONLY_NO_GO"
    assert result["counts"] == {
        "staged_review_required": 0, "quarantined_rows": 2,
        "ohlc_range_conflict_rows": 2, "duplicate_day_rows": 0,
        "public_exact_displayed_ohlcv": 1,
        "public_displayed_field_difference": 1, "public_missing_day": 0,
    }
    assert result["quarantine_sample"][0]["public_comparison"] == "EXACT_DISPLAYED_OHLCV"
    assert result["quarantine_sample"][1]["public_comparison"] == "DISPLAYED_FIELD_DIFFERENCE"
    assert not result["provider_original_pit_verified"]
    assert result["public_source_state"] == "SUPPLIED_TABLE_OBSERVATION"
    assert result["public_origin_attested"] is False
    assert not result["historical_currency_verified"]
    assert not result["historical_settlement_verified"]
    assert not result["adjustment_basis_verified"]
    assert result["history_v2_writes"] == 0 and result["execution_allowed"] is False


def test_real_al30_range_conflict_and_clean_rows_are_distinguished(tmp_path):
    # AL30 2025-11-13: open=94300, high=94540, low=93610, close=93600,
    # nominal volume=138266907. Date shifted to remain in 365-day window.
    today = datetime.now(timezone.utc).astimezone(ART).date()
    a_day, b_day = today - timedelta(days=2), today - timedelta(days=1)
    anomaly = _bar(a_day, 94300, 94540, 93610, 93600, 138266907)
    clean = _bar(b_day, 85000, 86000, 84000, 85500, 1000)
    sidecar = _sidecar(tmp_path, [anomaly, clean], symbol="AL30", family="BONOS", currency="ARS")
    page = _page("AL30", [_row(a_day, "94,300.00", "94,540.00", "93,610.00",
                                    "93,600.00", "138,266,907.00")])
    result = audit(sidecar, public_get=lambda *_: page)
    assert result["counts"]["quarantined_rows"] == 1
    assert result["counts"]["staged_review_required"] == 1
    assert result["counts"]["public_exact_displayed_ohlcv"] == 1
    assert result["counts"]["public_missing_day"] == 1
    assert result["quarantine_sample"][0]["reason"] == "OHLC_RANGE_CONFLICT"
    with sqlite3.connect(sidecar) as db:
        assert json.loads(db.execute(
            "SELECT retained_rows_json FROM fresh_captures_rc6").fetchone()[0]) == [anomaly, clean]


def test_public_parser_requires_exact_symbol_table_and_unique_days():
    day = datetime(2026, 3, 27).date()
    row = _row(day, "4.55", "4.72", "4.39", "4.30", "40,711.00")
    public = parse_public_history(_page("GGALD", [row]), "GGALD")
    assert public[day.isoformat()][6] == 40711
    with pytest.raises(ValueError, match="IOL_PUBLIC_TABLE_IDENTITY_UNVERIFIED"):
        parse_public_history(_page("GGAL", [row]), "GGALD")
    with pytest.raises(ValueError, match="IOL_PUBLIC_DUPLICATE_DAY"):
        parse_public_history(_page("GGALD", [row, row]), "GGALD")
    with pytest.raises(ValueError, match="IOL_PUBLIC_NUMBER_INVALID"):
        parse_public_history(_page("GGALD", [row[:-1] + ("NaN",)]), "GGALD")
    with pytest.raises(ValueError, match="IOL_PUBLIC_BODY_INVALID"):
        parse_public_history(b"x" * 2_000_001, "GGALD")


def test_failed_or_forged_public_page_cannot_promote_local_evidence(tmp_path):
    day = datetime.now(timezone.utc).astimezone(ART).date() - timedelta(days=1)
    sidecar = _sidecar(tmp_path, [_bar(day, 4.55, 4.72, 4.39, 4.30, 40711)])
    for getter in (lambda *_: _page("GGAL", [_row(day, "4.55", "4.72", "4.39",
                                                         "4.30", "40,711.00")]),
                   lambda *_: (_ for _ in ()).throw(OSError("Bearer secret")),
                   lambda *_: (_ for _ in ()).throw(ValueError("IOL_PUBLIC_Bearer secret"))):
        result = audit(sidecar, public_get=getter)
        assert result["public_source_state"] == "UNAVAILABLE"
        assert result["public_body_sha256"] is None
        assert result["counts"]["quarantined_rows"] == 1
        assert result["counts"]["public_exact_displayed_ohlcv"] == 0
        assert "secret" not in json.dumps(result)
        assert result["public_failure_reason"] in {
            "IOL_PUBLIC_TABLE_IDENTITY_UNVERIFIED", "IOL_PUBLIC_SOURCE_UNAVAILABLE"}


def test_identical_duplicate_mcp_days_are_quarantined_even_if_capture_allows_them(tmp_path):
    day = datetime.now(timezone.utc).astimezone(ART).date() - timedelta(days=1)
    bar = _bar(day, 4.55, 4.72, 4.39, 4.60, 40711)
    sidecar = _sidecar(tmp_path, [bar, bar])
    result = audit(sidecar, public_get=lambda *_: _page("GGALD", [
        _row(day, "4.55", "4.72", "4.39", "4.60", "40,711.00")]))
    assert result["counts"]["ohlc_range_conflict_rows"] == 0
    assert result["counts"]["duplicate_day_rows"] == 2
    assert result["counts"]["quarantined_rows"] == 2
    assert all(row["reason"] == "DUPLICATE_DAY" for row in result["quarantine_sample"])


def test_old_sidecar_forged_ready_is_rejected_before_public_get(tmp_path):
    day = datetime.now(timezone.utc).astimezone(ART).date() - timedelta(days=1)
    sidecar = _sidecar(tmp_path, [_bar(day, 4.55, 4.72, 4.39, 4.30, 40711)])
    with sqlite3.connect(sidecar) as db:
        db.execute("UPDATE fresh_captures_rc6 SET status='STAGED_REVIEW_REQUIRED'")
    called = []
    with pytest.raises(ValueError, match="FRESH_VERIFY_STATUS_INCONSISTENT"):
        audit(sidecar, public_get=lambda *_: called.append(True))
    assert not called


def test_receipt_private_exclusive_value_free_and_no_legacy_write(tmp_path, monkeypatch):
    import scripts.rc6_iol_mcp_history_reconcile as module
    day = datetime.now(timezone.utc).astimezone(ART).date() - timedelta(days=1)
    sidecar = _sidecar(tmp_path, [_bar(day, 4.55, 4.72, 4.39, 4.30, 40711)])
    legacy = tmp_path / "market_history.db"
    legacy.write_bytes(b"unchanged")
    receipt = tmp_path / "receipt.json"
    synced = []
    real_fsync = os.fsync

    def checked_fsync(fd):
        mode = os.fstat(fd).st_mode
        synced.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", checked_fsync)
    result = write_receipt(sidecar, receipt, public_get=lambda *_: _page("GGALD", [
        _row(day, "4.55", "4.72", "4.39", "4.30", "40,711.00")]))
    assert synced == ["file", "directory"]
    assert json.loads(receipt.read_text()) == result
    assert receipt.stat().st_mode & 0o777 == 0o600
    assert legacy.read_bytes() == b"unchanged"
    assert '"open"' not in receipt.read_text() and '"close"' not in receipt.read_text()
    assert '"adjusted_close"' not in receipt.read_text()
    with pytest.raises(ValueError, match="FRESH_GENERATION_ALREADY_EXISTS"):
        write_receipt(sidecar, receipt, public_get=lambda *_: b"unused")
    with pytest.raises(ValueError, match="FRESH_LEGACY_DESTINATION_FORBIDDEN"):
        write_receipt(sidecar, legacy, public_get=lambda *_: b"unused")
    assert legacy.read_bytes() == b"unchanged"


def test_public_url_exact_and_no_arbitrary_external_host():
    assert public_url("GGALD", "BCBA") == (
        "https://iol.invertironline.com/Titulo/DatosHistoricos?mercado=BCBA&simbolo=GGALD")
    with pytest.raises(ValueError, match="IOL_PUBLIC_IDENTITY_INVALID"):
        public_url("GGALD&account=123", "BCBA")


def test_exact_public_get_uses_one_account_free_request_and_rejects_redirect(monkeypatch):
    import scripts.rc6_iol_mcp_history_reconcile as module
    day = datetime(2026, 3, 27).date()
    html = _page("GGALD", [_row(day, "4.55", "4.72", "4.39", "4.30", "40,711.00")])
    calls = []

    class Response:
        status = 200
        headers = Message()
        headers.add_header("Content-Type", "text/html; charset=utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return public_url("GGALD", "BCBA")

        def read(self, size):
            assert size == module.PUBLIC_LIMIT_BYTES + 1
            return html

    class Opener:
        def open(self, request, *, timeout):
            calls.append(request)
            assert timeout == 10
            return Response()

    monkeypatch.setattr(module, "build_opener", lambda *_: Opener())
    assert fetch_public_history("GGALD", "BCBA") == html
    assert len(calls) == 1 and calls[0].full_url == public_url("GGALD", "BCBA")
    assert calls[0].get_method() == "GET"
    assert not any(name.lower() in {"authorization", "cookie"}
                   for name in calls[0].headers)
    with pytest.raises(ValueError, match="IOL_PUBLIC_REDIRECT_FORBIDDEN"):
        module._NoRedirect().redirect_request(None, None, 302, None, None)
