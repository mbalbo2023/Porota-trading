#!/usr/bin/env python3
"""Read-only HTTP consistency audit for the RC6 dashboard.

Run from the host/container after a candidate dashboard is up.  It performs no
writes.  It verifies canonical navigation, one runtime-truth banner per page,
closed-market wording, legacy misleading labels and the real_orders invariant.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.getenv("POROTA_DASHBOARD_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.getenv("DASHBOARD_ACCESS_TOKEN", "")
EXPECTED_ROUTES = (
    "/", "/en-vivo", "/trading", "/universo-operativo", "/scalping",
    "/validacion", "/instrumentos", "/historicos", "/aprendizaje",
    "/reportes", "/sistema", "/motor-trading",
)
CANONICAL_NAV = (
    "/", "/en-vivo", "/trading", "/scalping", "/validacion", "/instrumentos",
    "/historicos", "/aprendizaje", "/reportes", "/sistema",
)


def fetch(path: str) -> tuple[int, str]:
    headers = {"Cache-Control": "no-cache"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def page_errors(path: str, html: str, truth: dict) -> list[str]:
    errors = []
    if html.count("id='porota-canonical-nav'") + html.count('id="porota-canonical-nav"') != 1:
        errors.append("CANONICAL_NAV_COUNT")
    if html.count("id='porota-runtime-truth'") + html.count('id="porota-runtime-truth"') != 1:
        errors.append("RUNTIME_TRUTH_BANNER_COUNT")
    for route in CANONICAL_NAV:
        if f"href='{route}'" not in html and f'href="{route}"' not in html:
            errors.append("NAV_LINK_MISSING:" + route)
    if "Economía matemática BINDING" in html:
        errors.append("LEGACY_BINDING_AS_MODE_LABEL")
    if truth.get("session_state") != "MARKET_OPEN":
        if "Reloj independiente: <b>RUNNING</b>" in html:
            errors.append("CLOSED_MARKET_SUPERVISOR_PRESENTED_RUNNING")
        if path == "/scalping" and re.search(r"Scanner<br><b[^>]*>RUNNING</b>", html):
            errors.append("CLOSED_MARKET_SCANNER_PRESENTED_RUNNING")
    return errors


def main() -> int:
    status, payload = fetch("/api/dashboard/truth")
    if status != 200:
        print(f"DASHBOARD_TRUTH_HTTP={status}")
        return 2
    try:
        truth = json.loads(payload)
    except json.JSONDecodeError:
        print("DASHBOARD_TRUTH_JSON=INVALID")
        return 3

    failures = []
    if int(truth.get("real_orders_sent") or 0) != 0:
        failures.append("REAL_ORDERS_NONZERO")
    for path in EXPECTED_ROUTES:
        status, html = fetch(path)
        if status != 200:
            failures.append(f"{path}:HTTP_{status}")
            continue
        for error in page_errors(path, html, truth):
            failures.append(f"{path}:{error}")

    print("POROTA RC6 — DASHBOARD CONSISTENCY")
    print("MODE=" + str(truth.get("mode")))
    print("EXECUTION=" + str(truth.get("execution")))
    print("SESSION=" + str(truth.get("session_state")))
    print("PROCESS=" + str(truth.get("process_state")))
    print("REAL_ORDERS_SENT=" + str(truth.get("real_orders_sent")))
    print("ROUTES_CHECKED=" + str(len(EXPECTED_ROUTES)))
    if failures:
        for item in failures:
            print("FAIL=" + item)
        print("RESULT=RED")
        return 1
    print("RESULT=GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
