"""Authenticated, read-only JSON-RPC adapter for the IOL MCP.

Tokens are read only from the existing root-owned OAuth bootstrap store.  This
module never logs credentials and permits only market-data tools.
"""
from __future__ import annotations

import json
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

ALLOWED_TOOLS = frozenset({"get_asset_info", "get_asset_quote"})
DEFAULT_STORE = Path("/root/.config/porota/iol_mcp_oauth_bootstrap.json")


class IOLMCPError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code, self.retry_after = status_code, retry_after


def _retry_after(headers: dict[str, str]) -> float | None:
    try:
        value = headers.get("Retry-After") or headers.get("retry-after")
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _json_from_mcp_body(body: str) -> dict[str, Any]:
    # Streamable HTTP MCP may return a JSON object or an SSE data frame.
    candidates = [body.strip()]
    candidates.extend(line[5:].strip() for line in body.splitlines() if line.startswith("data:"))
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if isinstance(value.get("error"), dict):
                raise IOLMCPError("IOL_MCP_RPC_ERROR:" + str(value["error"].get("code", "unknown")))
            result = value.get("result", value)
            return result if isinstance(result, dict) else {"content": result}
    raise IOLMCPError("IOL_MCP_INVALID_RESPONSE")


class OAuthStoreReadOnlyMCP:
    """Small session client.  It owns no persistence and makes no trading call."""

    def __init__(self, store: Path | str = DEFAULT_STORE, *, timeout_seconds: int = 20) -> None:
        self.store, self.timeout_seconds = Path(store), timeout_seconds
        self._request_id = 0
        self._headers: dict[str, str] | None = None

    def _load_headers(self) -> dict[str, str]:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
            token = (data.get("tokens") or {}).get("access_token")
            issuer = data.get("issuer")
        except (OSError, json.JSONDecodeError) as exc:
            raise IOLMCPError("IOL_MCP_OAUTH_STORE_UNAVAILABLE") from exc
        if not isinstance(token, str) or not token or not isinstance(issuer, str) or not issuer:
            raise IOLMCPError("IOL_MCP_OAUTH_NOT_READY")
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream", "_issuer": issuer}

    def _rpc(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        headers = self._headers or self._load_headers()
        self._headers = headers
        self._request_id += 1
        issuer = headers["_issuer"]
        outgoing = {key: value for key, value in headers.items() if key != "_issuer"}
        request = urllib.request.Request(
            issuer,
            data=json.dumps({"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}).encode(),
            headers=outgoing,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                response_headers = dict(response.headers)
                return _json_from_mcp_body(response.read().decode(errors="replace")), response_headers
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise IOLMCPError("IOL_MCP_HTTP_" + str(exc.code), status_code=exc.code,
                              retry_after=_retry_after(dict(exc.headers))) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IOLMCPError("IOL_MCP_TRANSPORT_UNAVAILABLE") from exc

    def _initialize(self) -> None:
        _, headers = self._rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "porota-rc6-iol-shadow", "version": "1.0"},
        })
        session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        if session_id:
            assert self._headers is not None
            self._headers["Mcp-Session-Id"] = session_id

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in ALLOWED_TOOLS:
            raise PermissionError("IOL_SHADOW_TOOL_DENIED:" + tool_name)
        if self._headers is None:
            self._initialize()
        result, _ = self._rpc("tools/call", {"name": tool_name, "arguments": dict(arguments)})
        return result
