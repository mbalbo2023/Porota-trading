import io
import json
from email.message import Message
from pathlib import Path
import time
import urllib.error
import urllib.request

import pytest

import iol_mcp_readonly_adapter_rc6 as adapter


class Response:
    def __init__(self, payload, headers=None):
        self.payload = b"" if payload is None else json.dumps(payload).encode()
        self.headers = headers or {}

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_401_refreshes_once_discovers_endpoint_and_reinitializes(tmp_path, monkeypatch):
    store = Path(tmp_path) / "oauth.json"
    store.write_text(json.dumps({
        "issuer": "https://mcp.example/rpc",
        "client_registration": {"client_id": "client"},
        "tokens": {"access_token": "expired", "refresh_token": "refresh"},
    }))
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        url = request.full_url
        if url == "https://mcp.example/rpc" and request.get_header("Authorization") == "Bearer expired":
            headers = Message()
            headers["WWW-Authenticate"] = 'Bearer resource_metadata="https://mcp.example/resource"'
            raise urllib.error.HTTPError(url, 401, "Unauthorized", headers, io.BytesIO(b""))
        if url == "https://mcp.example/resource":
            return Response({"authorization_servers": ["https://auth.example"]})
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            return Response({"token_endpoint": "https://auth.example/token"})
        if url == "https://auth.example/token":
            assert b"grant_type=refresh_token" in request.data
            return Response({"access_token": "fresh", "refresh_token": "rotated", "expires_in": 3600})
        if url == "https://mcp.example/rpc":
            body = json.loads(request.data)
            if body["method"] == "initialize":
                return Response({"result": {"protocolVersion": adapter.MCP_PROTOCOL_VERSION}},
                                {"Mcp-Session-Id": "session"})
            if body["method"] == "notifications/initialized":
                return Response(None)
            assert request.get_header("Authorization") == "Bearer fresh"
            return Response({"result": {"content": [{"text": '{"unit_price": 123}'}]}})
        raise AssertionError(url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = adapter.OAuthStoreReadOnlyMCP(store)
    assert client.call("get_asset_quote", {"symbol": "YPFD"}) == {"unit_price": 123}
    saved = json.loads(store.read_text())
    assert saved["tokens"]["access_token"] == "fresh"
    assert saved["tokens"]["refresh_token"] == "rotated"
    assert len([r for r in calls if r.full_url == "https://auth.example/token"]) == 1
    assert {request.get_header("User-agent") for request in calls} == {"Porota-Trading-RC6/1.0 (IOL MCP read-only)"}


def test_execution_tools_remain_denied(tmp_path):
    client = adapter.OAuthStoreReadOnlyMCP(Path(tmp_path) / "missing.json")
    try:
        client.call("place_order", {})
    except PermissionError as exc:
        assert "IOL_SHADOW_TOOL_DENIED" in str(exc)
    else:
        raise AssertionError("execution tool was not denied")


def test_proactive_refreshes_before_expiry_without_waiting_for_401(tmp_path, monkeypatch):
    store = Path(tmp_path) / "oauth.json"
    now = time.time()
    store.write_text(json.dumps({
        "issuer": "https://mcp.example/rpc",
        "token_endpoint": "https://auth.example/token",
        "token_obtained_at_epoch": now - 3550,
        "client_registration": {"client_id": "client"},
        "tokens": {
            "access_token": "almost-expired",
            "refresh_token": "refresh",
            "expires_in": 3600,
        },
    }))
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        url = request.full_url
        if url == "https://auth.example/token":
            assert b"grant_type=refresh_token" in request.data
            return Response({"access_token": "fresh", "refresh_token": "rotated", "expires_in": 3600})
        if url == "https://mcp.example/rpc":
            assert request.get_header("Authorization") == "Bearer fresh"
            body = json.loads(request.data)
            if body["method"] == "initialize":
                return Response({"result": {"protocolVersion": adapter.MCP_PROTOCOL_VERSION}},
                                {"Mcp-Session-Id": "session"})
            if body["method"] == "notifications/initialized":
                return Response(None)
            assert body["method"] == "tools/list"
            return Response({"result": {"tools": []}})
        raise AssertionError(url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = adapter.OAuthStoreReadOnlyMCP(store)
    assert client.list_tools() == {"tools": []}
    saved = json.loads(store.read_text())
    assert saved["tokens"]["access_token"] == "fresh"
    assert saved["tokens"]["refresh_token"] == "rotated"
    assert saved["token_endpoint"] == "https://auth.example/token"
    assert saved["token_obtained_at_epoch"] >= now
    assert saved["token_expires_at_epoch"] > saved["token_obtained_at_epoch"]
    assert len([r for r in calls if r.full_url == "https://auth.example/token"]) == 1


def test_proactive_refresh_uses_store_mtime_for_legacy_token_store(tmp_path, monkeypatch):
    store = Path(tmp_path) / "oauth.json"
    store.write_text(json.dumps({
        "issuer": "https://mcp.example/rpc",
        "token_endpoint": "https://auth.example/token",
        "client_registration": {"client_id": "client"},
        "tokens": {
            "access_token": "legacy-expired",
            "refresh_token": "refresh",
            "expires_in": 60,
        },
    }))
    old = time.time() - 3600
    import os
    os.utime(store, (old, old))

    def fake_urlopen(request, timeout):
        if request.full_url == "https://auth.example/token":
            return Response({"access_token": "fresh", "expires_in": 3600})
        if request.full_url == "https://mcp.example/rpc":
            assert request.get_header("Authorization") == "Bearer fresh"
            body = json.loads(request.data)
            if body["method"] == "initialize":
                return Response({"result": {"protocolVersion": adapter.MCP_PROTOCOL_VERSION}},
                                {"Mcp-Session-Id": "session"})
            if body["method"] == "notifications/initialized":
                return Response(None)
            return Response({"result": {"tools": []}})
        raise AssertionError(request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = adapter.OAuthStoreReadOnlyMCP(store)
    assert client.list_tools() == {"tools": []}
    saved = json.loads(store.read_text())
    assert saved["tokens"]["refresh_token"] == "refresh"
    assert saved["tokens"]["access_token"] == "fresh"


def test_fresh_token_does_not_refresh_early(tmp_path, monkeypatch):
    store = Path(tmp_path) / "oauth.json"
    store.write_text(json.dumps({
        "issuer": "https://mcp.example/rpc",
        "token_endpoint": "https://auth.example/token",
        "token_obtained_at_epoch": time.time(),
        "client_registration": {"client_id": "client"},
        "tokens": {
            "access_token": "fresh-enough",
            "refresh_token": "refresh",
            "expires_in": 3600,
        },
    }))

    def fake_urlopen(request, timeout):
        assert request.full_url != "https://auth.example/token"
        assert request.get_header("Authorization") == "Bearer fresh-enough"
        body = json.loads(request.data)
        if body["method"] == "initialize":
            return Response({"result": {"protocolVersion": adapter.MCP_PROTOCOL_VERSION}},
                            {"Mcp-Session-Id": "session"})
        if body["method"] == "notifications/initialized":
            return Response(None)
        return Response({"result": {"tools": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = adapter.OAuthStoreReadOnlyMCP(store)
    assert client.list_tools() == {"tools": []}


def test_token_with_one_hundred_seconds_remaining_does_not_rotate(tmp_path, monkeypatch):
    store = Path(tmp_path) / "oauth.json"
    store.write_text(json.dumps({
        "issuer": "https://mcp.example/rpc",
        "token_endpoint": "https://auth.example/token",
        "token_obtained_at_epoch": time.time() - 3500,
        "client_registration": {"client_id": "client"},
        "tokens": {
            "access_token": "still-valid",
            "refresh_token": "refresh",
            "expires_in": 3600,
        },
    }))

    def fake_urlopen(request, timeout):
        assert request.full_url != "https://auth.example/token"
        assert request.get_header("Authorization") == "Bearer still-valid"
        body = json.loads(request.data)
        if body["method"] == "initialize":
            return Response({"result": {"protocolVersion": adapter.MCP_PROTOCOL_VERSION}},
                            {"Mcp-Session-Id": "session"})
        if body["method"] == "notifications/initialized":
            return Response(None)
        return Response({"result": {"tools": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert adapter.OAuthStoreReadOnlyMCP(store).list_tools() == {"tools": []}


@pytest.mark.parametrize('status', [400, 401, 429, 503])
def test_refresh_http_failure_exposes_only_status_not_body_or_tokens(
        tmp_path,monkeypatch,status):
    store=tmp_path/'oauth.json'
    original={'issuer':'https://mcp.example/rpc',
              'token_endpoint':'https://auth.example/token',
              'token_obtained_at_epoch':time.time()-1000,
              'client_registration':{'client_id':'client'},
              'tokens':{'access_token':'secret-access','refresh_token':'secret-refresh',
                        'expires_in':900}}
    store.write_text(json.dumps(original))
    def fake_urlopen(request,timeout):
        assert request.full_url=='https://auth.example/token'
        assert b'secret-refresh' in request.data
        headers=Message()
        headers['Retry-After']='1'
        raise urllib.error.HTTPError(request.full_url,status,'secret-url',headers,
                                     io.BytesIO(b'secret-response-body'))
    monkeypatch.setattr(urllib.request,'urlopen',fake_urlopen)
    with pytest.raises(adapter.IOLMCPError) as raised:
        adapter.OAuthStoreReadOnlyMCP(store).list_tools()
    exc=raised.value
    assert str(exc)=='IOL_MCP_OAUTH_REFRESH_FAILED'
    assert exc.status_code==status and exc.failure_class=='HTTP'
    assert exc.retry_after==1 and exc.__cause__ is None
    assert 'secret' not in str(exc)
    assert json.loads(store.read_text())==original


@pytest.mark.parametrize("last_page,mode,reason", [
    ("market", "read_only", None),
    ("market", "auto", "IOL_MCP_ACCESS_MODE_UNVERIFIED"),
    ("execution", "read_only", "IOL_MCP_NON_READ_TOOL_INVENTORY"),
    ("repeat_cursor", "read_only", "IOL_MCP_TOOL_CURSOR_INVALID"),
])
def test_staged_token_checks_the_complete_session_before_persisting(
        monkeypatch, last_page, mode, reason):
    seen = []

    def fake_urlopen(request, timeout):
        assert request.full_url == adapter.IOL_MCP_ISSUER
        assert request.get_header("Authorization") == "Bearer fixture-token"
        body = json.loads(request.data)
        method = body["method"]
        seen.append(method)
        if method == "initialize":
            assert body["params"]["protocolVersion"] == adapter.MCP_PROTOCOL_VERSION
            return Response({"result": {"protocolVersion": "2025-03-26"}}, {"MCP-Session-ID": "session"})
        assert request.get_header("Mcp-protocol-version") == "2025-03-26"
        assert request.get_header("Mcp-session-id") == "session"
        if method == "notifications/initialized":
            assert "id" not in body
            return Response(None)
        if method == "tools/list":
            if not body["params"]:
                return Response({"result": {"tools": [{"name": "get_access_mode"}], "nextCursor": "page2"}})
            assert body["params"] == {"cursor": "page2"}
            page = {"tools": [{"name": "get_asset_quote"}, {"name": "get_price_history"}]}
            if last_page == "execution":
                page["tools"].append({"name": "place_order"})
            if last_page == "repeat_cursor":
                page["nextCursor"] = "page2"
            return Response({"result": page})
        assert method == "tools/call" and body["params"] == {"name": "get_access_mode", "arguments": {}}
        return Response({"result": {"structuredContent": {"mode": mode, "saved_mode": mode}}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    if reason:
        with pytest.raises(adapter.IOLMCPError, match="^" + reason + "$"):
            adapter.verify_read_only_token("fixture-token")
    else:
        assert adapter.verify_read_only_token("fixture-token") == {"mode": "read_only", "read_only_tool_count": 3}
    assert seen[:4] == ["initialize", "notifications/initialized", "tools/list", "tools/list"]
    assert seen[4:] == (["tools/call"] if last_page == "market" else [])
