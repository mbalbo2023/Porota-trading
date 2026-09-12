"""RC6 pure SWING_PAPER overnight shadow policy.

This module is deliberately non-binding. It does not import the PAPER broker,
SQLite, PPI, HTTP clients, the exit supervisor or any order/execution surface.
It answers one counterfactual question only: if a position had explicitly been
admitted as SWING_PAPER, would the evidence support carrying it overnight?

Existing positions are never silently reclassified as swing. Intraday and
scalping positions remain force-flat candidates at EOD. Missing calendar,
quote, thesis, holding-horizon or gap-risk evidence fails closed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation


SWING_STYLE = "SWING_PAPER"
INTRADAY_STYLES = {"SCALPING_PAPER", "INTRADAY_PAPER"}
SHADOW_MODE = "SHADOW_ONLY"


@dataclass(frozen=True)
class SwingShadowVerdict:
    mode: str
    action: str
    reason: str
    execution_style: str
    overnight_eligible: bool
    eod_exit_binding: bool
    real_execution_allowed: bool
    holding_sessions: int
    max_holding_sessions: int
    gap_risk_fraction: str | None
    max_gap_risk_fraction: str | None
    economics_model: str

    def as_dict(self):
        return asdict(self)


def _decimal(value, label, *, nonnegative=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(label + "_INVALID") from exc
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError(label + "_INVALID")
    return result


def execution_style(position) -> str:
    """Return only an explicit execution style; never infer SWING_PAPER."""
    if not isinstance(position, dict):
        return "UNKNOWN"
    raw = position.get("features_json")
    features = raw if isinstance(raw, dict) else {}
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw or "{}")
            features = decoded if isinstance(decoded, dict) else {}
        except (ValueError, TypeError):
            return "UNKNOWN"
    style = str(features.get("execution_style") or "").strip().upper()
    return style or "UNKNOWN"


def _verdict(*, action, reason, style, eligible, holding_sessions,
             max_holding_sessions, gap=None, gap_limit=None):
    return SwingShadowVerdict(
        mode=SHADOW_MODE,
        action=action,
        reason=reason,
        execution_style=style,
        overnight_eligible=eligible,
        eod_exit_binding=False,
        real_execution_allowed=False,
        holding_sessions=holding_sessions,
        max_holding_sessions=max_holding_sessions,
        gap_risk_fraction=None if gap is None else str(gap),
        max_gap_risk_fraction=None if gap_limit is None else str(gap_limit),
        economics_model="SWING_NON_INTRADAY",
    )


def evaluate_overnight_shadow(
    position,
    *,
    mark_price,
    quote_fresh,
    calendar_verified,
    next_session_verified,
    thesis_valid,
    holding_sessions,
    max_holding_sessions,
    gap_risk_fraction,
    max_gap_risk_fraction,
    underlying_calendar_verified=True,
    underlying_next_session_open=True,
):
    """Evaluate overnight eligibility without changing any live PAPER decision.

    `gap_risk_fraction` is an externally produced conservative estimate. This
    policy never invents one. CEDEARs additionally require the underlying
    calendar to be verified and open for the next session.
    """
    style = execution_style(position)
    try:
        held = int(holding_sessions)
        max_held = int(max_holding_sessions)
    except (TypeError, ValueError) as exc:
        raise ValueError("HOLDING_SESSIONS_INVALID") from exc
    if held < 0 or max_held <= 0:
        raise ValueError("HOLDING_SESSIONS_INVALID")

    if style in INTRADAY_STYLES:
        return _verdict(
            action="FORCE_FLAT", reason="INTRADAY_STYLE_EOD",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held)
    if style != SWING_STYLE:
        return _verdict(
            action="FAIL_CLOSED", reason="SWING_STYLE_NOT_EXPLICIT",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held)
    if not calendar_verified or not next_session_verified:
        return _verdict(
            action="FAIL_CLOSED", reason="NEXT_SESSION_CALENDAR_UNVERIFIED",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held)
    if not quote_fresh:
        return _verdict(
            action="FAIL_CLOSED", reason="EOD_MARK_NOT_FRESH",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held)
    if not thesis_valid:
        return _verdict(
            action="FORCE_FLAT", reason="SWING_THESIS_INVALID",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held)
    if held >= max_held:
        return _verdict(
            action="FORCE_FLAT", reason="SWING_MAX_HOLDING_SESSIONS_REACHED",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held)

    asset_class = str(position.get("asset_class") or "").strip().upper()
    if asset_class in {"CEDEAR", "CEDEARS"}:
        if not underlying_calendar_verified:
            return _verdict(
                action="FAIL_CLOSED", reason="UNDERLYING_CALENDAR_UNVERIFIED",
                style=style, eligible=False, holding_sessions=held,
                max_holding_sessions=max_held)
        if not underlying_next_session_open:
            return _verdict(
                action="FORCE_FLAT", reason="UNDERLYING_NEXT_SESSION_CLOSED",
                style=style, eligible=False, holding_sessions=held,
                max_holding_sessions=max_held)

    try:
        mark = _decimal(mark_price, "MARK_PRICE")
        stop = _decimal(position.get("stop_price"), "STOP_PRICE")
        target = _decimal(position.get("target_price"), "TARGET_PRICE")
        gap = _decimal(gap_risk_fraction, "GAP_RISK", nonnegative=True)
        gap_limit = _decimal(max_gap_risk_fraction, "MAX_GAP_RISK", nonnegative=True)
    except ValueError as exc:
        return _verdict(
            action="FAIL_CLOSED", reason=str(exc), style=style, eligible=False,
            holding_sessions=held, max_holding_sessions=max_held)

    if mark <= 0 or stop <= 0 or target <= 0 or not stop < target:
        return _verdict(
            action="FAIL_CLOSED", reason="PRICE_CONTRACT_INVALID",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held, gap=gap, gap_limit=gap_limit)
    if mark <= stop:
        return _verdict(
            action="FORCE_FLAT", reason="STOP_ALREADY_REACHED",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held, gap=gap, gap_limit=gap_limit)
    if mark >= target:
        return _verdict(
            action="FORCE_FLAT", reason="TARGET_ALREADY_REACHED",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held, gap=gap, gap_limit=gap_limit)
    if gap > gap_limit:
        return _verdict(
            action="FORCE_FLAT", reason="OVERNIGHT_GAP_RISK_EXCEEDS_LIMIT",
            style=style, eligible=False, holding_sessions=held,
            max_holding_sessions=max_held, gap=gap, gap_limit=gap_limit)

    return _verdict(
        action="CARRY_OVERNIGHT", reason="EXPLICIT_SWING_EVIDENCE_PASSES_SHADOW_POLICY",
        style=style, eligible=True, holding_sessions=held,
        max_holding_sessions=max_held, gap=gap, gap_limit=gap_limit)


def assert_shadow_only() -> None:
    assert SHADOW_MODE == "SHADOW_ONLY"
    assert SWING_STYLE not in INTRADAY_STYLES
