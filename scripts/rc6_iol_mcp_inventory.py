#!/usr/bin/env python3
"""Inventory the authenticated IOL MCP without invoking any tool.

This is a read-only capability discovery. It records only tool names,
descriptions and input schemas returned by MCP tools/list; it never calls a
market, account, order or execution tool.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP, IOLMCPError

ROOT = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/opt/porota-trading/data/market"))
OUTPUT = ROOT / "iol_mcp_tools_latest.json"
FORBIDDEN_WORDS = ("order", "account", "portfolio", "position", "execute", "trade", "cancel", "withdraw", "deposit")


def _atomic_write(payload: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, OUTPUT)


def main() -> int:
    client = OAuthStoreReadOnlyMCP()
    try:
        raw = client.list_tools()
    except Exception as exc:
        payload = {
            "schema_version": 1,
            "source": "IOL_MCP",
            "mode": "SHADOW",
            "decision_effect": "OBSERVE_ONLY",
            "real_money_authorized": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "status": "UNAVAILABLE",
            "error": f"{type(exc).__name__}:{str(exc)[:200]}",
            "tools": [],
        }
        _atomic_write(payload)
        print("IOL_MCP_TOOL_INVENTORY=UNAVAILABLE")
        return 2

    tools = raw.get("tools") if isinstance(raw, dict) else []
    safe = []
    for item in tools if isinstance(tools, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        description = str(item.get("description") or "")[:1000]
        schema = item.get("inputSchema")
        blocked_by_name = any(word in name.lower() or word in description.lower() for word in FORBIDDEN_WORDS)
        safe.append({
            "name": name,
            "description": description,
            "input_schema": schema if isinstance(schema, dict) else {},
            "policy_class": "FORBIDDEN_UNTIL_EXPLICIT_REVIEW" if blocked_by_name else "UNCLASSIFIED_READ_ONLY_CANDIDATE",
        })

    payload = {
        "schema_version": 1,
        "source": "IOL_MCP",
        "mode": "SHADOW",
        "decision_effect": "OBSERVE_ONLY",
        "live_decision_authority": False,
        "real_money_authorized": False,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "AVAILABLE",
        "tool_count": len(safe),
        "tools": safe,
    }
    _atomic_write(payload)
    print("IOL_MCP_TOOL_INVENTORY=AVAILABLE")
    print("IOL_MCP_TOOL_COUNT=" + str(len(safe)))
    print("IOL_MCP_TOOL_NAMES=" + ",".join(item["name"] for item in safe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
