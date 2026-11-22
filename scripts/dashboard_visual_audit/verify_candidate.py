"""Focused browser verification of the candidate with two simulated sessions."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep
import unittest
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

from browser_audit import row_details
from tests.test_rc6_postclose_validation_publish import Campaign, RELEASE
from rc6_scheduler_snapshot import EXPORT_UNIT

HOST = "http://127.0.0.1:18765"
ROUTES = ("/sistema/evidencia", "/validacion", "/sistema/validacion?channel=campaign", "/sistema/scheduler", "/en-vivo")


def prepare(fixture):
    first_session = fixture.now - timedelta(days=7)
    receipt = fixture.data / "deploy" / "CURRENT_STATE_V2.json"
    fixture.write_json(receipt, {
        **json.loads(receipt.read_text(encoding="utf-8")),
        "recorded_at": (first_session - timedelta(hours=2)).isoformat(),
    })
    for now in (first_session, first_session + timedelta(days=4)):
        fixture.mark_session(now)
        with fixture.store.connect() as c:
            c.execute("INSERT INTO paper_events(event_at,source,event_type,detail) VALUES (?,?,?,?)",
                      (now.isoformat(), "FIXTURE", "CANDIDATE_REVIEW", "{}"))
        if fixture.publish(now)["state"] != "PUBLISHED":
            raise AssertionError("Simulated postclose receipt was not published")
    seen = datetime.now(timezone.utc)
    rows = [EXPORT_UNIT, "porota-snapshot-postclose-rc6.timer",
            *(f"porota-fixture-{i:02d}.timer" for i in range(30))]
    fixture.write_json(fixture.data / "scheduler" / "systemd_timers.json", {
        "schema": "porota-scheduler-systemd-v1", "state": "OK", "recorded_at": seen.isoformat(),
        "timers": [{"unit": name, "description": "Fixture local", "active_state": "active",
                    "enabled_state": "enabled", "last_result": "success", "exec_main_status": "0",
                    "last_trigger": seen.strftime("%a %Y-%m-%d %H:%M:%S UTC"),
                    "next_elapse": (seen + timedelta(minutes=10)).strftime("%a %Y-%m-%d %H:%M:%S UTC"),
                    "next_elapse_monotonic_at": (seen + timedelta(minutes=5)).isoformat(),
                    "next_elapse_monotonic": "2h 5min"} for name in rows]})
    os.environ.update({
        "POROTA_VALIDATION_ROOT": str(fixture.root),
        "POROTA_SCHEDULER_STATE_PATH": str(fixture.data / "scheduler" / "systemd_timers.json"),
        "DASHBOARD_ACCESS_TOKEN": "candidate-paper-dashboard-fixture-only",
        "DASHBOARD_SESSION_STORE": str(fixture.data / "session.json"),
    })


def review(out):
    fixture = Campaign(methodName="test_two_audited_sessions_progress_and_retries")
    fixture.setUp()
    blocked = Counter()
    try:
        prepare(fixture)
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "o_dashboard:app",
                                   "--host", "127.0.0.1", "--port", "18765", "--no-access-log"])
        try:
            for _ in range(80):
                if server.poll() is not None:
                    raise RuntimeError("Fixture dashboard stopped")
                try:
                    with urlopen(HOST + "/health", timeout=1):
                        break
                except (URLError, TimeoutError):
                    sleep(.1)
            else:
                raise RuntimeError("Fixture dashboard unavailable")

            report = {"kind": "OFFHOST_SYNTHETIC_CANDIDATE_NOT_DROPLET", "candidate": os.environ["POROTA_QA_SHA"],
                      "utc": datetime.now(timezone.utc).isoformat(), "viewports": {}}
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="chrome", headless=True,
                                            args=["--disable-dev-shm-usage"])
                report["browser"] = browser.version
                for width, height in ((1280, 800), (390, 844)):
                    ctx = browser.new_context(viewport={"width": width, "height": height},
                                              reduced_motion="reduce", service_workers="block")
                    def local_get(route):
                        req = route.request
                        if urlsplit(req.url).netloc != urlsplit(HOST).netloc or req.method not in {"GET", "HEAD"}:
                            blocked[req.method] += 1
                            route.abort()
                            return
                        route.continue_(headers={**req.headers, "authorization": "Bearer " + os.environ["DASHBOARD_ACCESS_TOKEN"]})
                    ctx.route("**/*", local_get)
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(type(error).__name__))
                    numbers = {}
                    for path in ROUTES:
                        result = page.goto(HOST + path, wait_until="domcontentloaded", timeout=7000)
                        assert result.status == 200, (path, result.status)
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, path)
                        if path == "/sistema/evidencia":
                            page.get_by_role("link", name="Ver hitos M0–M11").click()
                            page.wait_for_url("**/sistema/validacion")
                            expect(page.locator("#terminal-content")).to_have_attribute("data-view", "sistema/validacion")
                        if path == "/validacion":
                            expect(page.locator("#terminal-content")).to_have_attribute("data-view", "sistema/validacion")
                            expect(page.locator("dialog[id^='validation-M']")).to_have_count(12)
                            safety_card = page.locator("article").filter(
                                has=page.locator("[data-detail-dialog='validation-M1-dialog']")
                            )
                            expired = safety_card.locator("[data-status='EVIDENCIA VENCIDA']")
                            expect(expired).to_be_visible()
                            expect(expired).to_contain_text("Evidencia vencida")
                            for n in (0, 1, 4, 11):
                                trigger = page.locator(f"[data-detail-dialog='validation-M{n}-dialog']")
                                trigger.click(timeout=4000)
                                dialog = page.locator(f"dialog#validation-M{n}-dialog")
                                assert dialog.evaluate("(node) => node.open && node.matches(':modal')")
                                expect(dialog.get_by_text("Criterios de salida", exact=True)).to_be_visible()
                                page.keyboard.press("Escape")
                                expect(trigger).to_be_focused()
                            expect(page.locator("#validacion-activity tr[data-row='true']")).to_have_count(2)
                            numbers["milestone_dialogs"] = 4
                        if "?channel=campaign" in path:
                            expect(page.locator("#validacion-campaign_days tr[data-row='true']")).to_have_count(2)
                            expect(page.get_by_role("heading", name="Historial de avances por rueda y versión")).to_be_visible()
                            results = row_details(page, path, max_rows=2)
                            assert results["opened"] == 2 and not results["field_mismatches"], results
                            numbers["campaign_dialogs"] = results["opened"]
                        if path == "/sistema/scheduler":
                            result_rows = row_details(page, path, max_rows=10 if width == 1280 else 3)
                            assert result_rows["opened"] == (10 if width == 1280 else 3), result_rows
                            assert not any(result_rows[k] for k in ("field_mismatches", "generic_labels",
                                                                   "focus_not_restored", "duplicate_ids")), result_rows
                            trigger = page.get_by_role("button", name=f"Detalle fila 1 · {EXPORT_UNIT}")
                            trigger.click()
                            modal = page.locator("dialog[open]")
                            for label in ("Próxima por calendario", "Próxima por reloj monotónico"):
                                expect(modal.get_by_text(label, exact=True)).to_be_visible()
                            assert not modal.get_by_text("Ticker", exact=True).count()
                            page.keyboard.press("Escape")
                            expect(trigger).to_be_focused()
                            numbers["scheduler_dialogs"] = result_rows["opened"]
                        if path == "/en-vivo":
                            expect(page.get_by_text("Autenticación PPI", exact=True)).to_be_visible()
                            expect(page.get_by_text("Workers críticos en RUNNING u OK", exact=True)).to_be_visible()
                    assert not errors, errors
                    report["viewports"][str(width)] = {"paths": list(ROUTES), **numbers, "overflow": 0}
                    ctx.close()
                browser.close()
            report["blocked_requests"] = dict(blocked)
            (out / "playwright.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    review(Path(sys.argv[1]))
