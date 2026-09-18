"""Rotating read-only IOL SHADOW collection for the RC6 operational universe."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP
from iol_shadow_collector_rc6 import CollectionPolicy, is_operational_market_window, run_batch

# Emergency fallback only. Normal operation derives ACCIONES/CEDEARs from the
# existing local catalog; it never calls PPI from this worker.
DEFAULT_UNIVERSE = ("GGAL", "YPFD", "PAMP", "BMA", "BBAR", "SUPV", "CEPU", "AAPL")
DEFAULT_ROOT = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/opt/porota-trading/data/market"))
DEFAULT_DB = os.getenv("POROTA_IOL_OPERATIONAL_DB", "/opt/porota-trading/data/paper_v17/observer_v17.db")
BATCH_SIZE = 20


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(path)


def _operational_universe() -> list[str]:
    configured = os.getenv("POROTA_IOL_SHADOW_UNIVERSE", "").strip()
    if configured:
        return sorted({item.strip().upper() for item in configured.split(",") if item.strip()})
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True, timeout=10)
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM financial_instrument_catalog "
            "WHERE status='AVAILABLE' AND upper(instrument_type) IN ('ACCIONES','CEDEARS') "
            "AND trim(ticker)<>'' ORDER BY ticker"
        ).fetchall()
        conn.close()
        universe = sorted({str(row[0]).strip().upper() for row in rows if row and row[0]})
        if universe:
            return universe
    except sqlite3.Error:
        pass
    return list(DEFAULT_UNIVERSE)


def _rotation(universe: list[str]) -> tuple[list[str], int]:
    state_path = DEFAULT_ROOT / "iol_shadow_rotation.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    start = int(state.get("next_index") or 0) % max(1, len(universe))
    selected = [universe[(start + offset) % len(universe)] for offset in range(min(BATCH_SIZE, len(universe)))]
    return selected, start

def _commit_rotation(universe: list[str], start: int, selected: list[str]) -> None:
    state_path = DEFAULT_ROOT / "iol_shadow_rotation.json"
    _atomic_json(state_path, {"schema_version": 1, "universe_size": len(universe),
                              "next_index": (start + len(selected)) % len(universe)})


def _primary_snapshot() -> dict[str, float]:
    for candidate in (
        os.getenv("POROTA_PRIMARY_LAST_CACHE_PATH", "").strip(),
        str(DEFAULT_ROOT / "primary_last.json"),
        "/app/data/market/primary_last.json",
    ):
        if not candidate:
            continue
        try:
            payload: Any = json.loads(Path(candidate).read_text(encoding="utf-8"))
            values = payload.get("last_by_symbol") if isinstance(payload, dict) else {}
            if isinstance(values, dict):
                return values
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _publish_progress(total: int, batch: list[str]) -> dict[str, Any]:
    path = DEFAULT_ROOT / "iol_shadow_latest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
    ready = sum(isinstance(row, dict) and row.get("state") == "READY" for row in rows)
    telemetry = payload.get("telemetry") if isinstance(payload.get("telemetry"), dict) else {}
    payload["progress"] = {"scheduled": total, "completed": len(rows), "batch_size": len(batch)}
    payload["telemetry"] = {**telemetry, "ready_total": ready, "last_batch_size": len(batch)}
    _atomic_json(path, payload)
    return payload


def main() -> int:
    if os.getenv("POROTA_IOL_SHADOW_MODE", "OBSERVE_ONLY").strip() != "OBSERVE_ONLY":
        print("IOL_SHADOW_COLLECTION=BLOCKED_BY_POLICY")
        return 0
    if not is_operational_market_window():
        print("IOL_SHADOW_COLLECTION=NOT_DUE_OUTSIDE_MARKET")
        return 0
    universe = _operational_universe()
    batch, rotation_start = _rotation(universe)
    payload = run_batch(batch, OAuthStoreReadOnlyMCP(), root=DEFAULT_ROOT,
        primary_last_by_symbol=_primary_snapshot(),
        policy=CollectionPolicy(batch_size=BATCH_SIZE, min_interval_seconds=1.0, max_calls_per_minute=40))
    _commit_rotation(universe, rotation_start, batch)
    payload = _publish_progress(len(universe), batch)
    ready = sum(1 for row in payload.get("symbols", []) if row.get("state") == "READY")
    print(f"IOL_SHADOW_COLLECTION=COMPLETE READY={ready} TOTAL={len(payload.get('symbols', []))} UNIVERSE={len(universe)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
