#!/usr/bin/env python3
"""Navigate the installed dashboard in Chromium through a private Actions tunnel."""
from __future__ import annotations

import argparse
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
from rc6_trader_dashboard.navigation import CANONICAL_PATHS
from scripts.porota_actions_host import connection, decode_receipt, require, validate_runtime_receipt


SCHEMA = "porota.dashboard.playwright.v1"
BODY_LIMIT = 2 * 1024**2
PROXY_SECONDS = 240
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
            for path, view in CANONICAL_PATHS.items():
                require(time.monotonic() < deadline, "DASHBOARD_NAVIGATION_TIMEOUT")
                started = time.monotonic()
                row = {"path": path, "http_status": None, "verified": False}
                receipt["routes"].append(row)
                try:
                    result = page.goto(base_url + path, wait_until="domcontentloaded", timeout=10_000)
                    row["http_status"] = result.status if result is not None else None
                    content = page.locator("#terminal-content")
                    row["verified"] = bool(result and result.status == 200 and page.url == base_url + path and
                                           result.headers.get("x-porota-projection") == PROJECTION_SCHEMA and
                                           page.locator("#porota-canonical-nav").is_visible() and content.is_visible() and
                                           content.get_attribute("data-view") == "/".join(view) and
                                           page.locator("#porota-paper-mode").get_attribute("aria-label") ==
                                           "MODO SIMULACIÓN PRODUCTIVA")
                except Error:
                    pass
                row["elapsed_ms"] = round((time.monotonic() - started) * 1000)

            page.goto(base_url + "/sistema/evidencia", wait_until="domcontentloaded", timeout=10_000)
            page.locator("a[href='/sistema/validacion']").first.click()
            expect(page.locator("#terminal-content")).to_have_attribute("data-view", "sistema/validacion")
            for code in ("M0", "M1"):
                heading = page.get_by_role("heading", name=re.compile(r"^" + code + r" · "))
                expect(heading).to_be_visible()
                panel = heading.locator("..")
                panel.get_by_role("button", name=re.compile(r"^Detalle " + code + r" · ")).click()
                expect(panel.locator("dialog")).to_be_visible()
                page.keyboard.press("Escape")
                expect(panel.locator("dialog")).to_be_hidden()
            receipt["milestone_details"] = ["M0", "M1"]

            page.set_viewport_size({"width": 800, "height": 1100})
            page.locator("#terminal-menu").click()
            expect(page.locator("#porota-canonical-nav")).to_be_visible()
            page.locator("#porota-canonical-nav a[href='/']").click()
            expect(page.locator("#terminal-content")).to_have_attribute("data-view", "inicio/resumen")
            page.locator("#refresh-data").click()
            expect(page.locator("#refresh-status")).to_contain_text("Datos actualizados")
            receipt["mobile_navigation_and_refresh"] = True
            receipt["javascript_error_count"] = len(errors)
            require(all(row["verified"] for row in receipt["routes"]) and not errors, "DASHBOARD_ROUTES_FAILED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-receipt", type=Path)
    parser.add_argument("--receipt", type=Path)
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
        receipt.update(state="VERIFICADO", real_orders_sent=0, browser="chromium", read_only=True)
    except Exception as error:
        receipt["reason"] = (str(error) if isinstance(error, ValueError) and
                             re.fullmatch(r"[A-Z0-9_]{1,80}", str(error)) else "DASHBOARD_PLAYWRIGHT_UNVERIFIED")
    args.receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if receipt["state"] == "VERIFICADO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
