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
        assert timeout == 6
        if request.full_url == watch.RUNS_URL:
            return Response({"workflow_runs": [{"id": 1357, "event": "schedule", "path": watch.WORKFLOW,
                "head_branch": "main", "status":"completed", "run_attempt":1,
                "created_at": "2026-10-14T16:26:00Z", "run_started_at":"2026-10-14T16:27:00Z"}]})
        assert request.full_url == watch.JOBS_URL.format(1357)
        return Response({"jobs": [{"name":"watch", "status":"completed", "conclusion":"success",
            "steps":[{"name":watch.WATCH_STEP,"status":"completed","conclusion":"success",
                      "started_at":"2026-10-14T16:29:00Z"}]}]})
    assert watch.latest_schedule(opener=opener) == datetime(
        2026, 10, 14, 16, 29, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="GITHUB_RUN_IDENTITY_INVALID"):
        watch.latest_schedule(opener=lambda request, timeout: Response({"workflow_runs": [{
            "event": "workflow_dispatch", "path": watch.WORKFLOW,
            "head_branch": "main", "created_at": "2026-10-14T16:27:00Z"}]}))
    for status,attempt in [('queued',1),('in_progress',1),('completed',2)]:
        value={'workflow_runs':[{'event':'schedule','path':watch.WORKFLOW,
            'head_branch':'main','status':status,'run_attempt':attempt,
            'created_at':'2026-10-14T16:27:00Z','run_started_at':'2026-10-14T16:27:00Z'}]}
        assert watch.latest_schedule(opener=lambda request,timeout:Response(value)) is None


def test_completed_workflow_with_skipped_or_missing_host_probe_is_not_ok():
    run = {"workflow_runs": [{"id": 1357, "event": "schedule", "path": watch.WORKFLOW,
        "head_branch": "main", "status": "completed", "run_attempt": 1,
        "run_started_at": "2026-11-02T20:27:00Z"}]}
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
    for conclusion, step in (("skipped", "skipped"), ("success", "skipped"),
                             ("success", "missing")):
        jobs = {"jobs": [{"name":"watch", "status":"completed", "conclusion":conclusion,
            "steps": [] if step == "missing" else [{"name":watch.WATCH_STEP,
                "status":"completed" if step=="success" else "skipped", "conclusion":step,
                "started_at":"2026-11-02T20:29:00Z"}]}]}
        def opener(request, timeout):
            return Response(run if request.full_url == watch.RUNS_URL else jobs)
        assert watch.latest_schedule(opener=opener) is None
    assert watch.classify(datetime(2026, 11, 2, 20, 53, tzinfo=timezone.utc), None) == "SILENT"


def test_current_queued_run_does_not_false_alarm_if_prior_watch_step_is_recent():
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
    identity = {"event":"schedule", "path":watch.WORKFLOW,
                "head_branch":"main", "run_attempt":1}
    runs = {"workflow_runs": [{**identity, "id":100, "status":"in_progress"},
                               {**identity, "id":99, "status":"completed"}]}
    jobs = {"jobs":[{"name":"watch", "status":"completed", "conclusion":"success",
                      "steps":[{"name":watch.WATCH_STEP, "status":"completed",
                                "conclusion":"success", "started_at":"2026-11-02T20:47:00Z"}]}]}
    def opener(request, timeout):
        assert timeout == 6
        if request.full_url == watch.RUNS_URL:
            return Response(runs)
        assert request.full_url == watch.JOBS_URL.format(99)
        return Response(jobs)
    observed = watch.latest_schedule(opener=opener)
    assert observed == datetime(2026,11,2,20,47,tzinfo=timezone.utc)
    assert watch.classify(datetime(2026,11,2,21,3,tzinfo=timezone.utc),observed) == "OK"


def test_new_t1_watch_window_is_date_family_and_holiday_gated():
    def local(day, hour, minute, month=11):
        return datetime(2026, month, day, hour, minute, tzinfo=watch.ART)
    assert not watch.active_window(local(30, 17, 30, month=10))
    assert watch.active_window(local(2, 17, 30))
    assert watch.active_window(local(2, 18, 13))
    assert not watch.active_window(local(2, 18, 15))
    assert not watch.active_window(local(9, 17, 30))
    assert not watch.active_window(local(10, 17, 30))


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
                ["systemd-analyze", "calendar", "--base-time=2026-11-01 00:00 UTC",
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
            events.update(datetime(2026, 11, 2, hour, minute,
                                   tzinfo=watch.ART).astimezone(timezone.utc)
                          for hour in range(first, last + 1) for minute in minutes)
    monday = sorted(moment for moment in events
                    if moment.astimezone(watch.ART).date().isoformat() == "2026-11-02")
    assert len(monday) == 44
    assert all(watch.active_window(moment) for moment in monday)
    assert all(moment.astimezone(watch.ART).minute != 50 for moment in monday)
    assert all(second - first == timedelta(minutes=10)
               for first, second in zip(monday, monday[1:]))
    assert monday[0].astimezone(watch.ART).strftime("%H:%M") == "11:03"
    assert monday[-1].astimezone(watch.ART).strftime("%H:%M") == "18:13"
