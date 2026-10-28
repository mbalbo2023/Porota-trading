"""RC6 GDELT retirement tombstone.

GDELT is no longer a POROTA source.  This module remains only so an old import
cannot resurrect network collection during a fix-forward transition.  It does
not read/write the historical GDELT DB and never performs network I/O.
"""
from __future__ import annotations
import json
import os

DEPRECATED = True
DEFAULT_DB = os.environ.get("POROTA_GDELT_EVENT_DB", "/app/data/event_risk/gdelt_shadow_rc6.db")
DEFAULT_SAFETY_DB = os.environ.get("POROTA_PAPER_DB", "/app/data/paper_v17/observer_v17.db")
DEFAULT_EVENT_TYPES = ()
DEFAULT_MAXRECORDS = 0
MAX_EVENT_TYPES_PER_RUN = 0
MAX_STORED_EVENTS_FOR_DASHBOARD = 0


class GDELTEventRiskJobError(RuntimeError):
    pass


def _status() -> dict:
    return {
        "state": "DEPRECATED_EXCLUDED",
        "last_run_state": "DEPRECATED_EXCLUDED",
        "freshness": "NOT_APPLICABLE",
        "freshness_seconds": None,
        "authority": "NONE",
        "decision_effect": "EXCLUDED",
        "events_total": 0,
        "requested_event_types": 0,
        "successful_event_types": 0,
        "fetched_events": 0,
        "stored_events": 0,
        "errors": {},
        "generic_news_feed": "UNRELATED",
        "real_order_routes": "NOT_PRESENT",
        "deprecated": True,
    }


def assert_paper_safety(*_args, **_kwargs) -> None:
    return None


def run_once(*_args, **_kwargs) -> dict:
    return _status()


def latest_status(*_args, **_kwargs) -> dict:
    return _status()


def latest_events(*_args, **_kwargs) -> list[dict]:
    return []


def main() -> int:
    print(json.dumps(_status(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
