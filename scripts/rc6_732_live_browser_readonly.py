#!/usr/bin/env python3
"""Bounded, authenticated, read-only inspection of the deployed PAPER dashboard.

Artifacts contain paths, counts, statuses and redacted screenshots, never bearer,
identities, order/account values or raw HTML. The request interceptor denies writes.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_trader_dashboard.navigation import CANONICAL_PATHS

BASE = "http://127.0.0.1:18085"
STATUS = re.compile(r"\b(?:NO_VERIFICADO|STALE|SOURCE_UNAVAILABLE|PRODUCTION_PAPER|"
                    r"COMMITTED_COHERENT_SHADOW|SHADOW|PAPER|HOLD|MARKET_CLOSED|"
                    r"WAITING_MARKET|SUSPENDED_BY_OPERATOR|RUNTIME_READY)\b", re.I)
MOBILE = ("/", "/en-vivo", "/en-vivo/posiciones", "/en-vivo/cierres",
          "/trading", "/trading/cauciones", "/universo", "/instrumentos",
          "/riesgo", "/analitica/anual", "/sistema", "/sistema/backups")
REDACT = r"""() => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest('script,style,noscript')) continue;
    node.nodeValue = node.nodeValue.replace(/[^\s]/g, '•');
  }
  document.querySelectorAll('input,textarea,select,img,svg,canvas,video').forEach(el => {
    el.style.visibility = 'hidden';
  });
}"""


def geometry(page):
    return page.evaluate("""() => ({viewport: innerWidth,
      body: document.body.scrollWidth, html: document.documentElement.scrollWidth,
      heading: document.querySelectorAll('h1').length,
      tables: document.querySelectorAll('table').length})""")


def inspect_rows(page, report, item, path, second=False):
    rows = page.locator("tr[data-row='true']")
    count = rows.count()
    key = "second_page" if second else "first_page"
    item[key] = {"visible_rows": count, "dialogs_checked": 0, "dialog_failures": 0}
    if count > 10:
        report["findings"].append({"kind": "page_exceeds_ten_rows", "path": path, "page": key})
    for index in range(min(count, 10)):
        row = rows.nth(index)
        trigger = row.locator("button[data-detail-dialog]")
        try:
            if trigger.count() != 1:
                raise RuntimeError("TRIGGER_COUNT")
            dialog_id = trigger.get_attribute("data-detail-dialog")
            candidate = page.locator("dialog[id]").filter(has=page.locator(".dialog-body"))
            local_dialog = row.locator("dialog.row-dialog")
            if not dialog_id or candidate.count() < 1 or local_dialog.count() != 1:
                raise RuntimeError("DIALOG_MISSING")
            trigger.click(timeout=3500)
            state = page.evaluate("""id => {
              const el = document.getElementById(id);
              if (!(el instanceof HTMLDialogElement)) return null;
              const b = el.getBoundingClientRect(), content = el.querySelector('.dialog-body');
              return {open:el.open, modal:el.matches(':modal'),
                in_view:b.width>0 && b.height>0 && b.left>=-2 && b.right<=innerWidth+2,
                fields:content?.querySelectorAll('dt,dd').length || 0,
                scrollable:!!content && content.scrollHeight>=content.clientHeight};
            }""", dialog_id)
            if not state or not all(state.values()) or not local_dialog.evaluate("el => el.open && el.matches(':modal')"):
                raise RuntimeError("DIALOG_STATE")
            item[key]["dialogs_checked"] += 1
            page.evaluate("id => document.getElementById(id).close()", dialog_id)
        except Exception as exc:
            item[key]["dialog_failures"] += 1
            report["findings"].append({"kind": "dialog_failed", "path": path,
                                       "page": key, "row_index": index,
                                       "error_type": type(exc).__name__})
            page.evaluate("() => document.querySelectorAll('dialog[open]').forEach(d => d.close())")


def snapshot(page, output, report, path, width, suffix=""):
    page.evaluate(REDACT)
    name = "home" if path == "/" else path.strip("/").replace("/", "_")
    filename = f"{name}-{width}{suffix}.png"
    page.screenshot(path=str(output / filename), full_page=True, animations="disabled")
    report["screenshots"].append({"path": path, "width": width, "file": filename,
                                  "redaction": "all text and input/image contents obscured"})


def main(output):
    token = os.environ.get("DASHBOARD_ACCESS_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("BEARER_UNAVAILABLE")
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "RC6_732_LIVE_CHROMIUM_V1", "as_of": datetime.now(timezone.utc).isoformat(),
              "target": "deployed production PAPER dashboard via localhost SSH tunnel",
              "request_policy": "localhost GET/HEAD only", "paths": [], "findings": [],
              "screenshots": [], "canonical_count_expected": len(CANONICAL_PATHS)}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1440, "height": 900},
                                      extra_http_headers={"Authorization": "Bearer " + token},
                                      reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(type(error).__name__))

        def read_only(route):
            req = route.request
            if req.method not in ("GET", "HEAD") or urlsplit(req.url).netloc != "127.0.0.1:18085":
                report["findings"].append({"kind": "blocked_request", "method": req.method})
                route.abort()
            else:
                route.continue_()

        page.route("**/*", read_only)
        response = page.goto(BASE + "/api/dashboard/truth", wait_until="domcontentloaded", timeout=8000)
        try:
            truth = json.loads(page.locator("body").inner_text())
        except Exception:
            truth = {}
        paper = response is not None and response.status == 200 and truth.get("mode") == "PRODUCTION_PAPER" and type(truth.get("real_orders_sent")) is int and truth["real_orders_sent"] == 0
        report["paper_guard"] = {"http": response.status if response else None,
                                 "mode": "PRODUCTION_PAPER" if paper else "NO_VERIFICADO",
                                 "real_orders_sent": 0 if paper else "NO_VERIFICADO"}
        if paper:
            for path in CANONICAL_PATHS:
                item = {"path": path}
                try:
                    response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=9000)
                    item["http"] = response.status if response else None
                    item["desktop"] = geometry(page)
                    safe_text = page.locator("body").inner_text(timeout=3500)
                    item["status_counts"] = dict(sorted(Counter(x.upper() for x in STATUS.findall(safe_text)).items()))
                    item["duplicate_dom_ids"] = page.evaluate("""() => {
                      const all = [...document.querySelectorAll('[id]')].map(el => el.id);
                      return all.length - new Set(all).size;
                    }""")
                    if item["duplicate_dom_ids"]:
                        report["findings"].append({"kind": "duplicate_dom_ids", "path": path,
                                                   "count": item["duplicate_dom_ids"]})
                    if item["http"] != 200 or item["desktop"]["heading"] < 1:
                        report["findings"].append({"kind": "route_render_failed", "path": path,
                                                   "http": item["http"]})
                    if max(item["desktop"]["body"], item["desktop"]["html"]) > 1442:
                        report["findings"].append({"kind": "desktop_horizontal_overflow", "path": path})
                    inspect_rows(page, report, item, path)
                    # A single second page confirms server pagination and modal binding.
                    next_links = page.get_by_role("link", name="Mostrar 10 más")
                    if next_links.count():
                        item["has_second_page"] = True
                        next_links.first.click(timeout=3500)
                        if not page.url.startswith(BASE + path + "?"):
                            raise RuntimeError("PAGINATION_TARGET")
                        inspect_rows(page, report, item, path, second=True)
                        if item["second_page"]["visible_rows"] == 0:
                            page2 = page.url
                            span = page.locator("nav.pager span").first
                            item["second_page"]["pager_numbers"] = [int(n) for n in re.findall(
                                r"\d+", span.inner_text())] if span.count() else []
                            item["second_page"]["read_error_code"] = (
                                "CATALOG_READ_ERROR" if page.get_by_text("CATALOG_READ_ERROR").count()
                                else "NO_VERIFICADO" if page.get_by_text("NO_VERIFICADO", exact=True).count()
                                else "UNKNOWN")
                            retry = page.goto(page2, wait_until="domcontentloaded", timeout=9000)
                            item["second_page"]["retry_http"] = retry.status if retry else None
                            item["second_page"]["retry_visible_rows"] = page.locator("tr[data-row='true']").count()
                            report["findings"].append({"kind": "pagination_link_empty_page", "path": path,
                                                       "retry_rows": item["second_page"]["retry_visible_rows"]})
                        response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=9000)
                        if not response or response.status != 200:
                            raise RuntimeError("FIRST_PAGE_RETURN")
                    else:
                        item["has_second_page"] = False
                    if page.locator("tr[data-row='true'] button[data-detail-dialog]").count():
                        page.locator("tr[data-row='true'] button[data-detail-dialog]").first.click(timeout=3500)
                        if not page.locator("dialog.row-dialog[open]").count():
                            raise RuntimeError("DIALOG_SCREENSHOT_NOT_OPEN")
                        snapshot(page, output, report, path, 1440, "-dialog")
                    else:
                        snapshot(page, output, report, path, 1440)
                except Exception as exc:
                    report["findings"].append({"kind": "route_exception", "path": path,
                                               "error_type": type(exc).__name__})
                report["paths"].append(item)
            page.set_viewport_size({"width": 390, "height": 844})
            for path in MOBILE:
                try:
                    response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=9000)
                    if not response or response.status != 200:
                        raise RuntimeError("HTTP_ERROR")
                    metrics = geometry(page)
                    if max(metrics["body"], metrics["html"]) > 392:
                        report["findings"].append({"kind": "mobile_horizontal_overflow", "path": path})
                    first = page.locator("tr[data-row='true']").first
                    if first.count():
                        first.locator("button[data-detail-dialog]").click(timeout=3500)
                        if not first.locator("dialog.row-dialog").evaluate(
                                "el => el.open && el.matches(':modal') && el.getBoundingClientRect().width <= innerWidth + 2"):
                            raise RuntimeError("MOBILE_ROW_DIALOG_NOT_OPEN")
                        snapshot(page, output, report, path, 390, "-dialog")
                    else:
                        snapshot(page, output, report, path, 390)
                except Exception as exc:
                    report["findings"].append({"kind": "mobile_exception", "path": path,
                                               "error_type": type(exc).__name__})
        else:
            report["findings"].append({"kind": "paper_guard_not_proven"})
        report["canonical_count_observed"] = len(report["paths"])
        report["pageerror_count"] = len(errors)
        browser.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("RC6_732_BROWSER_SUMMARY=" + json.dumps({
        "canonical_count_expected": report["canonical_count_expected"],
        "canonical_count_observed": report["canonical_count_observed"],
        "dialogs_checked": sum(p.get(k, {}).get("dialogs_checked", 0) for p in report["paths"] for k in ("first_page", "second_page")),
        "screenshots": len(report["screenshots"]), "findings": report["findings"],
        "pageerror_count": report["pageerror_count"], "paths": report["paths"]},
        ensure_ascii=False, separators=(",", ":")))
    if report["findings"] or errors or len(report["paths"]) != len(CANONICAL_PATHS):
        raise SystemExit("LIVE_BROWSER_NOT_GREEN")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
