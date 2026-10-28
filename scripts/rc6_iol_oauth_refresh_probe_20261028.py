#!/usr/bin/env python3
"""One bounded IOL OAuth refresh via the existing market-only MCP adapter.

This intentionally can rotate the host's OAuth store. It neither starts a
timer/collector nor invokes account, validation, order or caucion placement.
Only sanitized booleans, expiry deltas and status codes leave the Droplet.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
import urllib.parse
import urllib.error
import urllib.request

ROOT = Path("/opt/porota-trading")
STORE = Path("/root/.config/porota/iol_mcp_oauth_bootstrap.json")
DB = ROOT / "data/paper_v17/observer_v17.db"
SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,80}\Z")


def metadata(key: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://169.254.169.254/metadata/v1/" + key, timeout=2) as response:
        if response.status != 200:
            raise ValueError("METADATA_STATUS_RED")
        return response.read(64).decode("ascii").strip()


def reason(exc: Exception) -> str:
    first = str(exc).split(":", 1)[0]
    return first if SAFE_CODE.fullmatch(first) else type(exc).__name__.upper()


def failure_class(exc: Exception) -> dict:
    """Expose only status and an OAuth-standard error enum, never response text."""
    cause = exc.__cause__
    if isinstance(cause, urllib.error.HTTPError):
        result = {"class": "HTTP", "status": cause.code}
        if cause.code in (400, 401, 403, 429, 500, 502, 503, 504):
            try:
                payload = json.loads(cause.read(2048))
                code = payload.get("error") if isinstance(payload, dict) else None
                if code in ("invalid_grant", "invalid_client", "unauthorized_client",
                            "invalid_request", "temporarily_unavailable", "server_error"):
                    result["oauth_error"] = code
            except (ValueError, TypeError, OSError):
                pass
        return result
    if isinstance(cause, urllib.error.URLError):
        return {"class": "TRANSPORT"}
    if isinstance(cause, TimeoutError):
        return {"class": "TIMEOUT"}
    if isinstance(cause, json.JSONDecodeError):
        return {"class": "INVALID_JSON"}
    if getattr(exc, "status_code", None) in (400, 401, 403, 429, 500, 502, 503, 504):
        return {"class": "MCP_HTTP", "status": exc.status_code}
    return {"class": "OTHER"}


def read_store() -> dict:
    fd = os.open(STORE, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_nlink != 1
                or stat.S_IMODE(st.st_mode) & 0o077 or not 0 < st.st_size <= 65536):
            raise ValueError("OAUTH_STORE_UNSAFE")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            value = json.load(stream)
        if type(value) is not dict:
            raise ValueError("OAUTH_STORE_SCHEMA_RED")
        return value
    finally:
        if fd >= 0:
            os.close(fd)


def approved_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443) and
            (hostname == "invertironline.com" or hostname.endswith(".invertironline.com")))


def expiry_seconds(data: dict) -> float | None:
    try:
        explicit = data.get("token_expires_at_epoch")
        if explicit is not None:
            return round(float(explicit) - time.time(), 1)
        tokens = data.get("tokens") or {}
        return round(float(data["token_obtained_at_epoch"]) + float(tokens["expires_in"]) - time.time(), 1)
    except (KeyError, ValueError, TypeError):
        return None


def main() -> int:
    report = {"schema": "RC6_IOL_OAUTH_REFRESH_PROBE_V1",
              "observed_at_utc": datetime.now(timezone.utc).isoformat(),
              "market_tools_called": [], "account_tools_called": [], "real_routes": "NOT_CALLED",
              "collector_started": False, "timers_changed": False}
    try:
        report["droplet_identity"] = {"id": metadata("id"), "region": metadata("region")}
        if report["droplet_identity"] != {"id": "594077619", "region": "nyc1"}:
            raise ValueError("DROPLET_IDENTITY_RED")
        if os.geteuid() != 0:
            raise ValueError("ROOT_REQUIRED")
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        finally:
            conn.close()
        report["paper_safety"] = {"mode": row[0] if row else None,
                                  "real_orders_sent": row[1] if row else None}
        if not row or row != ("PRODUCTION_PAPER", 0):
            raise ValueError("PAPER_SAFETY_RED")
        before = read_store()
        tokens = before.get("tokens") if isinstance(before.get("tokens"), dict) else {}
        registration = before.get("client_registration") if isinstance(before.get("client_registration"), dict) else {}
        if (not approved_url(before.get("issuer")) or
                before.get("token_endpoint") and not approved_url(before.get("token_endpoint")) or
                not isinstance(tokens.get("refresh_token"), str) or not tokens.get("refresh_token") or
                not isinstance(registration.get("client_id"), str) or not registration.get("client_id")):
            raise ValueError("OAUTH_BOOTSTRAP_CONTRACT_RED")
        report["before"] = {"access_token_present": bool(tokens.get("access_token")),
                            "refresh_token_present": True, "expires_in_seconds": expiry_seconds(before),
                            "token_endpoint_persisted": approved_url(before.get("token_endpoint")),
                            "token_obtained_at_present": isinstance(before.get("token_obtained_at_epoch"), (int, float))}
    except Exception as exc:
        report["status"] = "PRECHECK_RED"
        report["reason"] = reason(exc)
        print("RC6_IOL_OAUTH_PROBE=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 2

    try:
        sys.path.insert(0, str(ROOT))
        from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP, ALLOWED_TOOLS, FORBIDDEN_ACCOUNT_OR_EXECUTION_TOOLS
        if ALLOWED_TOOLS & FORBIDDEN_ACCOUNT_OR_EXECUTION_TOOLS:
            raise ValueError("IOL_ADAPTER_ROUTE_ALLOWLIST_RED")
        # tools/list invokes only MCP initialize and discovery; it may use the
        # preexisting refresh token under the adapter's own file lock.
        started = time.monotonic()
        discovered = OAuthStoreReadOnlyMCP(STORE, timeout_seconds=12).list_tools()
        after = read_store()
        current_tokens = after.get("tokens") if isinstance(after.get("tokens"), dict) else {}
        report["after"] = {"expires_in_seconds": expiry_seconds(after),
                           "access_token_rotated": current_tokens.get("access_token") != tokens.get("access_token"),
                           "refresh_token_rotated": current_tokens.get("refresh_token") != tokens.get("refresh_token"),
                           "refresh_token_present": bool(current_tokens.get("refresh_token"))}
        report["mcp"] = {"tools_list": "OK", "tool_count": len(discovered.get("tools") or [])
                         if isinstance(discovered, dict) else None,
                         "elapsed_seconds": round(time.monotonic() - started, 1)}
        if report["after"]["expires_in_seconds"] is None or report["after"]["expires_in_seconds"] < 60:
            raise ValueError("OAUTH_NEW_EXPIRY_UNPROVEN")
        report["status"] = "AUTHENTICATED"
    except Exception as exc:
        report["status"] = "REAUTH_OR_TRANSPORT_REVIEW_REQUIRED"
        report["reason"] = reason(exc)
        report["failure"] = failure_class(exc)
        try:
            after = read_store()
            report["after"] = {"expires_in_seconds": expiry_seconds(after),
                               "access_token_rotated": (after.get("tokens") or {}).get("access_token") != tokens.get("access_token"),
                               "refresh_token_rotated": (after.get("tokens") or {}).get("refresh_token") != tokens.get("refresh_token")}
        except Exception:
            report["after"] = {"status": "UNAVAILABLE"}
    print("RC6_IOL_OAUTH_PROBE=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0  # Diagnostic failure is reported as AMBER, never called deploy GREEN.


if __name__ == "__main__":
    raise SystemExit(main())
