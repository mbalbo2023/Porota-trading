#!/usr/bin/env python3
"""Seven-route, non-content browser probe for ambiguous RC6 #732 findings."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


BASE = "http://127.0.0.1:18085"
PATHS = (
    "/en-vivo/fuentes", "/trading/renta-fija", "/instrumentos/ficha",
    "/en-vivo/decisiones", "/trading/equity-spot", "/trading/tesoreria",
    "/instrumentos",
)


def modal_state(page, row):
    button = row.locator("button[data-detail-dialog]")
    if button.count() != 1:
        return {"error": "TRIGGER_COUNT"}
    target = button.get_attribute("data-detail-dialog")
    local = row.locator("dialog.row-dialog")
    if not target or local.count() != 1:
        return {"error": "LOCAL_DIALOG_MISSING"}
    button.click(timeout=3500)
    return local.evaluate("""el => {
      const target = document.getElementById(el.id);
      const b = el.getBoundingClientRect();
      const content = el.querySelector('.dialog-body');
      return {local_open:el.open, local_modal:el.matches(':modal'),
        target_is_local:target === el, target_open:target instanceof HTMLDialogElement && target.open,
        in_view:b.width>0 && b.height>0 && b.left>=-2 && b.right<=innerWidth+2,
        fields:content?.querySelectorAll('dt,dd').length || 0,
        scrollable:!!content && content.scrollHeight>=content.clientHeight};
    }""")


def main():
    token = os.environ.get("DASHBOARD_ACCESS_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("BEARER_UNAVAILABLE")
    output = Path("focus-evidence")
    output.mkdir(exist_ok=True)
    report = {"schema": "RC6_732_FOCUSED_NO_CONTENT_V1", "routes": [], "errors": [],
              "scope": list(PATHS), "screenshots": 0}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1440, "height": 900},
                                      extra_http_headers={"Authorization": "Bearer " + token},
                                      reduced_motion="reduce")
        page = context.new_page()

        def read_only(route):
            req = route.request
            if req.method in ("GET", "HEAD") and urlsplit(req.url).netloc == "127.0.0.1:18085":
                route.continue_()
            else:
                report["errors"].append({"kind": "blocked_request", "method": req.method})
                route.abort()

        page.route("**/*", read_only)
        response = page.goto(BASE + "/api/dashboard/truth", wait_until="domcontentloaded", timeout=9000)
        try:
            truth = json.loads(page.locator("body").inner_text())
        except (json.JSONDecodeError, ValueError):
            truth = {}
        report["paper_guard"] = (response is not None and response.status == 200
                                 and truth.get("mode") == "PRODUCTION_PAPER"
                                 and type(truth.get("real_orders_sent")) is int
                                 and truth["real_orders_sent"] == 0)
        if report["paper_guard"]:
            for path in PATHS:
                item = {"path": path}
                try:
                    response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=9000)
                    item["first_http"] = response.status if response else None
                    item["first_rows"] = page.locator("tr[data-row='true']").count()
                    with page.expect_navigation(wait_until="domcontentloaded", timeout=9000) as navigation:
                        page.get_by_role("link", name="Mostrar 10 más").first.click(timeout=3500)
                    item["second_http"] = navigation.value.status if navigation.value else None
                    item["second_url_matches"] = page.url.startswith(BASE + path + "?")
                    rows = page.locator("tr[data-row='true']")
                    item["second_rows"] = rows.count()
                    item["second_duplicate_ids"] = page.evaluate("""() => {
                      const ids = [...document.querySelectorAll('[id]')].map(el => el.id);
                      return ids.length - new Set(ids).size;
                    }""")
                    item["second_dialogs"] = []
                    for index in range(min(rows.count(), 10)):
                        try:
                            state = modal_state(page, rows.nth(index))
                            item["second_dialogs"].append({"index": index, "state": state})
                        except Exception as exc:
                            item["second_dialogs"].append({"index": index, "error_type": type(exc).__name__})
                        finally:
                            page.evaluate("() => document.querySelectorAll('dialog[open]').forEach(d => d.close())")
                    if item["second_rows"] == 0:
                        retry = page.goto(page.url, wait_until="domcontentloaded", timeout=9000)
                        item["retry_http"] = retry.status if retry else None
                        item["retry_rows"] = page.locator("tr[data-row='true']").count()
                except Exception as exc:
                    item["error_type"] = type(exc).__name__
                report["routes"].append(item)
        browser.close()
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("RC6_732_FOCUSED_SUMMARY=" + json.dumps(report, separators=(",", ":")))
    if not report["paper_guard"] or len(report["routes"]) != len(PATHS) or report["errors"]:
        raise SystemExit("FOCUSED_PROBE_INCOMPLETE")


if __name__ == "__main__":
    main()
