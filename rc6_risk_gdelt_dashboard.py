"""Dashboard compatibility tombstone for retired GDELT.

No panel is rendered and no runtime/store import is performed.
"""
from __future__ import annotations

DEPRECATED = True
_installed = False


def render_section() -> str:
    return ""


def _decorate(page: str) -> str:
    return page


def install() -> None:
    global _installed
    _installed = True
    return None
