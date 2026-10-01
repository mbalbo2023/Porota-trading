#!/usr/bin/env python3
"""Bounded IOL MCP OAuth health check for the RC6 Droplet.

The check invokes only MCP initialize/tools-list through the read-only adapter.
It may rotate OAuth tokens when the access token is near expiry, but it never
calls account, validation, DDJJ, caucion placement, FCI mutation or order tools.
No token or client secret is printed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from iol_mcp_readonly_adapter_rc6 import DEFAULT_STORE, OAuthStoreReadOnlyMCP


def _metadata(path: Path) -> dict:
    if not path.is_file():
        return {
            "store_present": False,
            "access_token_present": False,
            "refresh_token_present": False,
            "expires_in": None,
            "estimated_seconds_remaining": None,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
    try:
        expires_in = float(tokens.get("expires_in"))
    except (TypeError, ValueError):
        expires_in = None
    try:
        obtained = float(payload.get("token_obtained_at_epoch"))
    except (TypeError, ValueError):
        obtained = path.stat().st_mtime
    remaining = None
    if expires_in is not None and expires_in > 0:
        remaining = round(obtained + expires_in - time.time(), 1)
    return {
        "store_present": True,
        "mode": oct(path.stat().st_mode & 0o777),
        "access_token_present": bool(tokens.get("access_token")),
        "refresh_token_present": bool(tokens.get("refresh_token")),
        "expires_in": expires_in,
        "estimated_seconds_remaining": remaining,
        "token_endpoint_persisted": bool(payload.get("token_endpoint")),
        "token_obtained_at_persisted": payload.get("token_obtained_at_epoch") is not None,
    }


def main() -> int:
    store = Path(DEFAULT_STORE)
    before = _metadata(store)
    result = {
        "schema": "POROTA_RC6_IOL_OAUTH_HEALTH_V1",
        "authority": "IOL_MCP_OAUTH",
        "decision_effect": "OBSERVE_ONLY",
        "real_order_routes": "NOT_PRESENT",
        "before": before,
    }
    if not before["store_present"]:
        result["state"] = "REAUTH_REQUIRED"
        result["reason"] = "OAUTH_STORE_MISSING"
        print(json.dumps(result, sort_keys=True))
        return 2
    try:
        tools = OAuthStoreReadOnlyMCP(store, timeout_seconds=15).list_tools()
        count = len(tools.get("tools") or []) if isinstance(tools, dict) else 0
        result["state"] = "AUTHENTICATED"
        result["tools_discovered"] = count
        result["after"] = _metadata(store)
    except Exception as exc:
        result["state"] = "REAUTH_REQUIRED"
        result["reason"] = type(exc).__name__
        result["detail"] = str(exc)[:160]
        result["after"] = _metadata(store)
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
