"""One bounded RC6 IOL SHADOW collection cycle.

Run from the scheduler; writes only IOL's independent cache.  PPI is neither
called nor modified.  Failure is rendered as unavailable evidence, never raised
into trading execution.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP
from iol_shadow_collector_rc6 import CollectionPolicy, run_batch

DEFAULT_UNIVERSE = ("GGAL", "YPFD", "PAMP", "BMA", "BBAR", "SUPV", "CEPU", "AAPL")
DEFAULT_ROOT = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/opt/porota-trading/data/market"))


def _universe() -> list[str]:
    configured = os.getenv("POROTA_IOL_SHADOW_UNIVERSE", "").strip()
    raw = configured.split(",") if configured else DEFAULT_UNIVERSE
    return sorted({item.strip().upper() for item in raw if item.strip()})[:25]


def _primary_snapshot() -> dict[str, float]:
    # Deliberately cache-only. No PPI request is issued by the IOL worker.
    source = Path(os.getenv("POROTA_PRIMARY_LAST_CACHE_PATH", "/app/data/market/primary_last.json"))
    try:
        payload: Any = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    values = payload.get("last_by_symbol") if isinstance(payload, dict) else {}
    return values if isinstance(values, dict) else {}


def main() -> int:
    if os.getenv("POROTA_IOL_SHADOW_MODE", "OBSERVE_ONLY").strip() != "OBSERVE_ONLY":
        print("IOL_SHADOW_COLLECTION=BLOCKED_BY_POLICY")
        return 0
    payload = run_batch(
        _universe(), OAuthStoreReadOnlyMCP(), root=DEFAULT_ROOT,
        primary_last_by_symbol=_primary_snapshot(),
        policy=CollectionPolicy(batch_size=25, min_interval_seconds=1.0, max_calls_per_minute=40),
    )
    ready = sum(1 for row in payload.get("symbols", []) if row.get("state") == "READY")
    print(f"IOL_SHADOW_COLLECTION=COMPLETE READY={ready} TOTAL={len(payload.get('symbols', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
