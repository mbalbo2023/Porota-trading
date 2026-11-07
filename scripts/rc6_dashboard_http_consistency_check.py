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
SAFE_SESSION_LABELS = frozenset({
    "MARKET_OPEN", "MARKET_CLOSED", "PREOPEN", "WAITING_MARKET",
    "BOOT_CALENDAR", "CHECKING", "STARTING", "UNKNOWN",
})
SAFE_PROCESS_LABELS = frozenset({
    "READY", "RUNNING", "STOPPED", "WAITING_MARKET", "PAUSED",
    "DEGRADED", "ERROR", "UNKNOWN", "STARTING",
})


def _public_label(value: object, accepted: frozenset[str]) -> str:
    """Never echo arbitrary runtime strings into a public diagnostic log."""
    return value if type(value) is str and value in accepted else "NO_VERIFICADO"


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
    if not isinstance(truth, dict):
        print("DASHBOARD_TRUTH_SCHEMA=INVALID")
        return 3

    failures = []
    if truth.get("mode") != "PRODUCTION_PAPER":
        failures.append("NON_PAPER_MODE")
    if truth.get("execution") != "SIMULATED":
        failures.append("NON_SIMULATED_EXECUTION")
    orders = truth.get("real_orders_sent")
    # Only an actual, nonnegative integer is safety evidence. Missing, JSON
    # null, false, numeric text or floats cannot certify PAPER/zero.
    counter_verified = type(orders) is int and orders >= 0
    if not counter_verified:
        failures.append("REAL_ORDERS_UNVERIFIED")
    elif orders != 0:
        failures.append("REAL_ORDERS_NONZERO")
    for path in EXPECTED_ROUTES:
        status, html = fetch(path)
        if status != 200:
            failures.append(f"{path}:HTTP_{status}")
            continue
        for error in page_errors(path, html, truth):
            failures.append(f"{path}:{error}")

    print("POROTA RC6 — DASHBOARD CONSISTENCY")
    print("MODE=" + _public_label(truth.get("mode"), frozenset({"PRODUCTION_PAPER"})))
    print("EXECUTION=" + _public_label(truth.get("execution"), frozenset({"SIMULATED"})))
    print("SESSION=" + _public_label(truth.get("session_state"), SAFE_SESSION_LABELS))
    print("PROCESS=" + _public_label(truth.get("process_state"), SAFE_PROCESS_LABELS))
    print("REAL_ORDERS_SENT=" + (str(orders) if counter_verified else "NO_VERIFICADO"))
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
