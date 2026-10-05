"""Offline real-browser acceptance gate for issue #467.

Run with a development-only Playwright installation. It uses temporary
synthetic ledgers and intercepted HTTP, never a provider or deployed runtime.
The exact Python/financial contract remains automatically discovered pytest.
"""
import argparse
from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rc6_trader_dashboard.navigation import CANONICAL_PATHS, LEGACY
from rc6_trader_dashboard.routes import build_page
from tests.rc6_dashboard_native_fixture import native_fixture


def run(output):
    from playwright.sync_api import sync_playwright
    output.mkdir(parents=True, exist_ok=True)
    findings, requests, renders = [], [], []
    with tempfile.TemporaryDirectory(prefix="porota-ws08-browser-") as temporary:
        fixture = native_fixture(Path(temporary))
        fixture.broker.supervise_futures(fixture.as_of.isoformat())
        gc.collect()  # Finalize the native writer before browser read custody.
        database = fixture.database
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1440, "height": 980}, reduced_motion="reduce")
            def serve(route):
                request = route.request
                requests.append(request.url)
                parts = urlsplit(request.url)
                if parts.hostname != "terminal.test":
                    findings.append({"network": "UNEXPECTED_EXTERNAL_RESOURCE", "host": parts.hostname})
                    route.abort()
                    return
                if parts.path == "/favicon.ico":
                    route.fulfill(status=204)
                    return
                params = {key: values[-1] for key, values in parse_qs(parts.query).items()}
                html, headers = build_page(parts.path, params, database, now=fixture.as_of)
                renders.append({"path": parts.path, "width": page.viewport_size["width"], "queries": int(headers["X-Porota-Read-Queries"]), "server_timing": headers["Server-Timing"]})
                route.fulfill(status=200, content_type="text/html", body=html, headers=headers)
            page.route("**/*", serve)
            page.on("pageerror", lambda error: findings.append({"error": str(error)}))
            checks = 0
            for width in (1440, 1280, 1024, 800, 600, 360):
                page.set_viewport_size({"width": width, "height": 980})
                for path in CANONICAL_PATHS:
                    page.goto("http://terminal.test" + path, wait_until="load")
                    dimensions = page.evaluate("({viewport:innerWidth,body:document.body.scrollWidth,html:document.documentElement.scrollWidth})")
                    if max(dimensions["body"], dimensions["html"]) > width + 1:
                        findings.append({"path": path, "width": width, "overflow": dimensions})
                    if page.locator(".primary-nav > a").count() != 8:
                        findings.append({"path": path, "width": width, "navigation": "not eight"})
                    if page.locator("tr[data-row]").count() > 10:
                        findings.append({"path": path, "width": width, "rows": "over ten"})
                    duplicate_ids = page.locator("[id]").evaluate_all("els => {const seen=new Set();return els.map(el=>el.id).filter(id=>seen.has(id)||!seen.add(id))}")
                    if duplicate_ids:
                        findings.append({"path": path, "width": width, "duplicate_ids": duplicate_ids})
                    unnamed = page.locator("button, a, summary, input, select").evaluate_all("els => els.filter(el => !el.textContent.trim() && !el.getAttribute('aria-label') && !el.labels?.length).map(el=>el.outerHTML)")
                    if unnamed:
                        findings.append({"path": path, "width": width, "unnamed": unnamed})
                    checks += 1
                page.goto("http://terminal.test/instrumentos")
                page.screenshot(path=str(output / f"instrumentos-{width}.png"), full_page=False)
                print(f"BROWSER_VIEWPORT_GREEN={width}", flush=True)
            page.set_viewport_size({"width": 800, "height": 980})
            print("BROWSER_INTERACTION_START=manual", flush=True)
            page.goto("http://terminal.test/instrumentos?currency=ARS")
            detail_id = page.locator("details.row-detail").first.get_attribute("id")
            page.locator("details.row-detail summary").first.click()
            page.evaluate("window.scrollTo(0,250)")
            page.locator("#refresh-data").focus()
            y = page.evaluate("window.scrollY")
            page.locator("#refresh-data").click()
            page.wait_for_function("document.getElementById('refresh-status').textContent.includes('Datos actualizados')")
            assert page.locator("#" + detail_id).get_attribute("open") is not None
            assert page.locator("#filter-currency").input_value() == "ARS"
            assert page.evaluate("document.activeElement.id") == "refresh-data"
            assert abs(page.evaluate("window.scrollY") - y) < 2
            assert "/instrumentos?currency=ARS" in page.url
            print("BROWSER_INTERACTION_GREEN=manual", flush=True)
            page.locator("#filter-q").fill("pending-filter")
            page.locator("#refresh-data").click()
            assert page.locator("#filter-q").input_value() == "pending-filter"
            assert "Filtros pendientes" in page.locator("#refresh-status").inner_text()
            # If Voice Access starts a new interaction during an outstanding
            # read, the manual refresh must preserve that interaction too.
            print("BROWSER_INTERACTION_START=outstanding-read", flush=True)
            page.goto("http://terminal.test/instrumentos")
            page.evaluate("window.savedFetch=window.fetch; window.fetch=(...args)=>new Promise(resolve=>{window.resumeRefresh=()=>window.savedFetch(...args).then(resolve);}); void 0")
            page.locator("#refresh-data").click()
            page.locator("#filter-q").fill("interaction-during-read")
            print("BROWSER_INTERACTION_START=resume-read", flush=True)
            # Let the sync driver dispatch its route callback before awaiting
            # the response through a separate browser assertion.
            page.evaluate("window.resumeRefresh(); void 0")
            page.wait_for_function("document.getElementById('refresh-status').textContent.includes('interacción en curso')")
            assert page.locator("#filter-q").input_value() == "interaction-during-read"
            assert page.evaluate("document.activeElement.id") == "filter-q"
            print("BROWSER_INTERACTION_GREEN=outstanding-read", flush=True)
            # Exercise the actual interval callback without a blocking sleep.
            page.goto("http://terminal.test/instrumentos")
            page.clock.install()
            page.locator("#auto-refresh").click()
            page.locator("#filter-q").focus()
            before = len(requests)
            page.clock.fast_forward(60000)
            assert len(requests) == before, "automatic refresh while a control has focus"
            page.locator("#terminal-menu").click()
            assert page.locator("#terminal-menu").get_attribute("aria-expanded") == "true"
            page.keyboard.press("Escape")
            assert page.locator("#terminal-menu").get_attribute("aria-expanded") == "false"
            for path in LEGACY:
                page.goto("http://terminal.test" + path)
                assert page.locator("#terminal-content").get_attribute("data-view") == "/".join(LEGACY[path])
            page.goto("http://terminal.test/en-vivo?family=ACCIONES")
            before_funnel = page.locator("ol.funnel").inner_text()
            first_group = page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']").first.get_attribute("href")
            page.get_by_role("link", name="Mostrar 10 grupos más", exact=True).click()
            assert "funnel_offset=10" in page.url
            assert page.locator("ol.funnel").inner_text() == before_funnel
            assert page.get_by_role("link", name="Grupos anteriores", exact=True).count() == 1
            later_group = page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']").first
            assert later_group.get_attribute("href") != first_group
            later_group.click()
            assert "cohort=" in page.url and "funnel_offset=" not in page.url
            assert page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']").count() == 1
            assert "grupos 1–1 de 1" in page.locator("section.panel:has(ol.funnel)").inner_text()
            page.goto("http://terminal.test/en-vivo/posiciones?family=FUTUROS")
            assert page.locator("tr[data-row]").count() == 1
            assert "DLR/OCT26" in page.locator("tr[data-row]").inner_text()
            assert page.locator("tr[data-row] [data-status='WATCH_NO_QUOTE']").count() >= 1
            page.goto("http://terminal.test/analitica/salidas?lab=shadow")
            assert page.locator("tr[data-row]").count() == 1
            assert "T000" in page.locator("tr[data-row]").inner_text()
            page.goto("http://terminal.test/analitica/experimentos")
            assert page.locator("tr[data-row]").count() == 1
            page.goto("http://terminal.test/en-vivo/capacidad")
            assert page.locator("tr[data-row]").count() == 2
            assert page.locator("tr[data-row] > td:nth-child(2) [data-status='OFF']").count() == 2
            assert "WIRE_AND_PROJECTION_SEMANTICS" in page.locator(".data-panel .source-line").inner_text()
            assert set(fixture.cut["manifest"]["files"]) == {"report", "checkpoint", "status", "projection"}
            page.screenshot(path=str(output / "native-capacity-off.png"), full_page=False)
            browser.close()
    result = {"schema": "rc6.trader-terminal-browser-gate.v1", "status": "GREEN" if not findings else "RED",
              "widths": [1440, 1280, 1024, 800, 600, 360], "canonical_viewport_checks": checks,
              "legacy_checks": len(LEGACY), "interaction_checks": ["manual focus", "scroll", "details", "filters", "deep link", "dirty inputs", "interaction during outstanding read", "auto refresh pause", "menu Escape", "cohort pagination and exact selection"],
              "network": "all HTTP intercepted; native offline PAPER/SHADOW writers; provider_requests=0", "findings": findings, "renders": renders,
              "native_generation_schema": fixture.cut["pointer"]["schema"], "generation_id": fixture.cut["pointer"]["generation_id"],
              "native_generation_roles": sorted(fixture.cut["manifest"]["files"]),
              "native_generation_verification": "WIRE_AND_PROJECTION_SEMANTICS",
              "source_cut": fixture.as_of.isoformat(), "native_contract_checks": ["FUT ACTIVE visible", "FUT native supervision intent visible", "same-entry exit lab nonempty", "entry experiment nonempty", "OFF policy separate from OPEN evidence", "unique element IDs", "same four-role sealed generation", "explicit projected verification scope"],
              "as_of": datetime.now(timezone.utc).isoformat()}
    (output / "browser-gate.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    assert not findings, json.dumps(findings)
    print(json.dumps({k: v for k, v in result.items() if k != "renders"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        run(arguments.output)
    except Exception as error:
        arguments.output.mkdir(parents=True, exist_ok=True)
        (arguments.output/"browser-failure.json").write_text(json.dumps({
            "schema": "rc6.trader-terminal-browser-failure.v1", "status": "RED",
            "error_class": type(error).__name__, "message": str(error)[:2000],
            "as_of": datetime.now(timezone.utc).isoformat()}, sort_keys=True, indent=2)+"\n")
        raise
