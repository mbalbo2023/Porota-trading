import io
import json
from email.message import Message
from pathlib import Path
import urllib.error
import urllib.request

import iol_mcp_readonly_adapter_rc6 as adapter


class Response:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode()
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
                return Response({"result": {}}, {"Mcp-Session-Id": "session"})
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


def test_execution_tools_remain_denied(tmp_path):
    client = adapter.OAuthStoreReadOnlyMCP(Path(tmp_path) / "missing.json")
    try:
        client.call("place_order", {})
    except PermissionError as exc:
        assert "IOL_SHADOW_TOOL_DENIED" in str(exc)
    else:
        raise AssertionError("execution tool was not denied")
