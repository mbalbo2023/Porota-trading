"""RC6 GDELT retirement tombstone.

GDELT was explicitly deprecated and excluded from POROTA on 2026-10-02.
This compatibility module intentionally has no HTTP client, endpoint, query
pack, scheduler integration, decision authority or order capability.
"""
from __future__ import annotations

DEPRECATED = True
GDELT_DOC_URL = None
GDELT_HOST = None
QUERY_PACKS = {}
MAX_RECORDS_LIMIT = 0


class GDELTShadowError(RuntimeError):
    pass


def _retired(*_args, **_kwargs):
    raise GDELTShadowError("GDELT_DEPRECATED_EXCLUDED")


def build_params(*_args, **_kwargs):
    return _retired()


def fetch_articles(*_args, **_kwargs):
    return _retired()


def normalize_article(*_args, **_kwargs):
    return _retired()


def normalize_articles(*_args, **_kwargs):
    return []


def collect_shadow(*_args, **_kwargs):
    return _retired()


def is_market_relevant_title(*_args, **_kwargs):
    return False


def assert_shadow_only():
    assert DEPRECATED is True
    return True
