from datetime import datetime, timezone
from io import BytesIO
import json
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from iol_rest_readonly_rc6 import IOLRestError, IOLRestReadOnly
from iol_shadow_collector_rc6 import run_batch
from iol_shadow_decision_input_rc6 import read_for_decision
from rc6_broker_parity_evidence import iol_structured_records
from rc6_source_consolidation import consolidate
from scripts import rc6_iol_rest_units as units
from scripts import rc6_source_cycle_dispatch as dispatch
from scripts.porota_rootless_host import database_snapshot


NOW = datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc)
QUOTE_AT = "2026-11-24T13:59:48+00:00"
BOOK_AT = "2026-11-24T13:59:50+00:00"
ASSET = {"symbol": "GGAL", "market": "BCBA", "currency": "ARS", "asset_type": "ACCIONES"}


def test_rest_collects_explicit_clocks_and_keeps_legacy_rows_historical(tmp_path):
    cache = tmp_path / "iol_shadow_latest.json"
    cache.write_text(json.dumps({"source": "IOL_MCP", "symbols": [{"symbol": "YPFD", "state": "READY",
                     "captured_at": NOW.isoformat(), "quote": {"last": 30}}]}))
    config = tmp_path / "fixture.env"
    config.write_text("IOL_ENABLED=true\nIOL_USERNAME=fixture\nIOL_PASSWORD=fixture\n")
    calls = []
    def open_request(request, timeout):
        path = urlsplit(request.full_url)
        assert path.scheme == "https" and path.netloc == "api.iolinversiones.com"
        calls.append((request.get_method(), path.path))
        if path.path == "/auth/token":
            assert json.loads(request.data)["grant_type"] == "password"
            data = {"access_token": "fixture-access-token", "refresh_token": "fixture-refresh-token", "expires_in": 900}
        else:
            assert request.get_header("Authorization") == "Bearer fixture-access-token"
            identity = {"symbol": "GGAL", "market": "BCBA", "settlement_term": "T1"}
            data = {
                "/broker/market-data/GGAL/quote": {**identity, "last_updated": QUOTE_AT, "prices": {"current": 100}},
                "/broker/market-data/GGAL/depth": {**identity, "last_updated": BOOK_AT,
                    "bids": [{"price": 99, "size": 5}], "asks": [{"price": 101, "size": 2}]},
                "/broker/assets/GGAL": ASSET,
            }[path.path]
        return BytesIO(json.dumps({"data": data, "error": None}).encode())

    governor = SimpleNamespace(acquire=lambda: None, calls_total=0, calls_429=0)
    client = IOLRestReadOnly(tmp_path / "private", config=config, opener=SimpleNamespace(open=open_request),
                             governor=governor)
    out = run_batch(["GGAL"], client, root=tmp_path, governor=governor, now=lambda: NOW)
    row, prior = out["symbols"]
    assert out["source"] == row["source"] == "IOL_REST"
    assert (row["quote"]["provider_observed_at"], row["quote"]["provider_book_at"]) == (QUOTE_AT, BOOK_AT)
    assert calls == [("POST", "/auth/token"), ("GET", "/broker/market-data/GGAL/quote"),
                     ("GET", "/broker/market-data/GGAL/depth"), ("GET", "/broker/assets/GGAL")]
    assert client.store.stat().st_mode & 0o777 == 0o600
    assert "fixture-access-token" not in cache.read_text()
    assert prior["source"] == "IOL_MCP" and prior["state"] == "CACHE_STALE"
    assert read_for_decision("GGAL", root=tmp_path, now=NOW)["age_seconds"] == 12
    with pytest.raises(IOLRestError, match="IOL_REST_CAPABILITY_UNVERIFIED"):
        client.call("place_order", {})
    with pytest.raises(IOLRestError, match="IOL_REST_MARKET_IDENTITY_UNSUPPORTED"):
        client.call("get_asset_info", {"symbol": "../account"})


def test_rest_fallback_needs_exact_ppi_and_field_native_clock():
    identity = {"symbol": "GGAL", "family": "ACCIONES", "market": "BYMA", "currency": "ARS", "term": "T1"}
    ppi = {**identity, "last": 100, "provider_observed_at": QUOTE_AT, "captured_at": NOW.isoformat()}
    rest = {**identity, "source": "IOL_REST", "bid": 99, "ask": 101, "last": 100,
            "provider_observed_at": QUOTE_AT, "captured_at": NOW.isoformat()}
    fields = consolidate([ppi], [rest], as_of=NOW)["rows"][0]["validated_effective_fields"]
    assert fields["last"] == {"value": 100, "source": "PPI"}
    assert fields["bid"] == {"value": None, "source": None}
    rest["provider_book_at"] = BOOK_AT
    fields = consolidate([ppi], [rest], as_of=NOW)["rows"][0]["validated_effective_fields"]
    assert fields["bid"] == {"value": 99, "source": "IOL_REST"}
    fields = consolidate([], [rest], as_of=NOW)["rows"][0]["validated_effective_fields"]
    assert fields["bid"] == {"value": None, "source": None}
    for row in (ppi, rest):
        row.update(symbol="GD30", family="BONOS")
    assert consolidate([ppi], [rest], as_of=NOW)["rows"][0]["validated_effective_fields"]["bid"]["value"] is None


