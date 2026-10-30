"""Regression contract for the one-time OAuth loopback helper (offline only)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import urllib.parse
from pathlib import Path

import pytest


FILE = Path(__file__).resolve().parents[1] / "scripts/rc6_iol_oauth_loopback_rearm.py"
SPEC = importlib.util.spec_from_file_location("one_time_iol_rearm_test", FILE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture()
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, "STORE_DIR", tmp_path)
    monkeypatch.setattr(MODULE, "STORE", tmp_path / "store.json")
    monkeypatch.setattr(MODULE, "PENDING", tmp_path / "pending.json")
    monkeypatch.setattr(MODULE, "STATUS", tmp_path / "status.json")
    database = tmp_path / "paper.db"
    conn = sqlite3.connect(database)
    conn.execute("CREATE TABLE observer_state (id INTEGER, mode TEXT, real_orders_sent INTEGER)")
    conn.execute("INSERT INTO observer_state VALUES (1, 'PRODUCTION_PAPER', 0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(MODULE, "PAPER_DB", str(database))
    return tmp_path


def _pending():
    return {
        "issuer": MODULE.ISSUER,
        "client_registration": {"client_id": "fixture-client"},
        "pkce_verifier": "fixture-verifier",
        "pkce_challenge": "fixture-challenge",
        "oauth_state": "fixture-state",
        "expires_at_epoch": 2**32,
    }


def test_authorization_redirect_is_loopback_pkce_and_no_credentials():
    link = MODULE.create_authorization(_pending())
    parsed = urllib.parse.urlsplit(link)
    params = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "mcp.invertironline.com"
    assert parsed.path == "/authorize"
    assert params["redirect_uri"] == ["http://127.0.0.1:8765/callback"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"] == ["fixture-state"]
    assert "fixture-verifier" not in link
    assert "access_token" not in link


def test_paper_gate_refuses_nonpaper_and_nonzero_orders(isolated):
    MODULE.paper_safe()
    conn = sqlite3.connect(MODULE.PAPER_DB)
    conn.execute("UPDATE observer_state SET real_orders_sent=1")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="PAPER/0"):
        MODULE.paper_safe()


def test_prepare_stages_without_overwriting_old_token_store(isolated, monkeypatch, capsys):
    old = {"tokens": {"access_token": "old-value", "refresh_token": "old-refresh"}}
    MODULE.atomic_json(MODULE.STORE, old)
    original = MODULE.STORE.read_bytes()
    monkeypatch.setattr(MODULE, "PORT", 0)
    monkeypatch.setattr(MODULE, "http_json", lambda url, body: (201, {}, {"client_id": "new-client"}))

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, address):
            assert address == ("127.0.0.1", 0)

    monkeypatch.setattr(MODULE.socket, "socket", Socket)

    class Child:
        pid = 744

        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        MODULE.atomic_json(MODULE.STATUS, {"phase": "ready", "pid": 744})
        return Child()

    monkeypatch.setattr(MODULE.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(MODULE.time, "sleep", lambda *_: None)
    assert MODULE.prepare() == 0
    assert MODULE.STORE.read_bytes() == original
    pending = MODULE.read_json(MODULE.PENDING)
    assert pending["client_registration"]["client_id"] == "new-client"
    assert pending["pkce_verifier"]
    assert "LOCAL_URL=http://127.0.0.1:8765/start" in capsys.readouterr().out


def test_readonly_mcp_tools_are_required_and_trading_tools_forbidden(monkeypatch):
    calls = []

    def fake_http(_url, body, _headers):
        calls.append(body["method"])
        if len(calls) == 1:
            return 200, {}, {"result": {"protocolVersion": "2025-06-18"}}
        return 200, {}, {"result": {"tools": [{"name": "place_order"}]}}

    monkeypatch.setattr(MODULE, "http_json", fake_http)
    with pytest.raises(RuntimeError, match="SOLO LECTURA"):
        MODULE.mcp_smoke("fictional-test-token")
    calls.clear()

    def safe_http(_url, body, _headers):
        calls.append(body["method"])
        if len(calls) == 1:
            return 200, {}, {"result": {"protocolVersion": "2025-06-18"}}
        return 200, {}, {"result": {"tools": [{"name": "get_asset_info"}]}}

    monkeypatch.setattr(MODULE, "http_json", safe_http)
    assert MODULE.mcp_smoke("fictional-test-token") == 1
    assert calls == ["initialize", "tools/list"]


def test_failed_mcp_does_not_replace_existing_store(isolated, monkeypatch):
    MODULE.atomic_json(MODULE.STORE, {"tokens": {"access_token": "old"}})
    original = MODULE.STORE.read_bytes()
    monkeypatch.setattr(MODULE, "mcp_smoke", lambda *_: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(RuntimeError):
        MODULE.persist_success(_pending(), {"access_token": "new", "refresh_token": "new-refresh"})
    assert MODULE.STORE.read_bytes() == original


def test_successful_readonly_mcp_commits_new_tokens_atomically(isolated, monkeypatch):
    MODULE.atomic_json(MODULE.STORE, {"previous_note": "retained", "tokens": {"access_token": "old"}})
    monkeypatch.setattr(MODULE, "mcp_smoke", lambda *_: 15)
    assert MODULE.persist_success(
        _pending(), {"access_token": "new", "refresh_token": "new-refresh", "expires_in": 900}
    ) == 15
    stored = MODULE.read_json(MODULE.STORE)
    assert stored["tokens"]["access_token"] == "new"
    assert stored["tokens"]["refresh_token"] == "new-refresh"
    assert stored["previous_note"] == "retained"
    assert stored["token_endpoint"] == MODULE.ISSUER + "/token"
    assert "oauth_state" not in stored and "pkce_verifier" not in stored
    assert (MODULE.STORE.stat().st_mode & 0o777) == 0o600


def test_sse_mcp_response_is_parsed_without_failing_open():
    assert MODULE.parse_rpc('event: message\ndata: {"result":{"tools":[]}}\n\n') == {
        "result": {"tools": []}
    }
    with pytest.raises(RuntimeError, match="could not be verified"):
        MODULE.parse_rpc("data: never JSON\n")
