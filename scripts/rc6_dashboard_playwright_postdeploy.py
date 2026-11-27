#!/usr/bin/env python3
"""Navigate the installed dashboard in Chromium through a private Actions tunnel."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import socket
from socketserver import UnixStreamServer
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


SOURCE = Path(__file__).resolve().parents[1]
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from rc6_trader_dashboard import SCHEMA as PROJECTION_SCHEMA
from rc6_trader_dashboard.navigation import CANONICAL_PATHS, DESTINATIONS, view_path
from scripts.porota_actions_host import connection, decode_receipt, require, validate_runtime_receipt


SCHEMA = "porota.dashboard.playwright.v1"
BODY_LIMIT = 2 * 1024**2
PROXY_SECONDS = 240
PRIMARY_ROUTES = tuple(item.path for item in DESTINATIONS)
SUBROUTES = {item.key: tuple(view_path(item, tab) for tab, _ in item.tabs) for item in DESTINATIONS}
DETAIL_LIMIT = 60
SECONDARY_ROUTES = {"/validacion": "sistema/validacion", "/sistema/validacion?channel=campaign": "sistema/validacion",
                    "/sistema/scheduler?offset=20": "sistema/scheduler"}
UNKNOWN_AUDIT = r"""root => {
    const normalize = value => (value || '').normalize('NFD').replace(/\p{Diacritic}/gu, '').trim().toLowerCase();
    const textPattern = /(?:^|[^\p{L}\p{N}_])(NO[_\s-]+VERIFICAD[OA])(?=$|[^\p{L}\p{N}_])/giu;
    const hasVisibleText = element => element && element.getClientRects().length &&
        getComputedStyle(element).visibility === 'visible' && element.textContent.trim().length > 0;
    const legacyReason = /(?:\bfalta(?:n)?\b|\brequiere\b|\brevisar\b|\bsin (?:reloj|registros|eventos|datos|recibo|evidencia|publicaci[oó]n|consulta)\b|\bausente\b|\bvencid[oa]\b|\bno (?:disponible|publicad[oa]|acreditad[oa])\b|\bfuera del l[ií]mite\b)/iu;
    const typedReason = /(?:\bSHADOW_CUT_NOT_FRESH\b|\bCOMMITTED_GENERATION_(?:NOT_PUBLISHED|REJECTED)\b|\bSELECT_EXACT_IDENTITY_FOR_(?:MARKET|CONTRACT)\b)/u;
    function field(element) {
        const cell = element.closest('td[data-label]');
        const definition = element.closest('dd');
        const card = element.closest('.metric-card');
        const header = element.closest('.strip-item');
        const label = normalize(cell?.dataset.label || definition?.previousElementSibling?.textContent ||
            card?.querySelector('h2')?.textContent || header?.querySelector('b')?.textContent);
        if (/reloj|heartbeat|hora|fecha|ejecucion|cadencia/.test(label)) return 'CLOCK';
        if (/proveedor|fuente|autoridad|procedencia/.test(label)) return 'SOURCE';
        if (/motivo|bloqueo|alcance|cobertura/.test(label)) return 'REASON_OR_SCOPE';
        if (/frescu|vigenc|calidad/.test(label)) return 'FRESHNESS_OR_QUALITY';
        if (/riesgo|estado|readiness|selector|sesion|modo|autenticacion|capacidad/.test(label)) return 'STATE';
        if (/costo|precio|tasa|volumen|pnl|libro|caja|patrimonio|limite|score|muestra/.test(label)) return 'MARKET_OR_METRIC';
        if (/ticker|nombre|mercado|familia|moneda|settlement|contrato|instrumento/.test(label)) return 'IDENTITY';
        if (element.closest('nav')) return 'NAVIGATION';
        return cell || definition ? 'OTHER_FIELD' : 'PAGE_TEXT';
    }
    const tally = {total: 0, explained: 0, unexplained: 0, reason_types: {}, unexplained_fields: {}};
    const bump = (map, key, count) => { map[key] = (map[key] || 0) + count; };
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const el = node.parentElement;
        if (!el || el.closest('script,style,template,noscript,.sr-only,[hidden],[aria-hidden="true"],dialog:not([open])') ||
                !hasVisibleText(el)) continue;
        const count = [...node.nodeValue.matchAll(textPattern)].length;
        if (!count) continue;
        tally.total += count;
        const wrapper = el.closest('.unverified-value');
        const explanation = wrapper?.querySelector('.unverified-reason');
        if (hasVisibleText(explanation) && normalize(explanation.textContent).replace(textPattern, '').length > 10) {
            tally.explained += count;
            bump(tally.reason_types, 'VISIBLE_REASON', count);
            continue;
        }
        const sameField = el.closest('dd,td,.strip-item,.metric-card,li,.notice,.source-line,p') || el;
        const phrase = (sameField.textContent || '').trim();
        if (phrase.length <= 480 && (legacyReason.test(phrase) || typedReason.test(phrase))) {
            tally.explained += count;
            bump(tally.reason_types, 'INLINE_REASON', count);
        } else {
            tally.unexplained += count;
            bump(tally.unexplained_fields, field(el), count);
        }
    }
    return tally;
}"""
REMOTE_INIT = """import os, pathlib, re, runpy, sys
sha = sys.argv[1]
assert re.fullmatch('[0-9a-f]{40}', sha) and os.getuid() == os.geteuid() > 0
script = pathlib.Path.home() / '.local/state/porota-rc6/releases' / sha / 'source/scripts/rc6_dashboard_playwright_postdeploy.py'
assert all(not p.is_symlink() for p in (script, *script.parents))
sys.argv = [str(script), '--serve', *sys.argv[2:]]
runpy.run_path(str(script), run_name='__main__')
"""


def coordinates(receipt):
    result = {key: receipt[key] for key in ("source_sha", "source_tree", "image_id", "attempt")}
    result["dashboard_cid"] = receipt["roles"]["dashboard"]
    patterns = {"source_sha": r"[0-9a-f]{40}", "source_tree": r"[0-9a-f]{40}",
                "image_id": r"sha256:[0-9a-f]{64}", "attempt": r"[1-9][0-9]*-[1-9][0-9]*",
                "dashboard_cid": r"[0-9a-f]{64}"}
    require(all(isinstance(result[key], str) and re.fullmatch(pattern, result[key])
                for key, pattern in patterns.items()), "DASHBOARD_RELEASE_IDENTITY_INVALID")
    return result


def serve(expected, socket_path):
    from scripts.porota_rootless_host import DB_RELATIVE, Identity, database_snapshot, parse_env
    from scripts.porota_rootless_jobs import scheduler_lock, verified_host

    root = Path("/opt/porota-trading")
    identity = Identity.current()
    directory = socket_path.parent
    require(re.fullmatch(r"porota-rc6-visual-[0-9a-f]{32}", directory.name) and
            directory.parent == Path("/tmp") and socket_path.name == "dashboard.sock", "DASHBOARD_SOCKET_SCOPE")
    with scheduler_lock(identity):
        manager, current, _ = verified_host(root, identity)
        require(coordinates(current) == expected, "DASHBOARD_RUNTIME_MISMATCH")
        item = manager.check_role(current, "dashboard")
        environment = parse_env("\n".join(item["Config"]["Env"]))
        token = environment.get("DASHBOARD_ACCESS_TOKEN", "")
        require(bool(token) and "\n" not in token and "\r" not in token, "DASHBOARD_AUTH_UNAVAILABLE")

        class Proxy(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(5)
                super().setup()

            def log_message(self, *_args):
                pass

            def do_GET(self):
                target = urlsplit(self.path)
                if (target.netloc or target.scheme or "token" in parse_qs(target.query, keep_blank_values=True) or
                        target.path not in CANONICAL_PATHS.keys() | {"/health", "/validacion", "/favicon.ico"}):
                    self.send_error(404)
                    return
                upstream = HTTPConnection("127.0.0.1", 8000, timeout=8)
                try:
                    upstream.request("GET", self.path, headers={"Authorization": f"Bearer {token}",
                                                               "Cache-Control": "no-cache"})
                    reply = upstream.getresponse()
                    body = reply.read(BODY_LIMIT + 1)
                    if len(body) > BODY_LIMIT:
                        self.send_error(502)
                        return
                    self.send_response(reply.status)
                    for name in ("Content-Type", "X-Porota-Projection", "Cache-Control", "Server-Timing"):
                        value = reply.getheader(name)
                        if value is not None:
                            self.send_header(name, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (OSError, ValueError, HTTPException):
                    self.close_connection = True
                finally:
                    upstream.close()

        directory.mkdir(mode=0o700)
        try:
            with UnixStreamServer(str(socket_path), Proxy) as server, selectors.DefaultSelector() as selector:
                selector.register(sys.stdin, selectors.EVENT_READ, "control")
                selector.register(server, selectors.EVENT_READ, "dashboard")
                print("DASHBOARD_PROXY_READY", flush=True)
                deadline = time.monotonic() + PROXY_SECONDS
                while time.monotonic() < deadline:
                    for key, _mask in selector.select(max(0, deadline - time.monotonic())):
                        if key.data == "control":
                            require(sys.stdin.readline() == "finish\n", "DASHBOARD_PROXY_INTERRUPTED")
                            require(manager.check_role(current, "dashboard")["State"].get("Running") is True,
                                    "DASHBOARD_RUNTIME_CHANGED")
                            require(database_snapshot(root / DB_RELATIVE) == current["database"],
                                    "DASHBOARD_DATABASE_IDENTITY_CHANGED")
                            print("DASHBOARD_PROXY_VERIFIED", flush=True)
                            return
                        server.handle_request()
                raise ValueError("DASHBOARD_PROXY_TIMEOUT")
        finally:
            socket_path.unlink(missing_ok=True)
            directory.rmdir()


def read_signal(stream, expected):
    with selectors.DefaultSelector() as selector:
        selector.register(stream, selectors.EVENT_READ)
        require(bool(selector.select(25)) and stream.readline(64) == expected + b"\n", "DASHBOARD_TUNNEL_UNVERIFIED")


@contextmanager
def dashboard_tunnel(expected, env):
    with connection(env) as (ssh, _, target):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        remote_socket = f"/tmp/porota-rc6-visual-{uuid4().hex}/dashboard.sock"
        forward = f"127.0.0.1:{port}:{remote_socket}"
        command = shlex.join(["/usr/bin/python3", "-I", "-B", "-c", REMOTE_INIT,
                             expected["source_sha"], json.dumps(expected), remote_socket])
        with subprocess.Popen([*ssh[:-1], "-o", "ExitOnForwardFailure=yes", "-L", forward, target, command],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            try:
                read_signal(process.stdout, b"DASHBOARD_PROXY_READY")
                yield f"http://127.0.0.1:{port}"
                process.stdin.write(b"finish\n")
                process.stdin.flush()
                read_signal(process.stdout, b"DASHBOARD_PROXY_VERIFIED")
                require(process.wait(timeout=10) == 0, "DASHBOARD_TUNNEL_UNVERIFIED")
            finally:
                process.stdin.close()
                if process.poll() is None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()


def unknown_rollup(checks):
    result = {key: sum(row[key] for row in checks) for key in ("total", "explained", "unexplained")}
    for name in ("reason_types", "unexplained_fields"):
        combined = Counter()
        for row in checks:
            combined.update(row[name])
        result[name] = dict(sorted(combined.items()))
    return result


def audit_menus(page, view):
    primary = page.locator("#porota-canonical-nav a")
    secondary = page.locator(".subnav a")
    destinations = "links => links.map(link => new URL(link.href).pathname)"
    primary_active = page.locator("#porota-canonical-nav a[aria-current='page']")
    subview_active = page.locator(".subnav a[aria-current='page']")
    return {"primary_links": primary.count(), "subview_links": secondary.count(),
            "primary_destinations_match": tuple(primary.evaluate_all(destinations)) == PRIMARY_ROUTES,
            "subview_destinations_match": tuple(secondary.evaluate_all(destinations)) == SUBROUTES[view[0]],
            "active_primary": tuple(primary_active.evaluate_all(destinations)) == (SUBROUTES[view[0]][0],),
            "active_subview": len(subview_active.evaluate_all(destinations)) == 1 and
                CANONICAL_PATHS.get(subview_active.evaluate_all(destinations)[0]) == view}


def audit_row_details(page, deadline):
    from playwright.sync_api import Error

    buttons = page.locator("#terminal-content tr[data-row='true'] button[data-detail-dialog]")
    observed = {"available": buttons.count(), "attempted": 0, "opened": 0, "labels_match": 0,
                "fields_match": 0, "focus_restored": 0}
    findings = []
    for button in buttons.all()[:DETAIL_LIMIT]:
        if time.monotonic() >= deadline:
            break
        observed["attempted"] += 1
        target = button.get_attribute("aria-controls")
        if not target or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", target):
            continue
        dialog = page.locator("dialog[id=" + json.dumps(target) + "]")
        try:
            button.click(timeout=1_200)
            if dialog.count() != 1 or not dialog.evaluate("d => d.open && d.matches(':modal')"):
                continue
            observed["opened"] += 1
            parity = button.evaluate(r"""button => {
                const dialog = document.getElementById(button.getAttribute('aria-controls'));
                const normalize = text => (text || '').replace(/\s+/g, ' ').trim();
                const title = document.getElementById(dialog.getAttribute('aria-labelledby'));
                const facts = [...dialog.querySelectorAll('dl.facts > div')].map(d => [
                    normalize(d.querySelector('dt')?.textContent), normalize(d.querySelector('dd')?.textContent)]);
                const rows = [...button.closest('tr').querySelectorAll('td[data-label]:not([data-label="Detalle"])')];
                return {label: !!title && normalize(button.textContent) === normalize(title.textContent),
                    fields: facts.length > 0 && rows.every(cell => facts.some(([field, value]) =>
                        field === normalize(cell.dataset.label) && value === normalize(cell.textContent)))};
            }""")
            observed["labels_match"] += int(parity["label"])
            observed["fields_match"] += int(parity["fields"])
            findings.append(dialog.evaluate(UNKNOWN_AUDIT))
        except Error:
            pass
        finally:
            page.keyboard.press("Escape")
            observed["focus_restored"] += int(button.evaluate("b => document.activeElement === b"))
    observed["unverified_labels"] = unknown_rollup(findings)
    return observed


def audit_route(page, base_url, path, view, row):
    from playwright.sync_api import Error

    target = base_url + path
    selector = "#porota-canonical-nav" if path in PRIMARY_ROUTES else ".subnav"
    link = page.locator(selector + " a[href=" + json.dumps(path) + "]")
    response = None
    if link.count() == 1 and link.is_visible():
        try:
            with page.expect_navigation(url=target, wait_until="domcontentloaded", timeout=7_000) as event:
                link.click(timeout=2_000)
            response = event.value
            row["navigation"] = "PRIMARY_MENU" if path in PRIMARY_ROUTES else "SUBMENU"
        except Error:
            pass
    if response is None:
        row["navigation"] = "DIRECT_FALLBACK"
        response = page.goto(target, wait_until="domcontentloaded", timeout=8_000)
    row["http_status"] = response.status if response is not None else None
    content = page.locator("#terminal-content")
    row["verified"] = bool(response and response.status == 200 and page.url == target and
                           response.headers.get("x-porota-projection") == PROJECTION_SCHEMA and content.is_visible() and
                           content.get_attribute("data-view") == "/".join(view) and
                           page.locator("#porota-paper-mode").get_attribute("aria-label") == "MODO SIMULACIÓN PRODUCTIVA")
    if row["verified"]:
        row["menus"] = audit_menus(page, view)
        row["horizontal_overflow_px"] = page.evaluate("() => Math.max(0, document.documentElement.scrollWidth - innerWidth)")
        row["unverified_labels"] = page.locator("body").evaluate(UNKNOWN_AUDIT)


def navigate(base_url, receipt):
    from playwright.sync_api import Error, expect, sync_playwright

    def restrict(route):
        if route.request.method == "GET" and route.request.url.startswith(base_url + "/"):
            route.continue_()
        else:
            route.abort()

    deadline = time.monotonic() + PROXY_SECONDS - 25
    with sync_playwright() as playwright:
        with playwright.chromium.launch(channel="chrome") as browser:
            context = browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block")
            context.route("**/*", restrict)
            page = context.new_page()
            page.set_default_timeout(5_000)
            errors = []
            page.on("pageerror", lambda _error: errors.append(True))
            health = page.goto(base_url + "/health", timeout=10_000)
            require(health is not None and health.ok and health.json().get("status") == "ok", "DASHBOARD_HEALTH_FAILED")
            page.goto(base_url + "/", wait_until="domcontentloaded", timeout=8_000)
            for path, view in CANONICAL_PATHS.items():
                started = time.monotonic()
                row = {"path": path, "http_status": None, "verified": False, "navigation": "NO_VERIFICADO"}
                receipt["routes"].append(row)
                if started >= deadline - 30:
                    row["reason"] = "DASHBOARD_NAVIGATION_TIMEOUT"
                    continue
                try:
                    audit_route(page, base_url, path, view, row)
                    if row["verified"]:
                        row["first_page_details"] = audit_row_details(page, deadline - 30)
                except Error:
                    row["reason"] = "DASHBOARD_VIEW_UNVERIFIED"
                row["elapsed_ms"] = round((time.monotonic() - started) * 1000)

            details = [row["first_page_details"] for row in receipt["routes"] if "first_page_details" in row]
            receipt["first_page_details"] = {key: sum(row[key] for row in details)
                                             for key in ("available", "attempted", "opened", "labels_match",
                                                         "fields_match", "focus_restored")}
            checks = [row["unverified_labels"] for row in receipt["routes"] if "unverified_labels" in row]
            receipt["unverified_labels"] = unknown_rollup(checks + [row["unverified_labels"] for row in details])
            receipt["menu_navigation_count"] = sum(row["navigation"] in {"PRIMARY_MENU", "SUBMENU"} for row in receipt["routes"])
            receipt["javascript_error_count"] = len(errors)
            require(all(row["verified"] for row in receipt["routes"]), "DASHBOARD_ROUTES_FAILED")
            secondary_results = {}
            for path, view in SECONDARY_ROUTES.items():
                response = page.goto(base_url + path, wait_until="domcontentloaded", timeout=6_000)
                secondary_results[path] = bool(response and response.status == 200 and
                    page.locator("#terminal-content").get_attribute("data-view") == view)
            receipt["secondary_routes"] = secondary_results

            page.goto(base_url + "/sistema/evidencia", wait_until="domcontentloaded", timeout=10_000)
            page.locator("a[href='/sistema/validacion']").first.click()
            expect(page.locator("#terminal-content")).to_have_attribute("data-view", "sistema/validacion")
            receipt["milestone_details"] = []
            milestones = []
            for code in ("M" + str(index) for index in range(12)):
                heading = page.get_by_role("heading", name=re.compile(r"^" + code + r" · "))
                expect(heading).to_be_visible()
                panel = heading.locator("..")
                panel.get_by_role("button", name=re.compile(r"^Detalle " + code + r" · ")).click()
                expect(panel.locator("dialog")).to_be_visible()
                milestones.append(panel.locator("dialog").evaluate(UNKNOWN_AUDIT))
                page.keyboard.press("Escape")
                expect(panel.locator("dialog")).to_be_hidden()
                receipt["milestone_details"].append(code)
            receipt["milestone_unverified_labels"] = unknown_rollup(milestones)

            page.set_viewport_size({"width": 800, "height": 1100})
            page.locator("#terminal-menu").click()
            expect(page.locator("#porota-canonical-nav")).to_be_visible()
            page.locator("#porota-canonical-nav a[href='/']").click()
            expect(page.locator("#terminal-content")).to_have_attribute("data-view", "inicio/resumen")
            page.locator("#refresh-data").click()
            expect(page.locator("#refresh-status")).to_contain_text("Datos actualizados")
            receipt["mobile_navigation_and_refresh"] = True
            receipt["javascript_error_count"] = len(errors)
            require(all(secondary_results.values()) and not errors, "DASHBOARD_ROUTES_FAILED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--require-explained", action="store_true")
    parser.add_argument("--serve", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        try:
            serve(json.loads(args.serve[0]), Path(args.serve[1]))
        except Exception:
            return 1
        return 0
    if args.receipt is None or args.runtime_receipt is None:
        parser.error("--receipt and --runtime-receipt are required")
    receipt = {"schema": SCHEMA, "state": "NO_VERIFICADO", "routes": []}
    try:
        runtime = decode_receipt(args.runtime_receipt.read_bytes())
        expected = coordinates(runtime)
        validate_runtime_receipt(runtime, expected, 0)
        receipt.update(expected)
        with dashboard_tunnel(expected, os.environ) as base_url:
            navigate(base_url, receipt)
        if args.require_explained:
            require((receipt["unverified_labels"]["unexplained"] +
                     receipt["milestone_unverified_labels"]["unexplained"]) == 0,
                    "DASHBOARD_UNVERIFIED_LABELS_UNEXPLAINED")
        receipt.update(state="VERIFICADO", real_orders_sent=0, browser="chromium", read_only=True)
    except Exception as error:
        receipt["reason"] = (str(error) if isinstance(error, ValueError) and
                             re.fullmatch(r"[A-Z0-9_]{1,80}", str(error)) else "DASHBOARD_PLAYWRIGHT_UNVERIFIED")
    args.receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if receipt["state"] == "VERIFICADO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
