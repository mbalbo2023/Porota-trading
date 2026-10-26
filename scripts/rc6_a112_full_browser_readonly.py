#!/usr/bin/env python3
"""Bounded, no-content browser acceptance of the exact installed PAPER image.

The diagnostic branch has no effect on the product artifact.  The host mode
only inspects Docker identity and reads three indexed PAPER ledger predicates.
The browser mode only requests canonical dashboard GET/HEAD routes over an
authenticated loopback SSH tunnel.  Neither mode persists business content.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


CANDIDATE_SHA = "55e66d6c72e3cdbda0d6c8a4da561607158f1e8b"
CANDIDATE_TREE = "3946caa04db76db56b8f2f86b4bf7b4dbca436d4"
PRODUCT_BRANCH = "deploy/rc6-pr69-isolated-20260915"
DEPLOY_WORKFLOW = ".github/workflows/porota-deploy-v2-promote.yml"
BASE = "http://127.0.0.1:18085"
DB = "file:/opt/porota-trading/data/paper_v17/observer_v17.db?mode=ro"
MOBILE = (
    "/", "/en-vivo", "/en-vivo/posiciones", "/en-vivo/cierres",
    "/trading", "/trading/cauciones", "/universo", "/instrumentos",
    "/riesgo", "/analitica/anual", "/sistema", "/sistema/backups",
)
MAX_REQUESTS = 220
MAX_DIALOGS = 700
MAX_SECONDS = 720


class AuditRed(Exception):
    pass


def fail(code):
    raise AuditRed(code)


def exact_hex(value, length=40):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None


def validate_request(args):
    try:
        request = json.loads(Path(args.file).read_text())
        if set(request) != {"candidate_sha", "candidate_tree", "installed_image_id", "deploy_run_id"}:
            fail("REQUEST_FIELDS_INVALID")
        if request["candidate_sha"] != CANDIDATE_SHA or request["candidate_tree"] != CANDIDATE_TREE:
            fail("REQUEST_SOURCE_NOT_PINNED")
        image_id = request["installed_image_id"]
        if not isinstance(image_id, str) or not image_id.startswith("sha256:") or not exact_hex(image_id[7:], 64):
            fail("REQUEST_IMAGE_ID_INVALID")
        run_id = request["deploy_run_id"]
        if type(run_id) is not int or run_id <= 0:
            fail("REQUEST_RUN_ID_INVALID")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
            fail("REQUEST_REPOSITORY_INVALID")
        github_token = os.environ.get("GH_READ_TOKEN", "")
        if not github_token:
            fail("GITHUB_READ_TOKEN_MISSING")
        url = f"https://api.github.com/repos/{args.repository}/actions/runs/{run_id}"
        headers = {"Authorization": "Bearer " + github_token,
                   "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        with urlopen(Request(url, headers=headers), timeout=12) as response:
            run = json.load(response)
        if (run.get("status") != "completed" or run.get("conclusion") != "success"
                or run.get("path") != DEPLOY_WORKFLOW
                or run.get("head_branch") != PRODUCT_BRANCH
                or run.get("id") != run_id):
            fail("CANONICAL_DEPLOY_RUN_NOT_GREEN")
        merge_sha = run.get("head_sha")
        if not exact_hex(merge_sha):
            fail("DEPLOY_MERGE_SHA_MISSING")
        commit_url = f"https://api.github.com/repos/{args.repository}/commits/{merge_sha}"
        with urlopen(Request(commit_url, headers=headers), timeout=12) as response:
            merged = json.load(response)
        parents = merged.get("parents", [])
        if (merged.get("sha") != merge_sha or len(parents) != 2 or
                parents[1].get("sha") != CANDIDATE_SHA or
                merged.get("commit", {}).get("tree", {}).get("sha") != CANDIDATE_TREE):
            fail("DEPLOY_MERGE_NOT_EXACT_CANDIDATE_TREE")
        with open(args.output, "a", encoding="utf-8") as output:
            output.write(f"candidate_sha={CANDIDATE_SHA}\n")
            output.write(f"candidate_tree={CANDIDATE_TREE}\n")
            output.write(f"image_id={image_id}\n")
            output.write(f"deploy_run_id={run_id}\n")
        print("A112_REQUEST=VALIDATED_EXACT_SOURCE_IMAGE_AND_DEPLOY_RUN")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        fail("REQUEST_UNAVAILABLE_OR_INVALID:" + type(error).__name__)


def docker(*argv):
    result = subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=12, check=False)
    if result.returncode:
        fail("DOCKER_INSPECTION_FAILED")
    return result.stdout.strip()


def host_preflight(args):
    if args.candidate_sha != CANDIDATE_SHA or args.candidate_tree != CANDIDATE_TREE:
        fail("HOST_SOURCE_PIN_MISMATCH")
    if not args.image_id.startswith("sha256:") or not exact_hex(args.image_id[7:], 64):
        fail("HOST_IMAGE_PIN_INVALID")
    image_ids = set()
    for name in ("porota_production_observer", "porota_production_dashboard"):
        observed = json.loads(docker("inspect", "--format", "{{json .State}}", name))
        if observed.get("Status") != "running" or observed.get("OOMKilled") is True:
            fail("HOST_CONTAINER_NOT_HEALTHY")
        health = observed.get("Health")
        if health and health.get("Status") != "healthy":
            fail("HOST_HEALTHCHECK_NOT_GREEN")
        image_ids.add(docker("inspect", "--format", "{{.Image}}", name))
        identity = json.loads(docker("exec", "-e", "PYTHONDONTWRITEBYTECODE=1", name,
                                     "python3", "/app/scripts/porota_image_source_identity.py",
                                     "--expected-sha", CANDIDATE_SHA,
                                     "--expected-tree", CANDIDATE_TREE))
        if identity.get("status") != "GREEN":
            fail("HOST_SOURCE_IDENTITY_RED")
    if image_ids != {args.image_id}:
        fail("HOST_IMAGE_ID_CHANGED")
    if shutil.disk_usage("/opt/porota-trading").free < 2 * 1024 ** 3:
        fail("HOST_DISK_HEADROOM_LOW")
    with closing(sqlite3.connect(DB, uri=True, timeout=0.2)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=200")
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        state = db.execute(
            "SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at "
            "FROM observer_state WHERE id=1"
        ).fetchone()
        if not state or state[0] != "PRODUCTION_PAPER" or type(state[1]) is not int or state[1] != 0:
            fail("HOST_PAPER_ZERO_NOT_PROVEN")
        phase = ("OPEN" if state[2:4] == ("RUNNING", "MARKET_OPEN") else
                 "CLOSED" if state[2] == "WAITING_MARKET" and
                 state[3] in ("CLOSED", "MARKET_CLOSED") else None)
        if not phase:
            fail("HOST_HEALTHY_SESSION_NOT_PROVEN")
        heartbeat = datetime.fromisoformat(state[4].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
        if heartbeat.tzinfo is None or not 0 <= age <= 180:
            fail("HOST_HEARTBEAT_NOT_FRESH")
        for table, status in (("paper_positions", "OPEN"), ("paper_cauciones", "OPEN"),
                              ("paper_future_positions", "ACTIVE")):
            if db.execute(f"SELECT 1 FROM {table} WHERE status=? LIMIT 1", (status,)).fetchone():
                fail("HOST_PAPER_BOOK_NOT_FLAT")
    print("A112_HOST_GUARD=" + json.dumps({
        "status": "GREEN", "candidate_sha": CANDIDATE_SHA,
        "candidate_tree": CANDIDATE_TREE, "image_id": args.image_id,
        "mode": "PRODUCTION_PAPER", "real_orders_sent": 0, "flat": True,
        "phase": phase}, sort_keys=True))


def modal_check(page, row, *, check_close_button):
    trigger = row.locator("button[data-detail-dialog]")
    local = row.locator("dialog.row-dialog")
    if trigger.count() != 1 or local.count() != 1:
        fail("ROW_DETAIL_CONTROL_MISSING")
    target = trigger.get_attribute("data-detail-dialog")
    if not target or trigger.get_attribute("aria-controls") != target:
        fail("ROW_DETAIL_ARIA_TARGET_MISSING")
    if trigger.get_attribute("aria-haspopup") != "dialog":
        fail("ROW_DETAIL_ARIA_HASPOPUP_MISSING")
    trigger.focus()
    trigger.press("Enter", timeout=2500)
    state = local.evaluate("""el => {
      const button = el.parentElement.querySelector('button[data-detail-dialog]');
      const titleId = el.getAttribute('aria-labelledby');
      const title = titleId && document.getElementById(titleId);
      const box = el.getBoundingClientRect();
      const body = el.querySelector('.dialog-body');
      return {targetIsLocal:document.getElementById(button?.dataset.detailDialog) === el,
        localOpen:el.open, topLayer:el.matches(':modal'),
        inView:box.width>0 && box.height>0 && box.left>=-2 && box.right<=innerWidth+2,
        fields:!!body && body.querySelectorAll('dt,dd').length>0,
        name:!!title && !!title.textContent.trim() &&
          title.textContent.trim() === button.textContent.trim(),
        focusInside:el.contains(document.activeElement)};
    }""")
    if not state or not all(state.values()):
        fail("ROW_LOCAL_DIALOG_STATE_RED")
    if check_close_button:
        local.get_by_role("button", name="Cerrar detalle").click(timeout=2500)
        local.wait_for(state="hidden", timeout=2000)
        page.wait_for_function(
            "id => document.activeElement?.getAttribute('data-detail-dialog') === id",
            arg=target, timeout=2000)
        trigger.press("Enter", timeout=2500)
        if not local.evaluate("el => el.open && el.matches(':modal')"):
            fail("ROW_KEYBOARD_REOPEN_RED")
    page.keyboard.press("Escape")
    local.wait_for(state="hidden", timeout=2000)
    page.wait_for_function(
        "id => document.activeElement?.getAttribute('data-detail-dialog') === id",
        arg=target, timeout=2000)


def geometry(page):
    return page.evaluate("""() => ({
      viewport:innerWidth, body:document.body.scrollWidth,
      html:document.documentElement.scrollWidth,
      headings:document.querySelectorAll('h1').length,
      duplicateIds:(() => {const ids=[...document.querySelectorAll('[id]')].map(x=>x.id);
        return ids.length-new Set(ids).size})()
    })""")


def inspect_rows(page, item, report, key):
    tables = page.locator("table")
    item[key] = {"table_count": tables.count(), "visible_rows": 0,
                 "dialogs_checked": 0, "tables": []}
    for table_index in range(tables.count()):
        table = tables.nth(table_index)
        table_id = table.get_attribute("id")
        # IDs may be reported only when they are static-looking UI keys.
        safe_id = table_id if table_id and re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", table_id) else None
        rows = table.locator("tbody tr[data-row='true']")
        count = rows.count()
        entry = {"index": table_index, "id": safe_id, "rows": count, "dialogs_checked": 0}
        item[key]["tables"].append(entry)
        item[key]["visible_rows"] += count
        if count > 10:
            report["findings"].append({"kind": "TABLE_ROW_BOUND_EXCEEDED", "path": item["path"],
                                       "page": key, "table": table_index})
            continue
        for index in range(count):
            if report["dialogs_checked"] >= MAX_DIALOGS:
                report["findings"].append({"kind": "DIALOG_BUDGET_EXCEEDED", "path": item["path"]})
                return
            try:
                modal_check(page, rows.nth(index), check_close_button=index == 0)
                entry["dialogs_checked"] += 1
                item[key]["dialogs_checked"] += 1
                report["dialogs_checked"] += 1
            except Exception as error:
                report["findings"].append({"kind": "ROW_DIALOG_RED", "path": item["path"],
                                           "page": key, "table": table_index,
                                           "index": index, "error": type(error).__name__})
                page.evaluate("() => document.querySelectorAll('dialog[open]').forEach(d => d.close())")


def browser(args):
    from playwright.sync_api import sync_playwright
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rc6_trader_dashboard.navigation import CANONICAL_PATHS

    token = os.environ.get("DASHBOARD_ACCESS_TOKEN", "")
    if len(token) < 24 or any(ord(ch) < 33 for ch in token):
        fail("BROWSER_BEARER_UNAVAILABLE")
    if len(CANONICAL_PATHS) != 52:
        fail("CANONICAL_ROUTE_COUNT_DRIFT")
    allowed = set(CANONICAL_PATHS) | {"/api/dashboard/truth"}
    report = {"schema": "RC6_A112_FULL_BROWSER_NO_CONTENT_V1",
              "candidate_sha": CANDIDATE_SHA, "candidate_tree": CANDIDATE_TREE,
              "routes_expected": len(CANONICAL_PATHS), "paths": [],
              "mobile": [], "findings": [], "dialogs_checked": 0,
              "request_count": 0, "screenshots": 0,
              "modal_scope": "OBSERVED_ROWS_ONLY_EMPTY_TABLES_UNVERIFIED",
              "physical_voice_control": "NO_VERIFICADO",
              "screen_reader": "NO_VERIFICADO"}
    started = time.monotonic()
    errors = []
    with sync_playwright() as pw:
        chrome = pw.chromium.launch(channel="chrome", headless=True,
                                    args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = chrome.new_context(viewport={"width": 1440, "height": 900},
                                     service_workers="block", accept_downloads=False,
                                     reduced_motion="reduce")

        def read_only(route):
            request = route.request
            target = urlsplit(request.url)
            report["request_count"] += 1
            valid = (request.method in ("GET", "HEAD") and target.scheme == "http"
                     and target.netloc == "127.0.0.1:18085" and target.path in allowed
                     and target.query in ("", "offset=10")
                     and not target.username and not target.password
                     and report["request_count"] <= MAX_REQUESTS)
            if not valid:
                report["findings"].append({"kind": "REQUEST_BLOCKED", "method": request.method})
                route.abort()
            else:
                # Fetch locally without following a redirect. Playwright carries
                # overridden headers across redirects with route.continue_().
                # A local redirect must never forward the bearer elsewhere.
                try:
                    response = route.fetch(
                        headers={**request.headers, "Authorization": "Bearer " + token},
                        max_redirects=0, timeout=6000)
                    if 300 <= response.status < 400:
                        report["findings"].append({"kind": "REDIRECT_BLOCKED"})
                        route.abort()
                    else:
                        route.fulfill(response=response)
                except Exception:
                    report["findings"].append({"kind": "LOCAL_FETCH_FAILED"})
                    route.abort()

        context.route("**/*", read_only)
        def block_socket(socket):
            report["findings"].append({"kind": "WEBSOCKET_BLOCKED"})
            socket.close()
        context.route_web_socket("**/*", block_socket)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(type(error).__name__))
        page.on("popup", lambda popup: (report["findings"].append({"kind": "POPUP_BLOCKED"}), popup.close()))
        page.on("download", lambda download: report["findings"].append({"kind": "DOWNLOAD_BLOCKED"}))

        def truth_guard():
            response = page.goto(BASE + "/api/dashboard/truth", wait_until="domcontentloaded", timeout=8000)
            try:
                truth = json.loads(page.locator("body").inner_text(timeout=2500))
            except (ValueError, TypeError):
                truth = {}
            return (response is not None and response.status == 200
                    and truth.get("mode") == "PRODUCTION_PAPER"
                    and type(truth.get("real_orders_sent")) is int
                    and truth["real_orders_sent"] == 0)

        if not truth_guard():
            report["findings"].append({"kind": "PAPER_TRUTH_NOT_PROVEN"})
        else:
            for path in CANONICAL_PATHS:
                item = {"path": path}
                if time.monotonic() - started > MAX_SECONDS or report["request_count"] >= MAX_REQUESTS:
                    report["findings"].append({"kind": "BROWSER_BUDGET_EXCEEDED"})
                    break
                try:
                    response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=8000)
                    item["http"] = response.status if response else None
                    item["desktop"] = geometry(page)
                    if (item["http"] != 200 or item["desktop"]["headings"] < 1 or
                            item["desktop"]["duplicateIds"] or
                            max(item["desktop"]["body"], item["desktop"]["html"]) > 1442):
                        report["findings"].append({"kind": "ROUTE_LAYOUT_RED", "path": path})
                    inspect_rows(page, item, report, "first_page")
                    next_link = page.get_by_role("link", name="Mostrar 10 más")
                    if next_link.count():
                        with page.expect_navigation(wait_until="domcontentloaded", timeout=8000) as nav:
                            next_link.first.click(timeout=2500)
                        item["second_http"] = nav.value.status if nav.value else None
                        if (item["second_http"] != 200 or not page.url.startswith(BASE + path + "?")
                                or geometry(page)["duplicateIds"]):
                            report["findings"].append({"kind": "SECOND_PAGE_RED", "path": path})
                        inspect_rows(page, item, report, "second_page")
                        if not item["second_page"]["visible_rows"]:
                            report["findings"].append({"kind": "SECOND_PAGE_EMPTY", "path": path})
                except Exception as error:
                    report["findings"].append({"kind": "ROUTE_EXCEPTION", "path": path,
                                               "error": type(error).__name__})
                report["paths"].append(item)
            page.set_viewport_size({"width": 390, "height": 844})
            for path in MOBILE:
                if time.monotonic() - started > MAX_SECONDS or report["request_count"] >= MAX_REQUESTS:
                    report["findings"].append({"kind": "MOBILE_BUDGET_EXCEEDED"})
                    break
                item = {"path": path}
                try:
                    response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=8000)
                    item["http"] = response.status if response else None
                    size = geometry(page)
                    item["overflow"] = max(size["body"], size["html"]) > 392
                    if item["http"] != 200 or item["overflow"] or size["duplicateIds"]:
                        report["findings"].append({"kind": "MOBILE_LAYOUT_RED", "path": path})
                    item["first_dialogs_by_table"] = 0
                    tables = page.locator("table")
                    for table_index in range(tables.count()):
                        first = tables.nth(table_index).locator("tbody tr[data-row='true']").first
                        if first.count():
                            modal_check(page, first, check_close_button=True)
                            item["first_dialogs_by_table"] += 1
                except Exception as error:
                    report["findings"].append({"kind": "MOBILE_EXCEPTION", "path": path,
                                               "error": type(error).__name__})
                report["mobile"].append(item)
            if not truth_guard():
                report["findings"].append({"kind": "FINAL_PAPER_TRUTH_NOT_PROVEN"})
        report["pageerror_count"] = len(errors)
        context.close()
        chrome.close()
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    report["first_page_rows_observed"] = sum(
        path.get("first_page", {}).get("visible_rows", 0) for path in report["paths"])
    report["first_page_dialogs_checked"] = sum(
        path.get("first_page", {}).get("dialogs_checked", 0) for path in report["paths"])
    report["first_page_empty_tables"] = sum(
        table["rows"] == 0 for path in report["paths"]
        for table in path.get("first_page", {}).get("tables", []))
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    summary = {"routes": len(report["paths"]), "mobile": len(report["mobile"]),
               "dialogs": report["dialogs_checked"],
               "first_page_rows": report["first_page_rows_observed"],
               "first_page_dialogs": report["first_page_dialogs_checked"],
               "empty_tables": report["first_page_empty_tables"],
               "requests": report["request_count"],
               "findings": len(report["findings"]), "pageerrors": report["pageerror_count"]}
    print("A112_BROWSER_SUMMARY=" + json.dumps(summary, sort_keys=True))
    if (report["findings"] or errors or len(report["paths"]) != 52
            or len(report["mobile"]) != len(MOBILE)):
        fail("BROWSER_ACCEPTANCE_INCOMPLETE_OR_RED")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("validate-request")
    request.add_argument("--file", required=True)
    request.add_argument("--repository", required=True)
    request.add_argument("--output", required=True)
    host = sub.add_parser("host-preflight")
    host.add_argument("--candidate-sha", required=True)
    host.add_argument("--candidate-tree", required=True)
    host.add_argument("--image-id", required=True)
    run = sub.add_parser("browser")
    run.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        {"validate-request": validate_request, "host-preflight": host_preflight,
         "browser": browser}[args.command](args)
    except Exception as error:
        # No exception text or remote output: it can contain private material.
        code = str(error) if isinstance(error, AuditRed) else type(error).__name__
        print("A112_AUDIT_RED=" + re.sub(r"[^A-Z0-9_:]", "_", code)[:96])
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
