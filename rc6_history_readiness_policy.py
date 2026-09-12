"""RC6 post-final per-identity history readiness policy.

Pure/source-only and deliberately fail-closed. It does not decide trading or
promote PAPER. A caller must provide freshness derived from the authoritative
trading calendar; this module never guesses calendar/session freshness.
"""
from __future__ import annotations

MIN_BACKTEST_BARS = 30


def required_bars_for_warmup(warmup: int) -> int:
    """Match the strategy-backtest guard: max(30, configured warmup)."""
    value = int(warmup)
    if value < 0:
        raise ValueError("NEGATIVE_WARMUP")
    return max(MIN_BACKTEST_BARS, value)


def evaluate_identity_history(
    *,
    bar_count: int,
    required_bars: int,
    exact_identity: bool,
    market_compatible: bool,
    freshness_ok: bool,
) -> dict:
    """Evaluate history prerequisites only; never execution/PAPER eligibility."""
    bars = max(0, int(bar_count))
    required = int(required_bars)
    if required < MIN_BACKTEST_BARS:
        raise ValueError("REQUIRED_BARS_BELOW_CONSUMER_FLOOR")
    reasons = []
    if not exact_identity:
        reasons.append("IDENTITY_NOT_EXACT")
    if not market_compatible:
        reasons.append("MARKET_NOT_COMPATIBLE")
    if bars < required:
        reasons.append("INSUFFICIENT_BARS")
    if not freshness_ok:
        reasons.append("FRESHNESS_NOT_PROVEN")
    ready = not reasons
    return {
        "history_ready": ready,
        "bar_count": bars,
        "required_bars": required,
        "exact_identity": bool(exact_identity),
        "market_compatible": bool(market_compatible),
        "freshness_ok": bool(freshness_ok),
        "reasons": reasons,
        "paper_candidate": False,
    }


def assert_no_execution_capability() -> None:
    forbidden = {"send_order", "place_order", "cancel_order", "confirm_order", "execute_order"}
    assert not (forbidden & set(globals()))
