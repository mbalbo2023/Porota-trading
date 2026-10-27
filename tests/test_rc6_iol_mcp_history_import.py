"""Adversarial, offline tests for a single IOL MCP evidence import."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.rc6_iol_mcp_history_import import import_response, main
from scripts.rc6_history_fresh_verify import verify

ART = ZoneInfo("America/Argentina/Buenos_Aires")
TARGET = ["GGAL", "ACCIONES", "BCBA", "ARS", "T1"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("NETWORK_FORBIDDEN_IN_IOL_MCP_IMPORT")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _bar(day, close=100):
    local = datetime.combine(day, time(12), tzinfo=ART)
    return {"date": day.isoformat(), "time": int(local.timestamp()),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 123}


def _documents(tmp_path, bars=None):
    clock = datetime.now(timezone.utc)
    today = clock.astimezone(ART).date()
    start = today - timedelta(days=364)
    # The day before import is always in the requested window and never future.
    sample = bars if bars is not None else [_bar(today - timedelta(days=1))]
    manifest = {"version": 1, "declared_source": "IOL_MCP",
                "request": {"symbol": "GGAL", "market": "BCBA",
                            "from_date": start.isoformat(), "to_date": today.isoformat()},
                "target": TARGET.copy()}
    manifest_path = tmp_path / "manifest.json"
    response_path = tmp_path / "mcp-response.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    response_path.write_text(json.dumps({"bars": sample}), encoding="utf-8")
    return manifest_path, response_path, manifest, clock


def _import(tmp_path, manifest_path, response_path, clock):
    return import_response(manifest_path, response_path,
                           tmp_path / "fresh.sqlite", tmp_path / "receipt.json", now=clock)


def test_bounded_245_bar_export_is_private_evidence_without_historical_authority(tmp_path):
    today = datetime.now(timezone.utc).astimezone(ART).date()
    bars = [_bar(today - timedelta(days=day), close=100 + day) for day in range(1, 246)]
    manifest_path, response_path, _manifest, clock = _documents(tmp_path, bars)
    legacy = tmp_path / "market_history.db"
    legacy.write_bytes(b"legacy-not-touched")
    result = _import(tmp_path, manifest_path, response_path, clock)
    receipt_path, sidecar_path = tmp_path / "receipt.json", tmp_path / "fresh.sqlite"
    receipt = json.loads(receipt_path.read_text())
    assert result == receipt and result["state"] == "EVIDENCE_ONLY_NO_GO"
    assert result["source"] == "IOL_CONNECTOR"
    assert result["results"][0]["retained_rows"] == 245
    assert result["results"][0]["provider_currency_status"] == "REQUEST_ONLY"
    assert result["results"][0]["price_basis_status"] == "UNVERIFIED"
    assert result["local_fetch_invocations"] == 1 and result["mcp_requests_during_import"] == 0
    assert result["history_v2_writes"] == 0 and result["execution_allowed"] is False
    assert result["first_known_at_semantics"] == "LOCAL_IMPORT_OBSERVATION_ONLY"
    for field in ("provider_origin_attested", "request_identity_provider_attested",
                  "transport_raw_hash_verified", "provider_original_pit_verified",
                  "settlement_verified_from_bars", "currency_verified_from_bars",
                  "adjustment_basis_verified"):
        assert result[field] is False
    assert "generation_path" not in result and '"close":' not in receipt_path.read_text()
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    assert sidecar_path.stat().st_mode & 0o777 == 0o600
    assert legacy.read_bytes() == b"legacy-not-touched"
    assert verify(sidecar_path)["retained_rows"] == 245
    with sqlite3.connect(sidecar_path) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "history_canonical_v2" not in names
        capture = db.execute("SELECT first_known_at,provider_currency_status,price_basis_status,"
                             "retained_rows_json FROM fresh_captures_rc6").fetchone()
        assert capture[0] >= clock.isoformat() and capture[1:3] == ("REQUEST_ONLY", "UNVERIFIED")
        assert len(json.loads(capture[3])) == 245


@pytest.mark.parametrize("field,replacement,reason", [
    ("symbol", "GGALD", "IOL_MCP_REQUEST_IDENTITY_CONFLICT"),
    ("market", "NYSE", "IOL_MCP_REQUEST_IDENTITY_CONFLICT"),
    ("from_date", "2020-01-01", "IOL_MCP_REQUEST_WINDOW_MISMATCH"),
    ("to_date", "2020-01-02", "IOL_MCP_REQUEST_WINDOW_MISMATCH"),
])
def test_query_identity_and_exact_window_mismatch_fail_before_sidecar(tmp_path, field, replacement, reason):
    manifest_path, response_path, manifest, clock = _documents(tmp_path)
    manifest["request"][field] = replacement
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        _import(tmp_path, manifest_path, response_path, clock)
    assert not (tmp_path / "fresh.sqlite").exists()
    assert not (tmp_path / "receipt.json").exists()


@pytest.mark.parametrize("target_index,replacement,reason", [
    (0, "GGALD", "IOL_MCP_REQUEST_IDENTITY_CONFLICT"),
    (2, "NYSE", "IOL_MCP_REQUEST_IDENTITY_CONFLICT"),
    (3, "UNKNOWN", "FRESH_EXACT_IDENTITY_REQUIRED"),
    (4, "T1;DROP TABLE", "IOL_MCP_IDENTITY_INVALID"),
])
def test_manifest_target_conflicts_or_malformed_identity_fail_closed(tmp_path, target_index, replacement, reason):
    manifest_path, response_path, manifest, clock = _documents(tmp_path)
    manifest["target"][target_index] = replacement
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        _import(tmp_path, manifest_path, response_path, clock)
    assert not (tmp_path / "fresh.sqlite").exists()


@pytest.mark.parametrize("mutation,reason", [
    (lambda row: row.update(time=True), "IOL_MCP_BAR_CLOCK_INVALID"),
    (lambda row: row.update(date="2020-01-01"), "IOL_MCP_BAR_CLOCK_INVALID"),
    (lambda row: row.update(close="100"), "IOL_MCP_BAR_NUMBER_INVALID"),
    (lambda row: row.update(low=999), "IOL_MCP_BAR_RANGE_INVALID"),
    (lambda row: row.update(currency="ARS"), "IOL_MCP_BAR_SHAPE_INVALID"),
    (lambda row: row.update(volume=-1), "IOL_MCP_BAR_NUMBER_INVALID"),
])
def test_malformed_bar_never_creates_sidecar(tmp_path, mutation, reason):
    manifest_path, response_path, _manifest, clock = _documents(tmp_path)
    response = json.loads(response_path.read_text())
    mutation(response["bars"][0])
    response_path.write_text(json.dumps(response), encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        _import(tmp_path, manifest_path, response_path, clock)
    assert not (tmp_path / "fresh.sqlite").exists()


def test_payload_metadata_or_secret_is_rejected_and_never_echoed(tmp_path):
    manifest_path, response_path, _manifest, clock = _documents(tmp_path)
    secret = "Authorization: Bearer NEVER-LOG-ME"
    response_path.write_text(json.dumps({"bars": [], "account": secret}), encoding="utf-8")
    with pytest.raises(ValueError, match="IOL_MCP_RESPONSE_SHAPE_INVALID") as caught:
        _import(tmp_path, manifest_path, response_path, clock)
    assert secret not in str(caught.value)
    assert not (tmp_path / "receipt.json").exists()
    assert not (tmp_path / "fresh.sqlite").exists()


def test_cli_does_not_print_provider_content_or_paths_on_failure(tmp_path, capsys):
    manifest_path, response_path, _manifest, _clock = _documents(tmp_path)
    secret = "Authorization: Bearer NEVER-LOG-ME"
    response_path.write_text(json.dumps({"bars": [], "account": secret}), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        main(["--manifest", str(manifest_path), "--response", str(response_path),
              "--sidecar", str(tmp_path / "fresh.sqlite"),
              "--receipt", str(tmp_path / "receipt.json")])
    printed = capsys.readouterr()
    assert caught.value.code == 2
    assert printed.out == "" and printed.err == "IOL_MCP_RESPONSE_SHAPE_INVALID\n"
    assert secret not in printed.err and str(tmp_path) not in printed.err


def test_conflicting_same_day_export_stays_quarantined(tmp_path):
    today = datetime.now(timezone.utc).astimezone(ART).date()
    day = today - timedelta(days=1)
    manifest_path, response_path, _manifest, clock = _documents(tmp_path,
                                                                 [_bar(day, 100), _bar(day, 101)])
    result = _import(tmp_path, manifest_path, response_path, clock)
    assert result["results"][0]["status"] == "QUARANTINED"
    assert result["results"][0]["reason"] == "FRESH_DUPLICATE_DAY_CONFLICT"
    assert verify(tmp_path / "fresh.sqlite")["quarantined"] == 1


def test_duplicate_json_keys_nonfinite_and_row_budget_are_rejected(tmp_path):
    manifest_path, response_path, _manifest, clock = _documents(tmp_path)
    response_path.write_text('{"bars":[],"bars":[]}', encoding="utf-8")
    with pytest.raises(ValueError, match="IOL_MCP_JSON_DUPLICATE_KEY"):
        _import(tmp_path, manifest_path, response_path, clock)
    response_path.write_text('{"bars":[{"close":NaN}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="IOL_MCP_JSON_NONFINITE"):
        _import(tmp_path, manifest_path, response_path, clock)
    response_path.write_text(json.dumps({"bars": [{}] * 1001}), encoding="utf-8")
    with pytest.raises(ValueError, match="IOL_MCP_RESPONSE_BUDGET_EXCEEDED"):
        _import(tmp_path, manifest_path, response_path, clock)
    assert not (tmp_path / "fresh.sqlite").exists()


def test_aliases_existing_outputs_and_legacy_destinations_are_rejected(tmp_path):
    manifest_path, response_path, _manifest, clock = _documents(tmp_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(response_path)
    with pytest.raises(ValueError, match="IOL_MCP_INPUT_ALIAS_FORBIDDEN"):
        _import(tmp_path, manifest_path, alias, clock)
    hardlink = tmp_path / "hardlink.json"
    os.link(response_path, hardlink)
    with pytest.raises(ValueError, match="IOL_MCP_INPUT_ALIAS_FORBIDDEN"):
        _import(tmp_path, manifest_path, response_path, clock)
    hardlink.unlink()
    existing_receipt = tmp_path / "receipt.json"
    existing_receipt.write_bytes(b"retain")
    with pytest.raises(ValueError, match="FRESH_GENERATION_ALREADY_EXISTS"):
        _import(tmp_path, manifest_path, response_path, clock)
    assert existing_receipt.read_bytes() == b"retain"
    existing_receipt.unlink()
    with pytest.raises(ValueError, match="FRESH_LEGACY_DESTINATION_FORBIDDEN"):
        import_response(manifest_path, response_path, tmp_path / "market_history.db",
                        existing_receipt, now=clock)
    assert not (tmp_path / "fresh.sqlite").exists()
