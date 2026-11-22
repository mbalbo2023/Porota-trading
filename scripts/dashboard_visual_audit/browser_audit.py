"""Read-only Chromium audit. The live run emits only structural results and safe status codes."""
from argparse import ArgumentParser
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from time import sleep
from urllib.parse import urlsplit

from playwright.sync_api import Error, sync_playwright

from rc6_trader_dashboard.navigation import CANONICAL_PATHS

PRIORITY = ("/en-vivo", "/sistema", "/sistema/salud", "/sistema/workers",
            "/sistema/integraciones", "/sistema/scheduler", "/sistema/evidencia",
            "/sistema/scheduler?offset=20", "/validacion")
TABLET = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
CLOCK = re.compile(r"^\d{2}/\d{2} \d{2}:\d{2}:\d{2} ART$")
BADGE = re.compile(r"\b(?:NO_VERIFICADO|FRESH|STALE|BLANCO|AMARILLO|"
                   r"VERDE|ROJO|PENDIENTE|AVAILABLE|READY|PRODUCTION_PAPER|WAITING_MARKET|"
                   r"MARKET_CLOSED|BLOCKED|SERVICE_FAILED|TIMER_ACTIVE|SUSPENDED|"
                   r"SHADOW_IDLE_MARKET_CLOSED|INSUFFICIENT_EVIDENCE|DAILY_COMPACT)\b")
SOURCES = ("PPI_PRODUCTION_MARKETDATA", "PPI_PRODUCTION_AUTH", "IOL · caché complementario")
SAFE_UNITS = ("porota-1016-github-verify.timer", "porota-a3-history-daily-rc6.timer",
              "porota-candle-integrity-rc6.timer", "porota-contract-evidence-hf6.timer",
              "porota-contract-evidence-rc6.timer", "porota-scheduler-export-rc6.timer",
              "porota-scheduler-export-hf6.timer", "porota-fast-functional-health-rc6.timer",
              "porota-runtime-evidence-rc6.timer", "porota-rc6-candle-inmarket-verify.timer",
              "porota-snapshot-postclose-rc6.timer")
SAFE_DETAILS = {"Estado", "Fuente", "Freshness", "Data freshness", "Check freshness",
                "Habilitado", "Activo", "Resultado servicio", "Exit status",
                "Próxima ejecución", "Última ejecución", "Chequeo", "Reloj de la fuente",
                "Último éxito", "Próximo chequeo", "Cadencia", "Reloj proveedor"}
SAFE_VALUES = {"0", "1", "VERDE", "ROJO", "GRIS", "OK", "active", "inactive", "masked",
               "disabled", "enabled", "success", "exit-code", "unknown",
               "PRIMARY", "COMPLEMENT / OBSERVE_ONLY", "NO_VERIFICADO", "—"}
SCREEN_MASK = r"""() => {
  const allowed = /\b(?:PAPER|SHADOW|CLOSED|MARKET_CLOSED|PRODUCTION_PAPER|BLOCKED|READY|STALE|FRESH|NO_VERIFICADO|PENDIENTE|AMARILLO|ROJO|VERDE|DAILY_COMPACT|SCHEDULER)\b/gi;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    if (node.parentElement?.closest('script,style,noscript')) continue;
    const keep = new Set();
    for (const word of node.nodeValue.matchAll(allowed))
      for (let i = word.index; i < word.index + word[0].length; i++) keep.add(i);
    node.nodeValue = node.nodeValue.split('').map((c,i) => /\s/.test(c) || keep.has(i) ? c : '•').join('');
  }
  document.querySelectorAll('input,textarea,select,img,svg,canvas,video,iframe').forEach(el => {
    el.style.visibility = 'hidden';
  });
}"""
VALIDATION_CONTRAST = """() => Array.from(document.querySelectorAll('details.paper-trade > summary')).slice(0, 2).map(item => {
   const badge = item.querySelector('.paper-status');
   if (!badge) return null;
   const get = (e, prop) => getComputedStyle(e)[prop];
   const rgb = text => (text.match(/[0-9.]+/g) || []).map(Number);
   const foreground = get(badge, 'color');
   let el = badge, background = get(el, 'backgroundColor');
   while (el.parentElement && ((rgb(background).length >= 4 && rgb(background)[3] === 0) || background === 'transparent')) {
     el = el.parentElement; background = get(el, 'backgroundColor');
   }
   const luminance = color => rgb(color).slice(0, 3).map(v => v / 255)
     .map(v => v <= 0.04045 ? v / 12.92 : Math.pow((v+0.055)/1.055, 2.4))
     .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
   const lum = [luminance(foreground), luminance(background)].sort((a,b) => b - a);
   return {foreground, background, contrast: Number(((lum[0]+0.05)/(lum[1]+0.05)).toFixed(2))};
})"""


