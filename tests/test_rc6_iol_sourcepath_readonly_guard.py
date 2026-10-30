"""Run the actual inline NoRefresh class from the source-path Actions workflow.

The workflow must remain observational even when the stored OAuth token is
expired or the MCP later responds with 401.  No tests contact a provider.
"""

import io
import json
import textwrap
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

import iol_mcp_readonly_adapter_rc6 as adapter


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/rc6-consolidated-iol-sourcepath-readonly.yml"


def _real_workflow_client_class():
    source = WORKFLOW.read_text(encoding="utf-8")
    begin = source.index("          class NoRefresh(OAuthStoreReadOnlyMCP):")
    end = source.index("          client=NoRefresh(", begin)
    inline = textwrap.dedent(source[begin:end])
    namespace = {"OAuthStoreReadOnlyMCP": adapter.OAuthStoreReadOnlyMCP,
                 "IOLMCPError": adapter.IOLMCPError}
    exec(compile(inline, str(WORKFLOW), "exec"), namespace)
    cls = namespace["NoRefresh"]
    assert cls._refresh_if_due is not adapter.OAuthStoreReadOnlyMCP._refresh_if_due
    assert cls._refresh_after_401 is not adapter.OAuthStoreReadOnlyMCP._refresh_after_401
    return cls


def _store(tmp_path, *, expired):
    path = tmp_path / "oauth.json"
    value = {
        "issuer": "https://mcp.example/rpc",
        "token_endpoint": "https://auth.example/token",
        "token_obtained_at_epoch": time.time() - (3600 if expired else 0),
        "client_registration": {"client_id": "fixture-client"},
        "tokens": {"access_token": "fixture-access", "refresh_token": "fixture-refresh",
                   "expires_in": 900},
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class Response:
    def __init__(self, value, headers=None):
        self.data = json.dumps(value).encode()
        self.headers = headers or {}

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_expired_store_aborts_before_network_refresh_or_store_write(tmp_path, monkeypatch):
    NoRefresh = _real_workflow_client_class()
    store = _store(tmp_path, expired=True)
    before, modified = store.read_bytes(), store.stat().st_mtime_ns
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("network forbidden"))

    with pytest.raises(adapter.IOLMCPError) as raised:
        NoRefresh(store).list_tools()

    assert str(raised.value) == "IOL_MCP_REFRESH_REQUIRED_READONLY_PROBE"
    assert raised.value.status_code is None  # No HTTP status was observed.
    assert store.read_bytes() == before and store.stat().st_mtime_ns == modified
    assert not (tmp_path / "oauth.json.refresh.lock").exists()


def test_fresh_store_still_discovers_tools_without_token_endpoint_call(tmp_path, monkeypatch):
    NoRefresh = _real_workflow_client_class()
    store = _store(tmp_path, expired=False)
    before, modified = store.read_bytes(), store.stat().st_mtime_ns
    methods = []

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://mcp.example/rpc"
        body = json.loads(request.data)
        methods.append(body["method"])
        if body["method"] == "initialize":
            return Response({"result": {}}, {"Mcp-Session-Id": "session"})
        assert body["method"] == "tools/list"
        return Response({"result": {"tools": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert NoRefresh(store).list_tools() == {"tools": []}
    assert methods == ["initialize", "tools/list"]
    assert store.read_bytes() == before and store.stat().st_mtime_ns == modified
    assert not (tmp_path / "oauth.json.refresh.lock").exists()


def test_fresh_store_http_401_never_enters_reactive_refresh(tmp_path, monkeypatch):
    NoRefresh = _real_workflow_client_class()
    store = _store(tmp_path, expired=False)
    before = store.read_bytes()
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request.full_url)
        assert request.full_url == "https://mcp.example/rpc"
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", Message(), io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(adapter.IOLMCPError) as raised:
        NoRefresh(store).list_tools()

    assert str(raised.value) == "IOL_MCP_REFRESH_REQUIRED_READONLY_PROBE"
    assert raised.value.status_code == 401  # An actual MCP 401, not synthetic expiry.
    assert requests == ["https://mcp.example/rpc"]
    assert store.read_bytes() == before
    assert not (tmp_path / "oauth.json.refresh.lock").exists()
