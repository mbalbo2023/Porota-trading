"""Authenticated read-only JSON-RPC adapter for the IOL MCP.

The adapter permits only market-data tools.  It may refresh the existing OAuth
session after a 401 using the bootstrap's refresh token; it never logs
credentials and never invokes an execution tool.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# These tools are market-data/reference reads only. They cannot access account state
# or execute, validate, cancel, or schedule any transaction.
# These are the only MCP capabilities that can enrich instrument evidence
# without reading account state or invoking an order/validation route.  The
# remaining discovered tools stay inventoried but are deliberately denied.
READ_ONLY_MARKET_TOOLS = frozenset({
    "get_asset_info",
    "get_asset_quote",
    "get_price_history",
    "get_intraday_prices",
    "get_fixed_income_analytics",
    "simulate_fixed_income_by_amount",
    "simulate_fixed_income_by_nominals",
    "get_options_chain",
    "get_caucion_rates",
    "get_caucion_rate",
    "get_caucion_guarantee_assets",
    "get_fci_funds",
    "get_next_corporate_events",
})
FORBIDDEN_ACCOUNT_OR_EXECUTION_TOOLS = frozenset({
    "get_portfolio", "get_balance", "get_ddjj", "get_order_status",
    "get_activities", "validate_order", "buy_ggal_at_50_cents",
    "get_stop_loss_and_take_profit", "validate_caucion",
    "get_fci_funds", "validate_fci_subscription", "validate_fci_redemption",
    "simulate_fixed_income_by_amount", "simulate_fixed_income_by_nominals",
})
ALLOWED_TOOLS = READ_ONLY_MARKET_TOOLS
DEFAULT_STORE = Path("/root/.config/porota/iol_mcp_oauth_bootstrap.json")


class IOLMCPError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None,
        retry_after: float | None = None, response_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.response_headers = response_headers or {}


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
            if isinstance(result, dict):
                for item in result.get("content") or []:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        try:
                            decoded = json.loads(item["text"])
                        except json.JSONDecodeError:
                            continue
                        if isinstance(decoded, dict):
                            return decoded
                return result
            return {"content": result}
    raise IOLMCPError("IOL_MCP_INVALID_RESPONSE")


def _https_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urllib.parse.urlparse(value)
    return value if parsed.scheme == "https" and parsed.netloc else None


def _resource_metadata_url(headers: dict[str, str]) -> str | None:
    challenge = headers.get("WWW-Authenticate") or headers.get("www-authenticate") or ""
    marker = 'resource_metadata="'
    start = challenge.find(marker)
    if start < 0:
        return None
    value = challenge[start + len(marker):].split('"', 1)[0]
    return _https_url(value)


class OAuthStoreReadOnlyMCP:
    """Small session client. It refreshes a stored read-only OAuth session only after 401."""

    def __init__(self, store: Path | str = DEFAULT_STORE, *, timeout_seconds: int = 20) -> None:
        self.store, self.timeout_seconds = Path(store), timeout_seconds
        self._request_id = 0
        self._headers: dict[str, str] | None = None

    def _load_store(self) -> dict[str, Any]:
        try:
            value = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IOLMCPError("IOL_MCP_OAUTH_STORE_UNAVAILABLE") from exc
        if not isinstance(value, dict):
            raise IOLMCPError("IOL_MCP_OAUTH_NOT_READY")
        return value

    def _load_headers(self) -> dict[str, str]:
        data = self._load_store()
        token = (data.get("tokens") or {}).get("access_token")
        issuer = data.get("issuer")
        if not isinstance(token, str) or not token or not isinstance(issuer, str) or not _https_url(issuer):
            raise IOLMCPError("IOL_MCP_OAUTH_NOT_READY")
        return {
            "Authorization": "Bearer " + token, "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream", "_issuer": issuer,
        }

    def _read_json(self, url: str) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=self.timeout_seconds
            ) as response:
                value = json.loads(response.read().decode(errors="replace"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise IOLMCPError("IOL_MCP_OAUTH_DISCOVERY_FAILED") from exc
        if not isinstance(value, dict):
            raise IOLMCPError("IOL_MCP_OAUTH_DISCOVERY_FAILED")
        return value

    def _token_endpoint(self, headers: dict[str, str]) -> str:
        resource_url = _resource_metadata_url(headers)
        if not resource_url:
            raise IOLMCPError("IOL_MCP_OAUTH_REFRESH_UNAVAILABLE")
        resource = self._read_json(resource_url)
        direct = _https_url(resource.get("token_endpoint"))
        if direct:
            return direct
        servers = resource.get("authorization_servers")
        if not isinstance(servers, list):
            raise IOLMCPError("IOL_MCP_OAUTH_REFRESH_UNAVAILABLE")
        for server in servers:
            authority = _https_url(server)
            if not authority:
                continue
            metadata = self._read_json(authority.rstrip("/") + "/.well-known/oauth-authorization-server")
            endpoint = _https_url(metadata.get("token_endpoint"))
            if endpoint:
                return endpoint
        raise IOLMCPError("IOL_MCP_OAUTH_REFRESH_UNAVAILABLE")

    def _write_store(self, data: dict[str, Any]) -> None:
        try:
            mode = self.store.stat().st_mode & 0o777
            fd, temporary = tempfile.mkstemp(prefix=self.store.name + ".", dir=str(self.store.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, self.store)
        except OSError as exc:
            raise IOLMCPError("IOL_MCP_OAUTH_STORE_WRITE_FAILED") from exc

    def _refresh_after_401(self, headers: dict[str, str]) -> None:
        data = self._load_store()
        tokens = data.get("tokens")
        registration = data.get("client_registration")
        if not isinstance(tokens, dict) or not isinstance(registration, dict):
            raise IOLMCPError("IOL_MCP_OAUTH_REFRESH_UNAVAILABLE")
        refresh_token, client_id = tokens.get("refresh_token"), registration.get("client_id")
        if not isinstance(refresh_token, str) or not refresh_token or not isinstance(client_id, str) or not client_id:
            raise IOLMCPError("IOL_MCP_OAUTH_REFRESH_UNAVAILABLE")
        fields: dict[str, str] = {
            "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id,
        }
        secret = registration.get("client_secret")
        auth_method = registration.get("token_endpoint_auth_method")
        request_headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
        if isinstance(secret, str) and secret:
            if auth_method == "client_secret_basic":
                encoded = base64.b64encode((client_id + ":" + secret).encode()).decode()
                request_headers["Authorization"] = "Basic " + encoded
                fields.pop("client_id")
            else:
                fields["client_secret"] = secret
        request = urllib.request.Request(
            self._token_endpoint(headers), data=urllib.parse.urlencode(fields).encode(),
            headers=request_headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                refreshed = json.loads(response.read().decode(errors="replace"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise IOLMCPError("IOL_MCP_OAUTH_REFRESH_FAILED") from exc
        access_token = refreshed.get("access_token") if isinstance(refreshed, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise IOLMCPError("IOL_MCP_OAUTH_REFRESH_FAILED")
        updated_tokens = dict(tokens)
        for key in ("access_token", "refresh_token", "expires_in", "scope", "token_type"):
            if key in refreshed and refreshed[key] is not None:
                updated_tokens[key] = refreshed[key]
        data["tokens"] = updated_tokens
        self._write_store(data)
        self._headers = None

    def _rpc(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        headers = self._headers or self._load_headers()
        self._headers = headers
        self._request_id += 1
        outgoing = {key: value for key, value in headers.items() if key != "_issuer"}
        request = urllib.request.Request(
            headers["_issuer"],
            data=json.dumps({"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}).encode(),
            headers=outgoing, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return _json_from_mcp_body(response.read().decode(errors="replace")), dict(response.headers)
        except urllib.error.HTTPError as exc:
            exc.read()  # Consume the response without ever propagating its body.
            response_headers = dict(exc.headers)
            raise IOLMCPError(
                "IOL_MCP_HTTP_" + str(exc.code), status_code=exc.code,
                retry_after=_retry_after(response_headers), response_headers=response_headers,
            ) from None
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

    def list_tools(self) -> dict[str, Any]:
        """Discover server capabilities without invoking any tool."""
        for attempt in range(2):
            try:
                if self._headers is None:
                    self._initialize()
                result, _ = self._rpc("tools/list", {})
                return result if isinstance(result, dict) else {}
            except IOLMCPError as exc:
                if attempt == 0 and exc.status_code == 401:
                    self._refresh_after_401(exc.response_headers)
                    continue
                raise
        raise AssertionError("unreachable")

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in ALLOWED_TOOLS:
            raise PermissionError("IOL_SHADOW_TOOL_DENIED:" + tool_name)
        for attempt in range(2):
            try:
                if self._headers is None:
                    self._initialize()
                result, _ = self._rpc("tools/call", {"name": tool_name, "arguments": dict(arguments)})
                return result
            except IOLMCPError as exc:
                if attempt == 0 and exc.status_code == 401:
                    self._refresh_after_401(exc.response_headers)
                    continue
                raise
        raise AssertionError("unreachable")
