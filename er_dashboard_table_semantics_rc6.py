"""Pure RC6 table compaction decision rules.

No FastAPI/dashboard imports so CI can exercise the responsive decision logic
without installing the runtime web stack.
"""
from __future__ import annotations


def should_force_compact(columns: int, available_width: float,
                         scroll_width: float | None = None) -> bool:
    if columns <= 1:
        return False
    available = max(float(available_width or 0), 1.0)
    per_column = available / columns
    overflow = scroll_width is not None and float(scroll_width) > available + 4
    return columns >= 7 or per_column < 128 or overflow