def normalize(value):
    return " ".join(value.split())


def safe_value(value):
    value = normalize(value).lstrip("·✓! ")
    return value if value in SAFE_VALUES or CLOCK.fullmatch(value) or BADGE.fullmatch(value) else None


def selected_details(dialog):
    result = {}
    for field in dialog.locator(".facts > div").all():
        label = normalize(field.locator("dt").inner_text())
        if label in SAFE_DETAILS:
            value = safe_value(field.locator("dd").inner_text())
            if value is not None:
                result[label] = value
    return result


def row_details(page, path, max_rows, take_screenshot=None):
    main = page.locator("main#terminal-content")
    triggers = main.locator("tr[data-row='true'] button[data-detail-dialog]")
    ids = page.locator("[id]").evaluate_all("(nodes) => nodes.map(n => n.id)")
    duplicate_ids = sum(1 for n in Counter(ids).values() if n > 1)
    generic = problems = field_mismatches = missing_focus = test_count = 0
    samples = []
    for button in triggers.all()[:max_rows]:
        test_count += 1
        target = button.get_attribute("aria-controls")
        label = button.inner_text()
        generic += int("· registro" in label)
        row = button.locator("xpath=ancestor::tr")
        first_cell = normalize(row.locator("td").first.inner_text())
        useful = ((path.split("?")[0] == "/sistema/scheduler" and first_cell in SAFE_UNITS)
                  or (path == "/sistema/integraciones" and first_cell in SOURCES))
        try:
            button.scroll_into_view_if_needed(timeout=1200)
            button.click(timeout=1500)
            dialog = page.locator("dialog[id=" + json.dumps(target) + "]")
            if not dialog.count() or not dialog.evaluate("(d) => d.open && d.matches(':modal')"):
                problems += 1
                continue
            heading = dialog.get_by_role("heading", name=label, exact=True)
            if heading.count() != 1 or not dialog.locator("dl.facts > div").count():
                problems += 1
            fields = dict(dialog.locator(".facts > div").evaluate_all(
                """(rows) => rows.map(r => [r.querySelector('dt').textContent.trim(),
                                           r.querySelector('dd').textContent.trim()])"""))
            for cell in row.locator("td[data-label]:not([data-label='Detalle'])").all():
                col = cell.get_attribute("data-label")
                if col not in fields or normalize(cell.text_content()) != normalize(fields[col]):
                    field_mismatches += 1
            if useful:
                samples.append({"row": first_cell, "fields": selected_details(dialog),
                                "generic_label": "· registro" in label,
                                "instrument_fields": sum(k in fields for k in ("Ticker", "Familia", "Mercado", "Moneda", "Settlement"))})
            if take_screenshot and test_count == 1:
                take_screenshot()
            page.keyboard.press("Escape")
            if dialog.evaluate("(d) => d.open"):
                problems += 1
                dialog.get_by_role("button", name="Cerrar detalle").click()
            if not button.evaluate("(b) => b === document.activeElement"):
                missing_focus += 1
        except Error:
            problems += 1
            page.keyboard.press("Escape")
    return {"available": triggers.count(), "opened": test_count - problems,
            "attempted": test_count, "problems": problems,
            "field_mismatches": field_mismatches, "generic_labels": generic,
            "focus_not_restored": missing_focus, "duplicate_ids": duplicate_ids,
            "selected_rows": samples}


