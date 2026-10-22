#!/usr/bin/env python3
"""Real Chromium inspection of deployed RC6 through an authenticated local SSH tunnel.

The runner holds the bearer only in memory. Public artifacts contain allowlisted
operational codes and screenshots with all other text, input, image and canvas
content obscured. The remote host only serves bounded GET requests.
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
PRIORITY = (
    "/", "/en-vivo", "/en-vivo/capacidad", "/en-vivo/fuentes",
    "/en-vivo/cierres", "/en-vivo/workers", "/instrumentos",
    "/riesgo", "/riesgo/limites", "/analitica/anual",
    "/analitica/cierre-diario", "/sistema/scheduler", "/sistema/backups",
)
STATUS = re.compile(
    r"\b(?:NO_VERIFICADO|STALE|BASELINE_UNAVAILABLE|SOURCE_UNAVAILABLE|"
    r"COMMITTED_COHERENT_SHADOW|SIMULATED|WAITING_MARKET|MARKET_CLOSED|"
    r"PRODUCTION_PAPER|OFF_BASELINE|PAPER|SHADOW|OPEN|BACKLOG|"
    r"ANNUAL_NO_CANONICAL_BARS|ANNUAL_NO_SELECTABLE_CANONICAL_SERIES|"
    r"SUSPENDED_BY_OPERATOR|READY|UNAVAILABLE)\b", re.I
)
REDACT = r"""() => {
  const allowed = /\b(?:NO_VERIFICADO|STALE|BASELINE_UNAVAILABLE|SOURCE_UNAVAILABLE|COMMITTED_COHERENT_SHADOW|SIMULATED|WAITING_MARKET|MARKET_CLOSED|PRODUCTION_PAPER|OFF_BASELINE|PAPER|SHADOW|OPEN|BACKLOG|ANNUAL_NO_CANONICAL_BARS|ANNUAL_NO_SELECTABLE_CANONICAL_SERIES|SUSPENDED_BY_OPERATOR|READY|UNAVAILABLE)\b/gi;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest('script,style,noscript')) continue;
    const value = node.nodeValue;
    const ranges = [...value.matchAll(allowed)].map(m => [m.index, m.index + m[0].length]);
    node.nodeValue = value.split('').map((char, i) => {
      if (/\s/.test(char)) return char;
      return ranges.some(([start,end]) => i >= start && i < end) ? char : '•';
    }).join('');
  }
  document.querySelectorAll('input,textarea,select,img,svg,canvas,video').forEach(el => {
    el.style.visibility = 'hidden';
  });
}"""


def main(output):
    token = os.environ.get("DASHBOARD_ACCESS_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("BEARER_UNAVAILABLE")
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "RC6_685_LIVE_CHROMIUM_V1", "as_of": datetime.now(timezone.utc).isoformat(),
              "target": "production-paper-dashboard-via-ssh-local-tunnel", "browser": "Chrome",
              "paths": [], "findings": [], "screenshots": [], "request_policy": "localhost GET only"}
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
            request = route.request
            parsed = urlsplit(request.url)
            if request.method not in ("GET", "HEAD") or parsed.netloc != "127.0.0.1:18085":
                report["findings"].append({"kind": "blocked_nonread_request", "method": request.method})
                route.abort()
            else:
                route.continue_()

        page.route("**/*", read_only)
        response = page.goto(BASE + "/api/dashboard/truth", wait_until="domcontentloaded", timeout=8000)
        try:
            truth = json.loads(page.locator("body").inner_text())
        except Exception as exc:
            raise SystemExit("PAPER_TRUTH_INVALID:" + type(exc).__name__) from None
        if response.status != 200 or truth.get("mode") != "PRODUCTION_PAPER" or truth.get("real_orders_sent") != 0:
            raise SystemExit("PAPER_GUARD_NOT_PROVEN")
        report["paper_guard"] = {"http": 200, "mode": "PRODUCTION_PAPER", "real_orders_sent": 0}
        for path in CANONICAL_PATHS:
            try:
                response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=7000)
                body = page.locator("body").inner_text(timeout=3500)
                metrics = page.evaluate("""() => ({viewport:innerWidth,
                    body:document.body.scrollWidth, html:document.documentElement.scrollWidth,
                    h1:document.querySelectorAll('h1').length,
                    cards:document.querySelectorAll('article,.card,.panel').length,
                    tables:document.querySelectorAll('table').length})""")
                status_counts = dict(sorted(Counter(x.upper() for x in STATUS.findall(body)).items()))
                item = {"path": path, "http": response.status if response else None,
                        "rendered_characters": len(body), "status_counts": status_counts,
                        "desktop": metrics}
                if path == "/analitica/anual":
                    item["annual_selector_links"] = page.locator('a[href^="/analitica/anual?"]').count()
                    item["annual_no_selectable"] = "ANNUAL_NO_SELECTABLE_CANONICAL_SERIES" in body
                    if not item["annual_selector_links"] and not item["annual_no_selectable"]:
                        report["findings"].append({"kind": "annual_missing_honest_empty_state", "path": path})
                if path == "/sistema/backups":
                    item["backup_operator_suspended"] = ("SUSPENDED_BY_OPERATOR" in body and
                        "Generación de backups suspendida" in body)
                    if not item["backup_operator_suspended"]:
                        report["findings"].append({"kind": "backup_view_not_suspended", "path": path})
                if path == "/en-vivo/cierres":
                    item["paper_closure_scope"] = "Posiciones cerradas PAPER" in body
                    if not item["paper_closure_scope"]:
                        report["findings"].append({"kind": "paper_closure_scope_missing", "path": path})
                if path == "/en-vivo/capacidad":
                    item["open_capacity_caveat"] = "La capacidad real exige evidencia OPEN" in body
                    if not item["open_capacity_caveat"]:
                        report["findings"].append({"kind": "open_capacity_caveat_missing", "path": path})
                if path == "/en-vivo/fuentes":
                    item["source_authority_caveat"] = "PPI es autoridad primaria" in body
                    if not item["source_authority_caveat"]:
                        report["findings"].append({"kind": "source_authority_caveat_missing", "path": path})
                if response.status != 200 or not body.strip():
                    report["findings"].append({"kind": "rendered_route_error", "path": path,
                                               "http": item["http"]})
                if max(metrics["body"], metrics["html"]) > metrics["viewport"] + 2:
                    report["findings"].append({"kind": "horizontal_overflow", "path": path,
                                               "width": 1440})
                report["paths"].append(item)
            except Exception as exc:
                report["findings"].append({"kind": "browser_exception", "path": path,
                                           "error_type": type(exc).__name__})
                report["paths"].append({"path": path, "http": None})
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 900})
            for path in PRIORITY:
                try:
                    response = page.goto(BASE + path, wait_until="domcontentloaded", timeout=7000)
                    if response.status != 200:
                        raise RuntimeError("HTTP_ERROR")
                    page.wait_for_timeout(120)
                    metrics = page.evaluate("""() => ({viewport:innerWidth,
                       body:document.body.scrollWidth, html:document.documentElement.scrollWidth})""")
                    if max(metrics["body"], metrics["html"]) > width + 2:
                        report["findings"].append({"kind": "horizontal_overflow", "path": path,
                                                   "width": width, "scroll_width": max(metrics["body"], metrics["html"])})
                    # A local copy of live DOM is redacted only after measurement.
                    page.evaluate(REDACT)
                    safe_name = "home" if path == "/" else path.strip("/").replace("/", "_")
                    filename = f"{safe_name}-{width}.png"
                    page.screenshot(path=str(output / filename), full_page=True, animations="disabled")
                    report["screenshots"].append({"path": path, "width": width, "file": filename,
                                                  "redaction": "all text except operational code allowlist"})
                except Exception as exc:
                    report["findings"].append({"kind": "screenshot_error", "path": path,
                                               "width": width, "error_type": type(exc).__name__})
        report["pageerror_count"] = len(errors)
        report["canonical_count_expected"] = len(CANONICAL_PATHS)
        report["canonical_count_observed"] = len(report["paths"])
        browser.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("RC6_LIVE_BROWSER_SUMMARY=" + json.dumps({
        "canonical_count": report["canonical_count_observed"], "screenshots": len(report["screenshots"]),
        "findings": report["findings"], "pageerror_count": report["pageerror_count"],
        "paths": report["paths"]}, ensure_ascii=False, separators=(",", ":")))
    if report["canonical_count_observed"] != len(CANONICAL_PATHS) or report["findings"] or errors:
        raise SystemExit("LIVE_BROWSER_NOT_GREEN")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)
