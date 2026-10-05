"""Browser gate over a completed, frozen native 12000/60000 generation.

Unlike the normal browser gate, this runner never constructs or supervises a
writer. It receives the external synthetic DB/root and checks their custody
before and after the browser. All render timings include the source snapshot.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
from time import monotonic, perf_counter
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rc6_audit_evidence.sqlite_snapshot import readonly_copy
from rc6_shadow_runtime import persistence
from rc6_trader_dashboard.datasets import shadow_rows
from rc6_trader_dashboard.navigation import CANONICAL_PATHS, LEGACY
from rc6_trader_dashboard.projected_generation import VERIFICATION_LEVEL
from rc6_trader_dashboard.projection import Projection, Store
from rc6_trader_dashboard.routes import build_page
from tests.ci_rc6_projection_large_reader import (
    GateFailure, custody_inventory, protected_bytes, require,
    require_native_large_cut, source_sqlite_guard,
)


def run(database, root, output):
    require(not any(member.is_symlink() for path in (database, root) for member in (path, *path.parents)),
            "SOURCE_ALIAS_FORBIDDEN")
    database, root = database.resolve(), root.resolve()
    require(database.is_file() and root.is_dir(), "COMPLETED_NATIVE_FIXTURE_REQUIRED")
    require(not output.is_symlink() and not output.resolve().is_relative_to(root)
            and not output.resolve().is_relative_to(Path(str(root)+".authority"))
            and output.resolve() not in {Path(str(database)+suffix) for suffix in ("", "-wal", "-shm", "-journal")}
            and not output.exists(), "OUTPUT_MUST_BE_NEW_AND_OUTSIDE_SOURCES")
    before = custody_inventory(database, root)
    pointer = json.loads(protected_bytes(root/"CURRENT.json"))
    manifest = json.loads(protected_bytes(root/("gen-"+pointer["generation_id"])/"manifest.json"))
    require(set(manifest["files"]) == {"report", "checkpoint", "status", "projection"}, "FOUR_ROLES_REQUIRED")
    cut_at = datetime.fromisoformat(manifest["as_of"].replace("Z", "+00:00"))
    forbidden = {manifest["files"][role]["payload_digest"] for role in ("report", "checkpoint")}
    original_decode = persistence.decode_storage
    network, source_calls, browser_requests, renders = [], [], [], []

    def no_network(*_args, **_kwargs):
        network.append("BLOCKED")
        raise GateFailure("PROVIDER_OR_NETWORK_CALLED")

    def bounded_decode(value, *args, **kwargs):
        if isinstance(value, dict):
            require(value.get("logical_sha256") not in forbidden, "ORIGINAL_REPORT_OR_CHECKPOINT_DECODED")
        return original_decode(value, *args, **kwargs)

    with ExitStack() as guards:
        guards.enter_context(patch.dict("os.environ", {"POROTA_DYNAMIC_SHADOW_ROOT": str(root),
                                                      "POROTA_SHADOW_RUNTIME_ROOT": str(root)}))
        guards.enter_context(patch.object(socket.socket, "connect", no_network))
        guards.enter_context(patch.object(socket, "create_connection", no_network))
        guards.enter_context(patch.object(sqlite3, "connect", source_sqlite_guard(database, sqlite3.connect, source_calls)))
        guards.enter_context(patch.object(persistence, "decode_storage", bounded_decode))
        with readonly_copy(database, validate=False, deadline=monotonic()+2) as copied:
            catalog_count = copied.execute("SELECT count(*) FROM financial_instrument_catalog").fetchone()[0]
            observation_count = copied.execute("SELECT count(*) FROM ppi_intraday_points").fetchone()[0]
            last_identity = tuple(copied.execute("SELECT ticker,instrument_type,market,currency,settlement "
                                                "FROM financial_instrument_catalog ORDER BY ticker DESC LIMIT 1").fetchone())
        preflight_begin = perf_counter()
        with Store(database, now=cut_at) as store:
            projection = Projection(store)
            cut = projection.shadow
            require(cut["state"] == "COMMITTED_COHERENT_SHADOW", "PROJECTED_CUT_UNAVAILABLE",
                    {"state": cut["state"], "reason": cut["reason"], "error_class": cut.get("error_class"),
                     "elapsed_seconds": perf_counter()-preflight_begin, "scope": "default"})
            require(cut["pointer"] == pointer and cut["verification_level"] == VERIFICATION_LEVEL,
                    "BROWSER_PREFLIGHT_CUT_OR_VERIFICATION_MISMATCH")
            require_native_large_cut(cut, catalog_count=catalog_count, observation_count=observation_count)
            planner = shadow_rows(projection, "opportunities")
            require(planner.state == "AVAILABLE" and planner.total == 2*catalog_count, "PLANNER_DENOMINATOR_INCOMPLETE")
        require(not store.errors, "SOURCE_SNAPSHOT_REJECTED")
        preflight_begin = perf_counter()
        with Store(database, now=cut_at) as store:
            scoped_projection = Projection(store, {"family": last_identity[1]})
            scoped_cut = scoped_projection.shadow
            require(scoped_cut["state"] == "COMMITTED_COHERENT_SHADOW", "PROJECTED_CUT_UNAVAILABLE",
                    {"state": scoped_cut["state"], "reason": scoped_cut["reason"], "error_class": scoped_cut.get("error_class"),
                     "elapsed_seconds": perf_counter()-preflight_begin, "scope": "family"})
            scoped = scoped_projection.funnel_scope
            require(scoped_cut["pointer"] == pointer, "SCOPED_PREFLIGHT_CHANGED_CUT")
            require(scoped["state"] == "AVAILABLE" and scoped["total_groups"] >= 2*catalog_count,
                    "COMPLETE_COHORT_POPULATION_UNAVAILABLE")
        require(not store.errors, "SOURCE_SNAPSHOT_REJECTED")

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
                    begin = perf_counter()
                    html, headers = build_page(parts.path, params, database, now=cut_at)
                    elapsed = perf_counter()-begin
                    record = {"path": parts.path, "filters": params, "width": page.viewport_size["width"],
                              "elapsed_seconds": elapsed, "html_bytes": len(html.encode()),
                              "queries": int(headers["X-Porota-Read-Queries"]), "server_timing": headers["Server-Timing"]}
                    renders.append(record)
                    if elapsed > 1 or record["html_bytes"] >= 4*1024**2:
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
                        require(not findings, "BROWSER_RENDER_FAILED", {"findings": findings})
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
                page.goto("http://terminal.test/en-vivo?"+urlencode({**family_query, "funnel_offset": scoped["total_groups"]-10}))
                require(page.locator("ol.funnel").inner_text() == first_counts, "LAST_COHORT_PAGE_CHANGED_SELECTED_AGGREGATES")
                page.locator("nav[aria-label='Grupos del embudo'] a[href*='cohort=']").last.click()
                require("cohort=" in page.url and "funnel_offset=" not in page.url and
                        "grupos 1–1 de 1" in page.locator("section.panel:has(ol.funnel)").inner_text(), "LAST_COHORT_UNREACHABLE")
                page.goto("http://terminal.test/en-vivo/oportunidades?"+urlencode({"q": last_identity[0], "currency": last_identity[3]}))
                require(page.locator("tr[data-row]").count() == 2 and all(last_identity[0] in text for text in page.locator("tr[data-row]").all_inner_texts()),
                        "LAST_CATALOG_IDENTITY_UNREACHABLE")
                require(VERIFICATION_LEVEL in page.locator(".data-panel .source-line").inner_text(), "FALSE_BROWSER_VERIFICATION_SCOPE")
                require(not findings, "BROWSER_RENDER_FAILED", {"findings": findings})
            finally:
                browser.close()
    after = custody_inventory(database, root)
    require(before == after, "SOURCE_OR_CUSTODY_MUTATED")
    require(not network and "SOURCE_BLOCKED" not in source_calls, "NETWORK_OR_SOURCE_SQLITE_ATTEMPTED")
    return {"schema": "rc6.dashboard-native-large-browser-proof.v1", "status": "GREEN",
        "recorded_at": datetime.now(timezone.utc).isoformat(), "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pointer": pointer, "source_cut": cut_at.isoformat(), "native_generation_roles": sorted(manifest["files"]),
        "verification_level": VERIFICATION_LEVEL, "custody": cut["export_contract"]["custody"],
        "catalog_full_identities": catalog_count, "observations": observation_count, "planner_rows": planner.total,
        "funnel_groups": scoped["total_groups"], "widths": widths, "canonical_viewport_checks": checks,
        "legacy_checks": len(LEGACY), "interaction_checks": ["manual focus", "scroll", "details", "filters", "deep link", "dirty inputs",
            "interaction during outstanding read", "auto refresh pause", "menu Escape", "cohort pagination and final selection", "last catalog identity"],
        "network_attempts": 0, "provider_requests": 0, "source_sqlite_opens": 0,
        "source_custody_inventory_unchanged": True, "source_inventory": before, "renders": renders}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.output.is_symlink() or args.output.resolve().is_relative_to(args.root.resolve())
            or args.output.resolve().is_relative_to(Path(str(args.root.resolve())+".authority"))
            or args.output.resolve() in {Path(str(args.database.resolve())+suffix) for suffix in ("", "-wal", "-shm", "-journal")}
            or args.output.exists()):
        parser.error("Output must be a new directory outside the database, sidecars and SHADOW custody")
    try:
        result = run(args.database, args.root, args.output)
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