def validation(page, path, report):
    if path == "/sistema/evidencia":
        trigger = page.locator("main #sistema-evidencia-dialog").locator("xpath=preceding-sibling::button[1]")
        if trigger.count() == 1:
            trigger.click(timeout=2000)
            visible = page.locator("#sistema-evidencia-dialog").evaluate("(d) => d.open && d.matches(':modal')")
            page.keyboard.press("Escape")
            report["standalone_evidence_detail"] = {"opened": visible, "focus_restored": trigger.evaluate("(b) => b === document.activeElement")}
        link = page.get_by_role("link", name="Ver hitos M0–M11")
        if link.count() == 1:
            targets = []
            observe = lambda response: targets.append((urlsplit(response.url).path, response.status)) if response.request.is_navigation_request() else None
            page.on("response", observe)
            link.click(timeout=5000)
            page.wait_for_url("**/validacion", timeout=7000)
            page.wait_for_load_state("domcontentloaded", timeout=7000)
            report["validation_navigation"] = {"path": urlsplit(page.url).path, "responses": targets,
                                               "terminal_shell": page.locator("#terminal-content").count() == 1}
            page.remove_listener("response", observe)
        return None
    cards = page.locator("details.paper-trade > summary")
    output = {"hitos": cards.count(), "details": [], "contrast": page.evaluate(VALIDATION_CONTRAST),
              "legacy_shell": page.locator("#terminal-content").count() == 0}
    output["legacy_main"] = page.locator("#porota-main-content").count() == 1
    output["m4_current_text"] = ("VERIFICADO ACTUAL" in cards.nth(4).text_content()) if cards.count() > 4 else None
    output["m11_blocked"] = ("BLOQUEADO POR POLÍTICA" in cards.nth(11).text_content()) if cards.count() > 11 else None
    for n in range(min(12, cards.count())):
        summary = cards.nth(n)
        title = normalize(summary.text_content())
        if n not in {0, 1, 4, 11}:
            continue
        summary.evaluate("(s) => { s.scrollIntoView({block:'center'}); s.focus(); }")
        summary.press("Enter", timeout=2000)
        dialog = page.locator("dialog.paper-detail-dialog")
        dialog.wait_for(state="visible", timeout=2000)
        body = dialog.locator(".paper-detail-content")
        visible = body.inner_text()
        recorded = {"milestone": f"M{n}", "summary_status": BADGE.findall(title),
                    "percentages": re.findall(r"\d{1,3}%", title),
                    "opened": dialog.evaluate("(d) => d.open && d.matches(':modal')"),
                    "title_matches": normalize(dialog.locator("h2").inner_text()) == title,
                    "exit_criteria": body.locator("li").count(),
                    "details_include_blocker": "Blocker:" in visible,
                    "details_include_ref": "Referencia:" in visible}
        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden", timeout=2000)
        page.wait_for_function("(n) => document.activeElement === document.querySelectorAll('details.paper-trade > summary')[n]", arg=n, timeout=2500)
        recorded["focus_restored"] = True
        output["details"].append(recorded)
    return output


def view_states(page):
    panels = {}
    for label in ("PPI · autoridad primaria", "Último dato de mercado", "Heartbeat", "Órdenes reales observadas"):
        el = page.locator(".strip-item").filter(has=page.get_by_text(label, exact=True)).locator("span")
        if el.count():
            raw = normalize(el.first.inner_text())
            panels[label] = sorted(set(BADGE.findall(raw) + (["OK"] if raw == "OK" else [])))
    return panels


