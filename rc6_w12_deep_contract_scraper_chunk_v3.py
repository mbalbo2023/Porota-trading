#!/usr/bin/env python3
"""Chunk wrapper for rc6_w12_deep_contract_scraper_v2.

The base collector remains the single implementation. This wrapper narrows its
route set from W12_ROUTES so long live scraping can be split into short,
serial SSH sessions without weakening safety or evidence semantics.
"""
from __future__ import annotations

import os

import rc6_w12_deep_contract_scraper_v2 as base


def main():
    requested = [x.strip() for x in os.getenv("W12_ROUTES", "").split(",") if x.strip()]
    if not requested:
        raise SystemExit("W12_ROUTES_REQUIRED")
    unknown = [x for x in requested if x not in base.ROUTES]
    if unknown:
        raise SystemExit("W12_UNKNOWN_ROUTES:" + ",".join(unknown))
    base.ROUTES = requested
    base.DETAIL_PRIORITY = set(base.DETAIL_PRIORITY) & set(requested)
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
