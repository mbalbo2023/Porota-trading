"""RC6 compatibility tombstone for the retired GDELT source."""
from __future__ import annotations
from pathlib import Path

DEPRECATED = True
CACHE_VERSION = 3
DEFAULT_ENDPOINT = "DEPRECATED_EXCLUDED"


def cache_path(root: Path | str | None = None) -> Path:
    return Path(root) / "gdelt_retired" if root is not None else Path("/dev/null")


def refresh(*_args, **_kwargs) -> dict:
    return collect()


def collect(*_args, **_kwargs) -> dict:
    return {
        "mode": "NONE",
        "state": "DEPRECATED_EXCLUDED",
        "decision_effect": "EXCLUDED",
        "source": DEFAULT_ENDPOINT,
        "refreshed_at": None,
        "articles_count": 0,
        "freshness": "NOT_APPLICABLE",
        "reason": "GDELT_DEPRECATED_EXCLUDED",
    }


def assert_shadow_only():
    assert DEPRECATED is True
    return True
