"""IOL MCP collector for RC6 observation-only SHADOW validation.

This module deliberately has no imports from execution, portfolio, order, or PPI
modules.  Credentials are owned by an injected runtime session; neither OAuth
tokens nor refresh tokens are read, persisted, logged, returned, or accepted by
this collector.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Iterable, Protocol

from iol_shadow_observation_rc6 import (
    DECISION_EFFECT, DEFAULT_MARKET, MODE, SOURCE, _quote_summary,
    cache_path, _number,
)
from cy_market_source_arbitration_hf6 import compare_background_numeric

ALLOWED_TOOLS = frozenset({"get_asset_info", "get_asset_quote"})
CHECKPOINT_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 2
MAX_BATCH_SIZE = 50


class ReadOnlyMCP(Protocol):
    """Credential-owning runtime adapter. It may refresh OAuth internally."""

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class CollectionPolicy:
    batch_size: int = 25
    min_interval_seconds: float = 0.35
    tolerance_pct: float = 2.0

    def validate(self) -> None:
        if not 1 <= self.batch_size <= MAX_BATCH_SIZE:
            raise ValueError("batch_size must be between 1 and 50")
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be non-negative")
        if self.tolerance_pct < 0:
            raise ValueError("tolerance_pct must be non-negative")


class RateLimiter:
    def __init__(self, minimum_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.minimum_interval, self.clock, self.sleep, self.last = minimum_interval, clock, sleep, None

    def wait(self) -> None:
        now = self.clock()
        if self.last is not None:
            remaining = self.minimum_interval - (now - self.last)
            if remaining > 0:
                self.sleep(remaining)
        self.last = self.clock()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                            prefix=".iol-shadow-", suffix=".tmp", delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _root_path(root: Path | str | None) -> Path:
    return Path(root) if root is not None else cache_path().parent


def _checkpoint_path(root: Path | str | None) -> Path:
    return _root_path(root) / "iol_shadow_checkpoint.json"


def _symbols(values: Iterable[str]) -> list[str]:
    unique: set[str] = set()
    for value in values:
        symbol = str(value or "").upper().strip()
        if symbol:
            unique.add(symbol)
    return sorted(unique)


def _run_id(symbols: list[str], market: str) -> str:
    digest = hashlib.sha256(("|".join(symbols) + "|" + market).encode()).hexdigest()[:16]
    return f"IOL-SHADOW-{datetime.now(timezone.utc).date().isoformat()}-{digest}"


def _checkpoint(run_id: str, symbols: list[str], market: str) -> dict:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_id": run_id,
        "source": SOURCE,
        "mode": MODE,
        "decision_effect": DECISION_EFFECT,
        "market": market,
        "symbols": symbols,
        "completed": {},
        "status": "RUNNING",
        "real_money_authorized": False,
        "live_decision_authority": False,
    }


def _safe_call(client: ReadOnlyMCP, tool_name: str, arguments: dict[str, Any]) -> dict:
    if tool_name not in ALLOWED_TOOLS:
        raise PermissionError(f"IOL_SHADOW_TOOL_DENIED:{tool_name}")
    result = client.call(tool_name, arguments)
    return result if isinstance(result, dict) else {}


def run_batch(
    symbols: Iterable[str],
    client: ReadOnlyMCP,
    *,
    root: Path | str | None = None,
    market: str = DEFAULT_MARKET,
    primary_last_by_symbol: dict[str, float] | None = None,
    policy: CollectionPolicy = CollectionPolicy(),
    run_id: str | None = None,
    resume: bool = True,
    limiter: RateLimiter | None = None,
) -> dict:
    """Run a bounded, resumable, cache-only IOL observation batch.

    The returned data is evidence only. Exceptions become per-symbol rows, so a
    transient MCP failure cannot halt the rest of the batch or affect RC6.
    """
    policy.validate()
    universe = _symbols(symbols)
    if len(universe) > MAX_BATCH_SIZE:
        raise ValueError("IOL_SHADOW_BATCH_TOO_LARGE")
    active_run_id = run_id or _run_id(universe, market)
    checkpoint_file = _checkpoint_path(root)
    prior = _load_json(checkpoint_file)
    state = prior if (
        resume and prior.get("schema_version") == CHECKPOINT_SCHEMA_VERSION
        and prior.get("run_id") == active_run_id
        and prior.get("symbols") == universe and prior.get("market") == market
    ) else _checkpoint(active_run_id, universe, market)
    primary = primary_last_by_symbol or {}
    rate_limiter = limiter or RateLimiter(policy.min_interval_seconds)
    completed: dict = dict(state.get("completed") or {})

    for symbol in universe:
        if symbol in completed:
            continue
        captured_at = datetime.now(timezone.utc).isoformat()
        try:
            rate_limiter.wait()
            quote = _quote_summary(_safe_call(client, "get_asset_quote", {"symbol": symbol, "market": market}))
            rate_limiter.wait()
            info = _safe_call(client, "get_asset_info", {"symbol": symbol, "market": market})
            live_last = _number(primary.get(symbol))
            completed[symbol] = {
                "symbol": symbol, "market": market, "state": "READY" if quote else "UNAVAILABLE",
                "captured_at": captured_at, "quote": quote,
                "asset_type": str(info.get("type") or ""),
                "currency": str(info.get("currency") or ""),
                "units_per_lot": info.get("units_per_lot"),
                "primary_comparison": compare_background_numeric(
                    live_last, quote.get("last"), tolerance_pct=policy.tolerance_pct),
                "decision_effect": DECISION_EFFECT,
            }
        except Exception as exc:  # intentionally isolate external-source failures
            completed[symbol] = {
                "symbol": symbol, "market": market, "state": "UNAVAILABLE",
                "captured_at": captured_at, "reason": f"{type(exc).__name__}:{str(exc)[:160]}",
                "decision_effect": DECISION_EFFECT,
            }
        state["completed"] = completed
        state["status"] = "RUNNING"
        _atomic_json(checkpoint_file, state)

    rows = [completed[symbol] for symbol in universe if symbol in completed]
    state["status"] = "COMPLETE"
    state["completed_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(checkpoint_file, state)
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION, "source": SOURCE, "mode": MODE,
        "decision_effect": DECISION_EFFECT, "live_decision_authority": False,
        "real_money_authorized": False, "run_id": active_run_id, "market": market,
        "refreshed_at": state["completed_at"], "symbols": rows,
    }
    _atomic_json(cache_path(root), payload)
    return payload
