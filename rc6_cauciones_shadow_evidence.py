"""RC6 read-only evidence contract for cauciones.

This module does not add cauciones to the operational universe. It defines the
minimum evidence needed to study them through PPI-primary/IOL-complementary
observations without inferring terms or authorizing trades.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

REQUIRED_FIELDS = (
    "instrument_id", "market", "currency", "settlement", "term_days",
    "rate", "amount", "guarantee", "liquidation_at", "provider_observed_at",
)
MAX_AGE_SECONDS = 120.0

def _utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None

def evaluate(primary: dict[str, Any], secondary: dict[str, Any], *,
             now: datetime | None = None) -> dict[str, Any]:
    p, s = primary or {}, secondary or {}
    fields = {}
    missing = []
    conflicts = []
    complemented = []
    for field in REQUIRED_FIELDS:
        pv, sv = p.get(field), s.get(field)
        if pv in (None, "") and sv not in (None, "") and field not in {
            "instrument_id", "market", "currency", "settlement", "term_days", "rate"
        }:
            complemented.append(field)
            fields[field] = {"state": "COMPLEMENTED_SECONDARY", "primary": pv, "secondary": sv}
        elif pv in (None, ""):
            missing.append(field)
            fields[field] = {"state": "MISSING", "primary": pv, "secondary": sv}
        elif sv in (None, ""):
            fields[field] = {"state": "PRIMARY_ONLY", "primary": pv, "secondary": sv}
        elif str(pv).upper() == str(sv).upper():
            fields[field] = {"state": "MATCH", "primary": pv, "secondary": sv}
        else:
            conflicts.append(field)
            fields[field] = {"state": "CONFLICT", "primary": pv, "secondary": sv}
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    freshness = {}
    for source, payload in (("PPI", p), ("IOL", s)):
        observed = _utc(payload.get("provider_observed_at"))
        age = (ref - observed).total_seconds() if observed else None
        freshness[source] = {"age_seconds": age,
                             "state": "FRESH" if age is not None and 0 <= age <= MAX_AGE_SECONDS
                             else "STALE" if age is not None else "UNKNOWN"}
    if conflicts:
        state = "BLOCKED_CONFLICT"
    elif any(item["state"] == "STALE" for item in freshness.values()):
        state = "BLOCKED_STALE"
    elif missing:
        state = "INSUFFICIENT_EVIDENCE"
    elif complemented:
        state = "READY_SHADOW_COMPLEMENTED"
    else:
        state = "READY_SHADOW"
    return {"schema_version": 1, "state": state, "fields": fields,
            "missing": missing, "conflicts": conflicts, "complemented": complemented,
            "freshness": freshness, "source_order": "PPI_PRIMARY_IOL_COMPLEMENTARY",
            "decision_effect": "OBSERVE_ONLY", "live_decision_authority": False,
            "real_money_authorized": False}
