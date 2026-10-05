"""Browser gate over a completed, frozen native 12000/60000 generation.

Unlike the normal browser gate, this runner never constructs or supervises a
writer. It receives the external synthetic DB/root and checks their custody
before and after the browser. All render timings include the source snapshot.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.rc6_browser_ipc import (
    GateFailure, ProductClient, imported_source, output_guard, require,
)


def run(database, root, output, *, product_python=None, index=None, diagnostic=False, product_python_version=None,
        client_sink=None, diagnostic_deadline=None, require_complete_index=False):
    output_guard(output, database, root)
    database, root = database.resolve(), root.resolve()
    require(database.is_file() and root.is_dir(), "COMPLETED_NATIVE_FIXTURE_REQUIRED")
    browser_requests, renders = [], []
    with ProductClient(product_python or sys.executable, index=index, diagnostic=diagnostic,
                       python_version=product_python_version, diagnostic_deadline=diagnostic_deadline,
                       require_complete_index=require_complete_index) as product:
        if client_sink is not None:
            client_sink.append(product)
        preflight = product.request("initialize", mode="LARGE", database=str(database), root=str(root))
        pointer, cut_at = preflight["pointer"], preflight["source_cut"]
        CANONICAL_PATHS, LEGACY = preflight["canonical_paths"], preflight["legacy"]
        VERIFICATION_LEVEL = preflight["verification_level"]
        catalog_count, observation_count = preflight["catalog_full_identities"], preflight["observations"]
        last_identity, total_groups = preflight["last_identity"], preflight["funnel_groups"]
        # Import/launch occurs only after the large/OPEN gates passed. Small
        # self-tests can prove rejection without starting a browser or writer.
        from playwright.sync_api import sync_playwright
        output.mkdir(parents=True, exist_ok=False)
        findings, checks = [], 0
        widths = [1440, 1280, 1024, 800, 600, 360]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox", "--disable-background-networking"])
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 980}, reduced_motion="reduce",
                                              service_workers="block")
                page = context.new_page()
                page.on("pageerror", lambda error: findings.append({"gate": "BROWSER_PAGE_ERROR", "error_class": type(error).__name__}))
                def serve(route):
                    request = route.request
                    parts = urlsplit(request.url)
                    browser_requests.append(request.url)
                    if parts.hostname != "terminal.test":
                        findings.append({"gate": "UNEXPECTED_EXTERNAL_RESOURCE"})
                        route.abort()
                        return
                    if parts.path == "/favicon.ico":
                        route.fulfill(status=204)
                        return
                    params = {key: values[-1] for key, values in parse_qs(parts.query).items()}
                    try:
                        response = product.render(parts.path, params)
                    except GateFailure as failure:
                        findings.append({"gate": str(failure), "path": parts.path, "filters": params,
                            "width": page.viewport_size["width"], "details": failure.details})
                        route.fulfill(status=503, content_type="text/html", body="Native request rejected.")
                        return
                    html, headers = response["html"], response["headers"]
                    require(response["pointer"] == pointer and response["source_cut"] == cut_at, "RENDER_CHANGED_COMMITTED_CUT")
                    elapsed = response["request_wall_seconds_including_ipc_html_json"]
                    record = {"path": parts.path, "filters": params, "width": page.viewport_size["width"],
                              "elapsed_seconds": elapsed, "html_bytes": response["html_bytes"],
                              "response_json_bytes": response["response_json_bytes"],
                              "native_elapsed_seconds": response["native_elapsed_seconds"],
                              "process_cpu_seconds": response["native_process_cpu_seconds"],
                              "queries": int(headers["X-Porota-Read-Queries"]), "server_timing": headers["Server-Timing"]}
                    renders.append(record)
                    if elapsed > 1 or max(record["html_bytes"], record["response_json_bytes"]) >= 4*1024**2:
                        findings.append({"gate": "RENDER_EXCEEDS_REQUEST_BUDGET", **record})
                    if any(reason in html for reason in ("SOURCE_SNAPSHOT_REJECTED", "COMMITTED_PROJECTION_REJECTED",
                            "COMMITTED_GENERATION_REJECTED", "PROJECTED_NATIVE_ROW_CONTRACT_REJECTED",
                            "Corte de lectura no disponible dentro del presupuesto")):
                        findings.append({"gate": "RENDER_DISCARDED_VERIFIED_CUT", "path": parts.path})
                    route.fulfill(status=200, content_type="text/html", body=html, headers=headers)
                context.route("**/*", serve)
                for width in widths:
                    page.set_viewport_size({"width": width, "height": 980})
                    for path in CANONICAL_PATHS:
                        page.goto("http://terminal.test"+path, wait_until="load")
                        require(not findings, "BROWSER_RENDER_FAILED", {"findings": findings,
                                "canonical_viewport_checks_completed": checks, "renders_measured": renders})
                        dimensions = page.evaluate("({body:document.body.scrollWidth,html:document.documentElement.scrollWidth})")
                        require(max(dimensions.values()) <= width+1, "BROWSER_HORIZONTAL_OVERFLOW", {"path": path, "width": width})
                        require(page.locator(".primary-nav > a").count() == 8, "EIGHT_DESTINATIONS_NOT_PRESERVED")
                        require(page.locator("tr[data-row]").count() <= 10, "BROWSER_TABLE_OVER_TEN_ROWS")
                        duplicate = page.locator("[id]").evaluate_all("els => {const seen=new Set();return els.map(el=>el.id).filter(id=>seen.has(id)||!seen.add(id))}")
                        require(not duplicate, "BROWSER_DUPLICATE_IDS")
                        unnamed = page.locator("button, a, summary, input, select").evaluate_all("els => els.filter(el => !el.textContent.trim() && !el.getAttribute('aria-label') && !el.labels?.length).map(el=>el.tagName)")
                        require(not unnamed, "BROWSER_UNNAMED_VOICE_ACCESS_CONTROLS")
                        checks += 1
                    page.goto("http://terminal.test/instrumentos")
                    page.screenshot(path=str(output/f"native-large-instrumentos-{width}.png"), full_page=False)
                    print(f"NATIVE_LARGE_BROWSER_VIEWPORT_GREEN={width}", flush=True)
                page.set_viewport_size({"width": 800, "height": 980})
                page.goto("http://terminal.test/instrumentos?currency=ARS")
                detail = page.locator("details.row-detail").first.get_attribute("id")
                page.locator("details.row-detail summary").first.click()
                page.evaluate("window.scrollTo(0,250)")
                page.locator("#refresh-data").focus()
                scroll = page.evaluate("window.scrollY")
                page.locator("#refresh-data").click()
                page.wait_for_function("document.getElementById('refresh-status').textContent.includes('Datos actualizados')")
                require(page.locator("#"+detail).get_attribute("open") is not None, "REFRESH_LOST_DETAILS")
                require(page.locator("#filter-currency").input_value() == "ARS" and page.evaluate("document.activeElement.id") == "refresh-data",
                        "REFRESH_LOST_FILTER_OR_FOCUS")
                require(abs(page.evaluate("window.scrollY")-scroll) < 2 and "/instrumentos?currency=ARS" in page.url,
                        "REFRESH_LOST_SCROLL_OR_DEEP_LINK")
                page.locator("#filter-q").fill("pending-filter")
                page.locator("#refresh-data").click()
                require(page.locator("#filter-q").input_value() == "pending-filter" and "Filtros pendientes" in page.locator("#refresh-status").inner_text(),
                        "REFRESH_LOST_DIRTY_INPUT")
                page.goto("http://terminal.test/instrumentos")
                page.evaluate("window.savedFetch=window.fetch; window.fetch=(...args)=>new Promise(resolve=>{window.resumeRefresh=()=>window.savedFetch(...args).then(resolve);}); void 0")
                page.locator("#refresh-data").click()
                page.locator("#filter-q").fill("interaction-during-read")
                page.evaluate("window.resumeRefresh(); void 0")
                page.wait_for_function("document.getElementById('refresh-status').textContent.includes('interacción en curso')")
                require(page.locator("#filter-q").input_value() == "interaction-during-read" and page.evaluate("document.activeElement.id") == "filter-q",
                        "OUTSTANDING_READ_LOST_INTERACTION")
                page.goto("http://terminal.test/instrumentos")
                page.clock.install()
                page.locator("#auto-refresh").click()
                page.locator("#filter-q").focus()
                requests_before = len(browser_requests)
                page.clock.fast_forward(60000)
                require(len(browser_requests) == requests_before, "AUTO_REFRESH_IGNORED_CONTROL_FOCUS")
                page.locator("#terminal-menu").click()
                require(page.locator("#terminal-menu").get_attribute("aria-expanded") == "true", "MENU_DID_NOT_OPEN")
                page.keyboard.press("Escape")
                require(page.locator("#terminal-menu").get_attribute("aria-expanded") == "false", "ESCAPE_DID_NOT_CLOSE_MENU")
                for path in LEGACY:
                    page.goto("http://terminal.test"+path)
                    require(page.locator("#terminal-content").get_attribute("data-view") == "/".join(LEGACY[path]), "ALIAS_CHANGED_SUBVIEW")
                family_query = {"family": last_identity[1]}
                page.goto("http://terminal.test/en-vivo?"+urlencode(family_query))
                first_counts = page.locator("ol.funnel").inner_text()
                first_group = page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']").first.get_attribute("href")
                page.get_by_role("link", name="Mostrar 10 grupos más", exact=True).click()
                require("funnel_offset=10" in page.url and page.locator("ol.funnel").inner_text() == first_counts,
                        "COHORT_PAGE_CHANGED_SELECTED_AGGREGATES")
                require(page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']").first.get_attribute("href") != first_group,
                        "COHORT_PAGES_OVERLAP")
                page.goto("http://terminal.test/en-vivo?"+urlencode({**family_query, "funnel_offset": total_groups-10}))
                require(page.locator("ol.funnel").inner_text() == first_counts, "LAST_COHORT_PAGE_CHANGED_SELECTED_AGGREGATES")
                page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']").last.click()
                require("cohort=" in page.url and "funnel_offset=" not in page.url and
                        "grupos 1–1 de 1" in page.locator("section.panel:has(ol.funnel)").inner_text(), "LAST_COHORT_UNREACHABLE")
                page.goto("http://terminal.test/en-vivo/oportunidades?"+urlencode({"q": last_identity[0], "currency": last_identity[3]}))
                require(page.locator("tr[data-row]").count() == 2 and all(last_identity[0] in text for text in page.locator("tr[data-row]").all_inner_texts()),
                        "LAST_CATALOG_IDENTITY_UNREACHABLE")
                require(VERIFICATION_LEVEL in page.locator(".data-panel .source-line").inner_text(), "FALSE_BROWSER_VERIFICATION_SCOPE")
                require(not findings, "BROWSER_RENDER_FAILED", {"findings": findings,
                        "canonical_viewport_checks_completed": checks, "renders_measured": renders})
            except Exception:
                if findings:
                    raise GateFailure("BROWSER_RENDER_FAILED", {"findings": findings,
                        "canonical_viewport_checks_completed": checks, "renders_measured": renders})
                raise
            finally:
                browser.close()
    native_proof = product.finish_receipt
    closure, unexpected = imported_source(product.root, product.source_before)
    require(not unexpected and all(row["matches_archived_blob"] for row in closure), "DRIVER_SOURCE_IMPORT_PROOF_FAILED")
    require(not any(name.startswith(("rc6_trader_dashboard", "rc6_shadow_runtime", "be_paper_engine",
        "rc6_paper_family_lifecycle")) for name in sys.modules), "DRIVER_EXECUTED_PRODUCT_MODULE")
    return {"schema": "rc6.dashboard-native-large-browser-proof.v1", "status": "GREEN",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pointer": pointer, "source_cut": cut_at, "native_generation_roles": preflight["native_generation_roles"],
        "verification_level": VERIFICATION_LEVEL, "custody": preflight["custody"],
        "catalog_full_identities": catalog_count, "observations": observation_count, "planner_rows": preflight["planner_rows"],
        "funnel_groups": total_groups, "widths": widths, "canonical_viewport_checks": checks,
        "legacy_checks": len(LEGACY), "interaction_checks": ["manual focus", "scroll", "details", "filters", "deep link", "dirty inputs",
            "interaction during outstanding read", "auto refresh pause", "menu Escape", "cohort pagination and final selection", "last catalog identity"],
        "network_attempts": 0, "provider_requests": 0, "source_sqlite_opens": 0,
        "source_custody_inventory_unchanged": True, "source_inventory": native_proof["custody_inventory_before"], "renders": renders,
        "product_proof": native_proof, "product_environment": product.product_environment,
        "driver_environment": product.driver_environment, "driver_imported_source": closure,
        "tracked_source_hashes_and_modes_unchanged": product.source_before == product.source_after,
        "request_timing_scope": "DRIVER_ROUNDTRIP_INCLUDES_IPC_NATIVE_SNAPSHOT_RENDER_HTML_JSON"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-python", type=Path, required=True)
    parser.add_argument("--product-python-version", choices=("3.11", "3.12"), default="3.11")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output_guard(args.output, args.database, args.root)
    except GateFailure as error:
        parser.error(str(error))
    try:
        result = run(args.database, args.root, args.output, product_python=args.product_python,
                     index=args.index, product_python_version=args.product_python_version, require_complete_index=True)
    except Exception as error:
        result = {"schema": "rc6.dashboard-native-large-browser-proof.v1", "status": "RED",
            "recorded_at": datetime.now(timezone.utc).isoformat(), "error_class": type(error).__name__,
            "gate": str(error) if isinstance(error, GateFailure) else "NATIVE_BROWSER_REJECTED",
            "details": error.details if isinstance(error, GateFailure) else {}}
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/"browser-gate.json").open("x") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps({key: result[key] for key in ("status", "schema", "gate") if key in result}))
    raise SystemExit(0 if result["status"] == "GREEN" else 1)
