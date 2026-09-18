"""Bounded IOL MCP collector for RC6 observation-only SHADOW validation.

The collector never imports PPI, execution, portfolio, or order modules. Credentials
remain inside an injected adapter. IOL data is recorded separately and cannot alter
signals, READY/HOLD, sizing, or orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as clock_time, timezone
from zoneinfo import ZoneInfo

import ak_byma_calendar as byma
import json
import os
from pathlib import Path
import random
from tempfile import NamedTemporaryFile
import time
from typing import Any, Callable, Iterable, Protocol
from uuid import uuid4

from iol_shadow_observation_rc6 import (
    DECISION_EFFECT, DEFAULT_MARKET, MODE, SOURCE, _number, _quote_summary, cache_path,
)

ALLOWED_TOOLS = frozenset({"get_asset_info", "get_asset_quote"})
MAX_BATCH_SIZE = 50
DEFAULT_TERM = "t1"
METADATA_TTL_SECONDS = 24 * 60 * 60
CACHE_SCHEMA_VERSION = 3
CHECKPOINT_SCHEMA_VERSION = 2
MARKET_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
MARKET_OPEN = clock_time(10, 30)
MARKET_CLOSE = clock_time(17, 0)


def is_operational_market_window(now: datetime | None = None) -> bool:
    """True only during a normal BYMA session; unknown dates fail closed."""
    local = (now or datetime.now(MARKET_TZ)).astimezone(MARKET_TZ)
    return (
        byma.es_dia_habil_operativo(local.date())
        and MARKET_OPEN <= local.time() < MARKET_CLOSE
    )


class ReadOnlyMCP(Protocol):
    """Credential-owning adapter. OAuth refresh is internal and never logged here."""
    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CollectionPolicy:
    batch_size: int = 25
    min_interval_seconds: float = 1.0
    max_calls_per_minute: int = 40
    metadata_ttl_seconds: int = METADATA_TTL_SECONDS
    tolerance_pct: float = 2.0
    retry_attempts: int = 2
    circuit_breaker_seconds: float = 60.0

    def validate(self) -> None:
        if not 1 <= self.batch_size <= MAX_BATCH_SIZE:
            raise ValueError("batch_size must be between 1 and 50")
        if self.min_interval_seconds < 1.0:
            raise ValueError("min_interval_seconds must be at least 1.0")
        if not 1 <= self.max_calls_per_minute <= 40:
            raise ValueError("max_calls_per_minute must be between 1 and 40")
        if self.metadata_ttl_seconds < 60:
            raise ValueError("metadata_ttl_seconds must be at least 60")
        if self.retry_attempts < 0:
            raise ValueError("retry_attempts must be non-negative")


class CircuitOpenError(RuntimeError):
    pass


class RateGovernor:
    """Single-process global cadence, bounded rolling window and 429 circuit breaker."""

    def __init__(self, policy: CollectionPolicy, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, jitter: Callable[[], float] = random.random) -> None:
        self.policy, self.clock, self.sleep, self.jitter = policy, clock, sleep, jitter
        self.calls: list[float] = []
        self.last: float | None = None
        self.open_until = 0.0
        self.calls_total = 0
        self.calls_429 = 0

    def acquire(self) -> None:
        now = self.clock()
        if now < self.open_until:
            raise CircuitOpenError("IOL_SHADOW_CIRCUIT_OPEN")
        self.calls = [at for at in self.calls if now - at < 60.0]
        waits = []
        if self.last is not None:
            waits.append(self.policy.min_interval_seconds - (now - self.last))
        if len(self.calls) >= self.policy.max_calls_per_minute:
            waits.append(60.0 - (now - self.calls[0]))
        delay = max([0.0, *waits])
        if delay:
            self.sleep(delay)
        now = self.clock()
        self.calls = [at for at in self.calls if now - at < 60.0]
        self.calls.append(now)
        self.last = now
        self.calls_total += 1

    def trip(self, retry_after: float | None = None) -> None:
        self.calls_429 += 1
        delay = retry_after if retry_after is not None else self.policy.circuit_breaker_seconds
        self.open_until = max(self.open_until, self.clock() + max(1.0, delay))

    def retry_delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return max(0.0, retry_after)
        return min(30.0, 2.0 ** attempt) + self.jitter()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".iol-shadow-", suffix=".tmp", delete=False) as h:
        json.dump(value, h, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        h.write("\n"); h.flush(); os.fsync(h.fileno()); temporary = Path(h.name)
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


def _metadata_path(root: Path | str | None) -> Path:
    return _root_path(root) / "iol_shadow_metadata.json"


def _symbols(values: Iterable[str]) -> list[str]:
    return sorted({str(v or "").upper().strip() for v in values if str(v or "").strip()})


def _metadata_key(symbol: str, market: str) -> str:
    return f"{market}:{symbol}"


def _is_rate_limited(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 429 or "429" in str(exc) or "RATE_LIMIT" in str(exc).upper()


def _retry_after(exc: Exception) -> float | None:
    value = getattr(exc, "retry_after", None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_call(client: ReadOnlyMCP, tool_name: str, arguments: dict[str, Any],
               governor: RateGovernor, policy: CollectionPolicy) -> dict[str, Any]:
    if tool_name not in ALLOWED_TOOLS:
        raise PermissionError(f"IOL_SHADOW_TOOL_DENIED:{tool_name}")
    for attempt in range(policy.retry_attempts + 1):
        governor.acquire()
        try:
            result = client.call(tool_name, arguments)
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            if not _is_rate_limited(exc) or attempt >= policy.retry_attempts:
                if _is_rate_limited(exc):
                    governor.trip(_retry_after(exc))
                raise
            governor.trip(_retry_after(exc))
            governor.sleep(governor.retry_delay(attempt, _retry_after(exc)))
    raise AssertionError("unreachable")


def _comparison(primary_last: Any, shadow_last: Any, tolerance_pct: float) -> dict[str, Any]:
    primary, shadow = _number(primary_last), _number(shadow_last)
    if primary is None or shadow is None:
        return {"state": "BACKGROUND_COMPARISON_INCOMPLETE"}
    difference = abs(primary - shadow) / primary * 100.0 if primary else None
    return {"state": "MATCH" if difference is not None and difference <= tolerance_pct else "PRICE_DIVERGENCE",
            "difference_pct": difference}


def _metadata_from(info: dict[str, Any]) -> dict[str, Any]:
    payload = info if isinstance(info, dict) else {}
    return {
        "asset_type": str(payload.get("type") or payload.get("asset_type") or ""),
        "currency": str(payload.get("currency") or ""),
        "units_per_lot": payload.get("units_per_lot") or payload.get("lot_size"),
    }


def run_batch(symbols: Iterable[str], client: ReadOnlyMCP, *, root: Path | str | None = None,
              market: str = DEFAULT_MARKET, term: str = DEFAULT_TERM,
              primary_last_by_symbol: dict[str, float] | None = None,
              policy: CollectionPolicy = CollectionPolicy(), run_id: str | None = None,
              resume: bool = False, governor: RateGovernor | None = None,
              now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict:
    """Collect one *new* quote cycle; resume only when callers explicitly supply run_id."""
    policy.validate()
    universe = _symbols(symbols)
    if len(universe) > policy.batch_size:
        raise ValueError("IOL_SHADOW_BATCH_TOO_LARGE")
    if term != DEFAULT_TERM:
        raise ValueError("IOL_SHADOW_UNSUPPORTED_TERM")
    active_run_id = run_id or f"IOL-SHADOW-{now().strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    checkpoint_file = _checkpoint_path(root)
    prior = _load_json(checkpoint_file)
    state = prior if (resume and run_id and prior.get("run_id") == active_run_id and
                      prior.get("symbols") == universe and prior.get("market") == market and
                      prior.get("term") == term) else {"schema_version": CHECKPOINT_SCHEMA_VERSION, "run_id": active_run_id,
                      "source": SOURCE, "mode": MODE, "decision_effect": DECISION_EFFECT, "market": market,
                      "term": term, "symbols": universe, "completed": {}, "status": "RUNNING",
                      "real_money_authorized": False, "live_decision_authority": False}
    metadata_store = _load_json(_metadata_path(root))
    metadata_entries = dict(metadata_store.get("entries") or {})
    completed, primary = dict(state.get("completed") or {}), primary_last_by_symbol or {}
    rate_governor = governor or RateGovernor(policy)

    errors_total = 0
    for symbol in universe:
        if symbol in completed:
            continue
        captured_at = now().isoformat()
        try:
            quote = _quote_summary(_safe_call(client, "get_asset_quote",
                {"symbol": symbol, "market": market, "term": term}, rate_governor, policy))
            key, cached = _metadata_key(symbol, market), metadata_entries.get(_metadata_key(symbol, market), {})
            expires_at = cached.get("expires_at", "")
            if not cached or expires_at <= captured_at:
                info = _safe_call(client, "get_asset_info", {"symbol": symbol, "market": market}, rate_governor, policy)
                cached = {**_metadata_from(info), "fetched_at": captured_at,
                          "expires_at": datetime.fromtimestamp(now().timestamp() + policy.metadata_ttl_seconds, timezone.utc).isoformat()}
                metadata_entries[key] = cached
            completed[symbol] = {"symbol": symbol, "market": market, "term": term,
                "state": "READY" if quote.get("last") is not None else "UNAVAILABLE", "captured_at": captured_at,
                "quote": quote, **_metadata_from(cached),
                "primary_comparison": _comparison(primary.get(symbol), quote.get("last"), policy.tolerance_pct),
                "decision_effect": DECISION_EFFECT}
        except Exception as exc:
            errors_total += 1
            completed[symbol] = {"symbol": symbol, "market": market, "term": term, "state": "UNAVAILABLE",
                "captured_at": captured_at, "reason": f"{type(exc).__name__}:{str(exc)[:160]}",
                "decision_effect": DECISION_EFFECT}
        state["completed"], state["status"] = completed, "RUNNING"
        _atomic_json(checkpoint_file, state)

    metadata_store = {"schema_version": 1, "source": SOURCE, "mode": MODE, "entries": metadata_entries}
    _atomic_json(_metadata_path(root), metadata_store)
    state["status"], state["completed_at"] = "COMPLETE", now().isoformat()
    _atomic_json(checkpoint_file, state)
    # Preserve prior symbols so a rotating universe accumulates evidence instead
    # of replacing the dashboard with only the latest batch.
    prior_cache = _load_json(cache_path(root))
    prior_rows = {
        str(row.get("symbol") or "").upper(): row
        for row in (prior_cache.get("symbols") or []) if isinstance(row, dict) and row.get("symbol")
    }
    prior_rows.update({symbol: completed[symbol] for symbol in universe if symbol in completed})
    payload = {"schema_version": CACHE_SCHEMA_VERSION, "source": SOURCE, "mode": MODE,
        "decision_effect": DECISION_EFFECT, "live_decision_authority": False, "real_money_authorized": False,
        "run_id": active_run_id, "market": market, "term": term, "refreshed_at": state["completed_at"],
        "telemetry": {"batch_symbols": len(universe), "ready_in_batch": sum(
            1 for symbol in universe if completed.get(symbol, {}).get("state") == "READY"),
            "unavailable_in_batch": sum(1 for symbol in universe if completed.get(symbol, {}).get("state") != "READY"),
            "calls_total": rate_governor.calls_total, "calls_429": rate_governor.calls_429,
            "errors_total": errors_total},
        "symbols": [prior_rows[symbol] for symbol in sorted(prior_rows)]}
    _atomic_json(cache_path(root), payload)
    return payload
