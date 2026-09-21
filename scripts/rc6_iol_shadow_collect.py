"""Rotating read-only IOL SHADOW collection for the RC6 operational universe.

The collector is observational only.  It never changes PAPER readiness, signals,
sizing or order routing.  Coverage is defined by the active universe cycle, not
by arbitrary rows accumulated in cache across older cycles.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from iol_mcp_readonly_adapter_rc6 import OAuthStoreReadOnlyMCP
from iol_shadow_collector_rc6 import CollectionPolicy, is_operational_market_window, run_batch

DEFAULT_UNIVERSE = ("GGAL", "YPFD", "PAMP", "BMA", "BBAR", "SUPV", "CEPU", "AAPL")
DEFAULT_ROOT = Path(os.getenv("POROTA_IOL_SHADOW_ROOT", "/opt/porota-trading/data/market"))
DEFAULT_DB = os.getenv("POROTA_IOL_OPERATIONAL_DB", "/opt/porota-trading/data/paper_v17/observer_v17.db")
BATCH_SIZE = 20
PRIMARY_MAX_AGE_SECONDS = 300


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o644)
    temporary.replace(path)


def _read_operational_catalog() -> list[str] | None:
    """Read only currently AVAILABLE ACCIONES/CEDEARS; None means catalog unavailable."""
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True, timeout=10)
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM financial_instrument_catalog "
            "WHERE status='AVAILABLE' AND upper(instrument_type) IN ('ACCIONES','CEDEARS') "
            "AND trim(ticker)<>'' ORDER BY ticker"
        ).fetchall()
        conn.close()
        return sorted({str(row[0]).strip().upper() for row in rows if row and row[0]})
    except sqlite3.Error:
        return None


def _operational_universe_with_source() -> tuple[list[str], str]:
    """Configured symbols may narrow the allowed universe, never widen it."""
    configured = sorted({item.strip().upper() for item in
                         os.getenv("POROTA_IOL_SHADOW_UNIVERSE", "").split(",") if item.strip()})
    catalog = _read_operational_catalog()
    allowed = set(catalog) if catalog is not None else set(DEFAULT_UNIVERSE)
    if configured:
        selected = sorted(set(configured) & allowed)
        if selected:
            return selected, "CONFIGURED_OPERATIONAL_SUBSET" if catalog is not None else "CONFIGURED_FALLBACK_SUBSET"
        return [], "CONFIGURED_UNIVERSE_REJECTED_BY_SCOPE"
    if catalog is not None:
        return (catalog, "OBSERVER_OPERATIONAL_CATALOG") if catalog else ([], "OPERATIONAL_CATALOG_EMPTY")
    return list(DEFAULT_UNIVERSE), "EMERGENCY_FALLBACK_8"
def _operational_universe() -> list[str]:
    """Compatibility API: callers needing provenance use the explicit helper."""
    return _operational_universe_with_source()[0]


def _fingerprint(universe: list[str]) -> str:
    return sha256(",".join(universe).encode("utf-8")).hexdigest()[:16]




def _priority_symbols(universe: list[str]) -> list[str]:
    """Return observed hot/candidate/position symbols without widening scope."""
    allowed = set(universe)
    counts: dict[str, int] = {}
    candidates: list[str] = []
    positions: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True, timeout=3)
        conn.execute("PRAGMA query_only=ON")
        tables = {str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "decision_evidence_snapshots" in tables:
            rows = conn.execute(
                "SELECT payload_json FROM decision_evidence_snapshots "
                "WHERE captured_at >= datetime('now','-390 minutes') "
                "ORDER BY captured_at DESC LIMIT 3000").fetchall()
            for (raw,) in rows:
                try:
                    payload = json.loads(raw or "{}")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                decision = payload.get("decision") if isinstance(payload, dict) else {}
                symbol = str(decision.get("symbol") or "").strip().upper()
                if symbol not in allowed:
                    continue
                counts[symbol] = counts.get(symbol, 0) + 1
                action = str(decision.get("final_result") or decision.get("action") or "").upper()
                if action in {"BUY", "OPEN", "OPENED_SIMULATED", "ENTER"}:
                    candidates.append(symbol)
        if "paper_positions" in tables:
            for (symbol,) in conn.execute(
                "SELECT DISTINCT symbol FROM paper_positions "
                "WHERE upper(COALESCE(status,'')) IN ('OPEN','ACTIVE','OPENED','OPENED_SIMULATED')"):
                symbol = str(symbol or "").strip().upper()
                if symbol in allowed:
                    positions.append(symbol)
        conn.close()
    except (sqlite3.Error, OSError):
        return []
    ordered: list[str] = []
    for symbol in positions + candidates + [
        symbol for symbol, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]:
        if symbol in allowed and symbol not in ordered:
            ordered.append(symbol)
    return ordered[:BATCH_SIZE]


def _rotation(universe: list[str], fingerprint: str) -> tuple[list[str], int, dict[str, Any]]:
    state_path = DEFAULT_ROOT / "iol_shadow_rotation.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if state.get("universe_fingerprint") != fingerprint or int(state.get("universe_size") or 0) != len(universe):
        state = {"schema_version": 3, "cycle_id": 1, "next_index": 0, "seen": []}
    universe_set = set(universe)
    seen = {str(x).upper() for x in state.get("seen", []) if str(x).upper() in universe_set}
    if len(seen) >= len(universe):
        seen = set()
        state = {"schema_version": 3, "cycle_id": int(state.get("cycle_id") or 0) + 1, "next_index": 0, "seen": []}
    start = int(state.get("next_index") or 0) % max(1, len(universe))
    priority = _priority_symbols(universe)
    selected_priority = priority[:BATCH_SIZE]
    background = [
        universe[(start + offset) % len(universe)]
        for offset in range(len(universe))
        if universe[(start + offset) % len(universe)] not in selected_priority
        and universe[(start + offset) % len(universe)] not in seen
    ][:max(0, BATCH_SIZE - len(selected_priority))]
    selected = selected_priority + background
    state["seen"] = sorted(seen)
    state["background_count"] = len(background)
    state["priority_count"] = len(selected_priority)
    return selected, start, state


def _commit_rotation(universe: list[str], fingerprint: str, start: int, selected: list[str], state: dict[str, Any]) -> dict[str, Any]:
    seen = {str(x).upper() for x in state.get("seen", [])}
    background_count = int(state.get("background_count") or 0)
    background = selected[-background_count:] if background_count else []
    seen.update(background)
    committed = {
        "schema_version": 3,
        "universe_size": len(universe),
        "universe_fingerprint": fingerprint,
        "cycle_id": int(state.get("cycle_id") or 1),
        "next_index": (start + background_count) % len(universe),
        "seen": sorted(seen),
        "priority_count": int(state.get("priority_count") or 0),
        "background_count": background_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(DEFAULT_ROOT / "iol_shadow_rotation.json", committed)
    return committed


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _primary_snapshot() -> tuple[dict[str, float], dict[str, Any]]:
    for candidate in (os.getenv("POROTA_PRIMARY_LAST_CACHE_PATH", "").strip(), str(DEFAULT_ROOT / "primary_last.json"), "/app/data/market/primary_last.json"):
        if not candidate:
            continue
        try:
            payload: Any = json.loads(Path(candidate).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        values = payload.get("last_by_symbol") if isinstance(payload, dict) else {}
        timestamp = _parse_time(payload.get("observed_at") or payload.get("refreshed_at") or payload.get("captured_at")) if isinstance(payload, dict) else None
        source = str(payload.get("source") or "").upper() if isinstance(payload, dict) else ""
        market = str(payload.get("market") or "").upper() if isinstance(payload, dict) else ""
        age = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() if timestamp else None
        valid = isinstance(values, dict) and bool(values) and timestamp is not None and age is not None and 0 <= age <= PRIMARY_MAX_AGE_SECONDS and source.startswith("PPI") and market == "BCBA"
        contract = {"state": "READY" if valid else "UNAVAILABLE", "source": source or "UNKNOWN", "market": market or "UNKNOWN", "observed_at": timestamp.isoformat() if timestamp else None, "age_seconds": round(age, 1) if age is not None else None, "reason": "OK" if valid else "PRIMARY_CACHE_CONTRACT_INVALID_OR_STALE"}
        return (values if valid else {}), contract
    return {}, {"state": "UNAVAILABLE", "reason": "PRIMARY_CACHE_NOT_FOUND"}


def _publish_progress(total: int, batch: list[str], source: str, fingerprint: str, cycle: dict[str, Any], primary_contract: dict[str, Any]) -> dict[str, Any]:
    path = DEFAULT_ROOT / "iol_shadow_latest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
    by_symbol = {str(row.get("symbol") or "").upper(): row for row in rows if isinstance(row, dict)}
    observed = [symbol for symbol in cycle.get("seen", []) if symbol in by_symbol]
    ready = sum(str(by_symbol[symbol].get("state") or "").upper() == "READY" for symbol in observed)
    unavailable = len(observed) - ready
    cycle_complete = len(observed) == total and total > 0
    now = datetime.now(timezone.utc)
    fresh = 0
    for symbol in observed:
        row = by_symbol[symbol]
        captured_at = _parse_time(row.get("captured_at"))
        age = (now - captured_at.astimezone(timezone.utc)).total_seconds() if captured_at else None
        if str(row.get("state") or "").upper() == "READY" and age is not None and 0 <= age <= 120:
            fresh += 1
    telemetry = payload.get("telemetry") if isinstance(payload.get("telemetry"), dict) else {}
    payload["progress"] = {
        "scheduled": total, "completed": len(observed), "ready": ready, "unavailable": unavailable,
        "fresh": fresh, "freshness_max_age_seconds": 120,
        "batch_size": len(batch), "cycle_id": cycle.get("cycle_id"), "cycle_complete": cycle_complete,
        "universe_fingerprint": fingerprint, "universe_source": source,
    }
    payload["primary_comparison_contract"] = primary_contract
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
    universe, universe_source = _operational_universe_with_source()
    if not universe:
        print("IOL_SHADOW_COLLECTION=BLOCKED_EMPTY_OPERATIONAL_UNIVERSE")
        return 0
    fingerprint = _fingerprint(universe)
    batch, rotation_start, prior_cycle = _rotation(universe, fingerprint)
    primary, primary_contract = _primary_snapshot()
    run_batch(batch, OAuthStoreReadOnlyMCP(), root=DEFAULT_ROOT, primary_last_by_symbol=primary,
              policy=CollectionPolicy(batch_size=BATCH_SIZE, min_interval_seconds=1.0, max_calls_per_minute=40))
    cycle = _commit_rotation(universe, fingerprint, rotation_start, batch, prior_cycle)
    payload = _publish_progress(len(universe), batch, universe_source, fingerprint, cycle, primary_contract)
    progress = payload.get("progress", {})
    print(f"IOL_SHADOW_COLLECTION=COMPLETE READY={progress.get('ready', 0)} OBSERVED={progress.get('completed', 0)} UNIVERSE={len(universe)} SOURCE={universe_source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
