"""RC6 GDELT compatibility reader.

The old generic GDELT cache is deliberately retired.  RC6 has exactly one
network collector: rc6_gdelt_event_risk_job.  This module keeps legacy
dashboard callers read-only while projecting the structured store; it never
performs HTTP and never participates in decisions or orders.
"""
from __future__ import annotations

from pathlib import Path

CACHE_VERSION = 2
DEFAULT_ENDPOINT = "STRUCTURED_EVENT_RISK_STORE"


def cache_path(root: Path | str | None = None) -> Path:
    """Legacy path retained only for callers that display its location."""
    return Path(root) / "gdelt_shadow_latest.json" if root is not None else Path(
        "/app/data/event_risk/gdelt_shadow_rc6.db"
    )


def refresh(root: Path | str | None = None, *, opener=None) -> dict:
    """Never refresh the retired generic cache.

    The scheduler invokes rc6_gdelt_event_risk_job.run_once() instead.  Keeping
    this explicit makes an accidental second network feed visible and harmless.
    """
    del root, opener
    return {
        "schema_version": CACHE_VERSION,
        "mode": "SHADOW",
        "state": "RETIRED_GENERIC_COLLECTOR",
        "decision_effect": "OBSERVE_ONLY",
        "source": DEFAULT_ENDPOINT,
        "reason": "USE_RC6_GDELT_EVENT_RISK_JOB",
        "articles": [],
    }


def collect(root: Path | str | None = None) -> dict:
    """Read the structured local status only; no network and no broker access."""
    del root
    try:
        from rc6_gdelt_event_risk_job import latest_status
        status = dict(latest_status() or {})
    except Exception as exc:
        status = {
            "state": "READ_ERROR",
            "freshness": "UNKNOWN",
            "reason": f"{type(exc).__name__}:{exc}",
        }
    return {
        "mode": "SHADOW",
        "state": status.get("state", "NOT_RUN"),
        "decision_effect": "OBSERVE_ONLY",
        "source": DEFAULT_ENDPOINT,
        "refreshed_at": status.get("finished_at"),
        "articles_count": int(status.get("events_total") or 0),
        "freshness": status.get("freshness", "UNKNOWN"),
        "reason": status.get("reason") or status.get("errors_json"),
    }


def assert_shadow_only():
    sample = collect()
    assert sample["mode"] == "SHADOW"
    assert sample["decision_effect"] == "OBSERVE_ONLY"
