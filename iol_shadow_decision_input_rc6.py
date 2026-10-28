"""Cache-only IOL input for RC6 SHADOW dual evaluation; no side effects."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from iol_shadow_observation_rc6 import cache_path

SOURCE = "IOL_MCP"
MODE = "SHADOW_DUAL_EVALUATION"
DECISION_EFFECT = "NO_FACTUAL_BINDING"
CACHE_SCHEMA_VERSIONS = frozenset({1, 2, 3})


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _coverage(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose publisher data, never infer a completed cycle from cache size."""
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    active = progress.get("active_cycle") if isinstance(progress.get("active_cycle"), dict) else {}
    def integer(value: Any) -> int | None:
        try: return int(value) if value is not None else None
        except (TypeError, ValueError): return None
    return {"cycle_id": active.get("id", progress.get("cycle_id", payload.get("cycle_id"))),
            "cycle_status": active.get("status", progress.get("cycle_status", "UNKNOWN")),
            "expected": integer(active.get("expected", progress.get("expected"))),
            "observed": integer(active.get("observed", progress.get("observed"))),
            "universe_source": progress.get("universe_source", payload.get("universe_source", "UNKNOWN")),
            "run_id": payload.get("run_id")}


def _unavailable(symbol: str, reason: str, coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"source": SOURCE, "mode": MODE, "decision_effect": DECISION_EFFECT,
            "symbol": symbol, "state": "UNAVAILABLE", "quality": "UNKNOWN",
            "freshness": "UNKNOWN", "reason": reason, "coverage": coverage or {}}


def read_for_decision(symbol: str, *, root: Path | str | None = None,
                      now: datetime | None = None, max_age_seconds: float = 120.0) -> dict[str, Any]:
    """Read one existing cache row for a SHADOW decision; never calls IOL or writes files."""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return _unavailable("", "SYMBOL_MISSING")
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be non-negative")
    path = Path(root) / "iol_shadow_latest.json" if root is not None else cache_path()
    payload = _read_cache(path)
    if payload.get("schema_version") not in CACHE_SCHEMA_VERSIONS:
        return _unavailable(normalized, "CACHE_MISSING_OR_INVALID")
    coverage = _coverage(payload)
    rows = payload.get("symbols")
    if not isinstance(rows, list):
        return _unavailable(normalized, "CACHE_ROWS_MISSING", coverage)
    row = next((item for item in rows if isinstance(item, dict) and
                str(item.get("symbol") or "").upper().strip() == normalized), None)
    if row is None:
        return _unavailable(normalized, "SYMBOL_NOT_COVERED", coverage)
    captured_at = _as_utc(row.get("captured_at"))
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (reference - captured_at).total_seconds() if captured_at else None
    freshness = "FRESH" if age is not None and 0 <= age <= max_age_seconds else ("STALE" if age is not None else "UNKNOWN")
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    state = str(row.get("state") or "UNAVAILABLE")
    has_last = quote.get("last") is not None
    quality = "GOOD" if state == "READY" and has_last and freshness == "FRESH" else ("DEGRADED" if state == "READY" and has_last else "UNKNOWN")
    comparison = row.get("primary_comparison") if isinstance(row.get("primary_comparison"), dict) else {"state": "BACKGROUND_COMPARISON_INCOMPLETE"}
    quote_fields = {key: quote.get(key) for key in ("last", "bid", "ask", "spread_pct", "variation_pct", "cash_volume")}
    metadata = {
        "asset_type": row.get("asset_type") or None,
        "currency": row.get("currency") or None,
        "units_per_lot": row.get("units_per_lot"),
    }
    field_values = {
        **{f"quote.{key}": value for key, value in quote_fields.items()},
        **metadata,
    }
    present = sorted(name for name, value in field_values.items()
                     if value is not None and (not isinstance(value, str) or value.strip()))
    missing = sorted(set(field_values) - set(present))
    return {
        "source": SOURCE,
        "mode": MODE,
        "decision_effect": DECISION_EFFECT,
        "symbol": normalized,
        "market": row.get("market"),
        "term": row.get("term"),
        "state": state,
        "quality": quality,
        "freshness": freshness,
        "captured_at": captured_at.isoformat() if captured_at else None,
        "age_seconds": age,
        "quote": quote_fields,
        "metadata": metadata,
        "field_coverage": {
            "present": present,
            "missing": missing,
            "present_count": len(present),
            "expected_count": len(field_values),
        },
        "comparison": comparison,
        "coverage": coverage,
        "reason": row.get("reason"),
    }
