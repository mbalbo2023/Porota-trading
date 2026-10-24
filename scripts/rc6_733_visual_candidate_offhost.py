#!/usr/bin/env python3
"""Bounded browser audit of the exact candidate's synthetic dashboard renderer.

Diagnostic only: no deployed dashboard, provider, account, or production DB.
The fixture is deliberately not a claim of live business readiness.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rc6_trader_dashboard.navigation import CANONICAL_PATHS
from rc6_trader_dashboard.routes import build_page


def run(output: Path, candidate_sha: str, candidate_tree_sha: str):
    from playwright.sync_api import sync_playwright

    output.mkdir(parents=True, exist_ok=True)
    fixture = importlib.util.spec_from_file_location(
        "rc6_visual_fixture", ROOT / "tests/test_ws_dash_trader_terminal_08.py")
    module = importlib.util.module_from_spec(fixture)
    fixture.loader.exec_module(module)
    paths = sorted(CANONICAL_PATHS)
    assert len(paths) == 52, f"canonical route denominator changed: {len(paths)}"
    findings, coverage, outside_requests = [], [], []
    with tempfile.TemporaryDirectory(prefix="rc6-visual-synthetic-") as directory:
        database = module.database.__wrapped__(Path(directory))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 960},
                                          reduced_motion="reduce", service_workers="block")
            page = context.new_page()
            current = {"path": ""}

            def reject_external(route):
                outside_requests.append(route.request.url)
                route.abort()

            def serve(route):
                parts = urlsplit(route.request.url)
                if parts.path == "/favicon.ico":
                    route.fulfill(status=204)
                    return
                params = {key: values[-1] for key, values in parse_qs(parts.query).items()}
                try:
                    html, headers = build_page(parts.path, params, database)
                except Exception as exc:
                    findings.append({"path": current["path"], "render_error": repr(exc)})
                    route.fulfill(status=500, body="render failed")
                    return
                route.fulfill(status=200, content_type="text/html", body=html, headers=headers)

            page.route("**/*", reject_external)
            page.route("http://terminal.test/**", serve)
            page.on("pageerror", lambda error: findings.append(
                {"path": current["path"], "javascript_error": str(error)}))
            for width in (1440, 360):
                page.set_viewport_size({"width": width, "height": 960})
                for index, path in enumerate(paths, 1):
                    current["path"] = path
                    entry = {"path": path, "width": width, "first_page_rows": 0,
                             "first_page_dialogs_opened": 0, "screenshot": f"route-{index:02d}-{width}.png"}
                    try:
                        response = page.goto("http://terminal.test" + path,
                                             wait_until="load", timeout=15000)
                        assert response is not None and response.status == 200
                        assert page.locator("#terminal-content").count() == 1
                        assert page.locator("nav.primary-nav > a").count() == 8
                        assert page.locator("#porota-paper-mode").count() == 1
                        dimensions = page.evaluate("""({width: innerWidth,
                          body: document.body.scrollWidth,
                          html: document.documentElement.scrollWidth})""")
                        if max(dimensions["body"], dimensions["html"]) > width + 1:
                            findings.append({"path": path, "width": width,
                                             "horizontal_overflow": dimensions})
                        rows = page.locator("tr[data-row]")
                        entry["first_page_rows"] = rows.count()
                        assert entry["first_page_rows"] <= 10
                        triggers = page.locator("tr[data-row] button[data-detail-dialog]")
                        assert triggers.count() == rows.count(), "row missing detail trigger"
                        for row_index in range(triggers.count()):
                            trigger = triggers.nth(row_index)
                            target = trigger.get_attribute("data-detail-dialog")
                            assert target and trigger.get_attribute("aria-controls") == target
                            trigger.click(timeout=5000)
                            dialog = page.locator("dialog.row-dialog").filter(has=page.locator(
                                f"#{target}-title"))
                            assert dialog.count() == 1 and dialog.get_attribute("open") is not None
                            assert dialog.is_visible() and dialog.locator(".facts dt").count() > 0
                            rect = dialog.bounding_box()
                            assert rect and rect["width"] <= width + 1, (target, rect)
                            page.keyboard.press("Escape")
                            assert dialog.get_attribute("open") is None, target
                            entry["first_page_dialogs_opened"] += 1
                        page.evaluate("""() => {
                          const label = document.createElement('div');
                          label.textContent = 'DATOS SINTÉTICOS — NO RUNTIME / NO MERCADO REAL';
                          Object.assign(label.style, {
                            position: 'fixed', bottom: '0', left: '0', right: '0',
                            zIndex: '2147483647', padding: '6px 10px',
                            background: '#a40000', color: '#ffffff',
                            font: 'bold 13px sans-serif', textAlign: 'center',
                            pointerEvents: 'none'
                          });
                          document.body.appendChild(label);
                        }""")
                        page.evaluate("window.scrollTo(0, 0)")
                        page.screenshot(path=str(output / entry["screenshot"]),
                                        full_page=False, animations="disabled")
                    except Exception as exc:
                        findings.append({"path": path, "width": width,
                                         "failed_after_dialogs": entry["first_page_dialogs_opened"],
                                         "exception": repr(exc)[:700]})
                    coverage.append(entry)
                    print("VISUAL_ROUTE=" + json.dumps(entry, ensure_ascii=False), flush=True)
            current["path"] = "/instrumentos pagination"
            page.set_viewport_size({"width": 1440, "height": 960})
            try:
                page.goto("http://terminal.test/instrumentos", wait_until="load")
                counts = [page.locator("tr[data-row]").count()]
                for offset in (10, 20):
                    page.get_by_role("link", name="Mostrar 10 más").click()
                    assert f"offset={offset}" in page.url
                    counts.append(page.locator("tr[data-row]").count())
                assert counts == [10, 10, 5], counts
            except Exception as exc:
                findings.append({"path": current["path"], "exception": repr(exc)[:700]})
            context.close()
            browser.close()
    if outside_requests:
        findings.append({"external_requests_blocked": outside_requests[:10]})
    result = {
        "schema": "rc6.733.synthetic-visual.v1", "candidate_sha": candidate_sha,
        "candidate_tree_sha": candidate_tree_sha,
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "routes_expected": len(paths), "viewports": [1440, 360],
        "route_viewport_checks": len(coverage),
        "first_page_rows": sum(row["first_page_rows"] for row in coverage),
        "first_page_dialogs_opened": sum(row["first_page_dialogs_opened"] for row in coverage),
        "pagination_expected": [10, 10, 5],
        "fixture": "synthetic SQLite; no runtime, provider, broker, or production data",
        "screenshots": [row["screenshot"] for row in coverage
                        if (output / row["screenshot"]).exists()],
        "coverage": coverage, "findings": findings,
        "status": "GREEN" if not findings and len(coverage) == 104 and
        sum(row["first_page_rows"] for row in coverage) ==
        sum(row["first_page_dialogs_opened"] for row in coverage) else "RED",
    }
    (output / "visual-receipt.json").write_text(json.dumps(result, indent=2,
                                                  ensure_ascii=False, sort_keys=True) + "\n")
    print("VISUAL_RECEIPT=" + json.dumps({key: value for key, value in result.items()
                                          if key not in {"coverage", "screenshots"}}, ensure_ascii=False))
    assert result["status"] == "GREEN", findings


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--candidate-tree-sha", required=True)
    args = parser.parse_args()
    run(args.output, args.candidate_sha, args.candidate_tree_sha)
