#!/usr/bin/env python3
"""Bounded, read-only IOL MCP and cache diagnosis on the exact RC6 Droplet.

Staged on stdin by Actions. Never logs tokens, provider payload, account state,
prices, or free-form provider errors. The MCP adapter's token refresh paths are
overridden before any network request, so a 401 is reported as a health signal.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request

ROOT = Path("/opt/porota-trading")
STORE = Path("/root/.config/porota/iol_mcp_oauth_bootstrap.json")
CACHE = ROOT / "data/market/iol_family_reference_latest.json"
GUARDIAN = ROOT / "data/market/iol_oauth_guardian_state.json"
DB = ROOT / "data/paper_v17/observer_v17.db"
FALLBACK = ["IOL_LIVE_BOUNDED_RETRY", "IOL_LAST_KNOWN_GOOD", "PPI_PRIMARY", "BYMA_PUBLIC_COMPLEMENTARY"]
SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,80}\Z")


def read_json(path: Path, maximum: int) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= maximum:
            raise ValueError("UNSAFE_FILE")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            result = json.load(stream)
        if type(result) is not dict:
            raise ValueError("NOT_OBJECT")
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def age(value: object, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        seconds = (now - parsed.astimezone(timezone.utc)).total_seconds()
        return round(seconds, 1) if seconds >= 0 else None
    except (TypeError, ValueError):
        return None


def metadata(name: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://169.254.169.254/metadata/v1/" + name, timeout=2) as response:
        if response.status != 200:
            raise ValueError("METADATA_STATUS")
        value = response.read(64).decode("ascii").strip()
    return value


def code(exc: Exception) -> str:
    value = str(exc).split(":", 1)[0]
    return value if SAFE_CODE.fullmatch(value) else type(exc).__name__.upper()


def systemd(unit: str) -> dict:
    allowed = {"porota-iol-shadow-collector-rc6.service", "porota-iol-shadow-collector-rc6.timer",
               "porota-iol-oauth-health-rc6.service", "porota-iol-oauth-health-rc6.timer"}
    assert unit in allowed
    result = subprocess.run(
        ["systemctl", "show", unit, "--no-pager", "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,UnitFileState,LastTriggerUSec"],
        text=True, capture_output=True, check=False, timeout=4,
    )
    if result.returncode or len(result.stdout) > 2048:
        return {"status": "UNAVAILABLE"}
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return {key: fields.get(key) for key in ("LoadState", "ActiveState", "SubState", "Result", "ExecMainStatus", "UnitFileState", "LastTriggerUSec")}


def main() -> int:
    now = datetime.now(timezone.utc)
    report: dict = {"schema": "RC6_IOL_SOURCE_HEALTH_READONLY_V1", "observed_at_utc": now.isoformat(),
                    "mutations": "NONE", "real_routes": "NOT_CALLED"}
    try:
        identity = {"id": metadata("id"), "region": metadata("region")}
        report["droplet_identity"] = identity
        if identity != {"id": "594077619", "region": "nyc1"}:
            raise ValueError("DROPLET_IDENTITY_RED")
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
        try:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute("SELECT mode, real_orders_sent FROM observer_state WHERE id=1").fetchone()
        finally:
            conn.close()
        report["paper_safety"] = {"mode": row[0] if row else None,
                                  "real_orders_sent": row[1] if row else None}
        if not row or row[0] != "PRODUCTION_PAPER" or row[1] != 0:
            raise ValueError("PAPER_SAFETY_RED")
    except Exception as exc:
        report["safety_gate"] = "RED"
        report["safety_reason"] = code(exc)
        print("RC6_IOL_READONLY=" + json.dumps(report, sort_keys=True))
        return 2

    try:
        payload = read_json(CACHE, 8 * 1024 * 1024)
        refreshed_age = age(payload.get("refreshed_at"), now)
        lkg_age = age(payload.get("last_known_good_at"), now)
        sections = payload.get("section_states")
        live_sections = payload.get("live_sections")
        contract = (payload.get("continuation_state") == "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL"
                    and payload.get("fallback_order") == FALLBACK
                    and payload.get("real_money_authorized") is False
                    and payload.get("decision_effect") == "OBSERVE_ONLY")
        fresh = (payload.get("source") == "IOL_MCP" and payload.get("cache_state") == "LIVE_FRESH"
                 and lkg_age is not None and lkg_age <= 240
                 and refreshed_age is not None and refreshed_age <= 240
                 and isinstance(live_sections, list) and live_sections
                 and isinstance(sections, dict)
                 and any(sections.get(name) == "LIVE_FRESH" for name in live_sections))
        safe_sections = ({str(k): str(v) for k, v in sections.items()
                          if re.fullmatch(r"[A-Za-z0-9_]{1,40}", str(k)) and SAFE_CODE.fullmatch(str(v))}
                         if isinstance(sections, dict) and len(sections) <= 20 else {})
        source = str(payload.get("source") or "")
        state = str(payload.get("cache_state") or "")
        report["cache"] = {"source": source if SAFE_CODE.fullmatch(source) else "INVALID",
                           "declared_state": state if SAFE_CODE.fullmatch(state) else "INVALID",
                           "refresh_age_seconds": refreshed_age, "lkg_age_seconds": lkg_age,
                           "live_section_count": len(live_sections) if isinstance(live_sections, list) else None,
                           "section_states": safe_sections,
                           "safe_fallback_contract": contract,
                           "effective_state": "LIVE_FRESH" if fresh and contract else "AMBER_UNVERIFIED"}
    except Exception as exc:
        report["cache"] = {"effective_state": "AMBER_UNVERIFIED", "reason": code(exc)}

    try:
        credentials = read_json(STORE, 65536)
        tokens = credentials.get("tokens") if isinstance(credentials.get("tokens"), dict) else {}
        expiry = credentials.get("token_expires_at_epoch")
        if not isinstance(expiry, (int, float)):
            obtained, duration = credentials.get("token_obtained_at_epoch"), tokens.get("expires_in")
            expiry = float(obtained) + float(duration) if obtained is not None and duration is not None else None
        report["oauth"] = {"store_present": True, "access_token_present": bool(tokens.get("access_token")),
                           "refresh_token_present": bool(tokens.get("refresh_token")),
                           "expires_in_seconds": round(float(expiry) - time.time(), 1) if expiry is not None else None}
    except Exception as exc:
        report["oauth"] = {"store_present": STORE.exists(), "reason": code(exc)}

    try:
        guardian = read_json(GUARDIAN, 8192)
        raw_state = str(guardian.get("state") or "UNKNOWN")
        report["guardian"] = {"state": raw_state if SAFE_CODE.fullmatch(raw_state) else "INVALID",
                              "checked_age_seconds": age(guardian.get("checked_at"), now)}
    except Exception as exc:
        report["guardian"] = {"state": "UNKNOWN", "reason": code(exc)}
    report["units"] = {unit: systemd(unit) for unit in (
        "porota-iol-shadow-collector-rc6.service", "porota-iol-shadow-collector-rc6.timer",
        "porota-iol-oauth-health-rc6.service", "porota-iol-oauth-health-rc6.timer")}

    # No token refresh, lock creation, account tools, collector or host writes.
    # A 401 is a diagnostic result; the production guardian owns renewal.
    try:
        sys.path.insert(0, str(ROOT))
        from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP, ALLOWED_TOOLS, FORBIDDEN_ACCOUNT_OR_EXECUTION_TOOLS
        assert {"get_caucion_rates", "get_fci_funds"}.issubset(ALLOWED_TOOLS)
        assert not ALLOWED_TOOLS.intersection(FORBIDDEN_ACCOUNT_OR_EXECUTION_TOOLS)

        class NoRefresh(OAuthStoreReadOnlyMCP):
            def _refresh_if_due(self):
                return None

            def _refresh_after_401(self, headers):
                raise RuntimeError("IOL_MCP_REAUTH_REQUIRED_READONLY")

        client = NoRefresh(STORE, timeout_seconds=12)
        started = time.monotonic()
        tools = client.list_tools()
        report["mcp"] = {"tools_list": "OK", "tool_count": len(tools.get("tools") or [])
                         if isinstance(tools, dict) else None}
        for name, arguments in (("get_caucion_rates", {"currency": "ARS", "caucion_type": "colocadora"}),
                                ("get_fci_funds", {})):
            try:
                response = client.call(name, arguments)
                rows = response.get("result") if isinstance(response, dict) else None
                report["mcp"][name] = {"state": "OK", "row_count": len(rows) if isinstance(rows, list) else None}
            except Exception as exc:
                report["mcp"][name] = {"state": "ERROR", "reason": code(exc)}
        report["mcp"]["elapsed_seconds"] = round(time.monotonic() - started, 1)
    except Exception as exc:
        report["mcp"] = {"tools_list": "ERROR", "reason": code(exc)}

    cache_ok = report["cache"].get("effective_state") == "LIVE_FRESH"
    mcp_ok = (report["mcp"].get("tools_list") == "OK"
              and all(report["mcp"].get(name, {}).get("state") == "OK" for name in ("get_caucion_rates", "get_fci_funds")))
    report["source_health"] = "GREEN" if cache_ok and mcp_ok else "AMBER"
    print("RC6_IOL_READONLY=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