@pytest.mark.parametrize(("transport", "expected"), [("IOL_MCP", "IOL_PREVIOUS_TRANSPORT_REFERENCE"),
                                                   ("IOL_REST", "IOL_REST_REPO_BOOK_CLOCK_UNVERIFIED")])
def test_reference_rates_cannot_inherit_new_rest_capture_clock(transport, expected):
    rate = {"transport": transport, "term_days": 1, "tna": 30, "minimum_amount": 100000}
    payload = {"source": "IOL_REST", "schema": "rc6-iol-family-reference-v1",
               "refreshed_at": NOW.isoformat(), "cauciones": {"ARS": [rate]}}
    result = iol_structured_records(payload, [])
    assert result["accepted"] == []
    assert result["blocked"][0]["blocker"] == expected


def test_source_dispatch_requires_release_database_identity_before_children(tmp_path):
    db, market = tmp_path / "paper.db", tmp_path / "market"
    market.mkdir()
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE observer_state(id, mode, real_orders_sent);
            INSERT INTO observer_state VALUES(1, 'PRODUCTION_PAPER', 0);
            CREATE TABLE paper_workspace(id, dataset_id);
            INSERT INTO paper_workspace VALUES(1, 'fixture-dataset');
        """)
    sealed = database_snapshot(db)
    calls = []
    def runner(command, **kwargs):
        name, _, _, output = dispatch.STAGES[len(calls)]
        calls.append(name)
        assert kwargs["env"]["POROTA_IOL_OPERATIONAL_DB"] == str(db)
        (market / output).write_text("{}")
        return SimpleNamespace(returncode=0, stdout="IOL_SHADOW_COLLECTION=COMPLETE READY=1")
    kwargs = {"now": NOW, "db": db, "market_root": market, "lock": tmp_path / "lock", "runner": runner}
    with pytest.raises(dispatch.SourceCycleBlocked, match="PAPER_DATABASE_IDENTITY_UNVERIFIED"):
        dispatch.run_cycle(**kwargs, release_database={**sealed, "inode": 0})
    assert calls == []
    assert dispatch.run_cycle(**kwargs, release_database=sealed)["status"] == "COMPLETE"
    assert calls == ["iol", "public", "evidence"]


def test_iol_unit_cutover_is_idempotent_and_preserves_timers(tmp_path, monkeypatch):
    monkeypatch.setattr(units, "UNIT_ROOT", tmp_path)
    monkeypatch.setattr(units, "checked_path", lambda *a, **kw: None)
    account = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_name="porotaadmin", pw_dir="/home/porotaadmin")
    commands, updated = [], False
    def control(*args):
        nonlocal updated
        commands.append(args)
        if args == ("daemon-reload",):
            updated = True
    def properties(unit, keys):
        if keys == units.TIMER_KEYS:
            return {"LoadState": "loaded", "ActiveState": "active", "Triggers": unit.removesuffix(".timer") + ".service"}
        path = tmp_path / (unit + ".d") / units.OVERRIDE
        return {"LoadState": "loaded", "FragmentPath": str(tmp_path / unit), "NeedDaemonReload": "no",
                "Conditions": "", "DropInPaths": str(path) if updated else "", "User": account.pw_name if updated else "",
                "Group": "1000" if updated else "", "ExecStart": "{ argv[]=" + units.launcher_command(account, unit) + " ; }"
                         if updated else "/opt/porota-trading" + units.LEGACY[unit][0]}
    monkeypatch.setattr(units, "systemctl", control)
    monkeypatch.setattr(units, "properties", properties)
    assert units.reconcile(account, install=False)["state"] == "PREFLIGHT_OK"
    assert commands == []
    assert units.reconcile(account, install=True)["state"] == "EFFECTIVE"
    assert commands == [("stop", *units.LEGACY), ("daemon-reload",)]
    assert all((tmp_path / (unit + ".d") / units.OVERRIDE).read_bytes() == units.definition(account, unit)
               for unit in units.LEGACY)
    commands.clear()
    assert units.reconcile(account, install=True)["state"] == "EFFECTIVE"
    assert commands == []
