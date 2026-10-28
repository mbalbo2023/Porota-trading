#!/usr/bin/env python3
"""Read-only IOL family-cache freshness gate for Deploy V2.

Only the workflow can start the allowlisted OBSERVE_ONLY collector service.
This gate never calls a provider or alters cached prices/timestamps.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat


FALLBACK_ORDER = [
    "IOL_LIVE_BOUNDED_RETRY", "IOL_LAST_KNOWN_GOOD",
    "PPI_PRIMARY", "BYMA_PUBLIC_COMPLEMENTARY",
]
CONTINUATION_STATE = "CONTINUE_WITH_PROVENANCE_NEVER_ZERO_FILL"
REFRESH_AT_SECONDS = 180
LIVE_FRESH_MAX_SECONDS = 240
MAX_REFERENCE_BYTES = 8 * 1024 * 1024


class RefreshGateError(ValueError):
    """The cached source cannot prove the requested state."""


def _stamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RefreshGateError("IOL_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise RefreshGateError("IOL_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _age(value: object, now: datetime) -> float:
    return (now - _stamp(value)).total_seconds()


def _contract(payload: dict) -> str:
    if (payload.get("continuation_state") != CONTINUATION_STATE
            or payload.get("fallback_order") != FALLBACK_ORDER
            or payload.get("real_money_authorized") is True):
        raise RefreshGateError("IOL_FAILSAFE_CONTRACT_INVALID")
    sections = payload.get("section_states")
    if not isinstance(sections, dict) or not sections:
        raise RefreshGateError("IOL_SECTION_STATES_MISSING")
    state = str(payload.get("cache_state") or "").upper()
    if state not in {"LIVE_FRESH", "CACHE_FRESH", "CACHE_STALE", "SOURCE_UNAVAILABLE"}:
        raise RefreshGateError("IOL_CACHE_STATE_INVALID")
    if state != "SOURCE_UNAVAILABLE" and not payload.get("last_known_good_at"):
        raise RefreshGateError("IOL_LKG_MISSING")
    return state


def _live_section_proven(payload: dict) -> bool:
    live = payload.get("live_sections")
    sections = payload.get("section_states") or {}
    return (isinstance(live, list) and bool(live)
            and any(section in sections and sections[section] == "LIVE_FRESH"
                    for section in live))


def plan(payload: dict, now: datetime) -> str:
    """Avoid unnecessary 40-call collector cycles when source proof is fresh."""
    state = _contract(payload)
    if state != "LIVE_FRESH":
        # The postdeploy acceptance needs a current observation even when
        # historical fallback is valid for ordinary PAPER consumption.
        return "REFRESH_REQUIRED"
    try:
        ages = [_age(payload.get(key), now)
                for key in ("last_known_good_at", "refreshed_at")]
    except RefreshGateError:
        return "REFRESH_REQUIRED"
    if (payload.get("source") != "IOL_MCP"
            or payload.get("decision_effect") != "OBSERVE_ONLY"
            or payload.get("real_money_authorized") is not False
            or not _live_section_proven(payload)
            or any(not 0 <= age < REFRESH_AT_SECONDS for age in ages)):
        return "REFRESH_REQUIRED"
    return "FRESH"


def verify(payload: dict, started_at: datetime, now: datetime) -> None:
    """Require a new real provider observation from the just-started service."""
    state = _contract(payload)
    if state != "LIVE_FRESH" or payload.get("source") != "IOL_MCP":
        raise RefreshGateError("IOL_COLLECTOR_LIVE_SOURCE_UNPROVEN")
    if payload.get("decision_effect") != "OBSERVE_ONLY" or payload.get("real_money_authorized") is not False:
        raise RefreshGateError("IOL_COLLECTOR_OBSERVE_ONLY_UNPROVEN")
    if not _live_section_proven(payload):
        raise RefreshGateError("IOL_COLLECTOR_LIVE_SECTION_UNPROVEN")
    for field in ("last_known_good_at", "refreshed_at"):
        observed = _stamp(payload.get(field))
        age = (now - observed).total_seconds()
        if observed < started_at or not 0 <= age <= LIVE_FRESH_MAX_SECONDS:
            raise RefreshGateError("IOL_COLLECTOR_NEW_SAMPLE_UNPROVEN:" + field)


def _reference(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        meta = os.fstat(descriptor)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1
                or not 0 < meta.st_size <= MAX_REFERENCE_BYTES):
            raise RefreshGateError("IOL_REFERENCE_FILE_UNSAFE")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise RefreshGateError("IOL_REFERENCE_NOT_OBJECT")
        return payload
    finally:
        if descriptor != -1:
            os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=("plan", "verify"), required=True)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--started-at")
    args = parser.parse_args(argv)
    try:
        payload = _reference(args.path)
        now = datetime.now(timezone.utc)
        if args.action == "plan":
            decision = plan(payload, now)
            print("RC6_IOL_DEPLOY_REFRESH=" + decision)
            return 10 if decision == "REFRESH_REQUIRED" else 0
        if not args.started_at:
            raise RefreshGateError("IOL_START_TIMESTAMP_MISSING")
        verify(payload, _stamp(args.started_at), now)
        print("RC6_IOL_DEPLOY_REFRESH=GREEN|LIVE_FRESH|OBSERVE_ONLY|REAL_ROUTES=[]")
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, RefreshGateError) else type(exc).__name__
        print("RC6_IOL_DEPLOY_REFRESH=RED|" + reason)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
