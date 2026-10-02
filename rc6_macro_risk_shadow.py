"""Compatibility tombstone for the retired RC6 BCRA macro shadow feed.

Operator decision 2026-10-02: BCRA macro context is not an active POROTA
input.  Historical cache files are preserved as audit evidence, but this
module performs no network access, no SQLite reads and has no decision effect.
"""
from __future__ import annotations

BCRA_ACTIVE = False
RETIREMENT_REASON = "RETIRED_OPERATOR_DECISION_2026-10-02"

def collect(*_args, **_kwargs):
    return {
        "mode": "RETIRED",
        "state": RETIREMENT_REASON,
        "decision_effect": "NONE",
        "source": "NONE",
        "indicators": {},
        "feature_version": "rc6-bcra-retired-v1",
    }
