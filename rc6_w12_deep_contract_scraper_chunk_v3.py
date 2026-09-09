#!/usr/bin/env python3
"""Chunk wrapper for rc6_w12_deep_contract_scraper_v2.

The base collector remains the single implementation. This wrapper narrows its
route set from W12_ROUTES so long live scraping can be split into short,
serial SSH sessions without weakening safety or evidence semantics.

V4 compatibility fix (kept in this wrapper path): before invoking the deep
collector, warm the trusted PPI landing page with the same persistent profile.
The proven trusted-browser collector authenticates via the landing page first;
directly opening /Cotizaciones/Acciones from a cold headless context can be
redirected by the PPI SSO/navigation flow even when the trusted profile is
valid.  This warm-up performs only GET/HEAD/OPTIONS and never clicks or visits
operational routes.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

import rc6_w12_deep_contract_scraper_v2 as base


def _arg_value(name: str, default: str = "") -> str:
    try:
        idx = sys.argv.index(name)
        return sys.argv[idx + 1]
    except (ValueError, IndexError):
        return default


def _warm_trusted_landing() -> bool:
    profile = _arg_value("--profile")
    chrome = _arg_value("--chrome", "/usr/bin/google-chrome-stable")
    if not profile:
        print("W12_TRUSTED_LANDING=PROFILE_REQUIRED")
        return False

    from playwright.sync_api import sync_playwright

    blocked = []
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=profile,
            executable_path=chrome,
            headless=True,
            locale="es-AR",
            timezone_id="America/Argentina/Buenos_Aires",
            viewport={"width": 1440, "height": 1000},
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        def guard(route, request):
            method = request.method.upper()
            if method not in base.SAFE_METHODS:
                blocked.append(method)
                return route.abort()
            return route.continue_()

        ctx.route("**/*", guard)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(base.BASE + "/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1000)
        u = urlsplit(page.url)
        authenticated = (
            u.scheme == "https"
            and u.netloc == "trading.portfoliopersonal.com"
            and "login" not in u.path.lower()
        )
        if authenticated:
            page.goto(base.BASE + "/Cotizaciones/Acciones", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1000)
            authenticated = base.safe_page_url(page.url)
        print("W12_TRUSTED_LANDING=" + ("GREEN" if authenticated else "BLOCKED"))
        print("W12_TRUSTED_LANDING_NONREAD_BLOCKED=" + str(len(blocked)))
        ctx.close()
    return authenticated


def main():
    requested = [x.strip() for x in os.getenv("W12_ROUTES", "").split(",") if x.strip()]
    if not requested:
        raise SystemExit("W12_ROUTES_REQUIRED")
    unknown = [x for x in requested if x not in base.ROUTES]
    if unknown:
        raise SystemExit("W12_UNKNOWN_ROUTES:" + ",".join(unknown))
    base.ROUTES = requested
    base.DETAIL_PRIORITY = set(base.DETAIL_PRIORITY) & set(requested)
    if not _warm_trusted_landing():
        return 4
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
