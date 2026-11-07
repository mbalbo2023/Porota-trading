"""Versioned operator PAPER entry holds, independent of catalogue readiness.

Observation and EXIT remain available.  A contract, a READY capability, the
global operational scope or a scanner mode cannot lift an operator HOLD.
"""

from bs_instrument_contracts import family_name


OPERATOR_PAPER_ENTRY_HOLDS = frozenset({"ETFS"})


def paper_entry_hold_reason(family):
    """Return a stable entry denial for a normalized family or an empty string.

    Unknown families are deliberately *not* normalized by a fallback: the
    existing caller's own family validator must reject them separately.
    """
    normalized = family_name(family)
    return "ETF_OPERATOR_HOLD" if normalized in OPERATOR_PAPER_ENTRY_HOLDS else ""