def run(base, token, output, live=False, focused=False):
    output.mkdir(parents=True, exist_ok=True)
    scope = "LOOPBACK_DROPLET_VIA_ACTIONS" if live else "CHECKOUT_EXACTO_SQLITE_FIXTURE"
    results = {"schema": "porota.rc6.dashboard.browser-audit.v1", "captured_at": datetime.now(timezone.utc).isoformat(),
               "scope": scope, "routes": {}, "phone": {}, "blocked_requests": Counter(), "snapshots": []}
    origin = urlsplit(base).netloc
    with sync_playwright() as pw:
        options = {"channel": "chrome"} if live else {"executable_path": "/usr/lib/chromium/chromium"}
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"], **options)
        results["browser_version"] = browser.version
        context = browser.new_context(viewport=TABLET, reduced_motion="reduce", service_workers="block")
        def allow(route):
            request = route.request
            request_url = urlsplit(request.url)
            if request_url.netloc != origin or request.method not in {"GET", "HEAD"}:
                results["blocked_requests"][request.method] += 1
                return route.abort()
            return route.continue_(headers={**request.headers, "authorization": "Bearer " + token})

        context.route("**/*", allow)
        page = context.new_page()
        page_errors = []
        page.on("pageerror", lambda exc: page_errors.append(type(exc).__name__))

        def snapshot(filename):
            # No screenshots with row text from the live production dashboard.
            # For live visual layout capture, redaction transforms the rendered DOM only.
            if live:
                page.evaluate(SCREEN_MASK)
            page.screenshot(path=str(output / filename), animations="disabled", timeout=5000)
            results["snapshots"].append(filename)

        targets = ("/validacion",) if focused else [*dict.fromkeys((*PRIORITY, *CANONICAL_PATHS))]
        for path in targets:
            info = {"page_errors": 0}
            page_errors.clear()
            try:
                response = page.goto(base + path, wait_until="domcontentloaded", timeout=7000)
                info.update(status=response.status if response else None,
                            shell=page.locator("#terminal-content").count() == 1,
                            main=page.locator("#terminal-content").get_attribute("data-view", timeout=1200)
                            if page.locator("#terminal-content").count() else None,
                            h1_count=page.locator("h1").count(),
                            horizontal_overflow=page.evaluate("() => Math.max(0, document.documentElement.scrollWidth - innerWidth)"))
                if info["status"] == 200:
                    if path != "/validacion":
                        info.update(row_details(page, path, max_rows=100))
                        if path in PRIORITY:
                            info["header_states"] = view_states(page)
                    if path in ("/validacion", "/sistema/evidencia"):
                        report = validation(page, path, results)
                        if report:
                            info["validation"] = report
                    if path in ("/en-vivo", "/sistema/integraciones", "/sistema/scheduler", "/sistema/scheduler?offset=20", "/validacion"):
                        if path == "/validacion":
                            page.locator("details.paper-trade > summary").first.evaluate("(s) => s.scrollIntoView({block:'center'})")
                        elif not page.locator("tr[data-row='true']").count():
                            page.evaluate("window.scrollTo(0, 0)")
                        else:
                            page.locator("tr[data-row='true']").first.scroll_into_view_if_needed()
                        snapshot(("page" + path.replace("/", "-").replace("?offset=", "-")) + ".png")
                info["page_errors"] = len(page_errors)
            except (Error, TypeError, ValueError) as exc:
                info["error"] = type(exc).__name__
            results["routes"][path] = info
            if live:
                sleep(.18)
        page.set_viewport_size(PHONE)
        mobile = () if focused else ("/en-vivo", "/sistema/integraciones", "/sistema/scheduler", "/sistema/evidencia", "/validacion")
        for path in mobile:
            try:
                response = page.goto(base + path, wait_until="domcontentloaded", timeout=7000)
                info = {"status": response.status, "horizontal_overflow": page.evaluate(
                    "() => Math.max(0, document.documentElement.scrollWidth - innerWidth)")}
                if page.locator("#terminal-menu").count():
                    menu = page.locator("#terminal-menu")
                    info["mobile_menu_visible"] = menu.is_visible()
                    if info["mobile_menu_visible"]:
                        menu.click()
                        info["mobile_menu_opens"] = menu.get_attribute("aria-expanded") == "true"
                        page.keyboard.press("Escape")
                        info["mobile_menu_closes"] = menu.get_attribute("aria-expanded") == "false"
                    info["details"] = row_details(page, path, max_rows=3)
                    if path == "/sistema/scheduler":
                        first = page.locator("tr[data-row='true'] button[data-detail-dialog]").first
                        first.click(timeout=1500)
                        snapshot("phone-scheduler-dialog.png")
                results["phone"][path] = info
            except Error as exc:
                results["phone"][path] = {"error": type(exc).__name__}
        context.close()
        browser.close()
    (output / "audit.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
    return results


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run("http://127.0.0.1:18765", os.environ["DASHBOARD_ACCESS_TOKEN"], args.output)
    routes = list(result["routes"].values())
    print(json.dumps({"routes": len(routes), "http_200": sum(r.get("status")==200 for r in routes),
                      "rows": sum(r.get("attempted",0) for r in routes),
                      "row_problems": sum(r.get("problems",0) for r in routes),
                      "errors": [k for k,r in result["routes"].items() if r.get("error") or r.get("page_errors")]}))
