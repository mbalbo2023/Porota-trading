"""No-network contracts for the independent PAPER schedule-silence watchdog."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import rc6_market_watch_silence as watch


NOW = datetime(2026, 10, 14, 16, 32, tzinfo=timezone.utc)


def test_two_intervals_jitter_and_market_gate():
    assert watch.active_window(NOW)
    assert watch.classify(NOW, NOW - timedelta(minutes=24)) == "OK"
    assert watch.classify(NOW, NOW - timedelta(minutes=26)) == "SILENT"
    assert watch.classify(NOW, NOW + timedelta(minutes=1)) == "SILENT"
    assert not watch.active_window(datetime(2026, 10, 14, 13, 59, tzinfo=timezone.utc))
    assert not watch.active_window(datetime(2026, 10, 17, 16, 32, tzinfo=timezone.utc))


def test_alert_ack_dedupe_retry_and_recovery(tmp_path):
    path = tmp_path / "state.json"
    sent = []
    def deliver(message):
        sent.append(message)
        return str(len(sent) + 850)
    missing = lambda: None
    first = watch.tick(now=NOW, path=path, fetch=missing, send=deliver)
    assert first == {"status": "SILENT", "delivery": "SENT", "message_id": "851"}
    assert watch.tick(now=NOW, path=path, fetch=missing, send=deliver)["delivery"] == "NO_TRANSITION"
    assert len(sent) == 1
    recovery = watch.tick(now=NOW, path=path, fetch=lambda: NOW, send=deliver)
    assert recovery["status"] == "OK" and recovery["delivery"] == "SENT"
    assert len(sent) == 2
    assert "SIMULACIÓN" in sent[0] and "MONITOR_SCHEDULE_SILENT" in sent[0]
    assert "MONITOR_SCHEDULE_OK" in sent[1]
    assert json.loads(path.read_text())["status"] == "OK"


def test_unacknowledged_alert_retries_without_persisting(tmp_path):
    path = tmp_path / "state.json"
    assert watch.tick(now=NOW, path=path, fetch=lambda: None,
                      send=lambda text: "0")["delivery"] == "UNVERIFIED"
    assert not path.exists()
    assert watch.tick(now=NOW, path=path, fetch=lambda: None,
                      send=lambda text: "852")["delivery"] == "SENT"


def test_github_failure_fails_closed_and_invalid_state_does_not_suppress(tmp_path):
    path = tmp_path / "state.json"
    def unavailable():
        raise TimeoutError("never include remote details")
    alert = watch.tick(now=NOW, path=path, fetch=unavailable, send=lambda text: "853")
    assert alert == {"status": "UNKNOWN", "delivery": "SENT", "message_id": "853"}
    path.write_text("corrupt")
    assert watch.tick(now=NOW, path=path, fetch=unavailable,
                      send=lambda text: pytest.fail("must not send"))["status"] == "STATE_UNVERIFIED"
    path.unlink()
    path.symlink_to(tmp_path / "target")
    assert watch.tick(now=NOW, path=path, fetch=unavailable,
                      send=lambda text: pytest.fail("must not send"))["status"] == "STATE_UNVERIFIED"


def test_public_api_identity_and_bounded_response():
    class Response:
        def __init__(self, value):
            self.value = value
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            assert size == 131073
            return json.dumps(self.value).encode()
    def opener(request, timeout):
        assert timeout == 6 and request.full_url == watch.RUNS_URL
        return Response({"workflow_runs": [{"event": "schedule", "path": watch.WORKFLOW,
            "head_branch": "main", "status":"completed", "run_attempt":1,
            "created_at": "2026-10-14T16:26:00Z", "run_started_at":"2026-10-14T16:27:00Z"}]})
    assert watch.latest_schedule(opener=opener) == datetime(
        2026, 10, 14, 16, 27, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="GITHUB_RUN_IDENTITY_INVALID"):
        watch.latest_schedule(opener=lambda request, timeout: Response({"workflow_runs": [{
            "event": "workflow_dispatch", "path": watch.WORKFLOW,
            "head_branch": "main", "created_at": "2026-10-14T16:27:00Z"}]}))
    for status,attempt in [('queued',1),('in_progress',1),('completed',2)]:
        value={'workflow_runs':[{'event':'schedule','path':watch.WORKFLOW,
            'head_branch':'main','status':status,'run_attempt':attempt,
            'created_at':'2026-10-14T16:27:00Z','run_started_at':'2026-10-14T16:27:00Z'}]}
        assert watch.latest_schedule(opener=lambda request,timeout:Response(value)) is None


def test_holiday_and_unreviewed_calendar_never_report_active():
    assert not watch.active_window(NOW.replace(day=12))
    assert not watch.active_window(NOW.replace(year=2027))


def test_nonpaper_and_offhours_never_call_network_or_transport(tmp_path, monkeypatch):
    def forbidden():
        pytest.fail("network unavailable by contract")
    assert watch.tick(now=datetime(2026, 10, 14, 13, 0, tzinfo=timezone.utc),
                      path=tmp_path / "state", fetch=forbidden)["status"] == "OUTSIDE_WINDOW"
    monkeypatch.setattr(watch, "REAL_ORDER_CAPABILITY", "ENABLED")
    assert watch.tick(now=NOW, path=tmp_path / "state", fetch=forbidden)["status"] == "BLOCKED_NON_PAPER"


def test_installed_systemd_calendar_stays_in_watch_window_and_avoids_publisher():
    """Parse the actual unit with systemd, including its timezone and rollover."""
    timer = (Path(__file__).resolve().parents[1] /
             "systemd/porota-market-watch-silence-rc6.timer").read_text()
    expressions = [line.split("=", 1)[1] for line in timer.splitlines()
                   if line.startswith("OnCalendar=")]
    assert timer.count("Persistent=false") == 1
    assert expressions
    events = set()
    for expression in expressions:
        if shutil.which("systemd-analyze"):
            result = subprocess.run(
                ["systemd-analyze", "calendar", "--base-time=2026-10-26 00:00 UTC",
                 "--iterations=40", expression], check=True, capture_output=True,
                text=True, env={**os.environ, "TZ": "UTC"})
            stamps = re.findall(
                r"(?m)^\s+(?:Next elapse|Iteration #\d+): \w{3} "
                r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC$", result.stdout)
            assert len(stamps) == 40
            events.update(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc) for stamp in stamps)
        else:
            # The exact runtime image does not ship systemd. Interpret only
            # this explicit unit grammar; unsupported expressions fail closed.
            spec = re.fullmatch(
                r"Mon\.\.Fri \*-\*-\* (\d\d)(?:\.\.(\d\d))?:"
                r"(\d\d(?:,\d\d)*):00 America/Argentina/Buenos_Aires", expression)
            assert spec is not None
            first, last = int(spec[1]), int(spec[2] or spec[1])
            minutes = [int(x) for x in spec[3].split(",")]
            assert 0 <= first <= last <= 23 and all(0 <= x < 60 for x in minutes)
            events.update(datetime(2026, 10, 26, hour, minute,
                                   tzinfo=watch.ART).astimezone(timezone.utc)
                          for hour in range(first, last + 1) for minute in minutes)
    monday = sorted(moment for moment in events
                    if moment.astimezone(watch.ART).date().isoformat() == "2026-10-26")
    assert len(monday) == 38
    assert all(watch.active_window(moment) for moment in monday)
    assert all(moment.astimezone(watch.ART).minute != 50 for moment in monday)
    assert all(second - first == timedelta(minutes=10)
               for first, second in zip(monday, monday[1:]))
    assert monday[0].astimezone(watch.ART).strftime("%H:%M") == "11:03"
    assert monday[-1].astimezone(watch.ART).strftime("%H:%M") == "17:13"
