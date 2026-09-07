"""Pure RC6 table layout policy.

The operator-facing contract is invariant: native rows/columns are preserved on
all viewport widths.  Width pressure is handled by local table scrolling and
visual density, never by converting records into cards.

No FastAPI/dashboard imports so CI can exercise this policy directly.
"""
from __future__ import annotations


CLASSIC_TABLE_POLICY = "CLASSIC_ROWS_COLUMNS"


def should_force_compact(columns: int, available_width: float,
                         scroll_width: float | None = None) -> bool:
    """Compatibility predicate: RC6 never converts a table into cards."""
    _ = (columns, available_width, scroll_width)
    return False


def recommended_table_min_width(columns: int) -> int:
    """Readable local width used by the browser scroll container.

    Tables with fewer than six columns normally fit the card. Wider tables get
    a bounded minimum width so cells do not collapse into unreadable strips.
    """
    count = max(0, int(columns or 0))
    if count < 6:
        return 0
    return min(1320, max(700, count * 116))


def table_layout_policy() -> str:
    return CLASSIC_TABLE_POLICY
